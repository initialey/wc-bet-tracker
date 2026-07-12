"""集計整合性テスト(常設):
history.csvの生データから「独立に」再計算した成績が、
(1) main.analytics() の集計結果、(2) docs/index.html の表示値
と一致することを検証する。GitHub Actionsでダッシュボード生成後に実行され、
数字がズレていたらワークフローを失敗させて気づける状態を保つ。

仕様(答え合わせの取り扱い):
- 的中率: pushは分母・分子に含めない(win/loseのみ)
- 累積損益: pushも0として含める
- 履歴テーブル: 直近300件を表示(集計は全期間)
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.main import analytics, load_history  # noqa: E402
from src.config import PROB_HONMEI, PROB_SUISHO  # noqa: E402

HIST_DISPLAY_MAX = 300


def _tier(r):
    try:
        p = int(float(r["prob"]))
    except (TypeError, ValueError):
        p = 0
    return "hon" if p >= PROB_HONMEI else "sui" if p >= PROB_SUISHO else "ref"


def main():
    rows = load_history()
    if not os.path.exists("docs/index.html"):
        print("SKIP: docs/index.html がありません")
        return
    html = open("docs/index.html", encoding="utf-8").read()
    errors = []

    # --- 生データからの独立再計算 ---
    settled = [r for r in rows if r["result"] in ("win", "lose")]
    pushes = [r for r in rows if r["result"] == "push"]
    a = analytics(rows)

    # (1) analytics() の区分別集計 == 生データ再計算
    for t in a["tiers"]:
        grp = settled if t["key"] == "total" else [r for r in settled if _tier(r) == t["key"]]
        gp = pushes if t["key"] == "total" else [r for r in pushes if _tier(r) == t["key"]]
        w = sum(1 for r in grp if r["result"] == "win")
        pf = sum(float(r["profit"] or 0) for r in grp + gp)
        if (t["n"], t["win"], t["push"]) != (len(grp), w, len(gp)):
            errors.append(f"tier {t['key']}: analytics={t['win']}/{t['n']}(+p{t['push']}) "
                          f"raw={w}/{len(grp)}(+p{len(gp)})")
        if abs(t["profit"] - pf) > 0.005:
            errors.append(f"tier {t['key']}: profit analytics={t['profit']:.2f} raw={pf:.2f}")

    # (2) マーケット別の件数合計 == settled+push 全件(取りこぼし・重複なし)
    total_mkt = sum(m["n"] + m["push"] for sp in a["mroi"] for m in sp["markets"])
    if total_mkt != len(settled) + len(pushes):
        errors.append(f"mroi total {total_mkt} != settled+push {len(settled) + len(pushes)}")

    # (3) キャリブレーションの件数合計 == prob50以上のsettled件数
    calib_n = sum(c["n"] for c in a["calib"])
    in_bins = sum(1 for r in settled if 50 <= int(float(r["prob"])) <= 100)
    if calib_n != in_bins:
        errors.append(f"calib total {calib_n} != raw {in_bins}")

    # (4) ダッシュボード表示: 区分別テーブルに「win/n件」が出ているか
    i = html.find("区分別成績")
    seg = html[i:html.find("</table>", i)] if i >= 0 else ""
    for t in a["tiers"]:
        if t["n"] and f'{t["win"]}/{t["n"]}' not in seg:
            errors.append(f"dashboard tier {t['key']}: '{t['win']}/{t['n']}件' が表示に見当たらない")
        if t["push"] and f'+返金{t["push"]}' not in seg:
            errors.append(f"dashboard tier {t['key']}: '+返金{t['push']}' が表示に見当たらない")

    # (5) 履歴テーブルの表示行数 == min(全行, 300)
    shown = html.count("<tr data-date=")
    if shown != min(len(rows), HIST_DISPLAY_MAX):
        errors.append(f"履歴テーブル表示 {shown}行 != 期待 {min(len(rows), HIST_DISPLAY_MAX)}行")

    if errors:
        print("NG: 集計とダッシュボード表示に不整合があります:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print(f"OK: stats consistent (rows={len(rows)}, settled={len(settled)}, "
          f"push={len(pushes)}, hist shown={shown})")


if __name__ == "__main__":
    main()
