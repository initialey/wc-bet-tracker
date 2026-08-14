"""クロージングライン・バリュー(CLV)計測のモックテスト。
python tests/test_clv.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import dashboard, review, main, closing_odds  # noqa: E402
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
    """取得済みイベントオッズから各マーケットのピック価格を引ける(closing_odds.ymlでも共用)"""
    assert _closing_odds_for(_row(), EV) == 1.75                     # h2h ベスト
    assert _closing_odds_for(_row(market="O/U 8.5", pick="オーバー8.5"), EV) == 1.95
    assert _closing_odds_for(_row(market="O/U 8.5", pick="アンダー8.5"), EV) == 1.90
    assert _closing_odds_for(_row(market="O/U 9.5", pick="オーバー9.5"), EV) is None  # ライン違い
    assert _closing_odds_for(_row(market="ランライン", pick="Yankees -1.5"), EV) == 2.05
    assert _closing_odds_for(_row(market="90分勝敗", pick="引き分け"), EV) == 3.1   # Draw変換
    assert _closing_odds_for(_row(market="両チーム得点", pick="あり"), EV) is None  # 対象外


def test_analytics_clv_uses_precise_column_only():
    """CLV = 記録時オッズ/締切オッズ - 1 の平均を集計。approx_closing_oddsは対象外、
    closing_odds(精密値)が入っている行のみ対象"""
    hist = [
        _row(id="a|勝敗", odds="2.10", closing_odds="2.00", result="win", profit="1.10"),
        _row(id="b|勝敗", odds="1.90", closing_odds="2.00", result="lose", profit="-1.00"),
        _row(id="c|勝敗", odds="2.00", closing_odds="2.00"),   # 待ち(CLV 0%)も対象
        _row(id="d|勝敗", odds="2.00", result="win", profit="1.00"),  # closingなし→除外
        _row(id="e|勝敗", odds="1.50", approx_closing_odds="2.00",
             result="win", profit="0.50"),  # 近似値のみ→除外(精密値のみ集計)
    ]
    sp = [s for s in analytics(hist)["mroi"] if s["sport"] == "mlb"][0]
    m = [x for x in sp["markets"] if x["market"] == "勝敗"][0]
    # CLV: (2.1/2.0-1)+(1.9/2.0-1)+(2.0/2.0-1) = +5% -5% 0% → 平均0%、近似値行は含まれない
    assert m["clv_n"] == 3 and abs(m["clv"]) < 0.01
    assert sp["clv_n"] == 3
    # 全行closingなし → None
    m2 = [x for x in [s for s in analytics([_row(id="f|勝敗", result="win", profit="0.9")])["mroi"]
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
    """マーケット別成績にCLV列・件数(n=)・説明文が表示される"""
    path = os.path.join(SCRATCH, "test_clv_dash.html")
    hist = [_row(id=f"y{i}|勝敗", odds="2.10", closing_odds="2.00",
                 result="win", profit="1.10") for i in range(3)]
    dashboard.build(hist, [], stats=analytics(hist), path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    assert "<th>CLV</th>" in page
    assert "+5.0%" in page                          # 勝敗行のCLV値
    assert "(n=3)" in page                           # 件数が可視テキストとして併記
    assert "記録後に市場が予想方向へ動いた" in page   # 説明文(日)
    assert "beating the market" in page              # 説明文(英)
    assert "10分前" in page                          # 精密取得ジョブの説明
    assert "approx_closing_odds" in page             # 近似値列の温存を明記


def test_closing_odds_field_backcompat_and_migration():
    """新スキーマ(approx_closing_odds + closing_odds)がFIELDSに含まれ、
    旧CSV(closing_odds列のみ)を読むと自動的に近似値がapprox_closing_oddsへ移される"""
    assert "closing_odds" in FIELDS and "approx_closing_odds" in FIELDS

    path = os.path.join(SCRATCH, "test_clv_migration.csv")
    old_fields = [f for f in FIELDS if f != "approx_closing_odds"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        import csv
        w = csv.DictWriter(f, fieldnames=old_fields)
        w.writeheader()
        row = {k: "" for k in old_fields}
        row.update(id="old1|勝敗", league="MLB", match="A vs B", market="勝敗",
                   pick="A", prob="55", odds="1.90", closing_odds="1.85")
        w.writerow(row)

    orig_history = main.HISTORY
    main.HISTORY = path
    try:
        rows = main.load_history()
    finally:
        main.HISTORY = orig_history
    os.remove(path)
    assert rows[0]["approx_closing_odds"] == "1.85"   # 旧値が近似値列へ退避
    assert rows[0]["closing_odds"] == ""               # 新しいclosing_odds列は空でスタート


def test_closing_odds_job_targets_only_near_kickoff_pending_rows():
    """closing_odds.py: キックオフ間近(WINDOW_MINUTES以内)かつ未確定・未取得の行だけを
    対象にイベントIDでグルーピングする"""
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

    def ko(minutes):
        return (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = [
        _row(id="near1|勝敗", kickoff_utc=ko(5)),                        # 対象: 5分後
        _row(id="near1|O/U 8.5", market="O/U 8.5", kickoff_utc=ko(5)),   # 対象: 同じ試合の別マーケット
        _row(id="far|勝敗", kickoff_utc=ko(30)),                         # 対象外: 遠すぎる
        _row(id="past|勝敗", kickoff_utc=ko(-5)),                        # 対象外: キックオフ済み
        _row(id="done|勝敗", kickoff_utc=ko(3), result="win"),           # 対象外: 確定済み
        _row(id="got|勝敗", kickoff_utc=ko(3), closing_odds="1.90"),     # 対象外: 取得済み
    ]

    targets = {}
    for r in rows:
        kickoff = datetime.strptime(r["kickoff_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if r["result"] or r.get("closing_odds"):
            continue
        if not (now <= kickoff <= now + timedelta(minutes=closing_odds.WINDOW_MINUTES)):
            continue
        key_kind = closing_odds.KIND_BY_LEAGUE.get(r["league"])
        assert key_kind is not None
        sport_key, kind = key_kind
        ev_id = r["id"].split("|")[0]
        targets.setdefault((ev_id, sport_key, kind), []).append(r)

    assert set(targets.keys()) == {("near1", "baseball_mlb", "mlb")}
    assert len(targets[("near1", "baseball_mlb", "mlb")]) == 2


def test_closing_odds_job_end_to_end(monkeypatch=None):
    """closing_odds.main(): 対象行にclosing_odds(精密値)を書き込み保存する。
    未対象行(遠い試合)や確定済み行はodds_api呼び出しなしで無視される"""
    from datetime import datetime, timezone, timedelta
    now_dt = datetime.now(timezone.utc)
    near = (now_dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far = (now_dt + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = [
        _row(id="ev9|勝敗", league="MLB", match="Yankees vs Red Sox",
             kickoff_utc=near, odds="1.80"),
        _row(id="ev9|O/U 8.5", league="MLB", market="O/U 8.5", pick="オーバー8.5",
             kickoff_utc=near, odds="1.90"),
        _row(id="ev8|勝敗", league="MLB", match="Other Game", kickoff_utc=far),
    ]

    path = os.path.join(SCRATCH, "test_clv_job.csv")
    orig_history = main.HISTORY
    main.HISTORY = path
    main.save_history(rows)

    calls = []
    orig_get = closing_odds.odds_api.get_event_odds

    def fake_get_event_odds(api_key, sport, event_id, regions, markets="h2h,totals,spreads"):
        calls.append((event_id, sport, regions))
        return EV

    closing_odds.odds_api.get_event_odds = fake_get_event_odds
    os.environ["ODDS_API_KEY"] = "dummy"
    try:
        closing_odds.main()
        saved = main.load_history()
    finally:
        closing_odds.odds_api.get_event_odds = orig_get
        main.HISTORY = orig_history
        os.remove(path)

    assert calls == [("ev9", "baseball_mlb", closing_odds.MLB_REGIONS)]   # 1試合1回だけ
    by_id = {r["id"]: r for r in saved}
    assert by_id["ev9|勝敗"]["closing_odds"] == "1.75"        # EVのh2hベスト(Yankees)
    assert by_id["ev9|O/U 8.5"]["closing_odds"] == "1.95"     # EVのtotalsベスト(Over 8.5)
    assert by_id["ev8|勝敗"]["closing_odds"] == ""             # 対象外(遠い試合)は未変更


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
