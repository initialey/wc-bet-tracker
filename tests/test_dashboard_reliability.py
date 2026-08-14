"""ダッシュボードの信頼性可視化(件数少なめの参考値表示・95%信頼区間・確率帯×オッズ帯クロス集計)の
モックテスト。python tests/test_dashboard_reliability.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import dashboard, main as m  # noqa: E402
from src.main import FIELDS, analytics  # noqa: E402
from src.config import MIN_RELIABLE_N  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")


def _row(**kw):
    r = {k: "" for k in FIELDS}
    r.update(id="r1|勝敗", league="MLB", match="Yankees vs Red Sox",
             market="勝敗", pick="Yankees", prob="60", odds="1.85",
             created_utc="2026-07-20T00:00")
    r.update(kw)
    return r


def test_wilson_ci_bounds_and_width():
    """Wilson score intervalは0〜100%の範囲に収まり、標本が小さいほど区間が広い"""
    lo, hi = m._wilson_ci(win=6, n=10)
    assert 0 <= lo < 60 < hi <= 100
    lo_big, hi_big = m._wilson_ci(win=600, n=1000)
    assert (hi - lo) > (hi_big - lo_big)          # n=10の方が区間が広い
    assert m._wilson_ci(0, 0) is None              # n=0はNone


def test_roi_ci_widens_with_small_n():
    """回収率の信頼区間は標本が少ないほど広い。1件以下はNone(分散が定義できない)"""
    small = [_row(result="win", profit="0.80"), _row(result="lose", profit="-1.00")]
    big = [_row(result="win", profit="0.80") for _ in range(25)] + \
          [_row(result="lose", profit="-1.00") for _ in range(25)]
    ci_small = m._roi_ci(small)
    ci_big = m._roi_ci(big)
    assert ci_small is not None and ci_big is not None
    assert (ci_small[1] - ci_small[0]) > (ci_big[1] - ci_big[0])
    assert m._roi_ci([_row(result="win", profit="0.80")]) is None   # n=1


def test_agg_flags_low_n():
    """_aggはMIN_RELIABLE_N未満の件数にlow_n=Trueを付ける。ci類は必ず併記される"""
    small = [_row(id=f"s{i}|勝敗", result="win", profit="0.80") for i in range(5)]
    big = [_row(id=f"b{i}|勝敗", result="win", profit="0.80")
           for i in range(MIN_RELIABLE_N)]
    a_small = m._agg(small, [])
    a_big = m._agg(big, [])
    assert a_small["low_n"] is True and a_small["n"] == 5
    assert a_big["low_n"] is False and a_big["n"] == MIN_RELIABLE_N
    assert a_small["hit_ci"] is not None and a_small["roi_ci"] is not None


def test_dashboard_low_n_badge_and_ci_tooltip():
    """件数がMIN_RELIABLE_N未満のマーケット行はグレー化(low-nクラス)+「参考値」バッジ表示。
    的中率・回収率にツールチップ(title属性)で95%信頼区間が付く"""
    path = os.path.join(SCRATCH, "test_reliability_dash.html")
    # チーム得点n=5(小標本)とMLBランラインn=60(十分な標本)を作る
    hist = [_row(id=f"t{i}|チーム得点", league="プレミア", market="チーム得点",
                pick="Team A オーバー1.5", result="win", profit="1.20")
            for i in range(5)]
    hist += [_row(id=f"r{i}|ランライン", market="ランライン", pick="Yankees -1.5",
                  result="win" if i % 2 else "lose",
                  profit="0.80" if i % 2 else "-1.00")
             for i in range(60)]
    dashboard.build(hist, [], stats=analytics(hist), path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    assert 'class="low-n"' in page
    assert "参考値" in page and "Low sample" in page
    assert "95% CI:" in page
    # 十分な標本(n=60)の行には参考値バッジが付かないことを、行単位で確認
    idx = page.find("ランライン</span></td>")
    assert idx != -1
    row_start = page.rfind("<tr", 0, idx)
    row_end = page.find("</tr>", idx)
    assert "参考値" not in page[row_start:row_end]


def test_prob_odds_matrix_structure_and_low_band_separation():
    """確率帯×オッズ帯のクロス集計: 50-54%帯はメインのprob_odds_matrixから除外され、
    prob_odds_low_bandとして別枠に分離される"""
    hist = []
    # 50-54%帯(大量): メイン集計から除外されるはず
    hist += [_row(id=f"low{i}|勝敗", prob="52", odds="1.75",
                  result="win" if i % 2 else "lose",
                  profit="0.75" if i % 2 else "-1.00")
             for i in range(20)]
    # 60-64%帯 × オッズ1.60-1.90: メイン集計に含まれるはず
    hist += [_row(id=f"mid{i}|勝敗", prob="62", odds="1.75",
                  result="win", profit="0.75") for i in range(3)]
    a = analytics(hist)
    bins_in_matrix = {row["bin"] for row in a["prob_odds_matrix"]}
    assert "50-54%" not in bins_in_matrix
    assert "60-64%" in bins_in_matrix
    assert a["prob_odds_low_band"]["bin"] == "50-54%"
    assert a["prob_odds_low_band"]["n"] == 20
    assert a["odds_bands"] == ["〜1.60", "1.60-1.90", "1.90-2.20", "2.20〜"]
    mid_row = [row for row in a["prob_odds_matrix"] if row["bin"] == "60-64%"][0]
    cell = [c for c in mid_row["cells"] if c["band"] == "1.60-1.90"][0]
    assert cell["n"] == 3 and cell["win"] == 3


def test_dashboard_matrix_card_renders_and_separates_low_band():
    """ダッシュボードにクロス集計テーブルが出て、50-54%帯は別枠(注記付き)になる"""
    path = os.path.join(SCRATCH, "test_matrix_dash.html")
    hist = [_row(id=f"low{i}|勝敗", prob="52", odds="1.75",
                result="win", profit="0.75") for i in range(10)]
    hist += [_row(id=f"mid{i}|勝敗", prob="62", odds="1.75",
                  result="win", profit="0.75") for i in range(5)]
    dashboard.build(hist, [], stats=analytics(hist), path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    assert "確率帯 × オッズ帯" in page
    assert "1.60-1.90" in page
    matrix_start = page.find("確率帯 × オッズ帯")
    low_band_note_idx = page.find("50-54%帯", matrix_start)
    assert low_band_note_idx != -1
    # メインのクロス集計テーブル(低帯注記より前の部分)には50-54%の行が出ない
    main_table = page[matrix_start:low_band_note_idx]
    assert "<td>50-54%</td>" not in main_table
    assert "<td>60-64%</td>" in main_table


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
