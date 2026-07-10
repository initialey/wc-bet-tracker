"""毎日の実行フロー(マルチリーグ+優勝オッズ+クォータ表示版)"""
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

from . import odds_api, ai, model, dashboard
from .config import SPORTS, OUTRIGHTS, REGIONS, DAYS_AHEAD, STAKE, PROB_SUISHO

HISTORY = "data/history.csv"
FIELDS = ["id", "created_utc", "kickoff_utc", "match", "market", "pick",
          "prob", "odds", "ev", "reason", "result", "profit"]

M_H2H = "90分勝敗"
M_DNB = "勝敗(引分返金)"
M_OU15, M_OU25, M_OU35 = "O/U 1.5", "O/U 2.5", "O/U 3.5"
M_BTTS = "両チーム得点"
M_TEAM = "チーム得点"
M_CORNER = "コーナー(参考)"

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
            team, side = pick.rsplit(" ", 1)
            score = h if team == home else a
            won = (side.startswith("オーバー") and score >= 2) or (side.startswith("アンダー") and score <= 1)
        else:
            continue
        r["result"] = "win" if won else "lose"
        r["profit"] = f"{(float(r['odds']) - 1) * STAKE:.2f}" if won else f"{-STAKE:.2f}"


def _pick_side(cands):
    cands = [(p, pr, o) for p, pr, o in cands if o]
    return max(cands, key=lambda c: c[1]) if cands else None


def main():
    odds_key = os.environ["ODDS_API_KEY"]
    ai_key = os.environ["ANTHROPIC_API_KEY"]

    rows = load_history()
    ai_calls = 0

    # 1. 答え合わせ(全リーグ)
    for sport_key, _ in SPORTS:
        try:
            settle(rows, odds_api.get_scores(odds_key, sport_key))
        except Exception as e:
            print(f"[warn] settle failed for {sport_key}: {e}", file=sys.stderr)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=DAYS_AHEAD)
    predicted_keys = {(r["match"], r["market"]) for r in rows}
    display = []

    # 2. リーグごとに予測
    for sport_key, sport_label in SPORTS:
        try:
            events = odds_api.get_upcoming(odds_key, sport_key, REGIONS)
        except Exception as e:
            print(f"[warn] upcoming failed for {sport_key}: {e}", file=sys.stderr)
            continue

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
            corner_card = None
            if needed:
                extra = odds_api.get_extra_markets(odds_key, sport_key, ev["id"], REGIONS)
                for k, v in best["totals"].items():
                    extra["totals"][k] = max(extra["totals"].get(k, 0), v)
                try:
                    analysis = ai.analyze_match(ai_key, home, away, ev["commence_time"])
                    ai_calls += 1
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
                    for mname, line, ko, ku in ((M_OU15, 1.5, "over15", "under15"),
                                                (M_OU25, 2.5, "over25", "under25"),
                                                (M_OU35, 3.5, "over35", "under35")):
                        if mname in needed:
                            c = _pick_side([
                                (f"オーバー{line}", g[ko], extra["totals"].get(f"Over {line}")),
                                (f"アンダー{line}", g[ku], extra["totals"].get(f"Under {line}")),
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
                            if c:
                                new_rows.append((M_TEAM, *c, xg_reason))

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
                            corner_card = dict(kickoff=ev["commence_time"], match=match,
                                               market=M_CORNER, pick=pick, prob=round(prob * 100),
                                               odds=odd, ev=prob * odd - 1,
                                               reason=cn.get("reason", ""), recommended=False,
                                               league=sport_label)

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
                predicted_keys.add((match, market))

            for r in rows:
                if r["match"] == match and not r["result"]:
                    prob, odd = int(r["prob"]), float(r["odds"])
                    display.append(dict(kickoff=r["kickoff_utc"], match=match, market=r["market"],
                                        pick=r["pick"], prob=prob, odds=odd,
                                        ev=prob / 100 * odd - 1, reason=r["reason"],
                                        recommended=prob >= PROB_SUISHO, league=sport_label))
            if corner_card:
                display.append(corner_card)

    # 3. アウトライト(優勝オッズなど) 表示のみ
    outrights = []
    for key, label in OUTRIGHTS:
        lst = odds_api.get_outrights(odds_key, key, REGIONS)
        if lst:
            total_inv = sum(1 / o for _, o in lst)
            top = [(name, o, (1 / o) / total_inv) for name, o in lst[:10]]
            outrights.append({"label": label, "entries": top})

    save_history(rows)
    seen, uniq = set(), []
    for d in display:
        k = (d["match"], d["market"], d["pick"])
        if k not in seen:
            seen.add(k)
            uniq.append(d)

    meta = {
        "odds_remaining": odds_api.QUOTA["remaining"],
        "odds_used": odds_api.QUOTA["used"],
        "ai_calls": ai_calls,
    }
    dashboard.build(rows, sorted(uniq, key=lambda d: -d["prob"]), outrights, meta)
    print(f"done: {len(rows)} rows, {len(uniq)} predictions, {ai_calls} AI calls, "
          f"odds quota remaining={meta['odds_remaining']}")


if __name__ == "__main__":
    main()
