"""静的ダッシュボード(docs/index.html)生成"""
import html
import os
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

CSS = """
body{margin:0;background:#101828;color:#EAF0FA;font-family:'Hiragino Sans','Yu Gothic UI',sans-serif;padding:20px 14px 40px}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:22px;margin:4px 0}.sub{font-size:12px;color:#8B9BB8}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}
.stat{flex:1 1 130px;background:#182234;border:1px solid #2A3854;border-radius:12px;padding:12px}
.stat .l{font-size:11px;color:#8B9BB8}.stat .v{font-size:22px;font-weight:800;font-family:ui-monospace,monospace}
.card{background:#182234;border:1px solid #2A3854;border-radius:14px;padding:16px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:#8B9BB8;font-weight:600;padding:6px 8px;border-bottom:1px solid #2A3854}
td{padding:7px 8px;border-bottom:1px solid #1E2A40}
.mono{font-family:ui-monospace,monospace}.good{color:#4ADE80;font-weight:700}.bad{color:#F87171}
.reason{font-size:11px;color:#8B9BB8;line-height:1.6}
.btn{display:inline-block;background:#F5A524;color:#0B1220;font-weight:800;padding:10px 18px;border-radius:10px;text-decoration:none;font-size:14px}
.tag{font-size:10px;border:1px solid #4CC3F7;color:#4CC3F7;border-radius:20px;padding:1px 8px}
.disc{font-size:11px;color:#8B9BB8;line-height:1.8;border-top:1px solid #2A3854;padding-top:14px;margin-top:10px}
h2{font-size:15px;margin:0 0 10px}
"""


def _fmt_jst(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST)
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return iso


def build(history: list, predictions: list, path: str = "docs/index.html"):
    """history: 全行(dict), predictions: 今後の試合の予測表示用(dict)"""
    settled = [r for r in history if r["result"] in ("win", "lose")]
    wins = [r for r in settled if r["result"] == "win"]
    profit = sum(float(r["profit"] or 0) for r in settled)
    n = len(settled)
    hit = f"{len(wins)/n*100:.0f}%" if n else "—"
    roi = f"{profit/n*100:+.1f}%" if n else "—"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    action_url = f"https://github.com/{repo}/actions/workflows/analyze.yml" if repo else "#"
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M")

    pred_rows = ""
    for p in predictions:
        cls = "good" if p["ev"] >= 0 else "bad"
        rec = "✓ 推奨" if p["recommended"] else ("見送り" if p["ev"] < 0 else "微妙")
        pred_rows += f"""<tr>
<td class="mono">{_fmt_jst(p['kickoff'])}</td>
<td><b>{html.escape(p['match'])}</b><div class="reason">{html.escape(p['reason'])}</div></td>
<td><span class="tag">{html.escape(p['market'])}</span><br>{html.escape(p['pick'])}</td>
<td class="mono">{p['prob']}%</td>
<td class="mono">@{p['odds']:.2f}</td>
<td class="mono {cls}">{p['ev']*100:+.1f}%</td>
<td class="{cls}">{rec}</td></tr>"""

    hist_rows = ""
    for r in reversed(history[-60:]):
        res = {"win": '<span class="good">的中</span>', "lose": '<span class="bad">外れ</span>'}.get(r["result"], "待ち")
        pf = float(r["profit"] or 0)
        pf_s = f'<span class="{"good" if pf > 0 else "bad" if pf < 0 else ""}">{pf:+.2f}</span>' if r["result"] in ("win", "lose") else "—"
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
<div class="sub">最終更新: {now} JST · 毎朝9時自動更新</div></div>
<a class="btn" href="{action_url}" target="_blank">⚡ 再分析を実行</a></div>

<div class="stats">
<div class="stat"><div class="l">検証済み予想</div><div class="v">{n}</div></div>
<div class="stat"><div class="l">的中率</div><div class="v">{hit}</div></div>
<div class="stat"><div class="l">累積損益(1単位賭け)</div><div class="v">{profit:+.2f}</div></div>
<div class="stat"><div class="l">ROI</div><div class="v">{roi}</div></div>
</div>

<div class="card"><h2>今後の試合の予測</h2>
<div style="overflow-x:auto"><table>
<tr><th>日時(JST)</th><th>試合 / 根拠</th><th>予想</th><th>確率</th><th>最良オッズ</th><th>EV</th><th>判定</th></tr>
{pred_rows or '<tr><td colspan="7" class="sub">分析対象の試合がありません</td></tr>'}
</table></div></div>

<div class="card"><h2>予想履歴と答え合わせ</h2>
<div style="overflow-x:auto"><table>
<tr><th>試合日</th><th>試合</th><th>予想</th><th>確率/オッズ</th><th>結果</th><th>損益</th></tr>
{hist_rows or '<tr><td colspan="6" class="sub">まだ履歴がありません</td></tr>'}
</table></div></div>

<div class="disc">⚠️ 本予想はAIと統計による参考情報であり、的中を保証するものではありません。EVがプラスでも単発では普通に外れます。スポーツベッティングの合法性は国・地域により異なります（日本国内からの海外ブックメーカー利用は違法とされています）。お住まいの地域の法律を確認し、余剰資金の範囲でご利用ください。</div>
</div></body></html>"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
