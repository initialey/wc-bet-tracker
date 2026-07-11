"""静的ダッシュボード v2 - 日英切替(根拠含む) / リーグ+カテゴリタブ / API残量 / 優勝オッズ / オッズ変動 / 実績分析"""
import html
import os
import re
from datetime import datetime, timezone, timedelta

from .config import PROB_HONMEI, PROB_SUISHO

JST = timezone(timedelta(hours=9))

MKT_EN = {"90分勝敗": "Match result (90')", "勝敗": "Moneyline", "勝敗(引分返金)": "Draw no bet",
          "両チーム得点": "Both teams to score", "チーム得点": "Team goals",
          "コーナー(参考)": "Corners (ref)"}
GROUP = {"90分勝敗": "win", "勝敗": "win", "勝敗(引分返金)": "win",
         "両チーム得点": "goal", "チーム得点": "goal", "コーナー(参考)": "corner"}


def _mkt_en(m):
    return MKT_EN.get(m, m)


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
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 12px}
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
    "updated": ["最終更新", "Updated"], "auto": ["毎朝9時自動更新 · 当たりやすい順", "Auto-updates 9:00 JST · sorted by probability"],
    "rerun": ["⚡ 再分析を実行", "⚡ Re-analyze"],
    "s1": ["検証済み予想", "Settled predictions"], "s2": ["的中率", "Hit rate"],
    "s3": ["累積損益(1単位賭け)", "P/L (1-unit stakes)"], "s4": ["ROI", "ROI"],
    "s5": ["Odds API 残り", "Odds API remaining"], "s6": ["AI分析(今回)", "AI calls (this run)"],
    "t_all": ["すべて", "All"], "t_win": ["勝敗系", "Result"], "t_goal": ["得点系", "Totals/Goals"],
    "t_corner": ["コーナー", "Corners"], "t_hon": ["🟢本命のみ", "🟢 Strong only"],
    "d_all": ["📅 全日程", "📅 All dates"],
    "h_from": ["期間:", "Range:"], "h_clear": ["クリア", "Clear"],
    "legend": ["🟢 本命 = 確率65%以上（当たりやすいが増え方は小さい）／ 🟡 有力 = 55%以上 ／ ⚪ 参考 = 当たりにくい、基本見送り ／ EVマイナス = オッズが割高 ／ →はオッズ変動（記録時→現在）",
               "🟢 Strong = 65%+ / 🟡 Likely = 55%+ / ⚪ Longshot = usually skip / Negative EV = overpriced / → shows odds movement (recorded → now)"],
    "hist": ["予想履歴と答え合わせ", "History & results"],
    "outright": ["(市場の見立て)", "(market view)"],
    "calib": ["📏 確率のキャリブレーション検証", "📏 Probability calibration"],
    "calib_note": ["AIが「X%」と言った予想が実際にX%当たっているか。予測と実績が近いほど信頼できる",
                   "Does an 'X%' prediction actually win X% of the time? Closer = more trustworthy"],
    "mroi": ["📊 マーケット別成績", "📊 Performance by market"],
    "c1": ["確率帯", "Prob range"], "c2": ["件数", "N"], "c3": ["予測平均", "Predicted"], "c4": ["実績", "Actual"],
    "m1": ["マーケット", "Market"], "m2": ["件数", "N"], "m3": ["的中率", "Hit rate"], "m4": ["ROI", "ROI"],
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


def _fmt_jst(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST).strftime("%m/%d %H:%M")
    except Exception:
        return iso


WD_JA = ["月", "火", "水", "木", "金", "土", "日"]
WD_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _jst_dt(iso: str):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST)
    except Exception:
        return None


def _label(prob: int) -> str:
    if prob >= PROB_HONMEI:
        return '<span class="lb lb-h tr" data-ja="🟢 本命" data-en="🟢 Strong">🟢 本命</span>'
    if prob >= PROB_SUISHO:
        return '<span class="lb lb-y tr" data-ja="🟡 有力" data-en="🟡 Likely">🟡 有力</span>'
    return '<span class="lb lb-s tr" data-ja="⚪ 参考" data-en="⚪ Longshot">⚪ 参考</span>'


def _bullets(ja: str, en: str) -> str:
    pj = [p.strip(" 。") for p in re.split(r"[／/]|。", ja or "") if p.strip(" 。")]
    pe = [p.strip() for p in (en or "").split("/") if p.strip()]
    if not pj:
        return ""
    items = ""
    for i, p in enumerate(pj):
        e = pe[i] if i < len(pe) else p
        items += f'<li class="tr" data-ja="{html.escape(p)}" data-en="{html.escape(e)}">{html.escape(p)}</li>'
    return f'<ul class="rsn">{items}</ul>'


def _tr(key: str) -> str:
    ja, en = I18N[key]
    return f'<span class="tr" data-ja="{html.escape(ja)}" data-en="{html.escape(en)}">{html.escape(ja)}</span>'


def build(history, predictions, outrights=None, meta=None, stats=None, path="docs/index.html"):
    outrights, meta, stats = outrights or [], meta or {}, stats or {}
    settled = [r for r in history if r["result"] in ("win", "lose")]
    wins = [r for r in settled if r["result"] == "win"]
    profit = sum(float(r["profit"] or 0) for r in settled)
    n = len(settled)
    hit = f"{len(wins)/n*100:.0f}%" if n else "—"
    roi = f"{profit/n*100:+.1f}%" if n else "—"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    action_url = f"https://github.com/{repo}/actions/workflows/analyze.yml" if repo else "#"
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
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
        dt = _jst_dt(p["kickoff"])
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
        cards += f"""<div class="pcard{hon}" data-grp="{_grp(p['market'])}" data-lg="{html.escape(p.get('league',''))}" data-hon="{1 if p['prob'] >= PROB_HONMEI else 0}" data-date="{date_key}">
<div class="phead">{_label(p['prob'])}<span class="tag tr" data-ja="{html.escape(p['market'])}" data-en="{html.escape(_mkt_en(p['market']))}">{html.escape(p['market'])}</span>
<span class="lg">{html.escape(p.get('league',''))}</span>
<span class="sub mono">{_fmt_jst(p['kickoff'])}</span></div>
<div class="match">{html.escape(p['match'])}</div>
<div class="pick-row"><span class="pick tr" data-ja="{html.escape(p['pick'])}" data-en="{html.escape(_en_pick(p['pick']))}">{html.escape(p['pick'])}</span>
<span class="prob">{p['prob']}%</span></div>
<div class="meta"><span>@{p['odds']:.2f}{move}</span><span>{_tr('pay')}+{pay}</span>
<span class="{evc}">EV {p['ev']*100:+.1f}%</span>{ai_mkt}</div>
{_bullets(p['reason'], p.get('reason_en',''))}
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
        f'<tr><td>{html.escape(m["market"])}</td><td class="mono">{m["n"]}</td>'
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
        hdt = _jst_dt(r["kickoff_utc"])
        hist_rows += f"""<tr data-date="{hdt.strftime('%Y-%m-%d') if hdt else ''}"><td class="mono">{_fmt_jst(r['kickoff_utc'])}</td>
<td>{html.escape(r['match'])}</td><td>{html.escape(r['market'])}: {html.escape(r['pick'])}</td>
<td>{_label(prob_i)}</td>
<td class="mono">{r['prob']}% / @{r['odds']}</td><td>{res}</td><td class="mono">{pf_s}</td></tr>"""

    lg_tabs = "".join(f'<button class="tab" data-f="lg:{html.escape(l)}">{html.escape(l)}</button>'
                      for l in leagues if len(leagues) > 1)

    d_tabs = ""
    if len(date_map) > 1:
        btns = "".join(
            f'<button class="tab tr" data-d="{k}" data-ja="{dt.month}/{dt.day}({WD_JA[dt.weekday()]})" '
            f'data-en="{WD_EN[dt.weekday()]} {dt.month}/{dt.day}">{dt.month}/{dt.day}({WD_JA[dt.weekday()]})</button>'
            for k, dt in sorted(date_map.items()))
        d_tabs = f'<div class="tabs" id="dtabs"><button class="tab on" data-d="all">{_tr("d_all")}</button>{btns}</div>'

    page = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>AI Bet Tracker</title><style>{CSS}</style></head>
<body><div class="wrap">
<div class="topbar">
<div><div class="sub">AUTO TRACKER</div>
<h1>{_tr('title1')}<span style="color:#F5A524">{_tr('title2')}</span></h1>
<div class="sub">{_tr('updated')}: {now} JST · {_tr('auto')}</div></div>
<div style="display:flex;gap:8px;align-items:center">
<button class="lng" id="lng" onclick="tgl()">EN</button>
<a class="btn" href="{action_url}" target="_blank">{_tr('rerun')}</a></div></div>

<div class="stats">
<div class="stat"><div class="l">{_tr('s1')}</div><div class="v">{n}</div></div>
<div class="stat"><div class="l">{_tr('s2')}</div><div class="v">{hit}</div></div>
<div class="stat"><div class="l">{_tr('s3')}</div><div class="v">{profit:+.2f}</div></div>
<div class="stat"><div class="l">{_tr('s4')}</div><div class="v">{roi}</div></div>
<div class="stat"><div class="l">{_tr('s5')}</div><div class="v">{meta.get('odds_remaining') or '—'}<span style="font-size:11px;color:#8B9BB8">/500</span></div></div>
<div class="stat"><div class="l">{_tr('s6')}</div><div class="v">{meta.get('ai_calls', 0)}</div></div>
</div>

<div class="legend">{_tr('legend')}</div>

<div class="tabs" id="ftabs">
<button class="tab on" data-f="all">{_tr('t_all')}</button>
<button class="tab" data-f="win">{_tr('t_win')}</button>
<button class="tab" data-f="goal">{_tr('t_goal')}</button>
<button class="tab" data-f="corner">{_tr('t_corner')}</button>
<button class="tab" data-f="hon">{_tr('t_hon')}</button>
{lg_tabs}
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
var curF='all',curD='all';
function applyCards(){{
document.querySelectorAll('#grid .pcard').forEach(function(c){{
var okF = curF==='all' || c.dataset.grp===curF || (curF==='hon'&&c.dataset.hon==='1') ||
 (curF.indexOf('lg:')===0 && c.dataset.lg===curF.slice(3));
var okD = curD==='all' || c.dataset.date===curD;
c.style.display=(okF&&okD)?'':'none';}});}}
function bindTabs(box,fn){{
document.querySelectorAll(box+' .tab').forEach(function(t){{t.onclick=function(){{
document.querySelectorAll(box+' .tab').forEach(function(x){{x.classList.remove('on');}});
t.classList.add('on');fn(t);applyCards();}};}});}}
bindTabs('#ftabs',function(t){{curF=t.dataset.f;}});
bindTabs('#dtabs',function(t){{curD=t.dataset.d;}});
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
