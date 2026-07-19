"""確率補正層のモックテスト(実APIキー不要)。
python tests/test_calibration.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import calibration, review, main  # noqa: E402
from src.calibration import build_tables, correct, K_SHRINK, MIN_SPORT_N  # noqa: E402
from src.main import FIELDS  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")


def _rows(n, win, prob, league="MLB"):
    out = []
    for i in range(n):
        r = {k: "" for k in FIELDS}
        r.update(id=f"c{league}{prob}{i}|勝敗", league=league, match="A vs B",
                 market="勝敗", pick="A", prob=str(prob),
                 result="win" if i < win else "lose",
                 profit="0.90" if i < win else "-1.00")
        out.append(r)
    return out


def test_shrinkage_scales_with_n():
    """縮小の設計: 件数が多い帯ほど実績に寄り、少ない帯はほぼ無補正"""
    # n=200, 実績40%, 予測57% → (40*200+57*50)/250 = 43.4%
    t_big = build_tables(_rows(200, 80, 57))
    assert abs(correct(t_big, "mlb", 0.57) * 100 - 43.4) < 0.05
    # n=5(帯20件未満→sport→allフォールバックでも同じ5件), 実績40% → ほぼ無補正
    t_small = build_tables(_rows(5, 2, 57))
    adj = correct(t_small, "mlb", 0.57) * 100
    assert abs(adj - (40 * 5 + 57 * K_SHRINK) / (5 + K_SHRINK)) < 0.05   # ≒55.5%
    assert abs(adj - 57) < 2.0   # 5件では2pt未満しか動かない


def test_sport_fallback_under_20():
    """スポーツ別の帯が20件未満 → 全スポーツ合算テーブルにフォールバック"""
    # MLB: 10件(不足) + サッカー: 40件 → all=50件
    hist = _rows(10, 8, 62, league="MLB") + _rows(40, 12, 62, league="プレミア")
    t = build_tables(hist)
    assert t["mlb"][(60, 65)][0] == 10 < MIN_SPORT_N
    # mlbはallテーブル(n=50, hit=40%)で補正される
    expect = (40 * 50 + 62 * K_SHRINK) / (50 + K_SHRINK)
    assert abs(correct(t, "mlb", 0.62) * 100 - expect) < 0.05
    # サッカーは自前テーブル(n=40, hit=30%)
    expect_s = (30 * 40 + 62 * K_SHRINK) / (40 + K_SHRINK)
    assert abs(correct(t, "soccer", 0.62) * 100 - expect_s) < 0.05


def test_no_data_and_low_prob_unchanged():
    """データ無し/50%未満/該当帯なし → 無補正"""
    assert correct({}, "mlb", 0.57) == 0.57
    t = build_tables(_rows(30, 15, 57))
    assert correct(t, "mlb", 0.45) == 0.45          # 50%未満は帯が無い
    assert correct(t, "mlb", 0.66) == 0.66          # 65-69帯にデータ無し
    assert correct(t, "2way", 0.57) != 0.57          # その他種別はall適用


def test_calibrated_tuple_and_row():
    """_calibratedが補正後+prob_rawの7要素を返し、_mk_rowがprob_raw列に記録する"""
    main.CALIB_TABLES = build_tables(_rows(200, 80, 57))   # 57%帯: 実績40%
    c = main._calibrated(("A", 0.57, 1.90, 0.55, 0.58, None), "mlb")
    assert len(c) == 7 and c[6] == 0.57 and c[1] < 0.50    # 43.4%へ補正
    ev = {"id": "x", "commence_time": "2026-07-20T00:00:00Z",
          "home_team": "A", "away_team": "B"}
    from datetime import datetime, timezone
    row = main._mk_row(ev, "MLB", "勝敗", *c, "理由", "reason",
                       datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert row["prob_raw"] == 57 and row["prob"] == 43
    assert abs(float(row["ev"]) - (c[1] * 1.90 - 1)) < 0.01   # EVも補正後で計算
    main.CALIB_TABLES = {}


def test_proposal_dedup():
    """一度表示した提案は記録され、ROIが±5pt以上変化した時だけ再表示"""
    path = os.path.join(SCRATCH, "test_props_dedup.json")
    if os.path.exists(path):
        os.remove(path)
    p1 = [{"segment_ja": "確率帯 55-59%", "segment_en": "Prob band 55-59%",
           "n": 28, "roi": -24.6, "trend_ja": "t", "trend_en": "t",
           "suggest_ja": "s", "suggest_en": "s"}]
    shown, sup = review.filter_repeated_proposals(p1, path=path, today="2026-07-20")
    assert len(shown) == 1 and sup == 0                     # 初回は表示
    p2 = [dict(p1[0], roi=-26.0)]
    shown, sup = review.filter_repeated_proposals(p2, path=path, today="2026-07-21")
    assert len(shown) == 0 and sup == 1                     # 変化1.4pt → 抑制
    p3 = [dict(p1[0], roi=-31.0)]
    shown, sup = review.filter_repeated_proposals(p3, path=path, today="2026-07-22")
    assert len(shown) == 1 and sup == 0                     # 変化6.4pt → 再表示
    os.remove(path)


def test_prob_band_proposal_notes_calibration():
    """確率帯の提案には「確率補正層適用済み」の状態が付記される"""
    hist = _rows(20, 5, 57)   # 55-59帯 20件 全体ROI大幅マイナス
    from src.main import analytics
    out = review.build_proposals(analytics(hist))
    band = [p for p in out["proposals"] if p["segment_ja"].startswith("確率帯")]
    assert band and "確率補正層適用済み" in band[0]["suggest_ja"]
    assert "calibration correction layer" in band[0]["suggest_en"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
