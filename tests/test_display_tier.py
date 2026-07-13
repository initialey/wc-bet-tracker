"""表示ラベル格下げ(55〜59%帯→参考)のモックテスト。
- 表示: tier_of_display / バッジ / data-tier / 凡例 / remind通知が60%基準
- 集計: tier_of / analytics / キャリブレーション / レビュー提案条件は従来(55%)のまま
python tests/test_display_tier.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import (tier_of, tier_of_display,  # noqa: E402
                        PROB_SUISHO, PROB_SUISHO_DISPLAY)
from src.main import FIELDS, analytics  # noqa: E402
from src import dashboard, review, remind  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")


def _row(i, prob, **kw):
    r = {k: "" for k in FIELDS}
    r.update(id=f"t{i}|勝敗", created_utc="2026-07-12T08:00",
             kickoff_utc="2026-07-12T18:00:00Z", league="MLB",
             match="Team A vs Team B", market="勝敗", pick="Team A",
             prob=str(prob), odds="1.90")
    r.update(kw)
    return r


def test_thresholds_are_separate():
    """集計は55%基準のまま、表示だけ60%基準(境界値を総当たり)"""
    assert PROB_SUISHO == 55 and PROB_SUISHO_DISPLAY == 60
    for p, agg, disp in [(54, "ref", "ref"), (55, "sui", "ref"), (57, "sui", "ref"),
                         (59, "sui", "ref"), (60, "sui", "sui"), (64, "sui", "sui"),
                         (65, "hon", "hon"), (70, "hon", "hon")]:
        assert tier_of(p) == agg, f"tier_of({p})"
        assert tier_of_display(p) == disp, f"tier_of_display({p})"


def test_analytics_unchanged():
    """検証集計: 55〜59%帯は従来どおり「有力」区分にカウント(データ蓄積を止めない)"""
    hist = [_row(i, 57, result="win" if i % 2 else "lose",
                 profit="0.90" if i % 2 else "-1.00") for i in range(10)]
    a = analytics(hist)
    sui = next(t for t in a["tiers"] if t["key"] == "sui")
    ref = next(t for t in a["tiers"] if t["key"] == "ref")
    assert sui["n"] == 10 and ref["n"] == 0   # 集計では有力のまま
    # キャリブレーションも従来の5%ビンで55-59%帯に蓄積
    assert a["calib"][0]["bin"] == "55-59%" and a["calib"][0]["n"] == 10


def test_dashboard_uses_display_tier():
    """履歴テーブル・予測カードのdata-tier/バッジは表示区分(60%基準)"""
    path = os.path.join(SCRATCH, "test_disp_tier.html")
    hist = [_row(0, 57, result="win", profit="0.90"),   # 55-59帯 → 表示は参考
            _row(1, 62, result="lose", profit="-1.00")]  # 60-64帯 → 表示は有力
    pred = [{"kickoff": "2026-07-14T18:00:00Z", "match": "Team C vs Team D",
             "league": "MLB", "market": "勝敗", "pick": "Team C", "prob": 57,
             "odds": 1.90, "ev": 0.08, "reason": "理由", "reason_en": "reason",
             "kind": "mlb"}]
    dashboard.build(hist, pred, stats=analytics(hist), path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)

    import re
    attrs = re.findall(r'<tr data-date="[^"]*" data-tier="(\w+)" data-res="(\w+)"', page)
    by_res = {res: tier for tier, res in attrs}
    assert by_res.get("win") == "ref"    # 57% → 参考
    assert by_res.get("lose") == "sui"   # 62% → 有力
    # 予測カード(57%)も参考扱い
    assert re.search(r'<div class="pcard" data-grp="win"[^>]*data-tier="ref"', page)
    # 凡例の説明が更新されている
    assert "有力 = 60〜64%" in page and "Likely = 60-64%" in page


def test_review_gate_unchanged():
    """デイリーレビューの提案条件は不変(15件以上・ROI±15%超)。
    55-59%帯もこれまでどおり判定対象としてデータが使われる"""
    assert review.GATE_MIN_N == 15 and review.GATE_ROI_PCT == 15.0
    hist = [_row(i, 57, result="lose", profit="-1.00") for i in range(20)]
    out = review.build_proposals(analytics(hist))
    assert any("確率帯 55-59%" in p["segment_ja"] for p in out["proposals"])


def test_remind_uses_display_tier():
    """試合前通知も表示ラベルと同じ基準(55〜59%は通知対象外の参考扱い)"""
    assert remind._tier(57) == "⚪ 参考"
    assert remind._tier(62) == "🟡 有力"
    assert remind._tier(65) == "🟢 本命"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
