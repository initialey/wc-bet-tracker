"""クロージングライン・バリュー(CLV)計測のモックテスト。
python tests/test_clv.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta  # noqa: E402
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
        {"key": "h2h", "outcomes": [{"name": "Yankees", "price": 1.75}]},
        {"key": "draw_no_bet", "outcomes": [
            {"name": "Yankees", "price": 1.40}, {"name": "Red Sox", "price": 2.90}]},
        {"key": "btts", "outcomes": [{"name": "Yes", "price": 1.66}, {"name": "No", "price": 2.10}]},
        {"key": "team_totals", "outcomes": [
            {"name": "Over", "description": "Yankees", "price": 1.52, "point": 1.5},
            {"name": "Under", "description": "Yankees", "price": 2.40, "point": 1.5},
            {"name": "Over", "description": "Red Sox", "price": 1.80, "point": 1.5}]},
        {"key": "alternate_totals", "outcomes": [
            {"name": "Over", "price": 1.30, "point": 7.5}, {"name": "Under", "price": 3.20, "point": 7.5},
            {"name": "Over", "price": 2.00, "point": 8.5}]},        # 主要ライン8.5より良い価格
        {"key": "alternate_spreads", "outcomes": [
            {"name": "Yankees", "price": 3.10, "point": -2.5}]}]},
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
    assert _closing_odds_for(_row(market="O/U 8.5", pick="オーバー8.5"), EV) == 2.00   # alternate_totalsも参照
    assert _closing_odds_for(_row(market="O/U 8.5", pick="アンダー8.5"), EV) == 1.90
    assert _closing_odds_for(_row(market="O/U 7.5", pick="オーバー7.5"), EV) == 1.30   # alternateライン
    assert _closing_odds_for(_row(market="O/U 9.5", pick="オーバー9.5"), EV) is None  # ライン違い
    assert _closing_odds_for(_row(market="ランライン", pick="Yankees -1.5"), EV) == 2.05
    assert _closing_odds_for(_row(market="ハンディ -2.5", pick="Yankees -2.5"), EV) == 3.10  # alternate_spreads
    assert _closing_odds_for(_row(market="90分勝敗", pick="引き分け"), EV) == 3.1   # Draw変換
    assert _closing_odds_for(_row(market="勝敗(引分返金)", pick="Red Sox"), EV) == 2.90   # DNB
    assert _closing_odds_for(_row(market="両チーム得点", pick="あり"), EV) == 1.66   # BTTS Yes
    assert _closing_odds_for(_row(market="両チーム得点", pick="なし"), EV) == 2.10   # BTTS No
    assert _closing_odds_for(_row(market="チーム得点", pick="Yankees アンダー1.5"), EV) == 2.40
    assert _closing_odds_for(_row(market="チーム得点", pick="Red Sox オーバー1.5"), EV) == 1.80
    assert _closing_odds_for(_row(market="コーナー(参考)", pick="オーバー9.5"), EV) is None  # 対象外
    assert "closing_odds_at" in FIELDS and "closing_lead_min" in FIELDS and "closing_source" in FIELDS


def test_closing_markets_fallback_to_core_on_422():
    """種別ごとの全マーケット要求が失敗(未提供マーケット混在の422等)したら
    コア3マーケットで再試行する。コアも失敗したらそのまま例外"""
    from src import odds_api
    calls = []
    orig = odds_api._fetch_event_odds

    def fake(api_key, sport, event_id, regions, markets):
        calls.append(markets)
        if "btts" in markets:
            raise RuntimeError("422 Unprocessable Entity")
        return {"bookmakers": []}

    odds_api._fetch_event_odds = fake
    try:
        ev, used = odds_api.get_closing_event_odds("k", "soccer_epl", "e1", "eu", kind="soccer")
        assert used == odds_api.CLOSING_MARKETS_CORE and len(calls) == 2
        assert "btts" in calls[0] and calls[1] == odds_api.CLOSING_MARKETS_CORE
        calls.clear()
        ev, used = odds_api.get_closing_event_odds("k", "baseball_mlb", "e2", "us", kind="mlb")
        assert used == odds_api.CLOSING_MARKETS["mlb"] and len(calls) == 1   # MLBはbttsなし=1回で成功

        def always_fail(*a, **k):
            raise RuntimeError("boom")
        odds_api._fetch_event_odds = always_fail
        try:
            odds_api.get_closing_event_odds("k", "soccer_epl", "e3", "eu", kind="soccer")
            assert False, "should raise"
        except RuntimeError:
            pass
    finally:
        odds_api._fetch_event_odds = orig


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


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_capture(rows, now, fake_fetch, path_name="test_clv_job.csv"):
    """closing_odds.capture_once をモックで通し、(保存後の行, 通知本文リスト, 呼び出し記録, stats) を返す"""
    path = os.path.join(SCRATCH, path_name)
    orig_history, orig_fetch, orig_state = main.HISTORY, closing_odds.odds_api.get_closing_event_odds, closing_odds.ERR_STATE
    main.HISTORY = path
    closing_odds.ERR_STATE = os.path.join(SCRATCH, "test_clv_errstate.json")
    main.save_history(rows)
    calls, sent = [], []

    def fetch(api_key, sport, event_id, regions, kind="soccer"):
        calls.append((event_id, sport, regions, kind))
        return fake_fetch(event_id)

    closing_odds.odds_api.get_closing_event_odds = fetch
    os.environ["ODDS_API_KEY"] = "dummy"
    try:
        stats = closing_odds.capture_once(now=now, notify_fn=sent.append)
        saved = main.load_history()
    finally:
        closing_odds.odds_api.get_closing_event_odds = orig_fetch
        main.HISTORY = orig_history
        for f in (path, closing_odds.ERR_STATE):
            if os.path.exists(f):
                os.remove(f)
        closing_odds.ERR_STATE = orig_state
    return saved, sent, calls, stats


def test_capture_once_targets_window_and_records_metadata():
    """取得窓(キックオフまでCAPTURE_LEAD_MIN分以内)の未取得行だけを1試合1回で取得し、
    closing_odds/closing_odds_at/closing_lead_min/closing_source=live を記録する。
    遠い試合・取得済み・確定済み(未取得でも窓外)は触らない"""
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row(id="ev9|勝敗", kickoff_utc=_iso(now + timedelta(minutes=5)), odds="1.80"),
        _row(id="ev9|O/U 8.5", market="O/U 8.5", pick="オーバー8.5",
             kickoff_utc=_iso(now + timedelta(minutes=5)), odds="1.90"),
        _row(id="ev9|両チーム得点", market="両チーム得点", pick="あり",
             kickoff_utc=_iso(now + timedelta(minutes=5))),           # 新対応マーケット
        _row(id="ev8|勝敗", match="Other Game", kickoff_utc=_iso(now + timedelta(minutes=90))),  # 窓外
        _row(id="ev7|勝敗", match="Done Game", kickoff_utc=_iso(now + timedelta(minutes=3)),
             closing_odds="1.70", closing_source="live"),              # 取得済み
    ]
    saved, sent, calls, stats = _run_capture(rows, now, lambda ev_id: (EV, "h2h,totals,spreads,btts"))
    assert calls == [("ev9", "baseball_mlb", closing_odds.MLB_REGIONS, "mlb")]   # 1試合1回
    by_id = {r["id"]: r for r in saved}
    assert by_id["ev9|勝敗"]["closing_odds"] == "1.75"
    assert by_id["ev9|O/U 8.5"]["closing_odds"] == "2.00"
    assert by_id["ev9|両チーム得点"]["closing_odds"] == "1.66"
    assert by_id["ev9|勝敗"]["closing_source"] == "live"
    assert by_id["ev9|勝敗"]["closing_odds_at"] == "2026-09-07T12:00:00Z"
    assert by_id["ev9|勝敗"]["closing_lead_min"] == "5"
    assert by_id["ev8|勝敗"]["closing_odds"] == "" and by_id["ev8|勝敗"]["closing_source"] == ""
    assert by_id["ev7|勝敗"]["closing_odds"] == "1.70"
    assert stats["captured"] == 3 and stats["events"] == 1 and not sent   # 正常時は通知なし


def test_capture_once_marks_missed_and_notifies_once():
    """キックオフ後MISSED_GRACE_MIN分を過ぎても未取得の行は closing_source=missed にして
    Telegram(notify_fn)へ1回だけ通知。2回目の実行では再通知しない。
    MISSED_LOOKBACK_Hより古い未取得分は遡及取得の対象なので通知しない"""
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row(id="m1|勝敗", match="Missed Game", kickoff_utc=_iso(now - timedelta(minutes=30))),
        _row(id="m1|ランライン", market="ランライン", pick="Yankees -1.5",
             match="Missed Game", kickoff_utc=_iso(now - timedelta(minutes=30))),
        _row(id="g1|勝敗", match="Grace Game", kickoff_utc=_iso(now - timedelta(minutes=2))),  # 猶予内
        _row(id="o1|勝敗", match="Old Game", kickoff_utc=_iso(now - timedelta(days=3))),       # 遡り範囲外
    ]
    saved, sent, calls, stats = _run_capture(rows, now, lambda ev_id: (EV, "h2h"))
    by_id = {r["id"]: r for r in saved}
    assert calls == []                                        # 取得窓の試合なし=API呼び出しゼロ
    assert stats["missed"] == 2
    assert by_id["m1|勝敗"]["closing_source"] == "missed"
    assert by_id["g1|勝敗"]["closing_source"] == "" and by_id["o1|勝敗"]["closing_source"] == ""
    assert len(sent) == 1 and "取り逃し 2件" in sent[0] and "Missed Game" in sent[0]
    # 2回目(5分後): m1は記録済みなので再通知なし。猶予を過ぎたg1だけが新たにmissedになる
    saved2, sent2, _, stats2 = _run_capture(saved, now + timedelta(minutes=5), lambda e: (EV, "h2h"))
    assert stats2["missed"] == 1
    assert len(sent2) == 1 and "Grace Game" in sent2[0] and "Missed Game" not in sent2[0]


def test_capture_once_fetch_error_notified_without_spam():
    """取得失敗はTelegramへ通知するが、同じ試合の失敗は実行内(ERR_STATE)で重複通知しない。
    失敗した行は未取得のまま(窓内なら次の反復で再試行できる)"""
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    rows = [_row(id="bad|勝敗", match="Bad Game", kickoff_utc=_iso(now + timedelta(minutes=10)))]

    def boom(ev_id):
        raise RuntimeError("HTTP 500")

    path = os.path.join(SCRATCH, "test_clv_job_err.csv")
    orig_history, orig_fetch, orig_state = main.HISTORY, closing_odds.odds_api.get_closing_event_odds, closing_odds.ERR_STATE
    main.HISTORY = path
    closing_odds.ERR_STATE = os.path.join(SCRATCH, "test_clv_errstate2.json")
    main.save_history(rows)
    sent = []
    closing_odds.odds_api.get_closing_event_odds = lambda *a, **k: boom(None)
    os.environ["ODDS_API_KEY"] = "dummy"
    try:
        s1 = closing_odds.capture_once(now=now, notify_fn=sent.append)
        s2 = closing_odds.capture_once(now=now + timedelta(minutes=5), notify_fn=sent.append)
        saved = main.load_history()
    finally:
        closing_odds.odds_api.get_closing_event_odds = orig_fetch
        main.HISTORY = orig_history
        for f in (path, closing_odds.ERR_STATE):
            if os.path.exists(f):
                os.remove(f)
        closing_odds.ERR_STATE = orig_state
    assert len(s1["errors"]) == 1 and len(s2["errors"]) == 1
    assert len(sent) == 1 and "取得失敗" in sent[0] and "Bad Game" in sent[0]
    assert saved[0]["closing_odds"] == "" and saved[0]["closing_source"] == ""


def test_backfill_uses_historical_snapshot_within_budget():
    """遡及取得: 未取得・キックオフ済みの試合を新しい順にhistorical APIで取得し、
    closing_source=backfill・closing_lead_min=スナップショット時刻基準で記録。
    予算(クレジット)に達したら残りは見送り、失敗はbackfill_failedで記録して再照会しない"""
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    ko_new = now - timedelta(hours=5)
    ko_old = now - timedelta(days=2)
    rows = [
        _row(id="n1|勝敗", match="New Game", kickoff_utc=_iso(ko_new)),
        _row(id="n1|ランライン", market="ランライン", pick="Yankees -1.5",
             match="New Game", kickoff_utc=_iso(ko_new)),
        _row(id="f1|勝敗", match="Fail Game", kickoff_utc=_iso(now - timedelta(hours=8))),
        _row(id="o1|勝敗", match="Old Game", kickoff_utc=_iso(ko_old)),          # 予算切れで見送り
        _row(id="x1|勝敗", match="Too Old", kickoff_utc=_iso(now - timedelta(days=40))),  # days外
        _row(id="p1|勝敗", match="Future", kickoff_utc=_iso(now + timedelta(hours=1))),   # 未来=対象外
    ]
    path = os.path.join(SCRATCH, "test_clv_backfill.csv")
    orig_history, orig_hist = main.HISTORY, closing_odds.odds_api.get_historical_event_odds
    main.HISTORY = path
    main.save_history(rows)
    calls, sent = [], []

    def fake_hist(api_key, sport, event_id, regions, markets, date_iso):
        calls.append((event_id, markets, date_iso))
        if event_id == "f1":
            raise RuntimeError("HTTP 422")
        return EV, ko_new - timedelta(minutes=7)     # スナップショットはキックオフ7分前

    closing_odds.odds_api.get_historical_event_odds = fake_hist
    os.environ["ODDS_API_KEY"] = "dummy"
    try:
        stats = closing_odds.backfill(budget=60, days=30, markets="core", now=now,
                                      notify_fn=sent.append)
        saved = main.load_history()
    finally:
        closing_odds.odds_api.get_historical_event_odds = orig_hist
        main.HISTORY = orig_history
        os.remove(path)
    by_id = {r["id"]: r for r in saved}
    assert [c[0] for c in calls] == ["n1", "f1"]                 # 新しい順、予算60=2試合(30ずつ)
    assert calls[0][1] == closing_odds.odds_api.CLOSING_MARKETS_CORE
    assert calls[0][2] == _iso(ko_new - timedelta(minutes=closing_odds.BACKFILL_SNAPSHOT_MIN))
    assert by_id["n1|勝敗"]["closing_odds"] == "1.75" and by_id["n1|勝敗"]["closing_source"] == "backfill"
    assert by_id["n1|勝敗"]["closing_lead_min"] == "7"
    assert by_id["n1|ランライン"]["closing_odds"] == "2.05"
    assert by_id["f1|勝敗"]["closing_source"] == "backfill_failed" and by_id["f1|勝敗"]["closing_odds"] == ""
    assert by_id["o1|勝敗"]["closing_odds"] == "" and by_id["o1|勝敗"]["closing_source"] == ""
    assert by_id["p1|勝敗"]["closing_odds"] == ""
    assert stats == {"events": 2, "captured": 2, "spent": 60, "failed": 1, "skipped_budget": 1}
    assert len(sent) == 1 and "遡及取得" in sent[0]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
