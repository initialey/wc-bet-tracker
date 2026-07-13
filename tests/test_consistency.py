"""集計整合性テスト(常設):
history.csvの生データから「独立に」再計算した成績が、
(1) main.analytics() の集計結果、(2) docs/index.html の表示値
と一致することを検証する。GitHub Actionsでダッシュボード生成後に実行され、
数字がズレていたらワークフローを失敗させて気づける状態を保つ。

仕様(答え合わせの取り扱い):
- 的中率: pushは分母・分子に含めない(win/loseのみ)
- 累積損益: pushも0として含める
- 履歴テーブル: 直近300件を表示(集計は全期間)
- 区分は二本立て: 集計(検証)=PROB_SUISHO(55) / 表示ラベル=PROB_SUISHO_DISPLAY(60)。
  55〜59%帯は表示上「参考」格下げだが、記録・検証・キャリブレーション集計は従来どおり
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.main import analytics, load_history, CALIB_BINS  # noqa: E402
from src.config import PROB_HONMEI, PROB_SUISHO, PROB_SUISHO_DISPLAY, SPORTS  # noqa: E402

HIST_DISPLAY_MAX = 300


def _prob(r):
    try:
        return int(float(r["prob"]))
    except (TypeError, ValueError):
        return 0


def _tier(r):
    """集計(検証)区分: analyticsの区分別成績と同じ基準(PROB_SUISHO)"""
    p = _prob(r)
    return "hon" if p >= PROB_HONMEI else "sui" if p >= PROB_SUISHO else "ref"


def _tier_disp(r):
    """表示ラベル区分: 履歴テーブルのバッジ・data-tierと同じ基準(PROB_SUISHO_DISPLAY)。
    55〜59%帯は表示上「参考」格下げのため、集計区分(_tier)とは意図的に異なる"""
    p = _prob(r)
    return "hon" if p >= PROB_HONMEI else "sui" if p >= PROB_SUISHO_DISPLAY else "ref"


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

    # (3b) ★5%刻みビンの独立再計算: 件数・的中・予測平均・予実差(実績-予測)が一致
    def _calib_raw(grp_settled):
        out = {}
        for lo, hi in CALIB_BINS:
            g = [r for r in grp_settled if lo <= int(float(r["prob"])) < hi]
            if g:
                w = sum(1 for r in g if r["result"] == "win")
                pred = sum(int(float(r["prob"])) for r in g) / len(g)
                label = f"{lo}-{hi - 1}%" if hi < 101 else f"{lo}%+"
                out[label] = (len(g), w, pred, w / len(g) * 100 - pred)
        return out

    def _calib_check(name, bins, grp_settled):
        raw = _calib_raw(grp_settled)
        got = {c["bin"]: (c["n"], c["win"], c["pred"], c["diff"]) for c in bins}
        if set(raw) != set(got):
            errors.append(f"calib {name}: ビン不一致 analytics={sorted(got)} raw={sorted(raw)}")
            return
        for b, (n_, w_, pred_, diff_) in raw.items():
            gn, gw, gp, gd = got[b]
            if (gn, gw) != (n_, w_) or abs(gp - pred_) > 0.05 or abs(gd - diff_) > 0.05:
                errors.append(f"calib {name} {b}: analytics=({gn},{gw},{gp:.1f},{gd:+.1f}) "
                              f"raw=({n_},{w_},{pred_:.1f},{diff_:+.1f})")

    _calib_check("全体", a["calib"], settled)

    # (3c) ★スポーツ別キャリブレーション: 区分判定を独立再現して各ビンを突合、
    #      スポーツ別の件数合計 == 全体の件数
    label_kind = {label: kind for _, label, kind in SPORTS}

    def _sport(r):
        kind = label_kind.get(r["league"] or "", "soccer")
        return kind if kind in ("soccer", "mlb") else (r["league"] or "その他")

    for sp in a.get("calib_sport", []):
        _calib_check(f"sport:{sp['sport']}", sp["bins"],
                     [r for r in settled if _sport(r) == sp["sport"]])
    sport_n = sum(b["n"] for sp in a.get("calib_sport", []) for b in sp["bins"])
    if sport_n != calib_n:
        errors.append(f"calib_sport total {sport_n} != calib total {calib_n}")

    # (4) ダッシュボード表示: 区分別テーブルに「検証win/n」と「合計total件」が出ているか
    i = html.find("区分別成績")
    seg = html[i:html.find("</table>", i)] if i >= 0 else ""
    for t in a["tiers"]:
        if t["n"] and f'{t["win"]}/{t["n"]}' not in seg:
            errors.append(f"dashboard tier {t['key']}: 検証 '{t['win']}/{t['n']}' が表示に見当たらない")
        if f'合計</span> {t["total"]}' not in seg:
            errors.append(f"dashboard tier {t['key']}: '合計 {t['total']}件' が表示に見当たらない")

    # (4c) ダッシュボード表示: キャリブレーション表に全体の各ビン
    #      (ビン名・予測平均・予実差)が出ているか
    k = html.find("キャリブレーション")
    cseg = html[k:html.find("</table>", k)] if k >= 0 else ""
    for c in a["calib"]:
        if c["bin"] not in cseg:
            errors.append(f"dashboard calib: ビン '{c['bin']}' が表示に見当たらない")
        elif (f'{c["pred"]:.1f}%' not in cseg or f'{c["diff"]:+.1f}pt' not in cseg):
            errors.append(f"dashboard calib {c['bin']}: 予測平均 {c['pred']:.1f}% / "
                          f"予実差 {c['diff']:+.1f}pt が表示に見当たらない")

    # (4b) 履歴の結果別サマリーが表示行(直近300)の区分別内訳と一致するか
    disp = rows[-HIST_DISPLAY_MAX:]
    j = html.find("予想履歴")
    hseg = html[j:html.find("</table>", j)] if j >= 0 else ""
    for key in ("hon", "sui", "ref"):
        g = [r for r in disp if _tier_disp(r) == key]   # 履歴サマリーは表示区分で描画される
        w = sum(1 for r in g if r["result"] == "win")
        pe = sum(1 for r in g if r["result"] not in ("win", "lose", "push"))
        if f'的中</span>{w} ' not in hseg or f'待ち</span>{pe} ' not in hseg:
            errors.append(f"履歴サマリー {key}: 的中{w}/待ち{pe} が表示に見当たらない")

    # (5) 履歴テーブルの表示行数 == min(全行, 300)
    shown = html.count("<tr data-date=")
    if shown != min(len(rows), HIST_DISPLAY_MAX):
        errors.append(f"履歴テーブル表示 {shown}行 != 期待 {min(len(rows), HIST_DISPLAY_MAX)}行")

    # (6) ★履歴テーブルの描画行そのもの(data-tier/data-res属性)を区分×結果で数え、
    #     生データからの表示区分(_tier_disp)再計算と突合する。
    #     ※表示ラベルは55〜59%帯を「参考」に格下げしているため、区分別成績
    #     (集計区分=PROB_SUISHO)との直接一致はもはや仕様ではない。代わりに
    #     「表示行が表示区分の独立再計算と一致」+「全区分の合計=全行」を保証する
    import re
    tr_attrs = re.findall(r'<tr data-date="[^"]*" data-tier="(\w+)" data-res="(\w+)"', html)
    if len(tr_attrs) != shown:
        errors.append(f"履歴行の属性欠落: {len(tr_attrs)}/{shown}")
    if len(rows) <= HIST_DISPLAY_MAX:
        for key in ("hon", "sui", "ref"):
            g = [r for r in rows if _tier_disp(r) == key]
            want = (sum(1 for r in g if r["result"] in ("win", "lose")),
                    sum(1 for r in g if r["result"] == "win"),
                    sum(1 for r in g if r["result"] == "push"),
                    sum(1 for r in g if r["result"] not in ("win", "lose", "push")),
                    len(g))
            tbl = [res for tier, res in tr_attrs if tier == key]
            got = (sum(1 for x in tbl if x in ("win", "lose")),
                   sum(1 for x in tbl if x == "win"),
                   sum(1 for x in tbl if x == "push"),
                   sum(1 for x in tbl if x == "pending"),
                   len(tbl))
            if got != want:
                errors.append(f"履歴テーブル描画行と表示区分再計算の不一致 {key}: "
                              f"表(検証,的中,返金,待ち,合計)={got} != 再計算={want}")
        if len(tr_attrs) != len(rows):
            errors.append(f"履歴テーブル行数 {len(tr_attrs)} != 全履歴 {len(rows)}")

    if errors:
        print("NG: 集計とダッシュボード表示に不整合があります:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print(f"OK: stats consistent (rows={len(rows)}, settled={len(settled)}, "
          f"push={len(pushes)}, hist shown={shown})")


if __name__ == "__main__":
    main()
