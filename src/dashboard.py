"""静的ダッシュボード v2 - 日英切替(根拠含む) / リーグ+カテゴリタブ / API残量 / 優勝オッズ / オッズ変動 / 実績分析"""
import html
import os
import re
from datetime import datetime, timezone, timedelta

from .config import tier_of_display, is_live_bet, live_bet_lines, LIVE_BET_FILTERS

PHT = timezone(timedelta(hours=8))   # フィリピン時間 (UTC+8)

MKT_EN = {"90分勝敗": "Match result (90')", "勝敗": "Moneyline", "勝敗(引分返金)": "Draw no bet",
          "両チーム得点": "Both teams to score", "チーム得点": "Team goals",
          "コーナー(参考)": "Corners (ref)", "ランライン": "Run line",
          "スコア予想(参考)": "Correct score (ref)"}
GROUP = {"90分勝敗": "win", "勝敗": "win", "勝敗(引分返金)": "win", "ランライン": "win",
         "両チーム得点": "goal", "チーム得点": "goal", "コーナー(参考)": "corner",
         "スコア予想(参考)": "goal"}


def _mkt_en(m):
    if m.startswith("ハンディ "):
        return "Handicap " + m.split(" ", 1)[1]
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
    if m.startswith("ハンディ"):
        return "win"
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
    "updated": ["最終更新", "Updated"], "auto": ["毎朝8時までに自動更新(フィリピン時間) · 当たりやすい順", "Auto-updates by 08:00 PHT · sorted by probability"],
    "rerun": ["⚡ 再分析を実行", "⚡ Re-analyze"],
    "s1": ["検証済み予想", "Settled predictions"], "s2": ["的中率", "Hit rate"],
    "s3": ["累積損益(1単位賭け)", "P/L (1-unit stakes)"], "s4": ["回収率", "ROI"],
    "s5": ["Odds API 残り", "Odds API remaining"], "s6": ["AI分析(今回)", "AI calls (this run)"],
    "tier": ["🎯 区分別成績", "🎯 Performance by tier"],
    "tp_n": ["成績(的中/検証)", "Record (hits/settled)"],
    "hist_note": ["※ 表示は直近300件。上部の集計はすべて全期間が対象", "Showing the latest 300 rows; totals above cover all time"],
    "tp_pl": ["累積損益", "P/L"], "tp_total": ["合計", "Total"],
    "t_all": ["全部", "All"], "t_win": ["勝敗系", "Result"], "t_goal": ["得点系", "Totals/Goals"],
    "t_corner": ["コーナー", "Corners"],
    "tb_hon": ["🟢 本命", "🟢 Strong"], "tb_sui": ["🟡 有力", "🟡 Likely"],
    "tb_ref": ["⚪ 参考", "⚪ Longshot"],
    "d_all": ["📅 全日程", "📅 All dates"],
    "h_from": ["期間:", "Range:"], "h_clear": ["クリア", "Clear"],
    "legend": ["🟢 本命 = 確率65%以上（当たりやすいが増え方は小さい）／ 🟡 有力 = 60〜64%（55〜59%帯はキャリブレーション検証中につき参考扱い）／ ⚪ 参考 = 当たりにくい、基本見送り ／ 期待値マイナス = オッズが割高 ／ →はオッズ変動（記録時→現在）／ ⏱90分 = 90分間で判定（延長・PK戦は含まない）／ ⏱延長込み = 延長を含む最終スコアで判定 ／ ⚾ MLBは52%以下の予想を非表示（接戦が本質のスポーツで、52%あれば野球では十分な傾き）。ただし各試合の最有力1件は常に表示",
               "🟢 Strong = 65%+ / 🟡 Likely = 60-64% (55-59% shown as Longshot while calibration is under review) / ⚪ Longshot = usually skip / Negative EV = overpriced / → shows odds movement (recorded → now) / ⏱90 min = settled on 90 minutes (no extra time or penalties) / ⏱incl. extras = settled on final score incl. extra time / ⚾ MLB picks at 52% or below are hidden (52% is already a solid lean in baseball) except each game's top market"],
    "hist": ["予想履歴と答え合わせ", "History & results"],
    "outright": ["(市場の見立て)", "(market view)"],
    "calib": ["📏 確率のキャリブレーション検証", "📏 Probability calibration"],
    "calib_note": ["AIが「X%」と言った予想が実際にX%当たっているか。予測と実績が近いほど信頼できる",
                   "Does an 'X%' prediction actually win X% of the time? Closer = more trustworthy"],
    "mroi": ["📊 マーケット別成績", "📊 Performance by market"],
    "c1": ["確率帯", "Prob range"], "c2": ["件数", "N"], "c3": ["予測平均", "Predicted"], "c4": ["実績", "Actual"],
    "m1": ["マーケット", "Market"], "m2": ["件数", "N"], "m3": ["成績(的中/検証)", "Record (hits/settled)"], "m4": ["回収率", "ROI"],
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


# 区分キー → バッジ表示。表示ラベルの判定は config.tier_of_display に一元化し、ここでは描画だけ
# (集計はconfig.tier_of。55〜59%帯は表示上「参考」だが検証集計では従来どおり「有力」区分)
_TIER_BADGE = {
    "hon": ("lb-h", "🟢 本命", "🟢 Strong"),
    "sui": ("lb-y", "🟡 有力", "🟡 Likely"),
    "ref": ("lb-s", "⚪ 参考", "⚪ Longshot"),
}


def _label(prob) -> str:
    cls, ja, en = _TIER_BADGE[tier_of_display(prob)]
    return f'<span class="lb {cls} tr" data-ja="{ja}" data-en="{en}">{ja}</span>'


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


MAX_FACTS_DISPLAY = 3   # 根拠となる事実の表示は最大3点(過去に長く記録された分も表示上は丸める)


def _reason_html(ja: str, en: str) -> str:
    """先頭セグメント=💡結論(太字1行)、残り=📋折りたたみの事実リスト(最大3点)"""
    pj, pe = _split_reason(ja, en)
    if not pj:
        return ""
    h_ja, h_en = pj[0], (pe[0] if pe else pj[0])
    out = (f'<div class="verdict">💡 <span class="tr" data-ja="{html.escape(h_ja)}" '
           f'data-en="{html.escape(h_en)}">{html.escape(h_ja)}</span></div>')
    if len(pj) > 1:
        items = ""
        for i, p in enumerate(pj[1:1 + MAX_FACTS_DISPLAY], start=1):
            e = pe[i] if i < len(pe) else p
            items += (f'<li class="tr" data-ja="{html.escape(p)}" '
                      f'data-en="{html.escape(e)}">{html.escape(p)}</li>')
        out += (f'<details class="facts" open><summary class="tr" data-ja="📋 根拠となる事実" '
                f'data-en="📋 Supporting facts">📋 根拠となる事実</summary>'
                f'<ul class="rsn">{items}</ul></details>')
    return out


def _rule_pill(rule) -> str:
    """判定ルールの明示: サッカー=90分(延長・PK含まず) / MLB等=延長込み"""
    if rule == "90":
        return ('<span class="lg tr" data-ja="⏱90分" data-en="⏱90 min">⏱90分</span>')
    if rule == "ext":
        return ('<span class="lg tr" data-ja="⏱延長込み" data-en="⏱incl. extras">⏱延長込み</span>')
    return ""


def _tr(key: str) -> str:
    ja, en = I18N[key]
    return f'<span class="tr" data-ja="{html.escape(ja)}" data-en="{html.escape(en)}">{html.escape(ja)}</span>'


def _record_html(a: dict) -> str:
    """成績の統一表示「4/7件 (57%)」+ pushがあれば「+返金n」。
    集計値はmain.analytics()の結果のみを使う(dashboard側で再計算しない)"""
    if not a.get("n"):
        return f'—{_push_note(a)}'
    return (f'{a["win"]}/{a["n"]}<span class="tr" data-ja="件" data-en="">件</span> '
            f'({a["hit"]:.0f}%){_push_note(a)}')


def _push_note(a: dict) -> str:
    if not a.get("push"):
        return ""
    return (f' <span class="sub tr" data-ja="+返金{a["push"]}" '
            f'data-en="+{a["push"]} push">+返金{a["push"]}</span>')


def _tier_record_html(t: dict) -> str:
    """区分別成績の内訳をすべて見える形にする:
    「検証 6/8 (75%) ／ 待ち 4 ／ 返金 0 ／ 合計 12件」。履歴テーブルの件数と突合可能。"""
    if t.get("n"):
        settled = (f'<span class="tr" data-ja="検証" data-en="Settled">検証</span> '
                   f'{t["win"]}/{t["n"]} ({t["hit"]:.0f}%)')
    else:
        settled = '<span class="tr" data-ja="検証" data-en="Settled">検証</span> 0'
    return " ／ ".join([
        settled,
        f'<span class="tr" data-ja="待ち" data-en="Pending">待ち</span> {t.get("pending", 0)}',
        f'<span class="tr" data-ja="返金" data-en="Push">返金</span> {t.get("push", 0)}',
        f'<b><span class="tr" data-ja="合計" data-en="Total">合計</span> {t.get("total", 0)}'
        f'<span class="tr" data-ja="件" data-en="">件</span></b>',
    ])


def _hist_summary(disp_rows: list) -> str:
    """履歴テーブルの表示行を区分別に集計した結果別サマリー(表と件数を突合するため)。
    区分判定は表示用の config.tier_of_display に一元化(表のバッジ・タブと同じ基準)。"""
    defs = [("hon", "🟢 本命", "🟢 Strong"), ("sui", "🟡 有力", "🟡 Likely"),
            ("ref", "⚪ 参考", "⚪ Longshot")]
    segs = []
    for key, ja, en in defs:
        g = [r for r in disp_rows if tier_of_display(r["prob"]) == key]
        w = sum(1 for r in g if r["result"] == "win")
        lo = sum(1 for r in g if r["result"] == "lose")
        pu = sum(1 for r in g if r["result"] == "push")
        pe = len(g) - w - lo - pu
        segs.append(
            f'<span class="tr" data-ja="{ja}" data-en="{en}">{ja}</span> '
            f'<span class="tr" data-ja="的中" data-en="Win">的中</span>{w} '
            f'<span class="tr" data-ja="外れ" data-en="Loss">外れ</span>{lo} '
            f'<span class="tr" data-ja="返金" data-en="Push">返金</span>{pu} '
            f'<span class="tr" data-ja="待ち" data-en="Pending">待ち</span>{pe} '
            f'(<span class="tr" data-ja="計" data-en="total">計</span>{len(g)})')
    return " ／ ".join(segs)


def _review_card(review) -> str:
    """「📝 今日のレビュー」カード(日英対応)。改善提案がある日は💡バッジで目立たせる。
    提案は表示のみ(自動適用はしない旨を明記)"""
    if not review or not review.get("date"):
        return ""
    y = review.get("yesterday") or {}
    badge = ""
    if review.get("proposals"):
        badge = ('<span class="tag" style="background:#F5A524;color:#101826;font-weight:800">'
                 '💡 <span class="tr" data-ja="改善提案あり" data-en="Proposals inside">'
                 '改善提案あり</span></span>')

    rec_html = ""
    if y.get("n"):
        pf = y.get("profit", 0)
        pf_cls = "good" if pf > 0 else "bad" if pf < 0 else ""
        push_ja = f" ➖ {y['push']}分" if y.get("push") else ""
        push_en = f" ➖ {y['push']}P" if y.get("push") else ""
        rec_html = (f'<div style="margin:6px 0"><span class="tr" '
                    f'data-ja="昨日: ✅ {y["win"]}勝 ❌ {y["lose"]}敗{push_ja}" '
                    f'data-en="Yesterday: ✅ {y["win"]}W ❌ {y["lose"]}L{push_en}">'
                    f'昨日: ✅ {y["win"]}勝 ❌ {y["lose"]}敗{push_ja}</span> / '
                    f'<span class="mono {pf_cls}">{pf:+.2f}u</span></div>')

    c_ja = html.escape(review.get("comment_ja") or "")
    c_en = html.escape(review.get("comment_en") or review.get("comment_ja") or "")
    comment = (f'<div style="line-height:1.7"><span class="tr" data-ja="{c_ja}" '
               f'data-en="{c_en}">{c_ja}</span></div>') if c_ja else ""

    body = ""
    for p in review.get("proposals", []):
        t_ja, t_en = html.escape(p["trend_ja"]), html.escape(p["trend_en"])
        s_ja, s_en = html.escape(p["suggest_ja"]), html.escape(p["suggest_en"])
        body += (f'<div style="margin-top:8px;padding:8px 10px;border-left:3px solid #F5A524;'
                 f'background:rgba(245,165,36,.07);border-radius:4px">'
                 f'<div>📈 <span class="tr" data-ja="{t_ja}" data-en="{t_en}">{t_ja}</span></div>'
                 f'<div class="sub" style="margin-top:2px">💡 <span class="tr" '
                 f'data-ja="{s_ja}" data-en="{s_en}">{s_ja}</span></div></div>')
    if review.get("proposals"):
        body += ('<div class="sub" style="margin-top:6px">⚠️ <span class="tr" '
                 'data-ja="提案は自動では適用されません(表示・通知のみ)" '
                 'data-en="Proposals are never applied automatically (display and '
                 'notification only)">提案は自動では適用されません(表示・通知のみ)</span></div>')
    elif review.get("status_ja"):
        st_ja, st_en = html.escape(review["status_ja"]), html.escape(review.get("status_en") or "")
        body += (f'<div class="sub" style="margin-top:6px">📊 <span class="tr" '
                 f'data-ja="{st_ja}" data-en="{st_en}">{st_ja}</span></div>')

    return (f'<div class="card" style="margin-top:0;margin-bottom:14px">'
            f'<h2>📝 <span class="tr" data-ja="今日のレビュー" data-en="Daily Review">'
            f'今日のレビュー</span> {badge}</h2>'
            f'<div class="sub mono" style="margin-bottom:4px">{html.escape(review["date"])}</div>'
            f'{rec_html}{comment}{body}</div>')


def _league_emoji(kind, label):
    if kind == "mlb":
        return "⚾"
    if kind == "soccer":
        return "⚽"
    return {"NBA": "🏀", "NFL": "🏈", "NHL": "🏒"}.get(label, "🏆")


def _no_games_panel(league_status) -> str:
    """予想カードが0件の日にカード一覧の場所へ出す案内パネル(日英対応)。
    リーグ別の次の試合日は取得済みオッズの結果を再利用したもの(追加APIなし)。
    取得できないリーグは「オフシーズン/日程未取得」"""
    rows = ""
    for s in league_status:
        dt = _pht_dt(s.get("next")) if s.get("next") else None
        if dt:
            ja = f"次の試合: {dt.month}/{dt.day}({WD_JA[dt.weekday()]})"
            en = f"Next game: {WD_EN[dt.weekday()]} {dt.month}/{dt.day}"
        else:
            ja, en = "オフシーズン/日程未取得", "Off-season / no schedule"
        rows += (f'<div style="display:flex;justify-content:space-between;gap:12px;'
                 f'padding:7px 2px;border-bottom:1px solid rgba(139,155,184,.15)">'
                 f'<span>{_league_emoji(s.get("kind"), s.get("label"))} '
                 f'{html.escape(s.get("label", ""))}</span>'
                 f'<span class="sub mono tr" data-ja="{html.escape(ja)}" '
                 f'data-en="{html.escape(en)}">{ja}</span></div>')
    return (f'<div class="card" style="grid-column:1/-1;margin-top:0">'
            f'<h2>📅 <span class="tr" data-ja="本日は分析対象の試合がありません" '
            f'data-en="No games to analyze today">本日は分析対象の試合がありません</span></h2>'
            f'<div class="sub" style="margin-bottom:8px"><span class="tr" '
            f'data-ja="有効化されているリーグの状況:" data-en="Status of enabled leagues:">'
            f'有効化されているリーグの状況:</span></div>{rows}</div>')


def build(history, predictions, outrights=None, meta=None, stats=None, path="docs/index.html",
          review=None, league_status=None):
    outrights, meta, stats = outrights or [], meta or {}, stats or {}
    n = (stats.get("overall") or {}).get("n", 0)

    # 区分別成績: analytics()の集計結果をそのまま描画(独自集計しない)
    tier_rows = ""
    for t in stats.get("tiers", []):
        total = t["key"] == "total"
        pl_cls = "good" if t["profit"] > 0 else "bad" if t["profit"] < 0 else ""
        label = (f'<b>{_tr("tp_total")}</b>' if total else
                 f'<span class="tr" data-ja="{t["ja"]}" data-en="{t["en"]}">{t["ja"]}</span>')
        style = ' style="border-top:2px solid #2A3854;font-weight:700"' if total else ""
        roi_s = f'{t["roi"]:+.1f}%' if t["roi"] is not None else "—"
        tier_rows += (f'<tr{style}><td>{label}</td><td class="mono">{_tier_record_html(t)}</td>'
                      f'<td class="mono {pl_cls}">{t["profit"]:+.2f}</td>'
                      f'<td class="mono {pl_cls}">{roi_s}</td></tr>')
    # Odds APIの残量表示: 分母(プラン総量)は「残り+使用済み」から動的に計算
    # (プラン変更してもハードコード修正が不要。ヘッダが取れない場合は残りのみ表示)
    try:
        q_rem = int(float(meta.get("odds_remaining")))
        q_total = q_rem + int(float(meta.get("odds_used") or 0))
        quota_html = (f'{q_rem:,}<span style="font-size:11px;color:#8B9BB8">'
                      f'/{q_total:,}</span>')
    except (TypeError, ValueError):
        quota_html = meta.get("odds_remaining") or "—"

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    action_url = f"https://github.com/{repo}/actions/workflows/analyze.yml" if repo else "#"
    now = datetime.now(PHT).strftime("%Y/%m/%d %H:%M")
    leagues = sorted({p.get("league", "") for p in predictions if p.get("league")})

    cards = ""
    date_map = {}
    for p in predictions:
        dt = _pht_dt(p["kickoff"])
        date_key = dt.strftime("%Y-%m-%d") if dt else ""
        if dt:
            date_map.setdefault(date_key, dt)

        if p.get("info_card"):
            # お知らせ/分析スキップ等の情報カード(オッズ・確率なし)
            cards += f"""<div class="pcard" data-grp="win" data-lg="{html.escape(p.get('league',''))}" data-tier="ref" data-date="{date_key}">
<div class="phead"><span class="tag tr" data-ja="{html.escape(p['tag_ja'])}" data-en="{html.escape(p.get('tag_en') or p['tag_ja'])}">{html.escape(p['tag_ja'])}</span>
<span class="lg">{html.escape(p.get('league',''))}</span>
<span class="sub mono">{_fmt_pht(p['kickoff'])}</span></div>
<div class="match">{html.escape(p['match'])}</div>
<div class="sub"><span class="tr" data-ja="{html.escape(p['text_ja'])}" data-en="{html.escape(p.get('text_en') or p['text_ja'])}">{html.escape(p['text_ja'])}</span></div>
</div>"""
            continue

        if p.get("score_card"):
            # スコア予想(参考): オッズ・期待値・推奨なしの参考カード
            picks_s = " / ".join(f"{s} ({pr}%)" for s, pr in p["picks"])
            cards += f"""<div class="pcard" data-grp="goal" data-lg="{html.escape(p.get('league',''))}" data-tier="ref" data-date="{date_key}">
<div class="phead"><span class="tag tr" data-ja="スコア予想(参考)" data-en="Correct score (ref)">スコア予想(参考)</span>
{_rule_pill(p.get("rule"))}<span class="lg">{html.escape(p.get('league',''))}</span>
<span class="sub mono">{_fmt_pht(p['kickoff'])}</span></div>
<div class="match">{html.escape(p['match'])}</div>
<div class="pick-row"><span class="pick mono">{html.escape(picks_s)}</span></div>
<div class="sub">⚠️ <span class="tr" data-ja="的中率が低い賭けのため参考表示のみ(オッズ・推奨なし)" data-en="Reference only — correct-score bets rarely hit (no odds or recommendation)">的中率が低い賭けのため参考表示のみ(オッズ・推奨なし)</span></div>
</div>"""
            continue

        evc = "good" if p["ev"] >= 0 else "bad"
        pay = round((p["odds"] - 1) * 100)
        hon = " hon" if tier_of_display(p["prob"]) == "hon" else ""
        cur = p.get("cur")
        move = ""
        if cur:
            arrow, cls = ("▲", "good") if cur > p["odds"] else ("▼", "bad")
            move = f'<span class="{cls}">→{cur:.2f}{arrow}</span>'
        hint = ""
        if p.get("hint_ja"):
            h_en = p.get("hint_en") or p["hint_ja"]
            hint = (f'<div class="sub">💬 <span class="tr" data-ja="{html.escape(p["hint_ja"])}" '
                    f'data-en="{html.escape(h_en)}">{html.escape(p["hint_ja"])}</span></div>')
        rule_pill = _rule_pill(p.get("rule"))
        pai, pmkt, pstat = p.get("prob_ai"), p.get("prob_market"), p.get("prob_stat")
        ja_parts, en_parts = [f"AI {pai}%"], [f"AI {pai}%"]
        if pmkt not in ("", None):
            ja_parts.append(f"市場 {pmkt}%")
            en_parts.append(f"Market {pmkt}%")
        if pstat not in ("", None):
            ja_parts.append(f"統計 {pstat}%")
            en_parts.append(f"Stat {pstat}%")
        # 確率補正層の効果を可視化: 表示中の確率(補正後)と異なる場合のみ補正前を併記
        praw = p.get("prob_raw")
        if praw not in ("", None) and str(praw) != str(p["prob"]):
            ja_parts.append(f"補正前 {praw}%")
            en_parts.append(f"Raw {praw}%")
        ai_mkt = ""
        if pai not in ("", None) and len(ja_parts) > 1:
            ja_s, en_s = " / ".join(ja_parts), " / ".join(en_parts)
            ai_mkt = f'<span class="tr" data-ja="{ja_s}" data-en="{en_s}">{ja_s}</span>'
        # 最良オッズの提供ブックメーカー名(記録がある予想のみ小さく併記)
        bm_name = p.get("bookmaker") or ""
        bm_s = (f' <span style="font-size:10px;color:#8B9BB8">{html.escape(bm_name)}</span>'
                if bm_name else "")
        # 🎯 実弾候補(条件はconfig.LIVE_BET_FILTERS): 損益分岐・合格ライン・買い判定を表示
        live = is_live_bet(p.get("league", ""), p["market"], p["prob"])
        live_attr = ' data-live="1"' if live else ""
        live_block = ""
        if live:
            be, ok_line = live_bet_lines(p["prob"])
            eff = cur if cur else p["odds"]   # ダッシュボード取得時点の最良オッズ
            if eff >= ok_line - 1e-9:
                badge = ('<span class="good">✅ <span class="tr" data-ja="買い候補" '
                         'data-en="Bet candidate">買い候補</span></span>')
            else:
                badge = ('<span class="bad">⚠️ <span class="tr" '
                         'data-ja="要オッズ確認(取得時点では合格ライン未満)" '
                         'data-en="Check odds (below the pass line when fetched)">'
                         '要オッズ確認(取得時点では合格ライン未満)</span></span>')
            live_block = (
                f'<div style="background:rgba(245,165,36,.08);border:1px solid #F5A52466;'
                f'border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.7">'
                f'🎯 <span class="tr" data-ja="実弾候補" data-en="Live bet">実弾候補</span> '
                f'{badge}<br>'
                f'<span class="mono">損益分岐 @{be:.2f} / 合格ライン @{ok_line:.2f}</span> '
                f'<span class="tr" data-ja="— この値以上でのみベット" '
                f'data-en="— only bet at this price or better">— この値以上でのみベット</span>'
                f'</div>')
        tier = tier_of_display(p["prob"])
        cards += f"""<div class="pcard{hon}" data-grp="{_grp(p['market'])}" data-lg="{html.escape(p.get('league',''))}" data-tier="{tier}" data-date="{date_key}"{live_attr}>
<div class="phead">{_label(p['prob'])}<span class="tag tr" data-ja="{html.escape(_mkt_ja(p['market']))}" data-en="{html.escape(_mkt_en(p['market']))}">{html.escape(_mkt_ja(p['market']))}</span>
{rule_pill}<span class="lg">{html.escape(p.get('league',''))}</span>
<span class="sub mono">{_fmt_pht(p['kickoff'])}</span></div>
<div class="match">{html.escape(p['match'])}</div>
{f'<div class="sub mono" style="margin-top:-4px">⚾ {html.escape(p["note"])}</div>' if p.get("note") else ""}
<div class="pick-row"><span class="pick tr" data-ja="{html.escape(p['pick'])}" data-en="{html.escape(_en_pick(p['pick']))}">{html.escape(p['pick'])}</span>
<span class="prob">{p['prob']}%</span></div>
{live_block}
{hint}
<div class="meta"><span>@{p['odds']:.2f}{move}{bm_s}</span><span>{_tr('pay')}+{pay}</span>
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

    # キャリブレーション: 全体 + スポーツ別。diff(実績-予測平均)はanalytics()の値を表示するだけ
    def _calib_bin_rows(bins):
        rows_ = ""
        for c in bins:
            d_cls = "good" if abs(c["diff"]) <= 10 else "bad"
            rows_ += (f'<tr><td style="padding-left:16px">{c["bin"]}</td>'
                      f'<td class="mono">{c["pred"]:.1f}%</td>'
                      f'<td class="mono">{_record_html(c)}</td>'
                      f'<td class="mono {d_cls}">{c["diff"]:+.1f}pt</td></tr>')
        return rows_

    def _calib_grp(ja, en, bins):
        return (f'<tr><td colspan="4" style="font-weight:800;padding-top:10px">'
                f'<span class="tr" data-ja="{html.escape(ja)}" data-en="{html.escape(en)}">'
                f'{html.escape(ja)}</span></td></tr>') + _calib_bin_rows(bins)

    calib_rows = ""
    if stats.get("calib"):
        calib_rows += _calib_grp("全体", "Overall", stats["calib"])
    # スポーツが1種類だけの場合は全体と同一になるため省略(重複表示を避ける)
    if len(stats.get("calib_sport") or []) > 1:
        for sp in stats["calib_sport"]:
            calib_rows += _calib_grp(sp["ja"], sp["en"], sp["bins"])
    def _mroi_row(ja, en, m, indent=16, sub=False):
        roi_cls = "" if m["roi"] is None else ("good" if m["roi"] > 0 else "bad")
        roi_s = f'{m["roi"]:+.1f}%' if m["roi"] is not None else "—"
        clv = m.get("clv")
        clv_cls = "" if clv is None else ("good" if clv > 0 else "bad")
        clv_s = (f'<span title="n={m.get("clv_n", 0)}">{clv:+.1f}%</span>'
                 if clv is not None else "—")
        style = f'padding-left:{indent}px' + (";color:#8B9BB8" if sub else "")
        return (f'<tr><td style="{style}"><span class="tr" data-ja="{html.escape(ja)}" '
                f'data-en="{html.escape(en)}">{html.escape(ja)}</span></td>'
                f'<td class="mono">{_record_html(m)}</td>'
                f'<td class="mono {roi_cls}">{roi_s}</td>'
                f'<td class="mono {clv_cls}">{clv_s}</td></tr>')

    mroi_rows = ""
    for sp in stats.get("mroi", []):
        sp_clv = sp.get("clv")
        sp_clv_s = f' <span class="mono sub">CLV {sp_clv:+.1f}%</span>' if sp_clv is not None else ""
        mroi_rows += (f'<tr><td colspan="4" style="font-weight:800;padding-top:10px">'
                      f'<span class="tr" data-ja="{html.escape(sp["ja"])}" '
                      f'data-en="{html.escape(sp["en"])}">{html.escape(sp["ja"])}</span>'
                      f'{sp_clv_s}</td></tr>')
        for m in sp["markets"]:
            if m.get("agg_ou"):
                # O/Uは全ライン計の集約行+折りたたみのライン別詳細(1行あたりの
                # 件数が少なく判断不能なため。集計はanalytics()の値をそのまま表示)
                unit_ja = "合計得点" if sp["sport"] == "mlb" else "合計ゴール"
                unit_en = "Total runs" if sp["sport"] == "mlb" else "Total goals"
                mroi_rows += _mroi_row(f"{unit_ja}(全ライン計)", f"{unit_en} (all lines)", m)
                inner = "".join(_mroi_row(_mkt_ja(l["market"]), _mkt_en(l["market"]), l,
                                          indent=8, sub=True) for l in m["lines"])
                mroi_rows += (f'<tr><td colspan="4" style="padding:2px 8px 8px 24px">'
                              f'<details class="facts"><summary><span class="tr" '
                              f'data-ja="ライン別詳細({len(m["lines"])}ライン)" '
                              f'data-en="By line ({len(m["lines"])} lines)">'
                              f'ライン別詳細({len(m["lines"])}ライン)</span></summary>'
                              f'<table style="min-width:0;margin-top:6px">{inner}</table>'
                              f'</details></td></tr>')
                continue
            mroi_rows += _mroi_row(_mkt_ja(m["market"]), _mkt_en(m["market"]), m)
            for b in m.get("bands", []):
                mroi_rows += _mroi_row(f"└ 予想確率{b['band']}", f"└ Prob {b['band']}",
                                       b, indent=28, sub=True)
    # 🎯 実弾候補条件該当分の集計行(LIVE_BET_FILTERSを過去分にも遡及適用した検証成績)
    lb = stats.get("live_bets")
    if lb and lb.get("total"):
        f_ = LIVE_BET_FILTERS
        cond = f'{"/".join(f_["sports"])} × {"/".join(f_["markets"])} × {f_["min_prob"]}%+'
        live_hdr = (f'<tr><td colspan="4" style="font-weight:800;padding-top:4px">'
                    f'🎯 <span class="tr" data-ja="実弾候補条件該当分(遡及適用)" '
                    f'data-en="Live-bet criteria matches (retroactive)">'
                    f'実弾候補条件該当分(遡及適用)</span></td></tr>')
        mroi_rows = live_hdr + _mroi_row(cond, cond, lb) + mroi_rows

    # ブックメーカー別 最良オッズ提供回数(analytics()の集計をそのまま描画。
    # bookmaker列の記録がまだ無い間はカード自体を出さない)
    bmk_rows = "".join(
        f'<tr><td>{html.escape(b["name"])}</td>'
        f'<td class="mono">{b["week"]}</td><td class="mono">{b["total"]}</td></tr>'
        for b in stats.get("bookmakers", []))
    bmk_card = ""
    if bmk_rows:
        bmk_card = f"""<div class="card"><h2>🏦 <span class="tr" data-ja="ブックメーカー別 最良オッズ提供回数" data-en="Best-odds count by bookmaker">ブックメーカー別 最良オッズ提供回数</span></h2>
<div class="sub" style="margin-bottom:8px"><span class="tr" data-ja="記録した予想でベストオッズを提供していた回数。どの業者が一貫して良い値付けをしているかの目安" data-en="How often each bookmaker offered the best available odds on recorded picks — a gauge of who consistently prices well">記録した予想でベストオッズを提供していた回数。どの業者が一貫して良い値付けをしているかの目安</span></div>
<div style="overflow-x:auto"><table style="min-width:0">
<tr><th><span class="tr" data-ja="ブックメーカー" data-en="Bookmaker">ブックメーカー</span></th><th><span class="tr" data-ja="直近7日" data-en="Last 7 days">直近7日</span></th><th><span class="tr" data-ja="累計" data-en="Total">累計</span></th></tr>
{bmk_rows}</table></div></div>"""

    empty3 = f'<tr><td colspan="4" class="sub">{_tr("empty3")}</td></tr>'

    hist_rows = ""
    for r in reversed(history[-300:]):
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
        res_key = r["result"] if r["result"] in ("win", "lose", "push") else "pending"
        hist_rows += f"""<tr data-date="{hdt.strftime('%Y-%m-%d') if hdt else ''}" data-tier="{tier_of_display(prob_i)}" data-res="{res_key}"><td class="mono">{_fmt_pht(r['kickoff_utc'])}</td>
<td>{html.escape(r['match'])}</td><td>{pred}</td>
<td>{_label(prob_i)}</td>
<td class="mono">{r['prob']}% / @{r['odds']}</td><td>{res}</td><td class="mono">{pf_s}</td></tr>"""

    # 予想カードが0件の日(お知らせカードのみ含む)は専用の案内パネルに切り替える。
    # 「待ち」の予想は通常カードとして表示されるため、待ちがある日は0件扱いにならない。
    # 実績セクション(区分別成績・キャリブレーション・履歴・レビュー等)は通常どおり表示
    has_real_cards = any(not p.get("info_card") for p in predictions)
    if not has_real_cards and league_status:
        grid_html = _no_games_panel(league_status)
    else:
        # 実弾候補タブ選択時に候補0件なら表示する案内(JSで切替)
        live_empty = ('<div id="liveEmpty" class="sub" style="display:none;grid-column:1/-1">'
                      '<span class="tr" data-ja="本日の実弾候補はありません" '
                      'data-en="No live-bet candidates today">本日の実弾候補はありません</span></div>')
        grid_html = (cards or f'<div class="sub">{_tr("empty")}</div>') + live_empty

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
<div class="stat"><div class="l">{_tr('s5')}</div><div class="v">{quota_html}</div></div>
<div class="stat"><div class="l">{_tr('s6')}</div><div class="v">{meta.get('ai_calls', 0)}</div></div>
</div>

{_review_card(review)}

<div class="card" style="margin-top:0;margin-bottom:14px"><h2>{_tr('tier')}</h2>
<div style="overflow-x:auto"><table style="min-width:0">
<tr><th>{_tr('h_lb')}</th><th>{_tr('tp_n')}</th><th>{_tr('tp_pl')}</th><th>{_tr('m4')}</th></tr>
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
<button class="tab tr" data-v="live" data-ja="🎯 実弾候補" data-en="🎯 Live Bets">🎯 実弾候補</button>
<button class="tab on" data-v="all">{_tr('t_all')}</button>
<button class="tab" data-v="win">{_tr('t_win')}</button>
<button class="tab" data-v="goal">{_tr('t_goal')}</button>
<button class="tab" data-v="corner">{_tr('t_corner')}</button>
</div>
{d_tabs}

<div class="grid" id="grid">
{grid_html}
</div>

{out_html}

<div class="two">
<div class="card"><h2>{_tr('calib')}</h2><div class="sub" style="margin-bottom:8px">{_tr('calib_note')}</div>
<div style="overflow-x:auto"><table style="min-width:0">
<tr><th>{_tr('c1')}</th><th>{_tr('c3')}</th><th>{_tr('c4')}</th><th><span class="tr" data-ja="予実差" data-en="Diff">予実差</span></th></tr>
{calib_rows or empty3}</table></div></div>
<div class="card"><h2>{_tr('mroi')}</h2>
<div class="sub" style="margin-bottom:8px"><span class="tr" data-ja="CLV = 記録時オッズ ÷ 締切オッズ − 1。プラス = 記録後に市場が予想方向へ動いた(市場に先行できている)。締切オッズは試合前最後の実行時点の観測値(近似)" data-en="CLV = odds at record ÷ closing odds − 1. Positive = the market moved toward our pick after recording (beating the market). Closing odds are the last observed odds before kickoff (approximation)">CLV = 記録時オッズ ÷ 締切オッズ − 1。プラス = 記録後に市場が予想方向へ動いた(市場に先行できている)。締切オッズは試合前最後の実行時点の観測値(近似)</span></div>
<div style="overflow-x:auto"><table style="min-width:0">
<tr><th>{_tr('m1')}</th><th>{_tr('m3')}</th><th>{_tr('m4')}</th><th>CLV</th></tr>
{mroi_rows or empty3}</table></div></div>
</div>

{bmk_card}

<div class="card"><h2>{_tr('hist')}</h2>
<div class="sub" style="margin-bottom:6px">{_tr('hist_note')}</div>
<div class="sub" style="margin-bottom:8px;line-height:1.8">{_hist_summary(history[-300:])}</div>
<div class="tabs" id="hres">
<button class="tab on" data-v="all">{_tr('t_all')}</button>
<button class="tab tr" data-v="win" data-ja="的中" data-en="Win">的中</button>
<button class="tab tr" data-v="lose" data-ja="外れ" data-en="Loss">外れ</button>
<button class="tab tr" data-v="push" data-ja="返金" data-en="Push">返金</button>
<button class="tab tr" data-v="pending" data-ja="待ち" data-en="Pending">待ち</button>
</div>
<div class="tabs" id="htier">
<button class="tab on" data-v="all">{_tr('t_all')}</button>
<button class="tab" data-v="hon">{_tr('tb_hon')}</button>
<button class="tab" data-v="sui">{_tr('tb_sui')}</button>
<button class="tab" data-v="ref">{_tr('tb_ref')}</button>
</div>
<div class="hfilter">{_tr('h_from')}
<input type="date" id="hfrom"> –
<input type="date" id="hto">
<button class="clr" id="hclear">{_tr('h_clear')}</button>
<span><span class="tr" data-ja="表示中:" data-en="Showing:">表示中:</span> <b id="hcount">0</b><span class="tr" data-ja="件" data-en="">件</span></span></div>
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
 (curG==='all' || (curG==='live' ? c.dataset.live==='1' : c.dataset.grp===curG)) &&
 (curD==='all' || c.dataset.date===curD);
c.style.display=ok?'':'none';}});
var le=document.getElementById('liveEmpty');
if(le){{le.style.display=(curG==='live'&&!document.querySelector('#grid .pcard[data-live="1"]'))?'':'none';}}}}
function bindTabs(box,fn){{
document.querySelectorAll(box+' .tab').forEach(function(t){{t.onclick=function(){{
document.querySelectorAll(box+' .tab').forEach(function(x){{x.classList.remove('on');}});
t.classList.add('on');fn(t);applyCards();}};}});}}
bindTabs('#ltabs',function(t){{curLg=t.dataset.v;}});
bindTabs('#ttabs',function(t){{curT=t.dataset.v;}});
bindTabs('#gtabs',function(t){{curG=t.dataset.v;}});
bindTabs('#dtabs',function(t){{curD=t.dataset.v;}});
var curHR='all',curHT='all';
function applyHist(){{
var f=document.getElementById('hfrom').value,t=document.getElementById('hto').value,n=0;
document.querySelectorAll('#htbl tr[data-date]').forEach(function(r){{
var d=r.dataset.date;
var ok=((!f||(d&&d>=f))&&(!t||(d&&d<=t)))&&
 (curHR==='all'||r.dataset.res===curHR)&&
 (curHT==='all'||r.dataset.tier===curHT);
r.style.display=ok?'':'none';if(ok)n++;}});
document.getElementById('hcount').textContent=n;}}
function bindHist(box,fn){{
document.querySelectorAll(box+' .tab').forEach(function(t){{t.onclick=function(){{
document.querySelectorAll(box+' .tab').forEach(function(x){{x.classList.remove('on');}});
t.classList.add('on');fn(t);applyHist();}};}});}}
bindHist('#hres',function(t){{curHR=t.dataset.v;}});
bindHist('#htier',function(t){{curHT=t.dataset.v;}});
document.getElementById('hfrom').onchange=applyHist;
document.getElementById('hto').onchange=applyHist;
document.getElementById('hclear').onclick=function(){{
document.getElementById('hfrom').value='';
document.getElementById('hto').value='';applyHist();}};
applyHist();
</script>
</body></html>"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
