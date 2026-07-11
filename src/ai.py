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
    return json.loads(clean[clean.find("{"): clean.rfind("}") + 1])


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
- 各enは対応するjaの正確な英訳。
- h2hの確率は整数%で合計100（90分、引き分けあり）。xgは各チームの期待ゴール(0.3〜3.5)。cornersのtotalは合計期待コーナー(7〜13)。

回答は次のJSONのみ:
{{"facts": [{{"ja": "事実(時期を明記)", "en": "English"}}, ...5〜8個],
"h2h": {{"home": 45, "draw": 27, "away": 28, "verdict_ja": "勝敗の見立てと理由1文", "verdict_en": "English"}},
"xg": {{"home": 1.6, "away": 0.9}},
"market_verdicts": {{"ou": {{"ja": "合計ゴール数の見立てと理由1文", "en": "English"}},
"btts": {{"ja": "両チーム得点の有無の見立てと理由1文", "en": "English"}},
"team": {{"ja": "チーム別得点の見立てと理由1文", "en": "English"}}}},
"corners": {{"total": 9.5, "verdict_ja": "コーナー数の見立てと理由1文", "verdict_en": "English"}},
"news": "欠場情報の要点(80字以内)"}}"""
    return _call(api_key, prompt, max_tokens=6000, max_uses=8)


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
