"""デイリーレビューのモックテスト(実APIキー・ネットワーク不要)。
提案ゲートを満たす場合/満たさない場合の両方と、AI呼び出しの制御を検証する。
python tests/test_review.py で実行"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import review, dashboard  # noqa: E402
from src.main import FIELDS, analytics  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")


def _tmp_props():
    """テスト用の提案記録ファイル(毎回クリーンな一時パス。data/proposals.jsonを汚さない)"""
    path = os.path.join(SCRATCH, "test_props.json")
    if os.path.exists(path):
        os.remove(path)
    return path


def _row(i, **kw):
    r = {k: "" for k in FIELDS}
    r.update(id=f"ev{i}|O/U 8.5", created_utc="2026-07-11T08:00",
             kickoff_utc="2026-07-11T18:00:00Z", league="MLB",
             match="Team A vs Team B", market="O/U 8.5", pick="オーバー8.5",
             prob="55", odds="2.00")
    r.update(kw)
    return r


def _losing_history(n):
    """検証n件・全敗(ROI -100%)のMLB O/U履歴"""
    return [_row(i, result="lose", profit="-1.00") for i in range(n)]


class _FakeResp:
    def __init__(self, ja="昨日は1勝でした。", en="One win yesterday."):
        self._payload = {"content": [{"type": "text",
                                      "text": json.dumps({"ja": ja, "en": en})}]}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _patch_ai(fn):
    """review.requests.post を差し替えて呼び出し回数を数える"""
    calls = {"n": 0}

    def fake_post(*a, **kw):
        calls["n"] += 1
        return fn(*a, **kw)

    orig = review.requests.post
    review.requests.post = fake_post
    return calls, lambda: setattr(review.requests, "post", orig)


def test_gate_met_negative_roi():
    """検証15件以上かつROI±15%超 → 改善提案が出る(格下げ・重み見直しの文言)"""
    hist = _losing_history(20)
    out = review.build_proposals(analytics(hist))
    assert out["proposals"], "20件全敗なら提案が出るはず"
    segs = [p["segment_ja"] for p in out["proposals"]]
    assert any("MLB" in s and "O/U 8.5" in s for s in segs)
    p = out["proposals"][0]
    assert p["n"] >= 15 and p["roi"] <= -15
    assert "格下げ" in p["suggest_ja"] or "重み" in p["suggest_ja"]
    assert out["status_ja"] == ""
    assert len(out["proposals"]) <= review.MAX_PROPOSALS


def test_gate_met_positive_roi():
    """好調側(ROI > +15%)は「強化は時期尚早」トーンの提案"""
    hist = [_row(i, result="win", profit="1.00") for i in range(16)]
    out = review.build_proposals(analytics(hist))
    assert out["proposals"]
    assert "時期尚早" in out["proposals"][0]["suggest_ja"]


def test_gate_not_met_accumulating():
    """検証数不足 → 「データ蓄積中(あとX件で最初の判定)」のみ。提案は出さない"""
    hist = _losing_history(5)  # 全敗でROIは-100%だが5件のみ
    out = review.build_proposals(analytics(hist))
    assert out["proposals"] == []
    assert out["status_ja"] == "データ蓄積中(あと10件で最初の判定)"


def test_gate_not_met_no_bias():
    """検証数は十分でもROIが±15%以内 → 偏りなしの表示(提案は出さない)"""
    hist = ([_row(i, result="win", profit="1.00") for i in range(8)]
            + [_row(100 + i, result="lose", profit="-1.00") for i in range(7)])
    stats = analytics(hist)  # 15件 +1.00u → ROI +6.7%
    out = review.build_proposals(stats)
    assert out["proposals"] == []
    assert "偏りは現時点でありません" in out["status_ja"]


def test_no_settled_yesterday_skips_ai():
    """昨日確定0件 → AI呼び出しをスキップし「昨日は確定した予想なし」"""
    calls, restore = _patch_ai(lambda *a, **kw: _FakeResp())
    try:
        r = review.build_review(_losing_history(20), [], "dummy-key", proposals_path=_tmp_props())
    finally:
        restore()
    assert calls["n"] == 0 and r["ai_called"] is False
    assert r["comment_ja"] == "昨日は確定した予想なし"
    assert r["proposals"]  # ゲート判定はAIとは独立に実行される


def test_ai_called_once_with_settled():
    """確定ありの日はAI呼び出しがちょうど1回。短評がJSONから入る"""
    newly = [_row(0, result="win", profit="1.00"),
             _row(1, result="lose", profit="-1.00")]
    calls, restore = _patch_ai(lambda *a, **kw: _FakeResp(ja="テスト短評", en="Test comment"))
    try:
        r = review.build_review(_losing_history(5) + newly, newly, "dummy-key", proposals_path=_tmp_props())
    finally:
        restore()
    assert calls["n"] == 1 and r["ai_called"] is True
    assert r["comment_ja"] == "テスト短評" and r["comment_en"] == "Test comment"
    assert r["yesterday"] == {"n": 2, "win": 1, "lose": 1, "push": 0, "profit": 0.0}


def test_ai_failure_falls_back():
    """AI失敗でもレビューは成立(事実ベースの定型文にフォールバック)。円換算+絵文字で表示"""
    def boom(*a, **kw):
        raise RuntimeError("api down")
    newly = [_row(0, result="win", profit="1.00")]
    calls, restore = _patch_ai(boom)
    try:
        r = review.build_review(newly, newly, "dummy-key", proposals_path=_tmp_props())
    finally:
        restore()
    assert r["ai_called"] is False
    assert "1勝0敗" in r["comment_ja"]
    # フォールバック文もユニット表記を使わず円換算+絵文字
    assert "ユニット" not in r["comment_ja"]
    assert "💰 1回1,000円賭けた場合 +1,000円" in r["comment_ja"]


def test_yen_helpers():
    """円換算ヘルパー: 1ユニット=1,000円、絵文字はプラス💰/マイナス📉/ゼロ➖"""
    from src.config import yen_of, yen_result_line
    assert yen_of(5.29) == 5290 and yen_of(-3.2) == -3200 and yen_of("") == 0
    assert yen_result_line(5.29) == "💰 1回1,000円賭けた場合 +5,290円"
    assert yen_result_line(-3.2) == "📉 1回1,000円賭けた場合 -3,200円"
    assert yen_result_line(0) == "➖ 1回1,000円賭けた場合 +0円"


def test_notify_text():
    r = {"date": "2026-07-13",
         "yesterday": {"n": 4, "win": 3, "lose": 1, "push": 0, "profit": 5.29},
         "comment_ja": "短評テキスト", "comment_en": "c",
         "proposals": [{"trend_ja": "MLBは検証20件でROI-30.0%と偏りが出ています",
                        "trend_en": "t",
                        "suggest_ja": "提案", "suggest_en": "s", "n": 20, "roi": -30.0}],
         "status_ja": "", "status_en": ""}
    text = review.notify_text(r)
    assert "📝 デイリーレビュー" in text and "3勝1敗" in text
    # 円換算 + 絵文字(ユニット/Uは使わない)
    assert "💰 1回1,000円賭けた場合 +5,290円" in text
    assert "ユニット" not in text and "損益" not in text   # 旧「損益 X.Xu」表記の廃止
    # 提案のROI(=回収率)は「戻ってきた割合」に言い換え
    assert "戻ってきた割合" in text and "ROI" not in text
    assert "短評テキスト" in text and "💡 改善提案" in text
    assert "自動適用されません" in text
    assert review.notify_text({}) == ""

    # マイナス収支は📉
    r2 = {**r, "yesterday": {"n": 2, "win": 0, "lose": 2, "push": 0, "profit": -3.2},
          "proposals": []}
    text2 = review.notify_text(r2)
    assert "📉 1回1,000円賭けた場合 -3,200円" in text2


def test_dashboard_card():
    """ダッシュボードにレビューカードが日英対応で載る。提案がある日は💡バッジ"""
    path = os.path.join(SCRATCH, "test_review_dash.html")
    r = {"date": "2026-07-13",
         "yesterday": {"n": 2, "win": 1, "lose": 1, "push": 0, "profit": 0.0},
         "comment_ja": "短評テキスト", "comment_en": "Short comment",
         "proposals": [{"trend_ja": "傾向x", "trend_en": "trend-x",
                        "suggest_ja": "提案y", "suggest_en": "suggest-y",
                        "n": 20, "roi": -30.0}],
         "status_ja": "", "status_en": ""}
    dashboard.build([], [], stats=analytics([]), review=r, path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    assert "今日のレビュー" in page and "Daily Review" in page
    assert "改善提案あり" in page          # 💡バッジ
    assert "短評テキスト" in page and "Short comment" in page
    assert "自動では適用されません" in page

    # 提案がない日: バッジなし・ステータス表示
    r2 = {**r, "proposals": [], "status_ja": "データ蓄積中(あと3件で最初の判定)",
          "status_en": "Accumulating"}
    dashboard.build([], [], stats=analytics([]), review=r2, path=path)
    with open(path, encoding="utf-8") as f:
        page2 = f.read()
    os.remove(path)
    assert "改善提案あり" not in page2
    assert "データ蓄積中(あと3件で最初の判定)" in page2

    # review=None(初回実行など)でも壊れない
    dashboard.build([], [], stats=analytics([]), review=None, path=path)
    os.remove(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
