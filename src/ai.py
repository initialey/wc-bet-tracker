"""Claude API による確率推定 + 具体的根拠の生成（強化版）"""
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
「{home} vs {away}」（キックオフ: {kickoff} UTC）について、以下を必ずウェブ検索で調査してから分析してください:
1. 両チームの今大会/直近の全試合結果とスコア（得点・失点の内訳）
2. 欠場・怪我・出場停止選手（固有名詞）とその影響
3. 両チームの過去の直接対決の結果
4. 予想スタメン・戦術面の注目点（可能なら）

厳守事項:
- 各reasonには検索で確認した具体的事実を最低3つ含める。例:「Aは今大会5試合9得点2失点」「直接対決は過去5戦でAの3勝」「Bは主力DFの○○が出場停止」
- 抽象語（堅守/好調/強い）だけの根拠は禁止。確認できなかった数字の創作も禁止。
- 確率は整数%でマーケット内合計100。h2hは90分の結果（引き分けあり）。bttsは両チームが1点以上取るか。

回答は次のJSONのみ。他のテキストは一切含めない:
{{"h2h": {{"home": 45, "draw": 27, "away": 28, "reason": "具体的事実3つ以上を含む根拠(150字以内)"}},
"totals": {{"over": 48, "under": 52, "reason": "両チームの総得点・総失点・直近の試合スコアを明示した根拠(150字以内)"}},
"btts": {{"yes": 50, "no": 50, "reason": "両チームの得点力と無失点試合数を明示した根拠(120字以内)"}},
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
