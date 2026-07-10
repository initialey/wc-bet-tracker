"""The Odds API クライアント: 試合予定+オッズ取得、追加マーケット、結果取得"""
import sys
import requests

BASE = "https://api.the-odds-api.com/v4"


def get_upcoming(api_key: str, sport: str, regions: str) -> list:
    """今後の試合とオッズ(1X2, O/U)を取得"""
    r = requests.get(
        f"{BASE}/sports/{sport}/odds",
        params={
            "apiKey": api_key,
            "regions": regions,
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_extra_markets(api_key: str, sport: str, event_id: str, regions: str) -> dict:
    """試合単位の追加マーケット(両チーム得点, 引分返金)を取得。失敗しても空を返す"""
    try:
        r = requests.get(
            f"{BASE}/sports/{sport}/events/{event_id}/odds",
            params={
                "apiKey": api_key,
                "regions": regions,
                "markets": "btts,draw_no_bet",
                "oddsFormat": "decimal",
            },
            timeout=30,
        )
        r.raise_for_status()
        ev = r.json()
    except Exception as e:
        print(f"[warn] extra markets failed for {event_id}: {e}", file=sys.stderr)
        return {"btts": {}, "dnb": {}}

    out = {"btts": {}, "dnb": {}}
    for bm in ev.get("bookmakers", []):
        for mk in bm.get("markets", []):
            if mk["key"] == "btts":
                for o in mk["outcomes"]:
                    out["btts"][o["name"]] = max(out["btts"].get(o["name"], 0), o["price"])
            elif mk["key"] == "draw_no_bet":
                for o in mk["outcomes"]:
                    out["dnb"][o["name"]] = max(out["dnb"].get(o["name"], 0), o["price"])
    return out


def get_scores(api_key: str, sport: str, days_from: int = 3) -> list:
    """直近の確定スコアを取得（答え合わせ用）"""
    r = requests.get(
        f"{BASE}/sports/{sport}/scores",
        params={"apiKey": api_key, "daysFrom": days_from},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def best_odds(event: dict) -> dict:
    """全ブックメーカー中のベストオッズを選択肢別に抽出"""
    out = {"h2h": {}, "totals": {}}
    for bm in event.get("bookmakers", []):
        for mk in bm.get("markets", []):
            if mk["key"] == "h2h":
                for o in mk["outcomes"]:
                    name = o["name"]
                    out["h2h"][name] = max(out["h2h"].get(name, 0), o["price"])
            elif mk["key"] == "totals":
                for o in mk["outcomes"]:
                    if abs(float(o.get("point", 0)) - 2.5) < 0.01:
                        name = f"{o['name']} 2.5"
                        out["totals"][name] = max(out["totals"].get(name, 0), o["price"])
    return out
