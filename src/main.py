"""毎日の実行フロー(全部入り版):
答え合わせ → 新規分析(AI=h2h確率+xG+コーナー, ポアソンで全ゴール系確率) → 履歴保存 → ダッシュボード生成
1試合あたり最大8予想 + コーナー(参考表示のみ)"""
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

from . import odds_api, ai, model, dashboard
from .config import SPORT, REGIONS, DAYS_AHEAD, STAKE, PROB_SUISHO

HISTORY = "data/history.csv"
FIELDS = ["id", "created_utc", "kickoff_utc", "match", "market", "pick",
          "prob", "odds", "ev", "reason", "result", "profit"]

M_H2H = "90分勝敗"
M_DNB = "勝敗(引分返金)"
M_OU15, M_OU25, M_OU35 = "O/U 1.5", "O/U 2.5", "O/U 3.5"
M_BTTS = "両チーム得点"
M_TEAM = "チーム得点"
M_CORNER = "コーナー(参考)"  # 結果を自動取得できないため記録せず表示のみ

TRACKED = (M_H2H, M_DNB, M_OU15, M_OU25, M_OU35, M_BTTS, M_TEAM)


def load_history() -> list:
    if not os.path.exists(HISTORY):
        return []
    with open(HISTORY, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_history(rows: list):
    with open(HISTORY, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def settle(rows: list, scores: list):
    done = {}
    for ev in scores:
        if not ev.get("completed"):
            continue
        sc = {s["name"]: int(s["score"]) for s in (ev.get("scores") or [])}
        home, away = ev["home_team"], ev["away_team"]
        if home in sc and away in sc:
            done[f"{home} vs {away}"] = (sc[home], sc[away])

    for r in rows:
        if r["result"] or r["match"] not in done:
            continue
        h, a = done[r["match"]]
        home, away = r["match"].split(" vs ")
        pick, market = r["pick"], r["market"]

        if market == M_H2H:
            actual = home if h > a else (away if a > h else "引き分け")
            won = pick == actual
        elif market == M_DNB:
            if h == a:
                r["result"], r["profit"] = "push", "0.00"
                continue
            won = pick == (home if h > a else away)
        elif market in (M_OU15, M_OU25, M_OU35):
            line = float(market.split(" ")[1])
            won = (pick.startswith("オーバー") and h + a > line) or (pick.startswith("アンダー") and h + a < line)
        elif market == M_BTTS:
            won = (pick == "あり") == (h > 0 and a > 0)
        elif market == M_TEAM:
            team, side = pick.rsplit(" ", 1)  # "Argentina オーバー1.5"
            score = h if team == home else a
            won = (side.startswith("オーバー") and score >= 2) or (side.startswith("アンダー") and score <= 1)
        else:
            continue
        r["result"] = "win" if won else "lose"
        r["profit"] = f"{(float(r['odds']) - 1) * STAKE:.2f}" if won else f"{-STAKE:.2f}"


def _pick_side(cands):
    """[(pick, prob, odds)] からオッズが存在する中で最も確率の高い側を返す"""
    cands = [(p, pr, o) for p, pr, o in cands if o]
    return max(cands, key=lambda c: c[1]) if cands else None


def main():
    odds_key = os.environ["ODDS_API_KEY"]
    ai_key = os.environ["ANTHROPIC_API_KEY"]

    rows = load_history()
    try:
        settle(rows, odds_api.get_scores(odds_key, SPORT))
    except Exception as e:
        print(f"[warn] settle failed: {e}", file=sys.stderr)

    events = odds_api.get_upcoming(odds_key, SPORT, REGIONS)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=DAYS_AHEAD)
    predicted_keys = {(r["match"], r["market"]) for r in rows}
    display = []
    corner_display = []

    for ev in events:
        kickoff = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if not (now <= kickoff <= horizon):
            continue
        home, away = ev["home_team"], ev["away_team"]
        match = f"{home} vs {away}"
        best = odds_api.best_odds(ev)
        if not best["h2h"]:
            continue

        needed = [m for m in TRACKED if (match, m) not in predicted_keys]
        analysis, extra = None, None
        if needed:
            extra = odds_api.get_extra_markets(odds_key, SPORT, ev["id"], REGIONS)
            # 一括分と統合
            for k, v in best["totals"].items():
                extra["totals"][k] = max(extra["totals"].get(k, 0), v)
            try:
                analysis = ai.analyze_match(ai_key, home, away, ev["commence_time"])
            except Exception as e:
                print(f"[warn] AI failed for {match}: {e}", file=sys.stderr)

        new_rows = []
        if analysis:
            h2h = analysis.get("h2h", {})
            xg = analysis.get("xg", {})
            g = model.goal_probs(float(xg.get("home", 1.3)), float(xg.get("away", 1.3))) if xg else None
            xg_reason = xg.get("reason", "") if xg else ""

            if M_H2H in needed and h2h:
                c = _pick_side([
                    (home, h2h["home"] / 100, best["h2h"].get(home)),
                    ("引き分け", h2h["draw"] / 100, best["h2h"].get("Draw")),
                    (away, h2h["away"] / 100, best["h2h"].get(away)),
                ])
                if c:
                    new_rows.append((M_H2H, *c, h2h["reason"]))

            if M_DNB in needed and h2h and extra["dnb"]:
                ph, pa = h2h["home"], h2h["away"]
                fav, p_fav = (home, ph) if ph >= pa else (away, pa)
                if (ph + pa) > 0 and extra["dnb"].get(fav):
                    new_rows.append((M_DNB, fav, p_fav / (ph + pa), extra["dnb"][fav],
                                     f"引き分けなら返金の安全型／{h2h['reason']}"))

            if g:
                for mname, line, kover, kunder in (
                    (M_OU15, 1.5, "over15", "under15"),
                    (M_OU25, 2.5, "over25", "under25"),
                    (M_OU35, 3.5, "over35", "under35"),
                ):
                    if mname in needed:
                        c = _pick_side([
                            (f"オーバー{line}", g[kover], extra["totals"].get(f"Over {line}")),
                            (f"アンダー{line}", g[kunder], extra["totals"].get(f"Under {line}")),
                        ])
                        if c:
                            new_rows.append((mname, *c, xg_reason))

                if M_BTTS in needed and extra["btts"]:
                    c = _pick_side([
                        ("あり", g["btts_yes"], extra["btts"].get("Yes")),
                        ("なし", g["btts_no"], extra["btts"].get("No")),
                    ])
                    if c:
                        new_rows.append((M_BTTS, *c, xg_reason))

                if M_TEAM in needed and extra["team_totals"]:
                    for team, ko, ku in ((home, "home_over15", "home_under15"),
                                         (away, "away_over15", "away_under15")):
                        c = _pick_side([
                            (f"{team} オーバー1.5", g[ko], extra["team_totals"].get((team, "Over"))),
                            (f"{team} アンダー1.5", g[ku], extra["team_totals"].get((team, "Under"))),
                        ])
                        if c and (match, M_TEAM) not in {(r["match"], r["market"]) for r in rows
                                                          if r["pick"].startswith(team)}:
                            new_rows.append((M_TEAM, *c, xg_reason))

            # コーナー: 表示のみ(自動答え合わせ不可のため記録しない)
            cn = analysis.get("corners", {})
            if cn and extra["corners"]:
                lines = sorted({float(k.split(" ")[1]) for k in extra["corners"]})
                if lines:
                    line = min(lines, key=lambda x: abs(x - float(cn.get("total", 9.5))))
                    cp = model.corner_probs(float(cn.get("total", 9.5)), line)
                    c = _pick_side([
                        (f"オーバー{line}", cp["over"], extra["corners"].get(f"Over {line}")),
                        (f"アンダー{line}", cp["under"], extra["corners"].get(f"Under {line}")),
                    ])
                    if c:
                        pick, prob, odd = c
                        corner_display.append(dict(
                            kickoff=ev["commence_time"], match=match, market=M_CORNER,
                            pick=pick, prob=round(prob * 100), odds=odd,
                            ev=prob * odd - 1, reason=cn.get("reason", ""),
                            recommended=False))

        for market, pick, prob, odd, reason in new_rows:
            key_suffix = pick if market == M_TEAM else market
            rows.append({
                "id": f"{ev['id']}|{key_suffix}",
                "created_utc": now.strftime("%Y-%m-%dT%H:%M"),
                "kickoff_utc": ev["commence_time"],
                "match": match, "market": market, "pick": pick,
                "prob": round(prob * 100), "odds": f"{odd:.2f}",
                "ev": f"{prob * odd - 1:.3f}", "reason": reason,
                "result": "", "profit": "",
            })

        for r in rows:
            if r["match"] == match and not r["result"]:
                prob, odd = int(r["prob"]), float(r["odds"])
                display.append(dict(kickoff=r["kickoff_utc"], match=match, market=r["market"],
                                    pick=r["pick"], prob=prob, odds=odd, ev=prob / 100 * odd - 1,
                                    reason=r["reason"], recommended=prob >= PROB_SUISHO))

    save_history(rows)
    seen, uniq = set(), []
    for d in display + corner_display:
        k = (d["match"], d["market"], d["pick"])
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    dashboard.build(rows, sorted(uniq, key=lambda d: -d["prob"]))
    print(f"done: {len(rows)} total rows, {len(uniq)} upcoming predictions")


if __name__ == "__main__":
    main()
