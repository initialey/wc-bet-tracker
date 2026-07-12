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
    pendings = [r for r in rows if r["result"] not in ("win", "lose", "push")]
    a = analytics(rows)

    # (1) analytics() の区分別集計 == 生データ再計算(検証/返金/待ち/合計すべて)
    for t in a["tiers"]:
        grp = settled if t["key"] == "total" else [r for r in settled if _tier(r) == t["key"]]
        gp = pushes if t["key"] == "total" else [r for r in pushes if _tier(r) == t["key"]]
        pe = pendings if t["key"] == "total" else [r for r in pendings if _tier(r) == t["key"]]
        w = sum(1 for r in grp if r["result"] == "win")
        pf = sum(float(r["profit"] or 0) for r in grp + gp)
        if (t["n"], t["win"], t["push"]) != (len(grp), w, len(gp)):
            errors.append(f"tier {t['key']}: analytics={t['win']}/{t['n']}(+p{t['push']}) "
                          f"raw={w}/{len(grp)}(+p{len(gp)})")
        # ★ラベル別の件数が集計と履歴(生データ)で一致すること: 待ち・合計まで突合
        if (t["pending"], t["total"]) != (len(pe), len(grp) + len(gp) + len(pe)):
            errors.append(f"tier {t['key']}: 待ち/合計 analytics=({t['pending']},{t['total']}) "
                          f"raw=({len(pe)},{len(grp) + len(gp) + len(pe)})")
        if abs(t["profit"] - pf) > 0.005:
            errors.append(f"tier {t['key']}: profit analytics={t['profit']:.2f} raw={pf:.2f}")

    # (1b) 区分の合計 == 全履歴件数(取りこぼし・二重計上なし)
    tier_total = sum(t["total"] for t in a["tiers"] if t["key"] != "total")
    if tier_total != len(rows):
        errors.append(f"区分別合計 {tier_total} != 全履歴 {len(rows)}")

    # (2) マーケット別の件数合計 == settled+push 全件(取りこぼし・重複なし)
    total_mkt = sum(m["n"] + m["push"] for sp in a["mroi"] for m in sp["markets"])
    if total_mkt != len(settled) + len(pushes):
        errors.append(f"mroi total {total_mkt} != settled+push {len(settled) + len(pushes)}")

    # (3) キャリブレーションの件数合計 == prob50以上のsettled件数
    calib_n = sum(c["n"] for c in a["calib"])
    in_bins = sum(1 for r in settled if 50 <= int(float(r["prob"])) <= 100)
    if calib_n != in_bins:
        errors.append(f"calib total {calib_n} != raw {in_bins}")

    # (4) ダッシュボード表示: 区分別テーブルに「検証win/n」と「合計total件」が出ているか
    i = html.find("区分別成績")
    seg = html[i:html.find("</table>", i)] if i >= 0 else ""
    for t in a["tiers"]:
        if t["n"] and f'{t["win"]}/{t["n"]}' not in seg:
            errors.append(f"dashboard tier {t['key']}: 検証 '{t['win']}/{t['n']}' が表示に見当たらない")
        if f'合計</span> {t["total"]}' not in seg:
            errors.append(f"dashboard tier {t['key']}: '合計 {t['total']}件' が表示に見当たらない")

    # (4b) 履歴の結果別サマリーが表示行(直近300)の区分別内訳と一致するか
    disp = rows[-HIST_DISPLAY_MAX:]
    j = html.find("予想履歴")
    hseg = html[j:html.find("</table>", j)] if j >= 0 else ""
    for key in ("hon", "sui", "ref"):
        g = [r for r in disp if _tier(r) == key]
        w = sum(1 for r in g if r["result"] == "win")
        pe = sum(1 for r in g if r["result"] not in ("win", "lose", "push"))
        if f'的中</span>{w} ' not in hseg or f'待ち</span>{pe} ' not in hseg:
            errors.append(f"履歴サマリー {key}: 的中{w}/待ち{pe} が表示に見当たらない")

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
