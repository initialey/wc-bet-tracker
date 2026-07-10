"""Claude API による確率推定 + 具体的根拠の生成"""
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
    """1試合を分析。確率(%)と具体的根拠を返す"""
    prompt = f"""あなたはサッカーベッティング分析の専門家です。
「{home} vs {away}」（キックオフ: {kickoff} UTC）について、ウェブ検索で
「両チームの直近の全試合結果と得失点」「欠場・怪我・出場停止選手」「直近の試合内容」
を調べてから分析してください。

厳守事項:
- 各reasonには検索で確認した具体的事実（例:「今大会5試合で9得点2失点」「主力FWの○○が負傷欠場」）を必ず含める。
- 抽象的な表現（堅守/好調など）だけの根拠は禁止。確認できなかった数字の創作も禁止。
- 確率は整数%でマーケット内合計100。h2hは90分の結果（引き分けあり）。

回答は次のJSONのみ。他のテキストは一切含めない:
{{"h2h": {{"home": 45, "draw": 27, "away": 28, "reason": "具体的数字を含む根拠(100字以内)"}},
"totals": {{"over": 48, "under": 52, "reason": "両チームの総得点・失点数を明示した根拠(100字以内)"}},
"news": "欠場・怪我の固有名詞を含む要点(60字以内)"}}"""

    body = {
        "model": MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
    }
    r = requests.post(
        API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    text = "\n".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return _parse_json(text)
