"""静的ダッシュボード v2 - 日英切替(根拠含む) / リーグ+カテゴリタブ / API残量 / 優勝オッズ / オッズ変動 / 実績分析"""
import html
import os
import re
from datetime import datetime, timezone, timedelta

from .config import PROB_HONMEI, PROB_SUISHO

PHT = timezone(timedelta(hours=8))   # フィリピン時間 (UTC+8)

MKT_EN = {"90分勝敗": "Match result (90')", "勝敗": "Moneyline", "勝敗(引分返金)": "Draw no bet",
          "両チーム得点": "Both teams to score", "チーム得点": "Team goals",
          "コーナー(参考)": "Corners (ref)", "ランライン": "Run line"}
GROUP = {"90分勝敗": "win", "勝敗": "win", "勝敗(引分返金)": "win", "ランライン": "win",
         "両チーム得点": "goal", "チーム得点": "goal", "コーナー(参考)": "corner"}


def _mkt_en(m):
    return MKT_EN.get(m, m)


def _mkt_ja(m):
    """保存されたマーケット名(データ)を日本語表示用に変換。history.csvの値自体は変更しない。"""
    if m.startswith("O/U "):
        rest = m.split(" ", 1)[1]
        # ライン値でサッカー(1.5/2.5/3.5)と野球(6.5〜)を判別して単位を出し分ける
        try:
            unit = "合計得点" if float(rest) >= 5 else "合計ゴール"
        except ValueError:
            unit = "合計"
        return f"{unit} {rest}"
    if m == "BTTS":
        return "両チーム得点"
    return m


def _grp(m):
    if m.startswith("O/U"):
        return "goal"
    return GROUP.get(m, "win")


def _en_pick(p: str) -> str:
    return (p.replace("オーバー", "Over ").replace("アンダー", "Under ")
             .replace("引き分け", "Draw").replace("あり", "Yes").replace("なし", "No"))


CSS = """
*{box-sizing:border-box}
body{margin:0;background:#101828;color:#EAF0FA;font-family:'Hiragino Sans','Yu Gothic UI',sans-serif;padding:20px 3vw 40px}
.wrap{max-width:1400px;margin:0 auto}
h1{font-size:22px;margin:4px 0}.sub{font-size:12px;color:#8B9BB8}
.topbar{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:16px 0}
.stat{background:#182234;border:1px solid #2A3854;border-radius:12px;padding:12px}
.stat .l{font-size:11px;color:#8B9BB8}.stat .v{font-size:20px;font-weight:800;font-family:ui-monospace,monospace}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 8px}
.tab{background:#182234;border:1px solid #2A3854;color:#8B9BB8;border-radius:20px;padding:6px 14px;font-size:12px;font-weight:700;cursor:pointer}
.tab.on{background:#F5A524;color:#0B1220;border-color:#F5A524}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.pcard{background:#182234;border:1px solid #2A3854;border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:8px}
.pcard.hon{border-color:#4ADE8066}
.phead{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.match{font-size:15px;font-weight:800}
.pick-row{display:flex;justify-content:space-between;align-items:center;background:#1E2A40;border-radius:10px;padding:10px 12px}
.pick{font-size:15px;font-weight:800}
.prob{font-size:24px;font-weight:800;font-family:ui-monospace,monospace}
.meta{display:flex;gap:14px;font-size:12px;color:#8B9BB8;font-family:ui-monospace,monospace;flex-wrap:wrap}
ul.rsn{margin:0;padding-left:18px;font-size:12px;color:#B8C4D9;line-height:1.7}
ul.rsn li{margin-bottom:2px}
.verdict{font-size:13px;font-weight:800;line-height:1.6}
details.facts summary{cursor:pointer;color:#8B9BB8;font-size:11px;font-weight:700;user-select:none}
details.facts ul.rsn{margin-top:6px}
.mono{font-family:ui-monospace,monospace}.good{color:#4ADE80;font-weight:700}.bad{color:#F87171}
.btn{display:inline-block;background:#F5A524;color:#0B1220;font-weight:800;padding:10px 18px;border-radius:10px;text-decoration:none;font-size:14px;white-space:nowrap}
.lng{background:#1E2A40;border:1px solid #2A3854;color:#EAF0FA;border-radius:10px;padding:9px 14px;font-size:13px;font-weight:700;cursor:pointer}
.tag{font-size:10px;border:1px solid #4CC3F7;color:#4CC3F7;border-radius:20px;padding:1px 8px;white-space:nowrap}
.lg{font-size:10px;border:1px solid #8B9BB8;color:#8B9BB8;border-radius:20px;padding:1px 8px;white-space:nowrap}
.lb{font-size:11px;font-weight:800;border-radius:20px;padding:2px 10px;white-space:nowrap}
.lb-h{background:#153524;color:#4ADE80;border:1px solid #4ADE80}
.lb-y{background:#3A2E10;color:#F5A524;border:1px solid #F5A524}
.lb-s{background:#1E2A40;color:#8B9BB8;border:1px solid #2A3854}
.card{background:#182234;border:1px solid #2A3854;border-radius:14px;padding:16px;margin-top:14px}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:520px}
th{text-align:left;color:#8B9BB8;font-weight:600;padding:6px 8px;border-bottom:1px solid #2A3854}
td{padding:7px 8px;border-bottom:1px solid #1E2A40}
.disc{font-size:11px;color:#8B9BB8;line-height:1.8;border-top:1px solid #2A3854;padding-top:14px;margin-top:16px}
h2{font-size:15px;margin:0 0 10px}
.legend{font-size:11px;color:#8B9BB8;line-height:1.9;margin:8px 0 12px}
.obar{height:8px;background:#1E2A40;border-radius:4px;overflow:hidden}
.obar>div{height:100%;background:#4CC3F7}
.hfilter{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px;color:#8B9BB8;margin-bottom:10px}
.hfilter input{background:#1E2A40;border:1px solid #2A3854;color:#EAF0FA;border-radius:8px;padding:6px 8px;font-size:12px;font-family:inherit;color-scheme:dark}
.hfilter .clr{background:#1E2A40;border:1px solid #2A3854;color:#8B9BB8;border-radius:8px;padding:6px 12px;font-size:12px;font-weight:700;cursor:pointer}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:900px){.two{grid-template-columns:1fr}}
@media(max-width:640px){body{padding:14px 10px 30px}.grid{grid-template-columns:1fr}h1{font-size:19px}.prob{font-size:21px}}
"""

I18N = {
    "title1": ["AIベット予想", "AI Bet Prediction "], "title2": ["トラッカー", "Tracker"],
    "updated": ["最終更新", "Updated"], "auto": ["毎朝8時(フィリピン時間)自動更新 · 当たりやすい順", "Auto-updates 08:00 PHT · sorted by probability"],
    "rerun": ["⚡ 再分析を実行", "⚡ Re-analyze"],
    "s1": ["検証済み予想", "Settled predictions"], "s2": ["的中率", "Hit rate"],
    "s3": ["累積損益(1単位賭け)", "P/L (1-unit stakes)"], "s4": ["回収率", "ROI"],
    "s5": ["Odds API 残り", "Odds API remaining"], "s6": ["AI分析(今回)", "AI calls (this run)"],
    "tier": ["🎯 区分別成績", "🎯 Performance by tier"],
    "tp_n": ["的中数 / 件数", "Hits / N"],
    "tp_pl": ["累積損益", "P/L"], "tp_total": ["合計", "Total"],
    "t_all": ["全部", "All"], "t_win": ["勝敗系", "Result"], "t_goal": ["得点系", "Totals/Goals"],
    "t_corner": ["コーナー", "Corners"],
    "tb_hon": ["🟢 本命", "🟢 Strong"], "tb_sui": ["🟡 有力", "🟡 Likely"],
    "tb_ref": ["⚪ 参考", "⚪ Longshot"],
    "d_all": ["📅 全日程", "📅 All dates"],
    "h_from": ["期間:", "Range:"], "h_clear": ["クリア", "Clear"],
    "legend": ["🟢 本命 = 確率65%以上（当たりやすいが増え方は小さい）／ 🟡 有力 = 55%以上 ／ ⚪ 参考 = 当たりにくい、基本見送り ／ 期待値マイナス = オッズが割高 ／ →はオッズ変動（記録時→現在）",
               "🟢 Strong = 65%+ / 🟡 Likely = 55%+ / ⚪ Longshot = usually skip / Negative EV = overpriced / → shows odds movement (recorded → now)"],
    "hist": ["予想履歴と答え合わせ", "History & results"],
    "outright": ["(市場の見立て)", "(market view)"],
    "calib": ["📏 確率のキャリブレーション検証", "📏 Probability calibration"],
    "calib_note": ["AIが「X%」と言った予想が実際にX%当たっているか。予測と実績が近いほど信頼できる",
                   "Does an 'X%' prediction actually win X% of the time? Closer = more trustworthy"],
    "mroi": ["📊 マーケット別成績", "📊 Performance by market"],
    "c1": ["確率帯", "Prob range"], "c2": ["件数", "N"], "c3": ["予測平均", "Predicted"], "c4": ["実績", "Actual"],
    "m1": ["マーケット", "Market"], "m2": ["件数", "N"], "m3": ["的中率", "Hit rate"], "m4": ["回収率", "ROI"],
    "h1c": ["試合日", "Date"], "h2c": ["試合", "Match"], "h3c": ["予想", "Prediction"],
    "h4c": ["確率/オッズ", "Prob/Odds"], "h5c": ["結果", "Result"], "h6c": ["損益", "P/L"],
    "h_lb": ["区分", "Tier"],
    "empty": ["分析対象の試合がありません", "No matches to analyze"],
    "empty2": ["まだ履歴がありません", "No history yet"],
    "empty3": ["検証データが貯まると表示されます", "Shown once settled data accumulates"],
    "pay": ["100円→", "¥100→"],
    "disc": ["⚠️ 本予想はAIと統計による参考情報であり、的中を保証するものではありません。確率が高い予想でも外れる時は外れます。スポーツベッティングの合法性は国・地域により異なります（日本国内からの海外ブックメーカー利用は違法とされています）。お住まいの地域の法律を確認し、余剰資金の範囲でご利用ください。",
             "⚠️ Predictions are AI/statistical estimates and do not guarantee outcomes. The legality of sports betting varies by region (using offshore bookmakers from within Japan is considered illegal). Check local laws and only wager what you can afford to lose."],
}


def _fmt_pht(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PHT).strftime("%m/%d %H:%M")
    except Exception:
        return iso


WD_JA = ["月", "火", "水", "木", "金", "土", "日"]
WD_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _pht_dt(iso: str):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PHT)
    except Exception:
        return None


def _label(prob: int) -> str:
    if prob >= PROB_HONMEI:
        return '<span class="lb lb-h tr" data-ja="🟢 本命" data-en="🟢 Strong">🟢 本命</span>'
    if prob >= PROB_SUISHO:
        return '<span class="lb lb-y tr" data-ja="🟡 有力" data-en="🟡 Likely">🟡 有力</span>'
    return '<span class="lb lb-s tr" data-ja="⚪ 参考" data-en="⚪ Longshot">⚪ 参考</span>'


def _split_reason(ja: str, en: str):
    """根拠テキストを分割。区切りは全角「／」と「。」のみ。
    半角「/」は日付(7/8)や単位(1.4/90分)に使われるため区切りとして扱わない"""
    pj = [p.strip(" 。") for p in re.split(r"[／。]", ja or "") if p.strip(" 。")]
    en = en or ""
    if "／" in en:
        pe = [p.strip() for p in en.split("／") if p.strip()]
    else:  # 旧形式の英文は " / " 区切り(前後スペース必須にして日付等を保護)
        pe = [p.strip() for p in en.split(" / ") if p.strip()]
    return pj, pe


def _reason_html(ja: str, en: str) -> str:
    """先頭セグメント=💡結論(太字1行)、残り=📋折りたたみの事実リスト"""
    pj, pe = _split_reason(ja, en)
    if not pj:
        return ""
    h_ja, h_en = pj[0], (pe[0] if pe else pj[0])
    out = (f'<div class="verdict">💡 <span class="tr" data-ja="{html.escape(h_ja)}" '
           f'data-en="{html.escape(h_en)}">{html.escape(h_ja)}</span></div>')
    if len(pj) > 1:
        items = ""
        for i, p in enumerate(pj[1:], start=1):
            e = pe[i] if i < len(pe) else p
            items += (f'<li class="tr" data-ja="{html.escape(p)}" '
                      f'data-en="{html.escape(e)}">{html.escape(p)}</li>')
        out += (f'<details class="facts" open><summary class="tr" data-ja="📋 根拠となる事実" '
                f'data-en="📋 Supporting facts">📋 根拠となる事実</summary>'
                f'<ul class="rsn">{items}</ul></details>')
    return out


def _tr(key: str) -> str:
    ja, en = I18N[key]
    return f'<span class="tr" data-ja="{html.escape(ja)}" data-en="{html.escape(en)}">{html.escape(ja)}</span>'


def build(history, predictions, outrights=None, meta=None, stats=None, path="docs/index.html"):
    outrights, meta, stats = outrights or [], meta or {}, stats or {}
    settled = [r for r in history if r["result"] in ("win", "lose")]
    n = len(settled)

    # 区分別(本命/有力/参考)の成績: 件数・的中率・累積損益・回収率
    def _tier_key(r):
        try:
            p = int(float(r["prob"]))
        except (TypeError, ValueError):
            p = 0
        return "hon" if p >= PROB_HONMEI else "sui" if p >= PROB_SUISHO else "ref"

    tier_defs = [
        ("hon", "lb-h", f"🟢 本命({PROB_HONMEI}%+)", f"🟢 Strong ({PROB_HONMEI}%+)"),
        ("sui", "lb-y", f"🟡 有力({PROB_SUISHO}〜{PROB_HONMEI - 1}%)",
         f"🟡 Likely ({PROB_SUISHO}-{PROB_HONMEI - 1}%)"),
        ("ref", "lb-s", f"⚪ 参考(〜{PROB_SUISHO - 1}%)", f"⚪ Longshot (<{PROB_SUISHO}%)"),
    ]
    tier_rows = ""
    for key, _, ja_l, en_l in tier_defs + [("all", "", "", "")]:
        grp = settled if key == "all" else [r for r in settled if _tier_key(r) == key]
        gn = len(grp)
        g_win = sum(1 for r in grp if r["result"] == "win")
        gp = sum(float(r["profit"] or 0) for r in grp)
        g_hit = f"{g_win / gn * 100:.0f}%" if gn else "—"
        g_roi = f"{gp / gn * 100:+.1f}%" if gn else "—"
        pl_cls = "good" if gp > 0 else "bad" if gp < 0 else ""
        if key == "all":
            label = f'<b>{_tr("tp_total")}</b>'
            style = ' style="border-top:2px solid #2A3854;font-weight:700"'
        else:
            label = f'<span class="tr" data-ja="{ja_l}" data-en="{en_l}">{ja_l}</span>'
            style = ""
        tier_rows += (f'<tr{style}><td>{label}</td><td class="mono">{g_win} / {gn}</td>'
                      f'<td class="mono">{g_hit}</td>'
                      f'<td class="mono {pl_cls}">{gp:+.2f}</td>'
                      f'<td class="mono {pl_cls}">{g_roi}</td></tr>')
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    action_url = f"https://github.com/{repo}/actions/workflows/analyze.yml" if repo else "#"
    now = datetime.now(PHT).strftime("%Y/%m/%d %H:%M")
    leagues = sorted({p.get("league", "") for p in predictions if p.get("league")})

    cards = ""
    date_map = {}
    for p in predictions:
        evc = "good" if p["ev"] >= 0 else "bad"
        pay = round((p["odds"] - 1) * 100)
        hon = " hon" if p["prob"] >= PROB_HONMEI else ""
        cur = p.get("cur")
        move = ""
        if cur:
            arrow, cls = ("▲", "good") if cur > p["odds"] else ("▼", "bad")
            move = f'<span class="{cls}">→{cur:.2f}{arrow}</span>'
        dt = _pht_dt(p["kickoff"])
        date_key = dt.strftime("%Y-%m-%d") if dt else ""
        if dt:
            date_map.setdefault(date_key, dt)
        pai, pmkt, pstat = p.get("prob_ai"), p.get("prob_market"), p.get("prob_stat")
        ja_parts, en_parts = [f"AI {pai}%"], [f"AI {pai}%"]
        if pmkt not in ("", None):
            ja_parts.append(f"市場 {pmkt}%")
            en_parts.append(f"Market {pmkt}%")
        if pstat not in ("", None):
            ja_parts.append(f"統計 {pstat}%")
            en_parts.append(f"Stat {pstat}%")
        ai_mkt = ""
        if pai not in ("", None) and len(ja_parts) > 1:
            ja_s, en_s = " / ".join(ja_parts), " / ".join(en_parts)
            ai_mkt = f'<span class="tr" data-ja="{ja_s}" data-en="{en_s}">{ja_s}</span>'
        tier = "hon" if p["prob"] >= PROB_HONMEI else "sui" if p["prob"] >= PROB_SUISHO else "ref"
        cards += f"""<div class="pcard{hon}" data-grp="{_grp(p['market'])}" data-lg="{html.escape(p.get('league',''))}" data-tier="{tier}" data-date="{date_key}">
<div class="phead">{_label(p['prob'])}<span class="tag tr" data-ja="{html.escape(_mkt_ja(p['market']))}" data-en="{html.escape(_mkt_en(p['market']))}">{html.escape(_mkt_ja(p['market']))}</span>
<span class="lg">{html.escape(p.get('league',''))}</span>
<span class="sub mono">{_fmt_pht(p['kickoff'])}</span></div>
<div class="match">{html.escape(p['match'])}</div>
{f'<div class="sub mono" style="margin-top:-4px">⚾ {html.escape(p["note"])}</div>' if p.get("note") else ""}
<div class="pick-row"><span class="pick tr" data-ja="{html.escape(p['pick'])}" data-en="{html.escape(_en_pick(p['pick']))}">{html.escape(p['pick'])}</span>
<span class="prob">{p['prob']}%</span></div>
<div class="meta"><span>@{p['odds']:.2f}{move}</span><span>{_tr('pay')}+{pay}</span>
<span class="{evc}"><span class="tr" data-ja="期待値" data-en="EV">期待値</span> {p['ev']*100:+.1f}%</span>{ai_mkt}</div>
{_reason_html(p['reason'], p.get('reason_en',''))}
</div>"""

    out_html = ""
    for o in outrights:
        bars = "".join(
            f'<tr><td>{html.escape(nm)}</td><td class="mono">@{od:.2f}</td>'
            f'<td class="mono">{pr*100:.0f}%</td>'
            f'<td style="width:40%"><div class="obar"><div style="width:{min(pr*200,100):.0f}%"></div></div></td></tr>'
            for nm, od, pr in o["entries"])
        out_html += f"""<div class="card"><h2>🏆 {html.escape(o['label'])} {_tr('outright')}</h2>
<div style="overflow-x:auto"><table>{bars}</table></div></div>"""

    calib_rows = "".join(
        f'<tr><td>{c["bin"]}</td><td class="mono">{c["n"]}</td>'
        f'<td class="mono">{c["pred"]:.0f}%</td>'
        f'<td class="mono {"good" if abs(c["actual"]-c["pred"])<=10 else "bad"}">{c["actual"]:.0f}%</td></tr>'
        for c in stats.get("calib", []))
    mroi_rows = "".join(
        f'<tr><td><span class="tr" data-ja="{html.escape(_mkt_ja(m["market"]))}" '
        f'data-en="{html.escape(_mkt_en(m["market"]))}">{html.escape(_mkt_ja(m["market"]))}</span></td>'
        f'<td class="mono">{m["n"]}</td>'
        f'<td class="mono">{m["hit"]:.0f}%</td>'
        f'<td class="mono {"good" if m["roi"]>0 else "bad"}">{m["roi"]:+.1f}%</td></tr>'
        for m in stats.get("mroi", []))
    empty3 = f'<tr><td colspan="4" class="sub">{_tr("empty3")}</td></tr>'

    hist_rows = ""
    for r in reversed(history[-80:]):
        res = {"win": '<span class="good tr" data-ja="的中" data-en="Win">的中</span>',
               "lose": '<span class="bad tr" data-ja="外れ" data-en="Loss">外れ</span>',
               "push": '<span class="tr" data-ja="返金" data-en="Push">返金</span>'}.get(
            r["result"], '<span class="tr" data-ja="待ち" data-en="Pending">待ち</span>')
        pf = float(r["profit"] or 0)
        pf_s = f'<span class="{"good" if pf > 0 else "bad" if pf < 0 else ""}">{pf:+.2f}</span>' if r["result"] else "—"
        try:
            prob_i = int(float(r["prob"]))
        except (TypeError, ValueError):
            prob_i = 0
        hdt = _pht_dt(r["kickoff_utc"])
        mp_ja = f"{_mkt_ja(r['market'])}: {r['pick']}"
        mp_en = f"{_mkt_en(r['market'])}: {_en_pick(r['pick'])}"
        pred = f'<span class="tr" data-ja="{html.escape(mp_ja)}" data-en="{html.escape(mp_en)}">{html.escape(mp_ja)}</span>'
        hist_rows += f"""<tr data-date="{hdt.strftime('%Y-%m-%d') if hdt else ''}"><td class="mono">{_fmt_pht(r['kickoff_utc'])}</td>
<td>{html.escape(r['match'])}</td><td>{pred}</td>
<td>{_label(prob_i)}</td>
<td class="mono">{r['prob']}% / @{r['odds']}</td><td>{res}</td><td class="mono">{pf_s}</td></tr>"""

    l_tabs = ""
    if len(leagues) > 1:
        btns = "".join(f'<button class="tab" data-v="{html.escape(l)}">{html.escape(l)}</button>'
                       for l in leagues)
        l_tabs = (f'<div class="tabs" id="ltabs">'
                  f'<button class="tab on" data-v="all">{_tr("t_all")}</button>{btns}</div>')

    d_tabs = ""
    if len(date_map) > 1:
        btns = "".join(
            f'<button class="tab tr" data-v="{k}" data-ja="{dt.month}/{dt.day}({WD_JA[dt.weekday()]})" '
            f'data-en="{WD_EN[dt.weekday()]} {dt.month}/{dt.day}">{dt.month}/{dt.day}({WD_JA[dt.weekday()]})</button>'
            for k, dt in sorted(date_map.items()))
        d_tabs = f'<div class="tabs" id="dtabs"><button class="tab on" data-v="all">{_tr("d_all")}</button>{btns}</div>'

    page = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>AI Bet Tracker</title><style>{CSS}</style></head>
<body><div class="wrap">
<div class="topbar">
<div><div class="sub">AUTO TRACKER</div>
<h1>{_tr('title1')}<span style="color:#F5A524">{_tr('title2')}</span></h1>
<div class="sub">{_tr('updated')}: {now} PHT · {_tr('auto')}</div></div>
<div style="display:flex;gap:8px;align-items:center">
<button class="lng" id="lng" onclick="tgl()">EN</button>
<a class="btn" href="{action_url}" target="_blank">{_tr('rerun')}</a></div></div>

<div class="stats">
<div class="stat"><div class="l">{_tr('s1')}</div><div class="v">{n}</div></div>
<div class="stat"><div class="l">{_tr('s5')}</div><div class="v">{meta.get('odds_remaining') or '—'}<span style="font-size:11px;color:#8B9BB8">/500</span></div></div>
<div class="stat"><div class="l">{_tr('s6')}</div><div class="v">{meta.get('ai_calls', 0)}</div></div>
</div>

<div class="card" style="margin-top:0;margin-bottom:14px"><h2>{_tr('tier')}</h2>
<div style="overflow-x:auto"><table style="min-width:0">
<tr><th>{_tr('h_lb')}</th><th>{_tr('tp_n')}</th><th>{_tr('m3')}</th><th>{_tr('tp_pl')}</th><th>{_tr('m4')}</th></tr>
{tier_rows}</table></div></div>

<div class="legend">{_tr('legend')}</div>

{l_tabs}
<div class="tabs" id="ttabs">
<button class="tab on" data-v="all">{_tr('t_all')}</button>
<button class="tab" data-v="hon">{_tr('tb_hon')}</button>
<button class="tab" data-v="sui">{_tr('tb_sui')}</button>
<button class="tab" data-v="ref">{_tr('tb_ref')}</button>
</div>
<div class="tabs" id="gtabs">
<button class="tab on" data-v="all">{_tr('t_all')}</button>
<button class="tab" data-v="win">{_tr('t_win')}</button>
<button class="tab" data-v="goal">{_tr('t_goal')}</button>
<button class="tab" data-v="corner">{_tr('t_corner')}</button>
</div>
{d_tabs}

<div class="grid" id="grid">
{cards or f'<div class="sub">{_tr("empty")}</div>'}
</div>

{out_html}

<div class="two">
<div class="card"><h2>{_tr('calib')}</h2><div class="sub" style="margin-bottom:8px">{_tr('calib_note')}</div>
<div style="overflow-x:auto"><table style="min-width:0">
<tr><th>{_tr('c1')}</th><th>{_tr('c2')}</th><th>{_tr('c3')}</th><th>{_tr('c4')}</th></tr>
{calib_rows or empty3}</table></div></div>
<div class="card"><h2>{_tr('mroi')}</h2>
<div style="overflow-x:auto"><table style="min-width:0">
<tr><th>{_tr('m1')}</th><th>{_tr('m2')}</th><th>{_tr('m3')}</th><th>{_tr('m4')}</th></tr>
{mroi_rows or empty3}</table></div></div>
</div>

<div class="card"><h2>{_tr('hist')}</h2>
<div class="hfilter">{_tr('h_from')}
<input type="date" id="hfrom"> –
<input type="date" id="hto">
<button class="clr" id="hclear">{_tr('h_clear')}</button></div>
<div style="overflow-x:auto"><table id="htbl">
<tr><th>{_tr('h1c')}</th><th>{_tr('h2c')}</th><th>{_tr('h3c')}</th><th>{_tr('h_lb')}</th><th>{_tr('h4c')}</th><th>{_tr('h5c')}</th><th>{_tr('h6c')}</th></tr>
{hist_rows or f'<tr><td colspan="7" class="sub">{_tr("empty2")}</td></tr>'}
</table></div></div>

<div class="disc">{_tr('disc')}</div>
</div>
<script>
var lang='ja';
function tgl(){{lang=lang==='ja'?'en':'ja';
document.getElementById('lng').textContent=lang==='ja'?'EN':'日本語';
document.querySelectorAll('.tr').forEach(function(e){{e.textContent=e.dataset[lang];}});
document.documentElement.lang=lang;}}
var curLg='all',curT='all',curG='all',curD='all';
function applyCards(){{
document.querySelectorAll('#grid .pcard').forEach(function(c){{
var ok = (curLg==='all' || c.dataset.lg===curLg) &&
 (curT==='all' || c.dataset.tier===curT) &&
 (curG==='all' || c.dataset.grp===curG) &&
 (curD==='all' || c.dataset.date===curD);
c.style.display=ok?'':'none';}});}}
function bindTabs(box,fn){{
document.querySelectorAll(box+' .tab').forEach(function(t){{t.onclick=function(){{
document.querySelectorAll(box+' .tab').forEach(function(x){{x.classList.remove('on');}});
t.classList.add('on');fn(t);applyCards();}};}});}}
bindTabs('#ltabs',function(t){{curLg=t.dataset.v;}});
bindTabs('#ttabs',function(t){{curT=t.dataset.v;}});
bindTabs('#gtabs',function(t){{curG=t.dataset.v;}});
bindTabs('#dtabs',function(t){{curD=t.dataset.v;}});
function applyHist(){{
var f=document.getElementById('hfrom').value,t=document.getElementById('hto').value;
document.querySelectorAll('#htbl tr[data-date]').forEach(function(r){{
var d=r.dataset.date;
r.style.display=((!f||(d&&d>=f))&&(!t||(d&&d<=t)))?'':'none';}});}}
document.getElementById('hfrom').onchange=applyHist;
document.getElementById('hto').onchange=applyHist;
document.getElementById('hclear').onclick=function(){{
document.getElementById('hfrom').value='';
document.getElementById('hto').value='';applyHist();}};
</script>
</body></html>"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
