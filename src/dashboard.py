"""静的ダッシュボード生成 - 日英切替 / カテゴリタブ / API残量 / 優勝オッズ"""
import html
import json
import os
import re
from datetime import datetime, timezone, timedelta

from .config import PROB_HONMEI, PROB_SUISHO

JST = timezone(timedelta(hours=9))

MKT_EN = {"90分勝敗": "Match result (90')", "勝敗(引分返金)": "Draw no bet",
          "O/U 1.5": "O/U 1.5", "O/U 2.5": "O/U 2.5", "O/U 3.5": "O/U 3.5",
          "両チーム得点": "Both teams to score", "チーム得点": "Team goals",
          "コーナー(参考)": "Corners (ref)"}
GROUP = {"90分勝敗": "win", "勝敗(引分返金)": "win",
         "O/U 1.5": "goal", "O/U 2.5": "goal", "O/U 3.5": "goal",
         "両チーム得点": "goal", "チーム得点": "goal", "コーナー(参考)": "corner"}


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
table{width:100%;border-collapse:collapse;font-size:12px;min-width:560px}
th{text-align:left;color:#8B9BB8;font-weight:600;padding:6px 8px;border-bottom:1px solid #2A3854}
td{padding:7px 8px;border-bottom:1px solid #1E2A40}
.disc{font-size:11px;color:#8B9BB8;line-height:1.8;border-top:1px solid #2A3854;padding-top:14px;margin-top:16px}
h2{font-size:15px;margin:0 0 10px}
.legend{font-size:11px;color:#8B9BB8;line-height:1.9;margin:8px 0 12px}
.obar{height:8px;background:#1E2A40;border-radius:4px;overflow:hidden}
.obar>div{height:100%;background:#4CC3F7}
@media(max-width:640px){body{padding:14px 10px 30px}.grid{grid-template-columns:1fr}h1{font-size:19px}.prob{font-size:21px}}
"""

I18N = {
    "title2": ["トラッカー", "Tracker"], "title1": ["AIベット予想", "AI Bet Prediction "],
    "updated": ["最終更新", "Updated"], "auto": ["毎朝9時自動更新 · 当たりやすい順", "Auto-updates 9:00 JST · sorted by probability"],
    "rerun": ["⚡ 再分析を実行", "⚡ Re-analyze"],
    "s1": ["検証済み予想", "Settled predictions"], "s2": ["的中率", "Hit rate"],
    "s3": ["累積損益(1単位賭け)", "P/L (1-unit stakes)"], "s4": ["ROI", "ROI"],
    "s5": ["Odds API 残り", "Odds API remaining"], "s6": ["AI分析(今回実行)", "AI calls (this run)"],
    "t_all": ["すべて", "All"], "t_win": ["勝敗系", "Result"], "t_goal": ["ゴール系", "Goals"],
    "t_corner": ["コーナー", "Corners"], "t_hon": ["🟢本命のみ", "🟢 Strong only"],
    "legend": ["🟢 本命 = 確率65%以上（当たりやすいが増え方は小さい）／ 🟡 有力 = 55%以上 ／ ⚪ 参考 = 当たりにくい、基本見送り ／ EVマイナス = オッズが割高",
               "🟢 Strong = 65%+ probability (likely but low payout) / 🟡 Likely = 55%+ / ⚪ Longshot = usually skip / Negative EV = overpriced odds"],
    "hist": ["予想履歴と答え合わせ", "Prediction history & results"],
    "outright": ["優勝オッズ(市場の見立て)", "Winner odds (market view)"],
    "h1c": ["試合日", "Date"], "h2c": ["試合", "Match"], "h3c": ["予想", "Prediction"],
    "h4c": ["確率/オッズ", "Prob/Odds"], "h5c": ["結果", "Result"], "h6c": ["損益", "P/L"],
    "empty": ["分析対象の試合がありません", "No matches to analyze"],
    "empty2": ["まだ履歴がありません", "No history yet"],
    "note_ja": ["※根拠の文章は日本語のみ", "* Analysis text is Japanese only"],
    "pay": ["100円→", "¥100→"],
    "disc": ["⚠️ 本予想はAIと統計による参考情報であり、的中を保証するものではありません。確率が高い予想でも外れる時は外れます。スポーツベッティングの合法性は国・地域により異なります（日本国内からの海外ブックメーカー利用は違法とされています）。お住まいの地域の法律を確認し、余剰資金の範囲でご利用ください。",
             "⚠️ These predictions are AI/statistical estimates and do not guarantee outcomes. High-probability picks still lose. The legality of sports betting varies by country/region (using offshore bookmakers from within Japan is considered illegal). Check your local laws and only wager money you can afford to lose."],
}


def _fmt_jst(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST).strftime("%m/%d %H:%M")
    except Exception:
        return iso


def _label(prob: int) -> str:
    if prob >= PROB_HONMEI:
        return '<span class="lb lb-h tr" data-ja="🟢 本命" data-en="🟢 Strong">🟢 本命</span>'
    if prob >= PROB_SUISHO:
        return '<span class="lb lb-y tr" data-ja="🟡 有力" data-en="🟡 Likely">🟡 有力</span>'
    return '<span class="lb lb-s tr" data-ja="⚪ 参考" data-en="⚪ Longshot">⚪ 参考</span>'


def _bullets(reason: str) -> str:
    parts = [p.strip(" 。") for p in re.split(r"[／/]|。", reason) if p.strip(" 。")]
    if not parts:
        return ""
    return '<ul class="rsn">' + "".join(f"<li>{html.escape(p)}</li>" for p in parts) + "</ul>"


def _tr(key: str) -> str:
    ja, en = I18N[key]
    return f'<span class="tr" data-ja="{html.escape(ja)}" data-en="{html.escape(en)}">{html.escape(ja)}</span>'


def build(history: list, predictions: list, outrights: list = None, meta: dict = None,
          path: str = "docs/index.html"):
    outrights = outrights or []
    meta = meta or {}
    settled = [r for r in history if r["result"] in ("win", "lose")]
    wins = [r for r in settled if r["result"] == "win"]
    profit = sum(float(r["profit"] or 0) for r in settled)
    n = len(settled)
    hit = f"{len(wins)/n*100:.0f}%" if n else "—"
    roi = f"{profit/n*100:+.1f}%" if n else "—"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    action_url = f"https://github.com/{repo}/actions/workflows/analyze.yml" if repo else "#"
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    odds_rem = meta.get("odds_remaining") or "—"

    cards = ""
    for p in predictions:
        evc = "good" if p["ev"] >= 0 else "bad"
        pay = round((p["odds"] - 1) * 100)
        hon = " hon" if p["prob"] >= PROB_HONMEI else ""
        grp = GROUP.get(p["market"], "win")
        mkt_en = MKT_EN.get(p["market"], p["market"])
        cards += f"""<div class="pcard{hon}" data-grp="{grp}" data-hon="{1 if p['prob'] >= PROB_HONMEI else 0}">
<div class="phead">{_label(p['prob'])}<span class="tag tr" data-ja="{html.escape(p['market'])}" data-en="{html.escape(mkt_en)}">{html.escape(p['market'])}</span>
<span class="lg">{html.escape(p.get('league', ''))}</span>
<span class="sub mono">{_fmt_jst(p['kickoff'])}</span></div>
<div class="match">{html.escape(p['match'])}</div>
<div class="pick-row"><span class="pick tr" data-ja="{html.escape(p['pick'])}" data-en="{html.escape(_en_pick(p['pick']))}">{html.escape(p['pick'])}</span>
<span class="prob">{p['prob']}%</span></div>
<div class="meta"><span>@{p['odds']:.2f}</span><span>{_tr('pay')}+{pay}</span>
<span class="{evc}">EV {p['ev']*100:+.1f}%</span></div>
{_bullets(p['reason'])}
</div>"""

    out_html = ""
    for o in outrights:
        bars = ""
        for name, odd, prob in o["entries"]:
            bars += f"""<tr><td>{html.escape(name)}</td>
<td class="mono">@{odd:.2f}</td><td class="mono">{prob*100:.0f}%</td>
<td style="width:40%"><div class="obar"><div style="width:{min(prob*100*2,100):.0f}%"></div></div></td></tr>"""
        out_html += f"""<div class="card"><h2>🏆 {html.escape(o['label'])} {_tr('outright')}</h2>
<div style="overflow-x:auto"><table>{bars}</table></div></div>"""

    hist_rows = ""
    for r in reversed(history[-80:]):
        res = {"win": '<span class="good tr" data-ja="的中" data-en="Win">的中</span>',
               "lose": '<span class="bad tr" data-ja="外れ" data-en="Loss">外れ</span>',
               "push": '<span class="tr" data-ja="返金" data-en="Push">返金</span>'}.get(
            r["result"], '<span class="tr" data-ja="待ち" data-en="Pending">待ち</span>')
        pf = float(r["profit"] or 0)
        pf_s = f'<span class="{"good" if pf > 0 else "bad" if pf < 0 else ""}">{pf:+.2f}</span>' if r["result"] else "—"
        hist_rows += f"""<tr><td class="mono">{_fmt_jst(r['kickoff_utc'])}</td>
<td>{html.escape(r['match'])}</td><td>{html.escape(r['market'])}: {html.escape(r['pick'])}</td>
<td class="mono">{r['prob']}% / @{r['odds']}</td><td>{res}</td><td class="mono">{pf_s}</td></tr>"""

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
<div class="stat"><div class="l">{_tr('s5')}</div><div class="v">{odds_rem}<span style="font-size:11px;color:#8B9BB8">/500</span></div></div>
<div class="stat"><div class="l">{_tr('s6')}</div><div class="v">{meta.get('ai_calls', 0)}</div></div>
</div>

<div class="legend">{_tr('legend')}</div>

<div class="tabs">
<button class="tab on" data-f="all">{_tr('t_all')}</button>
<button class="tab" data-f="win">{_tr('t_win')}</button>
<button class="tab" data-f="goal">{_tr('t_goal')}</button>
<button class="tab" data-f="corner">{_tr('t_corner')}</button>
<button class="tab" data-f="hon">{_tr('t_hon')}</button>
</div>

<div class="grid" id="grid">
{cards or f'<div class="sub">{_tr("empty")}</div>'}
</div>

{out_html}

<div class="card"><h2>{_tr('hist')} <span class="sub">{_tr('note_ja')}</span></h2>
<div style="overflow-x:auto"><table>
<tr><th>{_tr('h1c')}</th><th>{_tr('h2c')}</th><th>{_tr('h3c')}</th><th>{_tr('h4c')}</th><th>{_tr('h5c')}</th><th>{_tr('h6c')}</th></tr>
{hist_rows or f'<tr><td colspan="6" class="sub">{_tr("empty2")}</td></tr>'}
</table></div></div>

<div class="disc">{_tr('disc')}</div>
</div>
<script>
var lang='ja';
function tgl(){{lang=lang==='ja'?'en':'ja';
document.getElementById('lng').textContent=lang==='ja'?'EN':'日本語';
document.querySelectorAll('.tr').forEach(function(e){{e.textContent=e.dataset[lang];}});
document.documentElement.lang=lang;}}
document.querySelectorAll('.tab').forEach(function(t){{t.onclick=function(){{
document.querySelectorAll('.tab').forEach(function(x){{x.classList.remove('on');}});
t.classList.add('on');var f=t.dataset.f;
document.querySelectorAll('#grid .pcard').forEach(function(c){{
c.style.display=(f==='all'||c.dataset.grp===f||(f==='hon'&&c.dataset.hon==='1'))?'':'none';}});}};}});
</script>
</body></html>"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
