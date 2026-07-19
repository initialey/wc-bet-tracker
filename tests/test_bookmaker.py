"""ブックメーカー名の記録・表示・ランキングのモックテスト。
python tests/test_bookmaker.py で実行"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import odds_api, dashboard, main  # noqa: E402
from src.main import FIELDS, analytics  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")

EVENT = {
    "id": "ev1", "home_team": "Arsenal", "away_team": "Chelsea",
    "commence_time": "2026-07-21T19:00:00Z",
    "bookmakers": [
        {"key": "pinnacle", "title": "Pinnacle", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 2.10}, {"name": "Chelsea", "price": 3.4},
                {"name": "Draw", "price": 3.3}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": 1.85, "point": 2.5},
                {"name": "Under", "price": 1.95, "point": 2.5}]}]},
        {"key": "bet365", "title": "bet365", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 2.25}, {"name": "Chelsea", "price": 3.2},
                {"name": "Draw", "price": 3.5}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": 1.80, "point": 2.5},
                {"name": "Under", "price": 2.00, "point": 2.5}]}]},
    ],
}


def test_best_odds_tracks_bookmaker():
    """best_oddsが最良オッズの提供ブックメーカー名を記録する"""
    best = odds_api.best_odds(EVENT)
    assert best["h2h"]["Arsenal"] == 2.25 and best["bm"]["h2h"]["Arsenal"] == "bet365"
    assert best["h2h"]["Chelsea"] == 3.4 and best["bm"]["h2h"]["Chelsea"] == "Pinnacle"
    assert best["totals"]["Over 2.5"] == 1.85 and best["bm"]["totals"]["Over 2.5"] == "Pinnacle"
    assert best["bm"]["totals"]["Under 2.5"] == "bet365"


def test_extra_markets_track_bookmaker():
    """追加マーケットのパースでもブックメーカー名を記録する"""
    out = {"btts": {}, "dnb": {}, "totals": {}, "team_totals": {}, "corners": {},
           "spreads": {}, "spread_n": {},
           "bm": {"btts": {}, "dnb": {}, "totals": {}, "team_totals": {},
                  "spreads": {}, "corners": {}}}
    bms = [
        {"title": "Pinnacle", "markets": [
            {"key": "btts", "outcomes": [{"name": "Yes", "price": 1.70}]},
            {"key": "spreads", "outcomes": [{"name": "Arsenal", "price": 1.90, "point": -0.5}]}]},
        {"title": "bet365", "markets": [
            {"key": "btts", "outcomes": [{"name": "Yes", "price": 1.75}]},
            {"key": "spreads", "outcomes": [{"name": "Arsenal", "price": 1.85, "point": -0.5}]}]},
    ]
    odds_api._parse_extra_bookmakers(bms, out)
    assert out["btts"]["Yes"] == 1.75 and out["bm"]["btts"]["Yes"] == "bet365"
    assert out["spreads"][("Arsenal", -0.5)] == 1.90
    assert out["bm"]["spreads"][("Arsenal", -0.5)] == "Pinnacle"


def test_mk_row_records_bookmaker():
    row = main._mk_row(EVENT, "プレミア", "90分勝敗", "Arsenal", 0.55, 2.25,
                       0.52, 0.53, None, 0.55, "理由", "reason",
                       datetime(2026, 7, 20, tzinfo=timezone.utc), bookmaker="bet365")
    assert row["bookmaker"] == "bet365"
    assert "bookmaker" in FIELDS


def _hist_row(i, bm, days_ago):
    r = {k: "" for k in FIELDS}
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    r.update(id=f"bm{i}|勝敗", created_utc=created.strftime("%Y-%m-%dT%H:%M"),
             kickoff_utc="2026-07-21T00:00:00Z", league="MLB", match="A vs B",
             market="勝敗", pick="A", prob="55", odds="1.90", bookmaker=bm)
    return r


def test_analytics_bookmaker_ranking():
    """ランキング集計: 累計は全期間、直近7日は新しい行のみ。空欄行は対象外"""
    hist = ([_hist_row(i, "Pinnacle", 1) for i in range(3)]
            + [_hist_row(10 + i, "bet365", 10) for i in range(5)]
            + [_hist_row(20, "", 1)])   # bookmaker未記録(旧行)は数えない
    bms = analytics(hist)["bookmakers"]
    assert [(b["name"], b["total"], b["week"]) for b in bms] == [
        ("bet365", 5, 0), ("Pinnacle", 3, 3)]   # 累計降順、bet365は7日窓の外


def test_dashboard_shows_bookmaker():
    """カードのオッズ横にブックメーカー名、ランキングカードが表示される"""
    path = os.path.join(SCRATCH, "test_bm_dash.html")
    hist = [_hist_row(i, "Pinnacle", 1) for i in range(2)]
    pred = [{"kickoff": "2026-07-21T19:00:00Z", "match": "Arsenal vs Chelsea",
             "league": "プレミア", "market": "90分勝敗", "pick": "Arsenal", "prob": 55,
             "odds": 2.25, "ev": 0.08, "reason": "理由", "reason_en": "reason",
             "kind": "soccer", "bookmaker": "bet365"}]
    dashboard.build(hist, pred, stats=analytics(hist), path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    assert "bet365" in page                             # カードのオッズ横
    assert "ブックメーカー別 最良オッズ提供回数" in page   # ランキングカード(日)
    assert "Best-odds count by bookmaker" in page        # 英語
    assert "Pinnacle" in page

    # 記録が無ければランキングカード自体を出さない
    dashboard.build([], [], stats=analytics([]), path=path)
    with open(path, encoding="utf-8") as f:
        page2 = f.read()
    os.remove(path)
    assert "ブックメーカー別 最良オッズ提供回数" not in page2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
