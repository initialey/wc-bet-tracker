"""静的ダッシュボード(docs/index.html)生成 - レスポンシブ・カード型"""
import html
import os
import re
from datetime import datetime, timezone, timedelta

from .config import PROB_HONMEI, PROB_SUISHO

JST = timezone(timedelta(hours=9))

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#101828;color:#EAF0FA;font-family:'Hiragino Sans','Yu Gothic UI',sans-serif;padding:20px 3vw 40px}
.wrap{max-width:1400px;margin:0 auto}
h1{font-size:22px;margin:4px 0}.sub{font-size:12px;color:#8B9BB8}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
.stat{background:#182234;border:1px solid #2A3854;border-radius:12px;padding:12px}
.stat .l{font-size:11px;color:#8B9BB8}.stat .v{font-size:22px;font-weight:800;font-family:ui-monospace,monospace}
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
.tag{font-size:10px;border:1px solid #4CC3F7;color:#4CC3F7;border-radius:20px;padding:1px 8px;white-space:nowrap}
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
@media(max-width:640px){
 body{padding:14px 10px 30px}
 .grid{grid-template-columns:1fr}
 h1{font-size:19px}.prob{font-size:21px}
 .btn{padding:8px 14px;font-size:13px}
}
"""


def _fmt_jst(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST)
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return iso


def _label(prob: int) -> str:
    if prob >= PROB_HONMEI:
        return '<span class="lb lb-h">🟢 本命</span>'
    if prob >= PROB_SUISHO:
        return '<span class="lb lb-y">🟡 有力</span>'
    return '<span class="lb lb-s">⚪ 参考</span>'


def _bullets(reason: str) -> str:
    """根拠テキストを箇条書きHTMLに変換（／・。区切りに対応）"""
    parts = [p.strip(" 。") for p in re.split(r"[／/]|。", reason) if p.strip(" 。")]
    if not parts:
        return ""
    items = "".join(f"<li>{html.escape(p)}</li>" for p in parts)
    return f'<ul class="rsn">{items}</ul>'


def build(history: list, predictions: list, path: str = "docs/index.html"):
    settled = [r for r in history if r["result"] in ("win", "lose")]
    wins = [r for r in settled if r["result"] == "win"]
    profit = sum(float(r["profit"] or 0) for r in settled)
    n = len(settled)
    hit = f"{len(wins)/n*100:.0f}%" if n else "—"
    roi = f"{profit/n*100:+.1f}%" if n else "—"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    action_url = f"https://github.com/{repo}/actions/workflows/analyze.yml" if repo else "#"
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M")

    cards = ""
    for p in predictions:
        evc = "good" if p["ev"] >= 0 else "bad"
        pay = round((p["odds"] - 1) * 100)
        hon = " hon" if p["prob"] >= PROB_HONMEI else ""
        cards += f"""<div class="pcard{hon}">
<div class="phead">{_label(p['prob'])}<span class="tag">{html.escape(p['market'])}</span>
<span class="sub mono">{_fmt_jst(p['kickoff'])}</span></div>
<div class="match">{html.escape(p['match'])}</div>
<div class="pick-row"><span class="pick">{html.escape(p['pick'])}</span>
<span class="prob">{p['prob']}%</span></div>
<div class="meta"><span>オッズ @{p['odds']:.2f}</span><span>100円→+{pay}円</span>
<span class="{evc}">EV {p['ev']*100:+.1f}%</span></div>
{_bullets(p['reason'])}
</div>"""

    hist_rows = ""
    for r in reversed(history[-60:]):
        res = {"win": '<span class="good">的中</span>', "lose": '<span class="bad">外れ</span>', "push": "返金"}.get(r["result"], "待ち")
        pf = float(r["profit"] or 0)
        pf_s = f'<span class="{"good" if pf > 0 else "bad" if pf < 0 else ""}">{pf:+.2f}</span>' if r["result"] in ("win", "lose", "push") else "—"
        hist_rows += f"""<tr><td class="mono">{_fmt_jst(r['kickoff_utc'])}</td>
<td>{html.escape(r['match'])}</td><td>{html.escape(r['market'])}: {html.escape(r['pick'])}</td>
<td class="mono">{r['prob']}% / @{r['odds']}</td><td>{res}</td><td class="mono">{pf_s}</td></tr>"""

    page = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>W杯ベット予想トラッカー</title><style>{CSS}</style></head>
<body><div class="wrap">
<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
<div><div class="sub">FIFA WORLD CUP 2026 · AUTO TRACKER</div>
<h1>AIベット予想<span style="color:#F5A524">トラッカー</span></h1>
<div class="sub">最終更新: {now} JST · 毎朝9時自動更新 · 当たりやすい順</div></div>
<a class="btn" href="{action_url}" target="_blank">⚡ 再分析を実行</a></div>

<div class="stats">
<div class="stat"><div class="l">検証済み予想</div><div class="v">{n}</div></div>
<div class="stat"><div class="l">的中率</div><div class="v">{hit}</div></div>
<div class="stat"><div class="l">累積損益(1単位賭け)</div><div class="v">{profit:+.2f}</div></div>
<div class="stat"><div class="l">ROI</div><div class="v">{roi}</div></div>
</div>

<div class="legend">🟢 本命 = 確率{PROB_HONMEI}%以上（当たりやすいが増え方は小さい）／ 🟡 有力 = {PROB_SUISHO}%以上 ／ ⚪ 参考 = 当たりにくい予想、基本見送り ／ EVマイナス = オッズが割高（長期では目減りする値段）</div>

<div class="grid">
{cards or '<div class="sub">分析対象の試合がありません</div>'}
</div>

<div class="card"><h2>予想履歴と答え合わせ</h2>
<div style="overflow-x:auto"><table>
<tr><th>試合日</th><th>試合</th><th>予想</th><th>確率/オッズ</th><th>結果</th><th>損益</th></tr>
{hist_rows or '<tr><td colspan="6" class="sub">まだ履歴がありません</td></tr>'}
</table></div></div>

<div class="disc">⚠️ 本予想はAIと統計による参考情報であり、的中を保証するものではありません。確率が高い予想でも外れる時は外れます。スポーツベッティングの合法性は国・地域により異なります（日本国内からの海外ブックメーカー利用は違法とされています）。お住まいの地域の法律を確認し、余剰資金の範囲でご利用ください。</div>
</div></body></html>"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
