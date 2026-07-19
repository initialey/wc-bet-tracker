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


def get_upcoming(api_key: str, sport: str, regions: str, markets: str = "h2h,totals") -> list:
    return _get(f"{BASE}/sports/{sport}/odds",
                {"apiKey": api_key, "regions": regions,
                 "markets": markets, "oddsFormat": "decimal"})


# 追加マーケット。試合/スポーツによっては未提供のものがあり、まとめてリクエストすると
# 未提供マーケットが1つでも混ざると API 全体が 422 になるため、失敗時は1つずつ取得する。
# コーナーは専用リクエストを投げず、一括取得のレスポンスに含まれていた場合のみ拾う「オマケ」扱い。
# → 一括取得(CORE+CORNER)が成功すればコーナーも取得。失敗時のフォールバックはCOREのみで、
#    コーナー単独のリクエストは行わない（AI消費/オッズAPI消費を削減）。
CORE_EXTRA_MARKETS = ["btts", "draw_no_bet", "alternate_totals", "team_totals",
                      "spreads", "alternate_spreads"]
CORNER_MARKETS = ["totals_corners", "alternate_totals_corners"]
EXTRA_MARKETS = CORE_EXTRA_MARKETS + CORNER_MARKETS


def _take(prices: dict, titles: dict, key, price, title) -> None:
    """最大オッズと、その最良オッズを提供したブックメーカー名を同時に記録する"""
    if price > prices.get(key, 0):
        prices[key] = price
        titles[key] = title


def _parse_extra_bookmakers(bookmakers: list, out: dict) -> None:
    for bm in bookmakers:
        title = bm.get("title") or bm.get("key", "")
        for mk in bm.get("markets", []):
            key = mk["key"]
            for o in mk.get("outcomes", []):
                name, price = o.get("name"), o.get("price", 0)
                point = o.get("point")
                if key == "btts":
                    _take(out["btts"], out["bm"]["btts"], name, price, title)
                elif key == "draw_no_bet":
                    _take(out["dnb"], out["bm"]["dnb"], name, price, title)
                elif key in ("totals", "alternate_totals") and point is not None:
                    if point in (1.5, 2.5, 3.5):
                        k2 = f"{name} {point}"
                        _take(out["totals"], out["bm"]["totals"], k2, price, title)
                elif key == "team_totals" and point is not None:
                    team = o.get("description", "")
                    if abs(point - 1.5) < 0.01 and team:
                        k2 = (team, name)
                        _take(out["team_totals"], out["bm"]["team_totals"], k2, price, title)
                elif key in ("spreads", "alternate_spreads") and point is not None:
                    # ハンディキャップ: 0.5刻みのライン(±0.5/±1.5/±2.5)のみ採用。
                    # 0.25/0.75等のクォーターラインは答え合わせが複雑なため除外
                    if abs(point) <= 2.5 and abs(point) % 1 == 0.5:
                        k2 = (name, point)
                        _take(out["spreads"], out["bm"]["spreads"], k2, price, title)
                        out["spread_n"][k2] = out["spread_n"].get(k2, 0) + 1
                elif key in ("totals_corners", "alternate_totals_corners") and point is not None:
                    k2 = f"{name} {point}"
                    _take(out["corners"], out["bm"]["corners"], k2, price, title)


def _fetch_event_odds(api_key: str, sport: str, event_id: str, regions: str, markets: str) -> dict:
    return _get(f"{BASE}/sports/{sport}/events/{event_id}/odds",
                {"apiKey": api_key, "regions": regions,
                 "markets": markets, "oddsFormat": "decimal"})


def get_extra_markets(api_key: str, sport: str, event_id: str, regions: str) -> dict:
    out = {"btts": {}, "dnb": {}, "totals": {}, "team_totals": {}, "corners": {},
           "spreads": {}, "spread_n": {},
           # 各マーケットの最良オッズ提供ブックメーカー名(キーは価格辞書と同じ)
           "bm": {"btts": {}, "dnb": {}, "totals": {}, "team_totals": {},
                  "spreads": {}, "corners": {}}}

    # 高速パス: 全マーケットを一括取得（すべて提供されていれば API コールは1回で済む）
    try:
        ev = _fetch_event_odds(api_key, sport, event_id, regions, ",".join(EXTRA_MARKETS))
        _parse_extra_bookmakers(ev.get("bookmakers", []), out)
        return out
    except Exception:
        pass  # 未提供マーケットが混ざると 422。1つずつ取得するフォールバックへ

    # フォールバック: COREマーケットのみ1つずつ取得（コーナーは専用リクエストしない）
    for m in CORE_EXTRA_MARKETS:
        try:
            ev = _fetch_event_odds(api_key, sport, event_id, regions, m)
            _parse_extra_bookmakers(ev.get("bookmakers", []), out)
        except Exception as e:
            print(f"[warn] market '{m}' unavailable for {event_id}: {e}", file=sys.stderr)
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
    out = {"h2h": {}, "totals": {}, "bm": {"h2h": {}, "totals": {}}}
    for bm in event.get("bookmakers", []):
        title = bm.get("title") or bm.get("key", "")
        for mk in bm.get("markets", []):
            if mk["key"] == "h2h":
                for o in mk["outcomes"]:
                    _take(out["h2h"], out["bm"]["h2h"], o["name"], o["price"], title)
            elif mk["key"] == "totals":
                for o in mk["outcomes"]:
                    point = float(o.get("point", 0))
                    if point in (1.5, 2.5, 3.5):
                        k = f"{o['name']} {point}"
                        _take(out["totals"], out["bm"]["totals"], k, o["price"], title)
    return out
