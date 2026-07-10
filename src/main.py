"""毎日の実行フロー: 答え合わせ → 新規分析 → 履歴保存 → ダッシュボード生成
(複数ベット版: 1試合につき最大4マーケット。各マーケットで最も当たりやすい選択肢を採用)"""
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

from . import odds_api, ai, dashboard
from .config import SPORT, REGIONS, DAYS_AHEAD, STAKE, PROB_SUISHO

HISTORY = "data/history.csv"
FIELDS = ["id", "created_utc", "kickoff_utc", "match", "market", "pick",
          "prob", "odds", "ev", "reason", "result", "profit"]

M_H2H = "90分勝敗"
M_OU = "O/U 2.5"
M_BTTS = "両チーム得点"
M_DNB = "勝敗(引分返金)"


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
    """確定した試合の予想に的中/外れ/返金と損益を記入"""
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
        elif market == M_OU:
            won = (pick.startswith("オーバー") and h + a >= 3) or (pick.startswith("アンダー") and h + a <= 2)
        elif market == M_BTTS:
            won = (pick == "あり") == (h > 0 and a > 0)
        elif market == M_DNB:
            if h == a:  # 引き分け → 返金
                r["result"] = "push"
                r["profit"] = "0.00"
                continue
            won = pick == (home if h > a else away)
        else:
            continue
        r["result"] = "win" if won else "lose"
        r["profit"] = f"{(float(r['odds']) - 1) * STAKE:.2f}" if won else f"{-STAKE:.2f}"


def main():
    odds_key = os.environ["ODDS_API_KEY"]
    ai_key = os.environ["ANTHROPIC_API_KEY"]

    rows = load_history()

    # 1. 答え合わせ
    try:
        settle(rows, odds_api.get_scores(odds_key, SPORT))
    except Exception as e:
        print(f"[warn] settle failed: {e}", file=sys.stderr)

    # 2. 今後の試合を取得
    events = odds_api.get_upcoming(odds_key, SPORT, REGIONS)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=DAYS_AHEAD)
    predicted_keys = {(r["match"], r["market"]) for r in rows}
    display = []

    for ev in events:
        kickoff = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if not (now <= kickoff <= horizon):
            continue
        home, away = ev["home_team"], ev["away_team"]
        match = f"{home} vs {away}"
        best = odds_api.best_odds(ev)
        if not best["h2h"]:
            continue

        # 3. AI分析（未分析の試合のみ）
        markets_needed = [m for m in (M_H2H, M_OU, M_BTTS, M_DNB)
                          if (match, m) not in predicted_keys]
        analysis = None
        extra = {"btts": {}, "dnb": {}}
        if markets_needed:
            extra = odds_api.get_extra_markets(odds_key, SPORT, ev["id"], REGIONS)
            try:
                analysis = ai.analyze_match(ai_key, home, away, ev["commence_time"])
            except Exception as e:
                print(f"[warn] AI failed for {match}: {e}", file=sys.stderr)

        new_rows = []
        if analysis:
            h2h = analysis.get("h2h", {})
            # --- 90分勝敗: 最有力を採用 ---
            if M_H2H in markets_needed and h2h:
                cands = [
                    (home, h2h["home"] / 100, best["h2h"].get(home)),
                    ("引き分け", h2h["draw"] / 100, best["h2h"].get("Draw")),
                    (away, h2h["away"] / 100, best["h2h"].get(away)),
                ]
                cands = [(p, pr, o) for p, pr, o in cands if o]
                if cands:
                    pick, prob, odd = max(cands, key=lambda c: c[1])
                    new_rows.append((M_H2H, pick, prob, odd, h2h["reason"]))

            # --- 勝敗(引分返金): 本命側。確率は引分を除外した条件付き確率 ---
            if M_DNB in markets_needed and h2h and extra["dnb"]:
                ph, pa = h2h["home"], h2h["away"]
                fav, p_fav = (home, ph) if ph >= pa else (away, pa)
                denom = ph + pa
                if denom > 0 and extra["dnb"].get(fav):
                    prob = p_fav / denom
                    new_rows.append((M_DNB, fav, prob, extra["dnb"][fav],
                                     f"引き分けなら返金の安全型。{h2h['reason']}"))

            # --- O/U 2.5 ---
            tot = analysis.get("totals", {})
            if M_OU in markets_needed and tot:
                cands = [
                    ("オーバー2.5", tot["over"] / 100, best["totals"].get("Over 2.5")),
                    ("アンダー2.5", tot["under"] / 100, best["totals"].get("Under 2.5")),
                ]
                cands = [(p, pr, o) for p, pr, o in cands if o]
                if cands:
                    pick, prob, odd = max(cands, key=lambda c: c[1])
                    new_rows.append((M_OU, pick, prob, odd, tot["reason"]))

            # --- 両チーム得点 ---
            btts = analysis.get("btts", {})
            if M_BTTS in markets_needed and btts and extra["btts"]:
                cands = [
                    ("あり", btts["yes"] / 100, extra["btts"].get("Yes")),
                    ("なし", btts["no"] / 100, extra["btts"].get("No")),
                ]
                cands = [(p, pr, o) for p, pr, o in cands if o]
                if cands:
                    pick, prob, odd = max(cands, key=lambda c: c[1])
                    new_rows.append((M_BTTS, pick, prob, odd, btts["reason"]))

        for market, pick, prob, odd, reason in new_rows:
            rows.append({
                "id": f"{ev['id']}|{market}",
                "created_utc": now.strftime("%Y-%m-%dT%H:%M"),
                "kickoff_utc": ev["commence_time"],
                "match": match, "market": market, "pick": pick,
                "prob": round(prob * 100), "odds": f"{odd:.2f}",
                "ev": f"{prob * odd - 1:.3f}", "reason": reason,
                "result": "", "profit": "",
            })

        # 表示用（未決着の予想をすべて再掲）
        for r in rows:
            if r["match"] == match and not r["result"]:
                prob = int(r["prob"])
                odd = float(r["odds"])
                display.append(dict(kickoff=r["kickoff_utc"], match=match, market=r["market"],
                                    pick=r["pick"], prob=prob, odds=odd, ev=prob / 100 * odd - 1,
                                    reason=r["reason"], recommended=prob >= PROB_SUISHO))

    save_history(rows)
    seen, uniq = set(), []
    for d in display:
        if (d["match"], d["market"]) not in seen:
            seen.add((d["match"], d["market"]))
            uniq.append(d)
    # 当たりやすい順に並べる
    dashboard.build(rows, sorted(uniq, key=lambda d: -d["prob"]))
    print(f"done: {len(rows)} total rows, {len(uniq)} upcoming predictions")


if __name__ == "__main__":
    main()
