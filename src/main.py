"""毎日の実行フロー: 答え合わせ → 新規分析 → 履歴保存 → ダッシュボード生成
(当たりやすさ優先版: 各マーケットで最も確率の高い選択肢を予想として採用)"""
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

from . import odds_api, ai, dashboard
from .config import SPORT, REGIONS, DAYS_AHEAD, STAKE, PROB_SUISHO

HISTORY = "data/history.csv"
FIELDS = ["id", "created_utc", "kickoff_utc", "match", "market", "pick",
          "prob", "odds", "ev", "reason", "result", "profit"]


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
    """確定した試合の予想に的中/外れと損益を記入"""
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
        pick, market = r["pick"], r["market"]
        if market == "90分勝敗":
            actual = "home" if h > a else ("away" if a > h else "draw")
            won = pick == {"home": r["match"].split(" vs ")[0],
                           "away": r["match"].split(" vs ")[1],
                           "draw": "引き分け"}[actual]
        elif market == "O/U 2.5":
            won = (pick.startswith("オーバー") and h + a >= 3) or (pick.startswith("アンダー") and h + a <= 2)
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

        # 3. AI分析（未分析の試合のみ = API節約 & 予想の固定）
        need_h2h = (match, "90分勝敗") not in predicted_keys
        need_tot = (match, "O/U 2.5") not in predicted_keys and best["totals"]
        analysis = None
        if need_h2h or need_tot:
            try:
                analysis = ai.analyze_match(ai_key, home, away, ev["commence_time"])
            except Exception as e:
                print(f"[warn] AI failed for {match}: {e}", file=sys.stderr)

        new_rows = []
        if analysis and need_h2h:
            h2h = analysis["h2h"]
            cands = [
                (home, h2h["home"] / 100, best["h2h"].get(home)),
                ("引き分け", h2h["draw"] / 100, best["h2h"].get("Draw")),
                (away, h2h["away"] / 100, best["h2h"].get(away)),
            ]
            cands = [(p, pr, o) for p, pr, o in cands if o]
            if cands:
                # ★ 最も当たりやすい選択肢を採用
                pick, prob, odd = max(cands, key=lambda c: c[1])
                new_rows.append(dict(market="90分勝敗", pick=pick, prob=round(prob * 100),
                                     odds=odd, ev=prob * odd - 1, reason=h2h["reason"]))
        if analysis and need_tot:
            tot = analysis["totals"]
            cands = [
                ("オーバー2.5", tot["over"] / 100, best["totals"].get("Over 2.5")),
                ("アンダー2.5", tot["under"] / 100, best["totals"].get("Under 2.5")),
            ]
            cands = [(p, pr, o) for p, pr, o in cands if o]
            if cands:
                # ★ 最も当たりやすい選択肢を採用
                pick, prob, odd = max(cands, key=lambda c: c[1])
                new_rows.append(dict(market="O/U 2.5", pick=pick, prob=round(prob * 100),
                                     odds=odd, ev=prob * odd - 1, reason=tot["reason"]))

        for nr in new_rows:
            rows.append({
                "id": f"{ev['id']}|{nr['market']}",
                "created_utc": now.strftime("%Y-%m-%dT%H:%M"),
                "kickoff_utc": ev["commence_time"],
                "match": match, "market": nr["market"], "pick": nr["pick"],
                "prob": nr["prob"], "odds": f"{nr['odds']:.2f}",
                "ev": f"{nr['ev']:.3f}", "reason": nr["reason"],
                "result": "", "profit": "",
            })

        # 表示用（既存予想も再掲、オッズは最新のベスト値で再評価）
        for r in rows:
            if r["match"] == match and not r["result"]:
                cur = best["h2h"].get(r["pick"]) or best["totals"].get(
                    r["pick"].replace("オーバー2.5", "Over 2.5").replace("アンダー2.5", "Under 2.5"))
                odd = cur or float(r["odds"])
                prob = int(r["prob"])
                evv = prob / 100 * odd - 1
                display.append(dict(kickoff=r["kickoff_utc"], match=match, market=r["market"],
                                    pick=r["pick"], prob=prob, odds=odd, ev=evv,
                                    reason=r["reason"], recommended=prob >= PROB_SUISHO))

    save_history(rows)
    seen, uniq = set(), []
    for d in display:
        if (d["match"], d["market"]) not in seen:
            seen.add((d["match"], d["market"]))
            uniq.append(d)
    # ★ 当たりやすい順に並べる
    dashboard.build(rows, sorted(uniq, key=lambda d: -d["prob"]))
    print(f"done: {len(rows)} total rows, {len(uniq)} upcoming predictions")


if __name__ == "__main__":
    main()
