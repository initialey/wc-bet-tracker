"""Claude API による確率・xG・コーナー推定 + 具体的根拠の生成"""
import json
import requests

from .config import MODEL

API_URL = "https://api.anthropic.com/v1/messages"


def _parse_json(text: str) -> dict:
    clean = text.replace("```json", "").replace("```", "").strip()
    start = clean.find("{")
    end = clean.rfind("}")
    return json.loads(clean[start : end + 1])


def analyze_match(api_key: str, home: str, away: str, kickoff: str) -> dict:
    """1試合を分析。90分勝敗の確率、両チームの期待ゴール(xG)、期待コーナー数を返す"""
    prompt = f"""あなたはサッカーベッティング分析の専門家です。
「{home} vs {away}」（キックオフ: {kickoff} UTC）について、以下を必ずウェブ検索で調査してから分析してください:
1. 両チームの今大会/直近の全試合結果とスコア（得点・失点の内訳）
2. 欠場・怪我・出場停止選手（固有名詞）とその影響
3. 両チームの過去の直接対決の結果
4. 両チームの平均コーナー数・攻撃スタイル（ポゼッション型かカウンター型か）

厳守事項:
- 各reasonには検索で確認した具体的事実を最低3つ、必ず「／」で区切って列挙する。例:「A今大会5試合9得点2失点／直接対決は過去5戦Aの3勝／Bは主力DF○○が出場停止」
- 抽象語（堅守/好調/強い）だけの根拠は禁止。確認できなかった数字の創作も禁止。
- h2hの確率は整数%で合計100（90分の結果、引き分けあり）。
- xgはこの試合で各チームが取ると予想される期待ゴール数（小数1桁、通常0.5〜3.0）。
- cornersは両チーム合計の期待コーナー数（小数1桁、通常7〜13）。

回答は次のJSONのみ。他のテキストは一切含めない:
{{"h2h": {{"home": 45, "draw": 27, "away": 28, "reason": "具体的事実3つ以上を／区切りで(150字以内)"}},
"xg": {{"home": 1.6, "away": 0.9, "reason": "両チームの得点力・失点傾向の具体的数字を／区切りで(150字以内)"}},
"corners": {{"total": 9.5, "reason": "両チームの平均コーナー数・攻撃スタイルを／区切りで(120字以内)"}},
"news": "欠場・怪我・出場停止の固有名詞を含む要点(80字以内)"}}"""

    body = {
        "model": MODEL,
        "max_tokens": 3000,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
    }
    r = requests.post(
        API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=240,
    )
    r.raise_for_status()
    data = r.json()
    text = "\n".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return _parse_json(text)
