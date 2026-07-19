"""クロージングライン・バリュー(CLV)計測のモックテスト。
python tests/test_clv.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import dashboard, review, main  # noqa: E402
from src.main import FIELDS, analytics, _closing_odds_for  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")

EV = {"bookmakers": [
    {"title": "A社", "markets": [
        {"key": "h2h", "outcomes": [
            {"name": "Yankees", "price": 1.70}, {"name": "Red Sox", "price": 2.30},
            {"name": "Draw", "price": 3.1}]},
        {"key": "totals", "outcomes": [
            {"name": "Over", "price": 1.95, "point": 8.5},
            {"name": "Under", "price": 1.90, "point": 8.5}]},
        {"key": "spreads", "outcomes": [
            {"name": "Yankees", "price": 2.05, "point": -1.5},
            {"name": "Red Sox", "price": 1.80, "point": 1.5}]}]},
    {"title": "B社", "markets": [
        {"key": "h2h", "outcomes": [{"name": "Yankees", "price": 1.75}]}]},
]}


def _row(**kw):
    r = {k: "" for k in FIELDS}
    r.update(id="clv1|勝敗", league="MLB", match="Yankees vs Red Sox",
             market="勝敗", pick="Yankees", prob="60", odds="1.85",
             created_utc="2026-07-20T00:00")
    r.update(kw)
    return r


def test_closing_odds_lookup():
    """取得済みイベントオッズから各マーケットのピック価格を引ける"""
    assert _closing_odds_for(_row(), EV) == 1.75                     # h2h ベスト
    assert _closing_odds_for(_row(market="O/U 8.5", pick="オーバー8.5"), EV) == 1.95
    assert _closing_odds_for(_row(market="O/U 8.5", pick="アンダー8.5"), EV) == 1.90
    assert _closing_odds_for(_row(market="O/U 9.5", pick="オーバー9.5"), EV) is None  # ライン違い
    assert _closing_odds_for(_row(market="ランライン", pick="Yankees -1.5"), EV) == 2.05
    assert _closing_odds_for(_row(market="90分勝敗", pick="引き分け"), EV) == 3.1   # Draw変換
    assert _closing_odds_for(_row(market="両チーム得点", pick="あり"), EV) is None  # 対象外


def test_analytics_clv_aggregation():
    """CLV = 記録時オッズ/締切オッズ - 1 の平均をマーケット別・スポーツ別に集計"""
    hist = [
        _row(id="a|勝敗", odds="2.10", closing_odds="2.00", result="win", profit="1.10"),
        _row(id="b|勝敗", odds="1.90", closing_odds="2.00", result="lose", profit="-1.00"),
        _row(id="c|勝敗", odds="2.00", closing_odds="2.00"),   # 待ち(CLV 0%)も対象
        _row(id="d|勝敗", odds="2.00", result="win", profit="1.00"),  # closingなし→除外
    ]
    sp = [s for s in analytics(hist)["mroi"] if s["sport"] == "mlb"][0]
    m = [x for x in sp["markets"] if x["market"] == "勝敗"][0]
    # CLV: (2.1/2.0-1)+(1.9/2.0-1)+(2.0/2.0-1) = +5% -5% 0% → 平均0%
    assert m["clv_n"] == 3 and abs(m["clv"]) < 0.01
    assert sp["clv_n"] == 3
    # 全行closingなし → None
    m2 = [x for x in [s for s in analytics([_row(id="e|勝敗", result="win", profit="0.9")])["mroi"]
                      if s["sport"] == "mlb"][0]["markets"] if x["market"] == "勝敗"][0]
    assert m2["clv"] is None


def test_review_beating_market_note():
    """平均CLV+2%以上の区分は「市場に先行できている」と評価される"""
    hist = [_row(id=f"w{i}|勝敗", odds="2.10", closing_odds="2.00",
                 result="win", profit="1.10") for i in range(16)]   # ROI+110%, CLV+5%
    out = review.build_proposals(analytics(hist))
    seg = [p for p in out["proposals"] if "勝敗" in p["segment_ja"]]
    assert seg and "市場に先行できている" in seg[0]["suggest_ja"]
    assert "beating the closing line" in seg[0]["suggest_en"]
    # CLVが低い場合は付記されない
    hist2 = [_row(id=f"x{i}|勝敗", odds="2.00", closing_odds="2.00",
                  result="win", profit="1.00") for i in range(16)]
    out2 = review.build_proposals(analytics(hist2))
    seg2 = [p for p in out2["proposals"] if "勝敗" in p["segment_ja"]]
    assert seg2 and "市場に先行" not in seg2[0]["suggest_ja"]


def test_dashboard_clv_column():
    """マーケット別成績にCLV列と説明文が表示される"""
    path = os.path.join(SCRATCH, "test_clv_dash.html")
    hist = [_row(id=f"y{i}|勝敗", odds="2.10", closing_odds="2.00",
                 result="win", profit="1.10") for i in range(3)]
    dashboard.build(hist, [], stats=analytics(hist), path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    assert "<th>CLV</th>" in page
    assert "+5.0%" in page                          # 勝敗行のCLV値
    assert "記録後に市場が予想方向へ動いた" in page   # 説明文(日)
    assert "beating the market" in page              # 説明文(英)
    assert "(近似)" in page                          # 近似である旨の明記


def test_closing_odds_field_backcompat():
    assert "closing_odds" in FIELDS
    r = main.load_history()  # 既存CSV(closing_odds列なし)が読めて空欄で埋まる
    if r:
        assert "closing_odds" in r[0]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
