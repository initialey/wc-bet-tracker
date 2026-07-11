"""毎日の実行フロー v2:
マルチスポーツ / 日英根拠 / オッズ変動 / 通知 / 実績分析(キャリブレーション・マーケット別ROI)"""
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

from . import odds_api, ai, model, stats_model, dashboard, notify
from .config import (SPORTS, OUTRIGHTS, REGIONS, DAYS_AHEAD, STAKE, PROB_SUISHO,
                     WEIGHT_MARKET, WEIGHT_AI, WEIGHT_STAT)

HISTORY = "data/history.csv"
FIELDS = ["id", "created_utc", "kickoff_utc", "league", "match", "market", "pick",
          "prob", "prob_ai", "prob_market", "prob_stat", "odds", "ev",
          "reason", "reason_en", "result", "profit"]

M_H2H = "90分勝敗"
M_DNB = "勝敗(引分返金)"
M_OU15, M_OU25, M_OU35 = "O/U 1.5", "O/U 2.5", "O/U 3.5"
M_BTTS = "両チーム得点"
M_TEAM = "チーム得点"
M_CORNER = "コーナー(参考)"
M_WIN = "勝敗"  # 汎用スポーツ用

SOCCER_TRACKED = (M_H2H, M_DNB, M_OU15, M_OU25, M_OU35, M_BTTS, M_TEAM)


def load_history() -> list:
    if not os.path.exists(HISTORY):
        return []
    with open(HISTORY, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:  # 旧形式との互換
        r.setdefault("reason_en", "")
        r.setdefault("league", "")
        for k in FIELDS:
            r.setdefault(k, "")
    return [{k: r.get(k, "") for k in FIELDS} for r in rows]


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
        sc = {s["name"]: int(float(s["score"])) for s in (ev.get("scores") or [])}
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
        elif market == M_WIN:
            if h == a:
                r["result"], r["profit"] = "push", "0.00"
                continue
            won = pick == (home if h > a else away)
        elif market == M_DNB:
            if h == a:
                r["result"], r["profit"] = "push", "0.00"
                continue
            won = pick == (home if h > a else away)
        elif market.startswith("O/U "):
            line = float(market.split(" ")[1])
            if h + a == line:
                r["result"], r["profit"] = "push", "0.00"
                continue
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
    """cands: マーケットの全選択肢 [(pick, AI確率, 統計確率orNone, オッズ)]。
    市場暗示確率(devig、全選択肢のオッズが揃う場合のみ)・AI確率・統計確率を
    重み付きブレンドし、最終確率が最大の選択肢を
    (pick, prob, odds, prob_ai, prob_market, prob_stat) で返す。
    使えないソースは重みごと除外して残りを再正規化(blendの仕様)"""
    mkt = model.devig({p: o for p, _, _, o in cands}) if cands else {}
    scored = []
    for p, pr, st, o in cands:
        if not o:
            continue
        m = mkt.get(p)
        final = model.blend([m, pr, st], [WEIGHT_MARKET, WEIGHT_AI, WEIGHT_STAT])
        scored.append((p, final, o, pr, m, st))
    return max(scored, key=lambda c: c[1]) if scored else None


def _mk_row(ev, league, market, pick, prob, odd, prob_ai, prob_market, prob_stat,
            reason, reason_en, now):
    suffix = pick if market == M_TEAM else market
    return {
        "id": f"{ev['id']}|{suffix}", "created_utc": now.strftime("%Y-%m-%dT%H:%M"),
        "kickoff_utc": ev["commence_time"], "league": league,
        "match": f"{ev['home_team']} vs {ev['away_team']}",
        "market": market, "pick": pick, "prob": round(prob * 100),
        "prob_ai": round(prob_ai * 100),
        "prob_market": round(prob_market * 100) if prob_market is not None else "",
        "prob_stat": round(prob_stat * 100) if prob_stat is not None else "",
        "odds": f"{odd:.2f}", "ev": f"{prob * odd - 1:.3f}",
        "reason": reason, "reason_en": reason_en, "result": "", "profit": "",
    }


def analytics(history: list) -> dict:
    """キャリブレーションとマーケット別ROI"""
    settled = [r for r in history if r["result"] in ("win", "lose")]
    bins = [(50, 60), (60, 70), (70, 80), (80, 101)]
    calib = []
    for lo, hi in bins:
        grp = [r for r in settled if lo <= int(r["prob"]) < hi]
        if grp:
            actual = sum(1 for r in grp if r["result"] == "win") / len(grp) * 100
            pred = sum(int(r["prob"]) for r in grp) / len(grp)
            calib.append({"bin": f"{lo}-{hi-1 if hi < 101 else 100}%", "n": len(grp),
                          "pred": pred, "actual": actual})
    mroi = []
    for mk in sorted({r["market"] for r in settled}):
        grp = [r for r in settled if r["market"] == mk]
        pf = sum(float(r["profit"] or 0) for r in grp)
        mroi.append({"market": mk, "n": len(grp),
                     "hit": sum(1 for r in grp if r["result"] == "win") / len(grp) * 100,
                     "roi": pf / len(grp) * 100})
    return {"calib": calib, "mroi": mroi}


def main():
    odds_key = os.environ["ODDS_API_KEY"]
    ai_key = os.environ["ANTHROPIC_API_KEY"]

    rows = load_history()
    ai_calls = 0

    # 統計モデル用レーティング(週1回再計算、data/ratings.jsonにキャッシュ)
    try:
        ratings = stats_model.load_or_build(
            [k for k, _, kind in SPORTS if kind == "soccer"])
    except Exception as e:
        print(f"[warn] stats ratings unavailable: {e}", file=sys.stderr)
        ratings = {}

    for sport_key, _, _ in SPORTS:
        try:
            settle(rows, odds_api.get_scores(odds_key, sport_key))
        except Exception as e:
            print(f"[warn] settle failed for {sport_key}: {e}", file=sys.stderr)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=DAYS_AHEAD)
    predicted_keys = {(r["match"], r["market"]) for r in rows}
    display = []

    for sport_key, sport_label, kind in SPORTS:
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

            corner_card = None

            if kind == "soccer":
                needed = [m for m in SOCCER_TRACKED if (match, m) not in predicted_keys]
                analysis, sg = None, None
                extra = {"totals": {}, "btts": {}, "dnb": {},
                         "team_totals": {}, "corners": {}}
                if needed:
                    # 統計ソース: レーティング→期待ゴール→全マーケット確率
                    # (代表戦などリーグ未対応・チーム不明ならNoneのまま=2ソースにフォールバック)
                    sxg = stats_model.predict(ratings.get(sport_key), home, away)
                    sg = model.goal_probs(*sxg) if sxg else None
                    extra = odds_api.get_extra_markets(odds_key, sport_key, ev["id"], REGIONS)
                    for k, v in best["totals"].items():
                        extra["totals"][k] = max(extra["totals"].get(k, 0), v)
                    try:
                        analysis = ai.analyze_match(ai_key, home, away, ev["commence_time"])
                        ai_calls += 1
                    except Exception as e:
                        print(f"[warn] AI failed for {match}: {e}", file=sys.stderr)

                if analysis:
                    h2h = analysis.get("h2h", {})
                    xg = analysis.get("xg", {})
                    g = model.goal_probs(float(xg.get("home", 1.3)),
                                         float(xg.get("away", 1.3))) if xg else None
                    xr, xre = xg.get("reason", ""), xg.get("reason_en", "")
                    hr, hre = h2h.get("reason", ""), h2h.get("reason_en", "")

                    if M_H2H in needed and h2h:
                        c = _pick_side([(home, h2h["home"] / 100, sg and sg["home_win"], best["h2h"].get(home)),
                                        ("引き分け", h2h["draw"] / 100, sg and sg["draw"], best["h2h"].get("Draw")),
                                        (away, h2h["away"] / 100, sg and sg["away_win"], best["h2h"].get(away))])
                        if c:
                            rows.append(_mk_row(ev, sport_label, M_H2H, *c, hr, hre, now))
                            predicted_keys.add((match, M_H2H))

                    if M_DNB in needed and h2h and extra["dnb"]:
                        ph, pa = h2h["home"], h2h["away"]
                        st_h = (sg["home_win"] / (sg["home_win"] + sg["away_win"])
                                if sg and (sg["home_win"] + sg["away_win"]) > 0 else None)
                        if (ph + pa) > 0:
                            c = _pick_side([(home, ph / (ph + pa), st_h, extra["dnb"].get(home)),
                                            (away, pa / (ph + pa),
                                             1 - st_h if st_h is not None else None,
                                             extra["dnb"].get(away))])
                            if c:
                                rows.append(_mk_row(ev, sport_label, M_DNB, *c,
                                                    f"引き分けなら返金の安全型／{hr}",
                                                    f"Draw refunded (safer) / {hre}", now))
                                predicted_keys.add((match, M_DNB))

                    if g:
                        for mname, line, ko, ku in ((M_OU15, 1.5, "over15", "under15"),
                                                    (M_OU25, 2.5, "over25", "under25"),
                                                    (M_OU35, 3.5, "over35", "under35")):
                            if mname in needed:
                                c = _pick_side([(f"オーバー{line}", g[ko], sg and sg[ko], extra["totals"].get(f"Over {line}")),
                                                (f"アンダー{line}", g[ku], sg and sg[ku], extra["totals"].get(f"Under {line}"))])
                                if c:
                                    rows.append(_mk_row(ev, sport_label, mname, *c, xr, xre, now))
                                    predicted_keys.add((match, mname))

                        if M_BTTS in needed and extra["btts"]:
                            c = _pick_side([("あり", g["btts_yes"], sg and sg["btts_yes"], extra["btts"].get("Yes")),
                                            ("なし", g["btts_no"], sg and sg["btts_no"], extra["btts"].get("No"))])
                            if c:
                                rows.append(_mk_row(ev, sport_label, M_BTTS, *c, xr, xre, now))
                                predicted_keys.add((match, M_BTTS))

                        if M_TEAM in needed and extra["team_totals"]:
                            for team, ko, ku in ((home, "home_over15", "home_under15"),
                                                 (away, "away_over15", "away_under15")):
                                c = _pick_side([(f"{team} オーバー1.5", g[ko], sg and sg[ko], extra["team_totals"].get((team, "Over"))),
                                                (f"{team} アンダー1.5", g[ku], sg and sg[ku], extra["team_totals"].get((team, "Under")))])
                                if c:
                                    rows.append(_mk_row(ev, sport_label, M_TEAM, *c, xr, xre, now))
                            predicted_keys.add((match, M_TEAM))

                    cn = analysis.get("corners", {})
                    if cn and extra["corners"]:
                        lines = sorted({float(k.split(" ")[1]) for k in extra["corners"]})
                        if lines:
                            line = min(lines, key=lambda x: abs(x - float(cn.get("total", 9.5))))
                            cp = model.corner_probs(float(cn.get("total", 9.5)), line)
                            c = _pick_side([(f"オーバー{line}", cp["over"], None, extra["corners"].get(f"Over {line}")),
                                            (f"アンダー{line}", cp["under"], None, extra["corners"].get(f"Under {line}"))])
                            if c:
                                pick, prob, odd, p_ai, p_mkt, _ = c
                                corner_card = dict(kickoff=ev["commence_time"], match=match,
                                                   market=M_CORNER, pick=pick, prob=round(prob * 100),
                                                   prob_ai=round(p_ai * 100),
                                                   prob_market=round(p_mkt * 100) if p_mkt is not None else "",
                                                   prob_stat="",
                                                   odds=odd, ev=prob * odd - 1,
                                                   reason=cn.get("reason", ""),
                                                   reason_en=cn.get("reason_en", ""),
                                                   recommended=False, league=sport_label, cur=None)

            else:  # 汎用スポーツ (2way/3way)
                three_way = kind == "3way"
                # 合計ラインはブックメーカーの主要ライン(最頻point)を使う
                tot_lines = {}
                for bm in ev.get("bookmakers", []):
                    for mk in bm.get("markets", []):
                        if mk["key"] == "totals":
                            for o in mk["outcomes"]:
                                pt = o.get("point")
                                if pt is not None:
                                    tot_lines.setdefault(pt, {})[o["name"]] = max(
                                        tot_lines.get(pt, {}).get(o["name"], 0), o["price"])
                line = max(tot_lines, key=lambda p: len(tot_lines[p])) if tot_lines else None
                m_ou = f"O/U {line}" if line is not None else None
                needed = [m for m in ([M_WIN] + ([m_ou] if m_ou else []))
                          if (match, m) not in predicted_keys]

                if needed:
                    try:
                        analysis = ai.analyze_generic(ai_key, sport_label, home, away,
                                                      ev["commence_time"], three_way, line or 0)
                        ai_calls += 1
                    except Exception as e:
                        analysis = None
                        print(f"[warn] AI failed for {match}: {e}", file=sys.stderr)

                    if analysis:
                        win = analysis.get("win", {})
                        wr, wre = win.get("reason", ""), win.get("reason_en", "")
                        if M_WIN in needed and win:
                            cands = [(home, win.get("home", 50) / 100, None, best["h2h"].get(home)),
                                     (away, win.get("away", 50) / 100, None, best["h2h"].get(away))]
                            if three_way:
                                cands.append(("引き分け", win.get("draw", 0) / 100, None,
                                              best["h2h"].get("Draw")))
                            c = _pick_side(cands)
                            if c:
                                rows.append(_mk_row(ev, sport_label, M_WIN, *c, wr, wre, now))
                                predicted_keys.add((match, M_WIN))

                        tot = analysis.get("total", {})
                        if m_ou and m_ou in needed and tot and line is not None:
                            tp = model.total_probs(float(tot.get("expected", line)), line)
                            c = _pick_side([(f"オーバー{line}", tp["over"], None, tot_lines[line].get("Over")),
                                            (f"アンダー{line}", tp["under"], None, tot_lines[line].get("Under"))])
                            if c:
                                rows.append(_mk_row(ev, sport_label, m_ou, *c,
                                                    tot.get("reason", ""), tot.get("reason_en", ""), now))
                                predicted_keys.add((match, m_ou))

            # 表示 (現在オッズとの変動: h2h/主要O/Uのみ再取得可能)
            for r in rows:
                if r["match"] == match and not r["result"]:
                    prob, odd = int(r["prob"]), float(r["odds"])
                    cur = None
                    if r["market"] in (M_H2H, M_WIN, M_DNB):
                        cur = best["h2h"].get(r["pick"]) or (best["h2h"].get("Draw") if r["pick"] == "引き分け" else None)
                    elif r["market"].startswith("O/U ") and r["market"] in (M_OU15, M_OU25, M_OU35):
                        en = r["pick"].replace("オーバー", "Over ").replace("アンダー", "Under ")
                        cur = best["totals"].get(en)
                    display.append(dict(kickoff=r["kickoff_utc"], match=match, market=r["market"],
                                        pick=r["pick"], prob=prob, odds=odd,
                                        prob_ai=r.get("prob_ai", ""),
                                        prob_market=r.get("prob_market", ""),
                                        prob_stat=r.get("prob_stat", ""),
                                        cur=cur if (cur and abs(cur - odd) >= 0.01) else None,
                                        ev=prob / 100 * odd - 1, reason=r["reason"],
                                        reason_en=r.get("reason_en", ""),
                                        recommended=prob >= PROB_SUISHO,
                                        league=r.get("league") or sport_label))
            if corner_card:
                display.append(corner_card)

    outrights = []
    for key, label in OUTRIGHTS:
        lst = odds_api.get_outrights(odds_key, key, REGIONS)
        if lst:
            total_inv = sum(1 / o for _, o in lst)
            outrights.append({"label": label,
                              "entries": [(nm, o, (1 / o) / total_inv) for nm, o in lst[:10]]})

    save_history(rows)
    seen, uniq = set(), []
    for d in display:
        k = (d["match"], d["market"], d["pick"])
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    uniq.sort(key=lambda d: -d["prob"])

    meta = {"odds_remaining": odds_api.QUOTA["remaining"],
            "odds_used": odds_api.QUOTA["used"], "ai_calls": ai_calls}
    dashboard.build(rows, uniq, outrights, meta, analytics(rows))
    notify.send([d for d in uniq if d["recommended"] and d["market"] != M_CORNER])
    print(f"done: {len(rows)} rows, {len(uniq)} predictions, {ai_calls} AI calls, "
          f"quota remaining={meta['odds_remaining']}")


if __name__ == "__main__":
    main()
