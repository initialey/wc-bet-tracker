"""The Odds API クライアント: 試合+オッズ、追加マーケット、アウトライト、結果、残クォータ"""
import sys
import requests

BASE = "https://api.the-odds-api.com/v4"

# 直近のレスポンスヘッダから取得したAPI残量
QUOTA = {"remaining": None, "used": None}


def _get(url: str, params: dict):
    r = requests.get(url, params=params, timeout=30)
    if "x-requests-remaining" in r.headers:
        QUOTA["remaining"] = r.headers.get("x-requests-remaining")
        QUOTA["used"] = r.headers.get("x-requests-used")
    r.raise_for_status()
    return r.json()


def get_upcoming(api_key: str, sport: str, regions: str) -> list:
    return _get(f"{BASE}/sports/{sport}/odds",
                {"apiKey": api_key, "regions": regions,
                 "markets": "h2h,totals", "oddsFormat": "decimal"})


def get_extra_markets(api_key: str, sport: str, event_id: str, regions: str) -> dict:
    out = {"btts": {}, "dnb": {}, "totals": {}, "team_totals": {}, "corners": {}}
    markets = "btts,draw_no_bet,alternate_totals,team_totals,totals_corners,alternate_totals_corners"
    try:
        ev = _get(f"{BASE}/sports/{sport}/events/{event_id}/odds",
                  {"apiKey": api_key, "regions": regions,
                   "markets": markets, "oddsFormat": "decimal"})
    except Exception as e:
        print(f"[warn] extra markets failed for {event_id}: {e}", file=sys.stderr)
        return out

    for bm in ev.get("bookmakers", []):
        for mk in bm.get("markets", []):
            key = mk["key"]
            for o in mk.get("outcomes", []):
                name, price = o.get("name"), o.get("price", 0)
                point = o.get("point")
                if key == "btts":
                    out["btts"][name] = max(out["btts"].get(name, 0), price)
                elif key == "draw_no_bet":
                    out["dnb"][name] = max(out["dnb"].get(name, 0), price)
                elif key in ("totals", "alternate_totals") and point is not None:
                    if point in (1.5, 2.5, 3.5):
                        k2 = f"{name} {point}"
                        out["totals"][k2] = max(out["totals"].get(k2, 0), price)
                elif key == "team_totals" and point is not None:
                    team = o.get("description", "")
                    if abs(point - 1.5) < 0.01 and team:
                        k2 = (team, name)
                        out["team_totals"][k2] = max(out["team_totals"].get(k2, 0), price)
                elif key in ("totals_corners", "alternate_totals_corners") and point is not None:
                    k2 = f"{name} {point}"
                    out["corners"][k2] = max(out["corners"].get(k2, 0), price)
    return out


def get_outrights(api_key: str, sport_key: str, regions: str) -> list:
    """優勝オッズ等。[(名前, ベストオッズ)] を返す"""
    try:
        events = _get(f"{BASE}/sports/{sport_key}/odds",
                      {"apiKey": api_key, "regions": regions,
                       "markets": "outrights", "oddsFormat": "decimal"})
    except Exception as e:
        print(f"[warn] outrights failed for {sport_key}: {e}", file=sys.stderr)
        return []
    best = {}
    for ev in events:
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                if mk["key"] != "outrights":
                    continue
                for o in mk.get("outcomes", []):
                    best[o["name"]] = max(best.get(o["name"], 0), o["price"])
    return sorted(best.items(), key=lambda x: x[1])


def get_scores(api_key: str, sport: str, days_from: int = 3) -> list:
    return _get(f"{BASE}/sports/{sport}/scores",
                {"apiKey": api_key, "daysFrom": days_from})


def best_odds(event: dict) -> dict:
    out = {"h2h": {}, "totals": {}}
    for bm in event.get("bookmakers", []):
        for mk in bm.get("markets", []):
            if mk["key"] == "h2h":
                for o in mk["outcomes"]:
                    out["h2h"][o["name"]] = max(out["h2h"].get(o["name"], 0), o["price"])
            elif mk["key"] == "totals":
                for o in mk["outcomes"]:
                    point = float(o.get("point", 0))
                    if point in (1.5, 2.5, 3.5):
                        k = f"{o['name']} {point}"
                        out["totals"][k] = max(out["totals"].get(k, 0), o["price"])
    return out
