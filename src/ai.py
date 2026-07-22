"""Claude API による分析（日英根拠・サッカー用/汎用）

API費用削減の3本柱:
1. プロンプトキャッシング: 各analyze_*の固定部分(ルール・出力フォーマット・市場定義)を
   systemブロック(cache_control: ephemeral)に分離。試合ごとに変わる動的な部分
   (チーム名・オッズ・Stats APIデータ)はuserメッセージ側でキャッシュ対象外のまま送る。
   同一実行内で同種の試合を連続分析するため、2件目以降はcache_read_input_tokensが
   乗ってヒットする(1件目はcache_creation_input_tokensで書き込み)。
2. 二段階スクリーニング: screen_match()でHaiku(検索なし・軽量)による機械的な足切りを行い、
   市場が既に一方的(暗示勝率97%以上)またはデータ明確に不足の試合は本分析(検索付き)に進めない。
   対象外リーグ・費用ガードはconfig.SPORTS/MAX_GAMES_PER_DAYで既に絞り込み済みのため、
   ここでは市場の歪みとデータ充足の2点だけを判定する。
3. 検索の絞り込み: Poisson/正規分布の確率計算はすべてsrc/model.py(Python側)で完結しており、
   AIは検索で確認できた事実(怪我人・直近成績等)とxG/期待値などの入力数値だけを返す。
   各analyze_*のウェブ検索は必要最小限のトピックに限定し、max_usesも引き下げている。

usage(cache_read_input_tokens等)は呼び出しごとに[cost]ログへ出力し、USAGE_LOGに蓄積する
(モデル別・処理別のコストレポート用。usage_summary()で集計)。"""
import json
import sys
import requests

from .config import MODEL

API_URL = "https://api.anthropic.com/v1/messages"
MODEL_LIGHT = "claude-haiku-4-5"   # 理由文整合性チェック・一次スクリーニング用の軽量モデル(低コスト)

RULES = """厳守事項:
- 各reasonには検索で確認した事実を最大3つ、必ず「／」で区切って列挙する。
- 各事実は1行(40字以内)で、「どっちが強いか」「最近の調子」「怪我人・欠場」レベルの誰でも分かる内容だけを書く。
- 専門用語や細かい成績数値の羅列は禁止(例:「xG」ではなく「決定機の多さ」と言い換える)。それ以外の詳細データは出力しない。
- 各reason_enはそのreasonの正確な英訳（事実も区切りも同じ、区切りは" / "）。
- 確認できなかった事実の創作は禁止。"""

USAGE_LOG = []   # [{"label", "model", "input_tokens", "output_tokens",
                 #   "cache_creation_input_tokens", "cache_read_input_tokens"}, ...]


def _log_usage(label: str, model: str, usage: dict):
    """API呼び出しのトークン使用量をログとUSAGE_LOGに記録する(モデル別・処理別コストレポート用)"""
    entry = {
        "label": label, "model": model,
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
    }
    USAGE_LOG.append(entry)
    print(f"[cost] {label} model={model} in={entry['input_tokens']} out={entry['output_tokens']} "
          f"cache_write={entry['cache_creation_input_tokens']} "
          f"cache_read={entry['cache_read_input_tokens']}", file=sys.stderr)


def usage_summary() -> dict:
    """USAGE_LOGをモデル別・処理(label)別に集計する(変更前後のコスト比較レポート用)。
    戻り値: {(label, model): {"calls", "input_tokens", "output_tokens",
                              "cache_creation_input_tokens", "cache_read_input_tokens"}}"""
    agg = {}
    for e in USAGE_LOG:
        key = (e["label"], e["model"])
        a = agg.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})
        a["calls"] += 1
        for k in ("input_tokens", "output_tokens",
                 "cache_creation_input_tokens", "cache_read_input_tokens"):
            a[k] += e[k]
    return agg


def _call(api_key: str, prompt: str, system=None, max_tokens: int = 4000, max_uses: int = 6,
          label: str = "analyze") -> dict:
    """system: キャッシュ対象の固定ブロック配列(例: [{"type":"text","text":...,
    "cache_control":{"type":"ephemeral"}}])。promptは試合ごとに変わる動的部分のみ"""
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
    }
    if system:
        body["system"] = system
    r = requests.post(
        API_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=240,
    )
    r.raise_for_status()
    data = r.json()
    _log_usage(label, MODEL, data.get("usage", {}))
    return _extract_json(data)


def _extract_json(data: dict) -> dict:
    text = "\n".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    clean = text.replace("```json", "").replace("```", "").strip()
    start = clean.find("{")
    try:
        return json.loads(clean[start: clean.rfind("}") + 1])
    except json.JSONDecodeError:
        # モデルがJSONの後に余計なテキストを付けた場合("Extra data")の救済:
        # 先頭の完全なJSONオブジェクトだけを取り出す
        return json.JSONDecoder().raw_decode(clean[start:])[0]


def _call_light(api_key: str, prompt: str, max_tokens: int = 300, label: str = "light") -> dict:
    """軽量モデルによる小さな判定・生成呼び出し(ウェブ検索なし)。
    プロンプトが毎回ほぼ一意(事実・ピック等を含む)でHaikuのキャッシュ最小長(2048トークン)に
    届かないため、cache_controlは付けない(付けても書き込み費用が乗るだけで得しない)"""
    body = {"model": MODEL_LIGHT, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    r = requests.post(
        API_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    _log_usage(label, MODEL_LIGHT, data.get("usage", {}))
    return _extract_json(data)


def screen_match(api_key: str, league: str, match: str, fav_prob, n_bookmakers: int,
                 data_note: str = "") -> dict:
    """一次スクリーニング(Haiku・検索なし・低コスト)。本分析(ウェブ検索付き、高コスト)に
    進める前に、市場が既に一方的(歪みが大きい)か、データが明確に不足している試合を
    機械的に除外する。対象外リーグ・1日あたりの分析上限はconfig.SPORTS/MAX_GAMES_PER_DAYで
    呼び出し側が既に絞り込み済みのため、ここでは以下2点だけを判定する:
    - 市場の暗示勝率(devig後)が極端(97%以上)で分析の付加価値が薄い
    - データ状況(例: MLBの先発投手未発表)が明確に不足している
    戻り値 {"proceed": bool, "reason_ja": str, "reason_en": str}。
    判定不能・キー欠損時はproceed=True(安全側=分析継続)にフォールバックする"""
    fav_s = f"{fav_prob:.0f}%" if fav_prob is not None else "不明(オッズ不足)"
    prompt = f"""スポーツベッティング分析のコスト管理担当です。以下の試合を、
ウェブ検索付きの本分析(コストがかかる)に進めるべきか機械的に判定してください。

試合: {league} {match}
市場の暗示勝率(favorite側、devig後): {fav_s}({n_bookmakers}社のオッズから算出)
データ状況: {data_note or "特記事項なし"}

除外基準(いずれかに該当すればproceed=false):
- 市場の暗示勝率が97%以上など、勝敗がほぼ確定的で詳細分析の付加価値が薄い
- データ状況に明確な不足がある(例: 先発投手未発表、主要情報が欠落)
上記に該当しなければ原則 proceed=true(通常の試合は基本的に進める)。独自の外部知識は使わず、
上記の数値・データ状況だけで判定すること。

回答は次のJSONのみ: {{"proceed": true または false, "reason_ja": "20字以内", "reason_en": "20 words or less"}}"""
    out = _call_light(api_key, prompt, max_tokens=200, label="screen_match")
    return {"proceed": bool(out.get("proceed", True)),
            "reason_ja": out.get("reason_ja", ""), "reason_en": out.get("reason_en", "")}


def check_verdict(api_key: str, match: str, market: str, pick: str, verdict_ja: str) -> bool:
    """理由文の結論部分が選択したベット側と方向矛盾していないかを軽量モデルで検証。
    ピックはモデル計算(ブレンド)で選ばれるため、AI由来の結論文が逆サイドを
    推しているケース(例: ピック「オーバー1.5」に結論「合計2ゴール以下が有力」)を検出する"""
    prompt = f"""スポーツベッティング予想の表示検品です。以下のベット選択と結論文が「方向として」矛盾していないかを判定してください。

試合: {match}
マーケット: {market}
選択(ベット): {pick}
結論文: 「{verdict_ja}」

矛盾の例: 選択がオーバーなのに結論文がロースコア・堅い試合・合計N点以下を推している/選択がアンダーなのに打ち合い・大量得点を推している/選択したチームと逆のチームの勝利を推している/選択「あり」なのに片方の無得点を推している。
注意: オーバー1.5に対する「合計2〜3点の見込み」のように、ライン次第で両立する内容は矛盾ではない。事実の羅列や消耗・欠場などの背景説明だけで方向を示さない文も矛盾ではない。

回答は次のJSONのみ: {{"consistent": true または false, "why": "20字以内"}}"""
    out = _call_light(api_key, prompt, max_tokens=200, label="check_verdict")
    return bool(out.get("consistent"))


def rewrite_verdict(api_key: str, match: str, market: str, pick: str, facts: list) -> dict:
    """選択済みのベット側を明示して結論文を再生成する(整合性チェックで矛盾が出た場合の救済)。
    戻り値 {"ja": ..., "en": ...}"""
    facts_ja = "、".join((f.get("ja") or "") for f in (facts or [])[:8] if f.get("ja"))
    prompt = f"""あなたはスポーツベッティング分析の専門家です。

試合: {match}
選択: {market}: {pick}

この選択を支持する根拠を1文で書け。選択と矛盾する結論は書くな。

厳守事項:
- 「〜なので{pick}が有力」の形の1文(50字以内)。断定や誇張は避ける。
- 根拠には下の参考事実だけを使い、数字の創作は禁止。事実が選択を強く支持しない場合は「僅差だが〜」のように控えめに書く。
- 文中に全角スラッシュ「／」と句点「。」を使わない(システムの区切り文字)。

参考事実: {facts_ja or "(なし)"}

回答は次のJSONのみ: {{"ja": "結論文", "en": "Accurate English translation"}}"""
    return _call_light(api_key, prompt, max_tokens=400, label="rewrite_verdict")


# --- サッカー用: 固定ルール・出力フォーマットのみ(キャッシュ対象)。
# チーム名・キックオフは含めない(動的部分はanalyze_match内でuserメッセージへ) ---
SOCCER_SYSTEM = """あなたはサッカーベッティング分析の専門家です。
与えられた試合について、次の情報を必要最小限のウェブ検索で確認してから分析してください(優先順):
1. 直近成績(直近5試合の結果) 2. 欠場・怪我・出場停止選手(固有名詞)
3. 過去の直接対決 4. 平均コーナー数(コーナー予想に使うため簡潔に)
検索は上記確認に必要な最小回数に留め、無関係な調査は行わない。

厳守事項:
- factsは検索で確認できた事実を最大3個。「どっちが強いか」「最近の調子」「怪我人・欠場」レベルの誰でも分かる内容だけを選ぶ。
- 各factは1行(40字以内)の平易な日本語。専門用語や細かい成績数値の羅列は禁止(例:「xG1.8」ではなく「決定機を多く作れている」、「直近5戦4勝1敗・平均2.1得点」ではなく「ここ5試合は4勝と好調」)。それ以外の詳細データはfactsに出力しない。
- 確認できなかった事実の創作は禁止。迷ったら得点・失点・勝敗など確実な情報だけで根拠を作る。
- 出力前に数字の妥当性を自己チェックする: チームの1試合xGは通常0.3〜3.5の範囲。選手1人の欠場でチーム全体の数値が2倍以上変わるような主張はしない。
- 各verdictは「〜なので〜が有力」の形の1文（50字以内）で、中学生にも分かる平易な言葉で書く。
- 単位や割合の表記に半角スラッシュ「/」を使わない（「2.1得点/試合」ではなく「1試合平均2.1得点」と書く）。日付の「7/8」は使ってよい。
- 文中に全角スラッシュ「／」と句点「。」を使わない（システムの区切り文字のため）。
- 日本語(ja)の文中でH2H・xG・GS・QF・R16・BTTSなどの英略語を使わない（「直接対決」「決定機の多さ」「グループステージ」「準々決勝」「決勝トーナメント1回戦」のように日本語で書く。チーム名・選手名は原語のままでよい）。
- 各enは対応するjaの正確な英訳。
- h2hの確率は整数%で合計100（90分、引き分けあり）。xgは各チームの期待ゴール(0.3〜3.5)。cornersのtotalは合計期待コーナー(7〜13)。
  ※h2h・xg・cornersなどの数値は計算に使う内部データなので従来通り正確に出力する(平易化の対象はfactsとverdictの文章だけ)。

回答は次のJSONのみ:
{"facts": [{"ja": "誰でも分かる事実1行", "en": "English"}, ...最大3個],
"h2h": {"home": 45, "draw": 27, "away": 28, "verdict_ja": "勝敗の見立てと理由1文", "verdict_en": "English"},
"xg": {"home": 1.6, "away": 0.9},
"market_verdicts": {"ou": {"ja": "合計ゴール数の見立てと理由1文", "en": "English"},
"btts": {"ja": "両チーム得点の有無の見立てと理由1文", "en": "English"}},
"corners": {"total": 9.5, "verdict_ja": "コーナー数の見立てと理由1文", "verdict_en": "English"},
"news": "欠場情報の要点(80字以内)"}"""


def analyze_match(api_key: str, home: str, away: str, kickoff: str) -> dict:
    """サッカー用: 事実リスト + マーケット別の結論(verdict) + h2h確率・xG・コーナー。
    固定ルール(SOCCER_SYSTEM)はキャッシュし、試合ごとに変わるチーム名・時刻だけを
    userメッセージで渡す"""
    prompt = f"分析対象の試合:「{home} vs {away}」（キックオフ: {kickoff} UTC）。上記のルールと出力形式に従って分析してください。"
    return _call(api_key, prompt,
                system=[{"type": "text", "text": SOCCER_SYSTEM,
                        "cache_control": {"type": "ephemeral"}}],
                max_tokens=6000, max_uses=5, label="analyze_match")


def _mlb_pitcher_line(label: str, p: dict) -> str:
    recent = "、".join(
        f"{r.get('date', '?')} {r.get('ip', '?')}回 {r.get('er', '?')}自責 {r.get('so', '?')}K"
        for r in (p.get("recent") or [])) or "データなし"
    return (f"{label}: {p.get('name', '未定')} — 防御率{p.get('era', '?')} "
            f"WHIP{p.get('whip', '?')} 投球回{p.get('ip', '?')} 先発{p.get('gs', '?')}試合。"
            f"直近3登板: {recent}")


def _mlb_form_line(label: str, f: dict) -> str:
    if not f:
        return f"{label}: 直近成績データなし"
    return (f"{label}: 直近{f.get('n', 10)}試合 {f.get('last10', '?')}、"
            f"1試合平均得点{f.get('rpg', '?')}・平均失点{f.get('rapg', '?')}")


# --- MLB用: 固定ルール・出力フォーマットのみ(キャッシュ対象)。
# Stats APIの試合ごとのデータはanalyze_mlb内でuserメッセージへ ---
MLB_SYSTEM = """あなたはMLB(メジャーリーグ)ベッティング分析の専門家です。野球は先発投手が最大の変数です。
ユーザーメッセージで示すMLB公式Stats APIから取得した確定データ(先発投手成績・球場・直近成績)を
分析の土台とし、数値はそのまま正として扱ってください。

ウェブ検索は次の最新確認だけに使ってください(最大3回): 各先発の登板が予定通りか(急な変更・故障)、
主力野手の欠場・当日ラインナップ、天候による影響。Stats APIデータを検索結果で上書きしないこと。

厳守事項:
- factsは最大3個。「先発投手の良し悪し」「どっちが強いか・最近の調子」「怪我人・欠場」レベルの誰でも分かる内容だけを選ぶ。
- 各factは1行(40字以内)の平易な日本語。防御率・WHIPなどの専門用語や細かい成績数値の羅列は禁止(例:「防御率2.29・WHIP1.17」ではなく「先発投手は今季安定して失点が少ない」のように言い換える)。それ以外の詳細データはfactsに出力しない。
- 確認できなかった事実の創作は禁止。Stats APIデータと矛盾する内容も禁止。
- 各verdictは「〜なので〜が有力」の形の1文(50字以内)で、中学生にも分かる平易な言葉。
- 単位や割合に半角スラッシュ「/」を使わない(「5.1点/試合」ではなく「1試合平均5.1点」)。日付の「7/5」は可。
- 文中に全角スラッシュ「／」と句点「。」を使わない(システムの区切り文字)。
- 日本語(ja)の文中でH2H等の英略語を使わない(「直接対決」のように日本語で書く)。
- 各enは対応するjaの正確な英訳。
- winは引き分けなしでhome+away=100(整数%)。totalのexpectedはユーザーメッセージの参考ラインを
  起点に、データに基づいて調整した合計得点の期待値(数値)。
  runlineのfav_coverはユーザーメッセージで示す本命チームが2点差以上で勝つ確率(整数%)。
  ※win・total・runlineの数値は計算に使う内部データなので、Stats APIの詳細データを踏まえて従来通り正確に出す(平易化の対象はfactsとverdictの文章だけ)。

回答は次のJSONのみ:
{"facts": [{"ja": "誰でも分かる事実1行", "en": "English"}, ...最大3個],
"win": {"home": 55, "away": 45, "verdict_ja": "勝敗の見立てと理由1文", "verdict_en": "English"},
"total": {"expected": 8.5, "verdict_ja": "合計得点の見立てと理由1文", "verdict_en": "English"},
"runline": {"fav_cover": 45, "verdict_ja": "ランラインの見立てと理由1文", "verdict_en": "English"},
"news": "欠場・ラインナップの要点(80字以内)"}"""


def analyze_mlb(api_key: str, ctx: dict, total_line: float, fav_team: str) -> dict:
    """MLB専用: Stats APIの構造化データをプロンプトに直接埋め込み、
    ウェブ検索は負傷者・ラインナップの最新確認の補助(max_uses=3)に留める。
    固定ルール(MLB_SYSTEM)はキャッシュし、試合ごとのStats APIデータ・チーム名・
    ラインだけをuserメッセージで渡す"""
    home, away = ctx["home"], ctx["away"]
    data = "\n".join([
        f"球場: {ctx.get('venue', '不明')}",
        _mlb_pitcher_line(f"ホーム先発({home})", ctx.get("home_pitcher", {})),
        _mlb_pitcher_line(f"アウェイ先発({away})", ctx.get("away_pitcher", {})),
        _mlb_form_line(f"ホーム({home})", ctx.get("home_form", {})),
        _mlb_form_line(f"アウェイ({away})", ctx.get("away_form", {})),
    ])
    prompt = f"""「{away} @ {home}」を分析します。
以下はMLB公式Stats APIから取得した確定データです:

{data}

市場の主軸: ランラインの本命(favorite)は {fav_team}(-1.5)。合計得点の参考ライン: {total_line}

上記のルールと出力形式に従って分析してください。"""
    return _call(api_key, prompt,
                system=[{"type": "text", "text": MLB_SYSTEM,
                        "cache_control": {"type": "ephemeral"}}],
                max_tokens=5000, max_uses=3, label="analyze_mlb")


def _generic_system(sport_label: str, three_way: bool) -> str:
    """汎用スポーツ用の固定ルール・出力フォーマット(キャッシュ対象)。
    sport_label・three_way(引分の有無でJSON形状が変わる)ごとに内容が変わるため、
    同じ組み合わせの試合が連続する間(NBA複数試合など)はキャッシュがヒットする"""
    draw_part = '"draw": 10, ' if three_way else ""
    draw_rule = "確率はhome+draw+away=100。" if three_way else "確率はhome+away=100（引き分けなし）。"
    return f"""あなたは{sport_label}のベッティング分析の専門家です。
与えられた試合について、両チームの直近成績・主力選手の出場状況・直接対決・ホームアドバンテージを
必要最小限のウェブ検索で確認してから分析してください。検索は上記確認に必要な最小回数に留める。

{RULES}
- {draw_rule}
- expected_totalはユーザーメッセージの参考ラインを起点に、データに基づいて調整した
  両チーム合計スコアの期待値。

回答は次のJSONのみ:
{{"win": {{"home": 55, {draw_part}"away": 45, "reason": "日本語150字以内", "reason_en": "English"}},
"total": {{"expected": 8.5, "reason": "日本語120字以内", "reason_en": "English"}},
"news": "欠場情報の要点(80字以内)"}}"""


def analyze_generic(api_key: str, sport_label: str, home: str, away: str,
                    kickoff: str, three_way: bool, total_line: float) -> dict:
    """サッカー以外用: 勝敗確率と合計スコア期待値 + 日英根拠。
    固定ルール(_generic_system)はキャッシュし、試合ごとのチーム名・時刻・参考ラインだけを
    userメッセージで渡す"""
    prompt = f"""試合:「{home} vs {away}」（開始: {kickoff} UTC）
参考ライン(合計得点): {total_line}

上記のルールと出力形式に従って分析してください。"""
    system_text = _generic_system(sport_label, three_way)
    return _call(api_key, prompt,
                system=[{"type": "text", "text": system_text,
                        "cache_control": {"type": "ephemeral"}}],
                max_tokens=4000, max_uses=4, label="analyze_generic")
