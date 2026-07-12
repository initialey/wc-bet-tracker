"""Claude API による分析（日英根拠・サッカー用/汎用）"""
import json
import requests

from .config import MODEL

API_URL = "https://api.anthropic.com/v1/messages"

RULES = """厳守事項:
- 各reasonには検索で確認した具体的事実を最低3つ、必ず「／」で区切って列挙する。
- 各reason_enはそのreasonの正確な英訳（事実も区切りも同じ、区切りは" / "）。
- 抽象語（堅守/好調/強い）だけの根拠は禁止。確認できなかった数字の創作も禁止。"""


def _call(api_key: str, prompt: str, max_tokens: int = 4000, max_uses: int = 6) -> dict:
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
    }
    r = requests.post(
        API_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=240,
    )
    r.raise_for_status()
    data = r.json()
    text = "\n".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    clean = text.replace("```json", "").replace("```", "").strip()
    start = clean.find("{")
    try:
        return json.loads(clean[start: clean.rfind("}") + 1])
    except json.JSONDecodeError:
        # モデルがJSONの後に余計なテキストを付けた場合("Extra data")の救済:
        # 先頭の完全なJSONオブジェクトだけを取り出す
        return json.JSONDecoder().raw_decode(clean[start:])[0]


def analyze_match(api_key: str, home: str, away: str, kickoff: str) -> dict:
    """サッカー用: 事実リスト + マーケット別の結論(verdict) + h2h確率・xG・コーナー"""
    prompt = f"""あなたはサッカーベッティング分析の専門家です。
「{home} vs {away}」（キックオフ: {kickoff} UTC）について、以下を必ずウェブ検索で調査してから分析してください:
1. 両チームの直近試合の結果とスコア（いつ・どの大会か） 2. 欠場・怪我・出場停止選手（固有名詞）
3. 過去の直接対決 4. 平均コーナー数・攻撃スタイル

厳守事項:
- factsは検索で確認できた事実を5〜8個。各factに必ず時期（日付やラウンド。例:「7/8のR16でエジプトに3-2勝利」）を含める。2026年の直近情報を優先する。
- 確認できなかった数字の創作は禁止。特にxGなどの高度な指標は、検索で明確なソースが見つかった場合だけ使い、見つからなければ得点・失点・勝敗などの確実な数字だけで根拠を作る。
- 出力前に数字の妥当性を自己チェックする: チームの1試合xGは通常0.3〜3.5の範囲。選手1人の欠場でチーム全体の数値が2倍以上変わるような主張はしない。
- 各verdictは「〜なので〜が有力」の形の1文（50字以内）で、中学生にも分かる平易な言葉で書く。
- 単位や割合の表記に半角スラッシュ「/」を使わない（「2.1得点/試合」ではなく「1試合平均2.1得点」と書く）。日付の「7/8」は使ってよい。
- 文中に全角スラッシュ「／」と句点「。」を使わない（システムの区切り文字のため）。
- 日本語(ja)の文中でH2H・xG・GS・QF・R16・BTTSなどの英略語を使わない（「直接対決」「期待ゴール」「グループステージ」「準々決勝」「決勝トーナメント1回戦」のように日本語で書く。チーム名・選手名は原語のままでよい）。
- 各enは対応するjaの正確な英訳。
- h2hの確率は整数%で合計100（90分、引き分けあり）。xgは各チームの期待ゴール(0.3〜3.5)。cornersのtotalは合計期待コーナー(7〜13)。

回答は次のJSONのみ:
{{"facts": [{{"ja": "事実(時期を明記)", "en": "English"}}, ...5〜8個],
"h2h": {{"home": 45, "draw": 27, "away": 28, "verdict_ja": "勝敗の見立てと理由1文", "verdict_en": "English"}},
"xg": {{"home": 1.6, "away": 0.9}},
"market_verdicts": {{"ou": {{"ja": "合計ゴール数の見立てと理由1文", "en": "English"}},
"btts": {{"ja": "両チーム得点の有無の見立てと理由1文", "en": "English"}}}},
"corners": {{"total": 9.5, "verdict_ja": "コーナー数の見立てと理由1文", "verdict_en": "English"}},
"news": "欠場情報の要点(80字以内)"}}"""
    return _call(api_key, prompt, max_tokens=6000, max_uses=8)


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


def analyze_mlb(api_key: str, ctx: dict, total_line: float, fav_team: str) -> dict:
    """MLB専用: Stats APIの構造化データをプロンプトに直接埋め込み、
    ウェブ検索は負傷者・ラインナップの最新確認の補助(max_uses=4)に留める。"""
    home, away = ctx["home"], ctx["away"]
    data = "\n".join([
        f"球場: {ctx.get('venue', '不明')}",
        _mlb_pitcher_line(f"ホーム先発({home})", ctx.get("home_pitcher", {})),
        _mlb_pitcher_line(f"アウェイ先発({away})", ctx.get("away_pitcher", {})),
        _mlb_form_line(f"ホーム({home})", ctx.get("home_form", {})),
        _mlb_form_line(f"アウェイ({away})", ctx.get("away_form", {})),
        f"市場の主軸: ランラインの本命(favorite)は {fav_team}(-1.5)。合計得点の主要ライン: {total_line}",
    ])
    prompt = f"""あなたはMLB(メジャーリーグ)ベッティング分析の専門家です。
「{away} @ {home}」を分析します。野球は先発投手が最大の変数です。
以下はMLB公式Stats APIから取得した確定データです(これを分析の土台とし、数値はこのまま正とする):

{data}

ウェブ検索は次の最新確認だけに使ってください(最大4回): 各先発の登板が予定通りか(急な変更・故障)、
主力野手の欠場・当日ラインナップ、天候による影響。上記のStats APIデータを検索結果で上書きしないこと。

厳守事項:
- factsは5〜8個。両先発の名前と今季成績(防御率・WHIP)はStats APIの数値を用いて必ずfactsに含める。
- 各factに時期を明記する(例:「7/5の登板は4.0回4自責」)。2026年の直近情報を優先。
- 確認できなかった数字の創作は禁止。検索で裏取りできない高度指標は使わない。
- 出力前に妥当性を自己チェック: 先発防御率は通常2.0〜6.5、1試合の合計得点は通常6〜12点の範囲。
- 各verdictは「〜なので〜が有力」の形の1文(50字以内)で、中学生にも分かる平易な言葉。
- 単位や割合に半角スラッシュ「/」を使わない(「5.1点/試合」ではなく「1試合平均5.1点」)。日付の「7/5」は可。
- 文中に全角スラッシュ「／」と句点「。」を使わない(システムの区切り文字)。
- 日本語(ja)の文中でH2H等の英略語を使わない(「直接対決」のように日本語で書く。WHIPなど定訳のない野球指標はそのままでよい)。
- 各enは対応するjaの正確な英訳。
- winは引き分けなしでhome+away=100(整数%)。totalのexpectedは合計得点の期待値(数値)。
  runlineのfav_coverは本命{fav_team}が2点差以上で勝つ確率(整数%)。

回答は次のJSONのみ:
{{"facts": [{{"ja": "事実(時期を明記)", "en": "English"}}, ...5〜8個],
"win": {{"home": 55, "away": 45, "verdict_ja": "勝敗の見立てと理由1文", "verdict_en": "English"}},
"total": {{"expected": {total_line}, "verdict_ja": "合計得点の見立てと理由1文", "verdict_en": "English"}},
"runline": {{"fav_cover": 45, "verdict_ja": "ランラインの見立てと理由1文", "verdict_en": "English"}},
"news": "欠場・ラインナップの要点(80字以内)"}}"""
    return _call(api_key, prompt, max_tokens=5000, max_uses=4)


def analyze_generic(api_key: str, sport_label: str, home: str, away: str,
                    kickoff: str, three_way: bool, total_line: float) -> dict:
    """サッカー以外用: 勝敗確率と合計スコア期待値 + 日英根拠"""
    draw_part = '"draw": 10, ' if three_way else ""
    draw_rule = "確率はhome+draw+away=100。" if three_way else "確率はhome+away=100（引き分けなし）。"
    prompt = f"""あなたは{sport_label}のベッティング分析の専門家です。
「{home} vs {away}」（開始: {kickoff} UTC）について、両チームの直近成績・主力選手の出場状況・
直接対決・ホームアドバンテージを必ずウェブ検索で調査してから分析してください。

{RULES}
- {draw_rule}
- expected_totalは両チーム合計スコアの期待値（参考ライン: {total_line}）。

回答は次のJSONのみ:
{{"win": {{"home": 55, {draw_part}"away": 45, "reason": "日本語150字以内", "reason_en": "English"}},
"total": {{"expected": {total_line}, "reason": "日本語120字以内", "reason_en": "English"}},
"news": "欠場情報の要点(80字以内)"}}"""
    return _call(api_key, prompt)
