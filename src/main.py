"""毎日の実行フロー v2:
マルチスポーツ / 日英根拠 / オッズ変動 / 通知 / 実績分析(キャリブレーション・マーケット別ROI)"""
import csv
import json
import os
import sys
from datetime import datetime, timezone, timedelta

from . import odds_api, ai, model, stats_model, mlb, dashboard, notify, review
from .config import (SPORTS, OUTRIGHTS, REGIONS, DAYS_AHEAD, ANALYZE_HOURS_BEFORE,
                     STAKE, PROB_HONMEI, PROB_SUISHO, PROB_SUISHO_DISPLAY,
                     PROB_DISPLAY_MIN, PROB_DISPLAY_MIN_MLB,
                     WEIGHT_MARKET, WEIGHT_AI, WEIGHT_STAT,
                     MLB_REGIONS, MLB_MAX_GAMES_PER_DAY,
                     SOCCER_MAX_GAMES_PER_DAY, GENERIC_MAX_GAMES_PER_DAY, tier_of)

HISTORY = "data/history.csv"
LEAGUE_STATE = "data/league_state.json"   # リーグ開幕検知の状態(開幕時にTelegram等へ通知)
FIELDS = ["id", "created_utc", "kickoff_utc", "league", "match", "market", "pick",
          "prob", "prob_ai", "prob_market", "prob_stat", "odds", "ev",
          "reason", "reason_en", "result", "profit"]

M_H2H = "90分勝敗"
M_DNB = "勝敗(引分返金)"
M_OU15, M_OU25, M_OU35 = "O/U 1.5", "O/U 2.5", "O/U 3.5"
M_BTTS = "両チーム得点"
M_TEAM = "チーム得点"
M_CORNER = "コーナー(参考)"
M_SCORE = "スコア予想(参考)"  # 表示のみ・記録しない
M_AH = "ハンディ"  # サッカーのハンディキャップ。実マーケット名は「ハンディ -1.5」のようにライン付き
M_WIN = "勝敗"  # 汎用スポーツ/MLBの勝敗(引き分けなし)
M_RUNLINE = "ランライン"  # MLB: スプレッド±1.5

SOCCER_TRACKED = (M_H2H, M_DNB, M_OU15, M_OU25, M_OU35, M_BTTS, M_TEAM)

# キャリブレーションの確率帯(5%刻み、最終帯は70%以上)。集計・表示・テストで共用
CALIB_BINS = [(50, 55), (55, 60), (60, 65), (65, 70), (70, 101)]


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
    """答え合わせ。試合の対応付けはイベントID(rowのid先頭 = The Odds APIのevent id)で行う。
    試合名(match)だけで対応付けるとMLBの連戦(同一カードが連日対戦)で
    「今日の予想が昨日の試合のスコアで誤判定される」バグが起きるため"""
    done = {}
    for ev in scores:
        if not ev.get("completed") or not ev.get("id"):
            continue
        sc = {s["name"]: int(float(s["score"])) for s in (ev.get("scores") or [])}
        home, away = ev["home_team"], ev["away_team"]
        if home in sc and away in sc:
            done[ev["id"]] = (sc[home], sc[away])

    for r in rows:
        ev_id = (r["id"] or "").split("|")[0]
        if r["result"] or ev_id not in done:
            continue
        h, a = done[ev_id]
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
        elif market == M_RUNLINE or market.startswith(f"{M_AH} "):
            # pick例: "Argentina -1.5" / "Switzerland +1.5"。最終スコアの得失点差で判定。
            # ハンディは0.5刻みライン限定なので引き分け(push)は生じない
            team, spread = pick.rsplit(" ", 1)
            score = h if team == home else a
            opp = a if team == home else h
            won = (score - opp) + float(spread) > 0
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


def _ah_verdict(pick: str, prob: float):
    """ハンディ予想の結論(verdict)を予想内容から自動生成する。
    ハンディのピックはモデル計算(ブレンド)で選ばれるため、AI生成の勝敗用verdictを
    流用すると予想と結論が矛盾しうる(例: 結論「イングランド有力」でピックNorway +0.5)。
    確率帯で文言を変える: 65%+/55-65%/50-55%(ごく僅差)"""
    team, spread = pick.rsplit(" ", 1)
    if prob >= 0.65:
        return (f"{team}が{spread}のハンデ込みで上回る可能性が高い見立て",
                f"{team} {spread} is likely to cover the handicap")
    if prob >= 0.55:
        return (f"{team}が{spread}のハンデ込みで上回る見立て",
                f"{team} {spread} is favored to cover the handicap")
    return (f"両者の力差は小さく、{team}が{spread}のハンデ込みで上回る確率がごく僅差で高い見立て",
            f"Very close call - {team} {spread} is only marginally more likely to cover")


def _ou_verdict(pick: str, prob: float, low_scoring=None):
    """合計ゴール(O/U)の結論をライン別にコード生成する(ハンディ_ah_verdictと同方針)。
    AIのou見解は主要ライン想定の1文で、3ライン(1.5/2.5/3.5)に使い回すと選択した
    ライン・サイドと矛盾して見える(例: オーバー1.5に「合計2ゴール以下が有力」)ため、
    結論はカバー確率の帯(70%+/60-69%/55-59%/僅差)とAIのxG合計から得た試合展開
    (low_scoring: True=ロースコア寄り/False=点が入りやすい/None=不明)で自動生成する。
    AIの見解自体は背景説明として事実欄に回す(_reason_textのfacts側)"""
    over = pick.startswith("オーバー")
    line = float(pick.replace("オーバー", "").replace("アンダー", ""))
    if over:
        k = int(line + 0.5)   # オーバー的中に必要な合計点(1.5→2点)
        ctx_ja, ctx_en = {
            True: ("点は少なめの見込みだが、", "A low-scoring game is expected, but "),
            False: ("点が入りやすい展開想定で、", "In an open attacking game, "),
            None: ("", ""),
        }[low_scoring]
        goal_ja, goal_en = f"合計{k}点には届く", f"the total reaching {k}+ goals"
    else:
        k = int(line - 0.5)   # アンダー的中の上限(2.5→2点以下)
        ctx_ja, ctx_en = {
            True: ("ロースコア想定で、", "With a low-scoring game expected, "),
            False: ("得点自体は見込まれるが、", "Some goals are expected, but "),
            None: ("", ""),
        }[low_scoring]
        goal_ja, goal_en = f"合計{k}点以下に収まる", f"the total staying at {k} or fewer goals"
    if prob >= 0.70:
        return ctx_ja + goal_ja + "確率が高い見立て", ctx_en + goal_en + " looks likely"
    if prob >= 0.60:
        return ctx_ja + goal_ja + "見込み", ctx_en + goal_en + " is expected"
    if prob >= 0.55:
        return ctx_ja + goal_ja + "見立て", ctx_en + goal_en + " is the lean"
    return ("ごく僅差だが" + ctx_ja + goal_ja + "確率がわずかに高い見立て",
            "Very close, but " + ctx_en + goal_en + " is marginally more likely")


def _replace_verdict(rj: str, rje: str, v_ja: str, v_en: str):
    """reason(結論／事実…)の結論セグメントだけをコード生成文に差し替える。
    ハンディ・O/Uの表示時再生成で使用(history.csvの記録自体は改変しない)"""
    f_ja = rj.split("／")[1:] if rj else []
    f_en = rje.split("／")[1:] if rje else []
    return "／".join([v_ja] + f_ja), "／".join([v_en] + f_en)


def _team_total_verdict(pick: str, prob: float):
    """チーム得点の結論を予想内容からコード生成する。
    AIのteam verdictは1文で片方のチームしか言及しないことが多く、
    2行(各チーム)に使い回すと逆チームの結論が付く矛盾が起きるため"""
    team, side = pick.rsplit(" ", 1)
    if side.startswith("オーバー"):
        base_ja, base_en = f"{team}が2点以上取る", f"{team} scoring 2+ goals"
    else:
        base_ja, base_en = f"{team}が1点以下に終わる", f"{team} being held to 0-1 goals"
    if prob >= 0.65:
        return base_ja + "可能性が高い見立て", base_en + " looks likely"
    if prob >= 0.55:
        return base_ja + "見立て", base_en + " is the lean"
    return ("ごく僅差だが" + base_ja + "確率がわずかに高い見立て",
            "Very close, but " + base_en + " is marginally more likely")


def _blend_reason(c, cands, ja, en, facts):
    """結論の使い回しによる矛盾ガード: ブレンドの結果、AI単独の最有力とは
    別のサイドが選ばれた場合、AI製verdictは逆サイドを推している可能性が高いので、
    コード生成の中立verdictに差し替える(factsはそのまま残す)"""
    valid_ai = [pr for _, pr, _, o in cands if o]
    if valid_ai and c[3] + 1e-9 < max(valid_ai):
        return _reason_text(
            f"AI単独では逆サイド寄りだが、市場オッズと統計を含めた総合評価では{c[0]}が優勢の見立て",
            f"AI alone leans the other way, but the blended view with market odds favors {c[0]}",
            facts)
    return ja, en


# 理由文整合性チェックの実行統計(費用レポート用に実行末尾で出力)
VERIFY_STATS = {"checks": 0, "regens": 0, "dropped": 0}


def _verify_reason(ai_key, match, market, pick, rj, rje, facts):
    """生成後の整合性チェック: reasonの結論部分(先頭セグメント)が選択した
    ピックと方向矛盾していないかを軽量モデル(ai.MODEL_LIGHT)で検証する。
    ピックは分析「後」にブレンドで決まるため、初回分析プロンプトに選択を渡すことは
    構造上できない。その代わり: 矛盾を検出したら「選択を明示した条件付き再生成」を
    最大2回試し、それでも矛盾する場合は結論なし(事実のみ)で掲載する。
    検証呼び出し自体の失敗時は元の文を維持する(APIノイズで理由文が全部消えるのを防ぐ)"""
    v_ja = (rj or "").split("／")[0].strip()
    if not v_ja or v_ja.startswith("AI単独では逆サイド寄り"):
        return rj, rje   # 結論なし、または_blend_reasonのコード生成文(矛盾しない)
    try:
        VERIFY_STATS["checks"] += 1
        if ai.check_verdict(ai_key, match, market, pick, v_ja):
            return rj, rje
        for _ in range(2):
            VERIFY_STATS["regens"] += 1
            re_v = ai.rewrite_verdict(ai_key, match, market, pick, facts)
            ja2 = (re_v.get("ja") or "").replace("／", "・").strip(" 。")
            en2 = (re_v.get("en") or "").strip()
            VERIFY_STATS["checks"] += 1
            if ja2 and ai.check_verdict(ai_key, match, market, pick, ja2):
                print(f"[info] verdict rewritten for pick consistency: "
                      f"{match} / {market} / {pick}")
                return _reason_text(ja2, en2, facts)
        VERIFY_STATS["dropped"] += 1
        print(f"[warn] verdict still inconsistent after 2 rewrites; "
              f"publishing without a verdict: {match} / {market} / {pick}")
        return _reason_text("", "", facts)   # 結論なし(事実のみ)で掲載
    except Exception as e:
        print(f"[warn] verdict consistency check failed ({e}); keeping original",
              file=sys.stderr)
        return rj, rje


def _ah_hint(pick: str):
    """ハンディキャップに馴染みのない人向けの1行説明。
    pick例: "Argentina -1.5" -> 「Argentinaが2点差以上で勝てば的中」"""
    team, spread = pick.rsplit(" ", 1)
    line = float(spread)
    if line < 0:
        k = int(-line + 0.5)
        if k == 1:
            return (f"{team}が勝てば的中(引き分けは外れ)",
                    f"Wins if {team} win (draw loses)")
        return (f"{team}が{k}点差以上で勝てば的中",
                f"Wins if {team} win by {k}+ goals")
    k = int(line + 0.5)
    if k == 1:
        return (f"{team}が引き分けか勝ちなら的中",
                f"Wins if {team} draw or win")
    return (f"{team}が{k - 1}点差以内の負け・引き分け・勝ちなら的中",
            f"Wins if {team} lose by {k - 1} or fewer, draw, or win")


def _reason_text(v_ja, v_en, facts):
    """根拠を「結論／事実1／事実2…」形式で組み立てる(区切りは全角／で旧形式と互換)。
    文中の「／」は区切りと衝突するため置換する"""
    pairs = [((v_ja or "").replace("／", "・").strip(" 。"),
              (v_en or v_ja or "").replace("／", " - ").strip())]
    for f in facts or []:
        fj = (f.get("ja") or "").replace("／", "・").strip(" 。")
        if fj:
            pairs.append((fj, (f.get("en") or fj).replace("／", " - ").strip()))
    pairs = [(j, e) for j, e in pairs if j]
    return "／".join(j for j, _ in pairs), "／".join(e for _, e in pairs)


def _next_kickoff(events, now):
    """取得済みイベント一覧から次の試合開始時刻(now以降で最小)を返す。無ければNone。
    追加のAPIリクエストは行わない(既存のオッズ取得結果を再利用する)"""
    nxt = None
    for ev in events or []:
        try:
            ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError, TypeError):
            continue
        if ko > now and (nxt is None or ko < nxt):
            nxt = ko
    return nxt


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


def _load_league_state() -> dict:
    if not os.path.exists(LEAGUE_STATE):
        return {}
    try:
        with open(LEAGUE_STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_league_state(state: dict):
    os.makedirs(os.path.dirname(LEAGUE_STATE) or ".", exist_ok=True)
    with open(LEAGUE_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def _update_league_state(state: dict, sport_key: str, label: str, upcoming: list, now):
    """リーグ開幕(オフシーズン→対象試合の出現)を検知してTelegram等へ1回だけ通知する。
    upcoming: [(kickoff_dt, event)] 対象期間内の試合。
    14日未満の空白(国際ブレーク等)での再出現は通知しない"""
    st = state.setdefault(sport_key, {})
    if not upcoming:
        st["active"] = False
        return
    if not st.get("active"):
        gap_ok = True
        try:
            gap_ok = (now - datetime.fromisoformat(st["last_seen"])).days >= 14
        except (KeyError, ValueError):
            pass
        if gap_ok:
            ko, ev = min(upcoming, key=lambda x: x[0])
            t = (ko + timedelta(hours=8)).strftime("%m/%d %H:%M")  # フィリピン時間
            notify.post(f"🔔 {label} が開幕しました。今日から予想対象に追加します。\n"
                        f"直近の試合: {ev['home_team']} vs {ev['away_team']}"
                        f"（{t} フィリピン時間）")
    st["active"] = True
    st["last_seen"] = now.isoformat()


def _agg(settled_grp: list, push_grp: list, pending_grp: list = None) -> dict:
    """成績集計の共通関数(集計仕様の一元化):
    - 的中率: pushは分母・分子に含めない(win/loseのみ)
    - 累積損益: pushも0として含める / ROI: 損益 / 検証数(win+lose)
    - n=検証数(win+lose) / win / lose / push=返金 / pending=待ち / total=全件(履歴と突合可能)"""
    pending_grp = pending_grp or []
    n = len(settled_grp)
    win = sum(1 for r in settled_grp if r["result"] == "win")
    profit = sum(float(r["profit"] or 0) for r in settled_grp + push_grp)
    return {"n": n, "win": win, "lose": n - win,
            "push": len(push_grp), "pending": len(pending_grp),
            "total": n + len(push_grp) + len(pending_grp), "profit": profit,
            "hit": win / n * 100 if n else None,
            "roi": profit / n * 100 if n else None}


def _tier_of(r) -> str:
    return tier_of(r["prob"])


def analytics(history: list) -> dict:
    """全集計を一元化: 区分別 / キャリブレーション / スポーツ別×マーケット別 / 全体。
    dashboard側では独自集計せず、この結果を表示するだけにする(数字の不整合防止)"""
    settled = [r for r in history if r["result"] in ("win", "lose")]
    pushes = [r for r in history if r["result"] == "push"]
    pendings = [r for r in history if r["result"] not in ("win", "lose", "push")]

    # 区分別(本命/有力/参考+合計)。待ち(pending)・合計(total)も含めて履歴と突合可能にする
    tier_defs = [
        ("hon", f"🟢 本命({PROB_HONMEI}%+)", f"🟢 Strong ({PROB_HONMEI}%+)"),
        ("sui", f"🟡 有力({PROB_SUISHO}〜{PROB_HONMEI - 1}%)",
         f"🟡 Likely ({PROB_SUISHO}-{PROB_HONMEI - 1}%)"),
        ("ref", f"⚪ 参考(〜{PROB_SUISHO - 1}%)", f"⚪ Longshot (<{PROB_SUISHO}%)"),
    ]
    tiers = []
    for key, ja, en in tier_defs:
        tiers.append({"key": key, "ja": ja, "en": en,
                      **_agg([r for r in settled if _tier_of(r) == key],
                             [r for r in pushes if _tier_of(r) == key],
                             [r for r in pendings if _tier_of(r) == key])})
    tiers.append({"key": "total", "ja": "合計", "en": "Total",
                  **_agg(settled, pushes, pendings)})

    # スポーツ(リーグ種別)の判定はキャリブレーション・マーケット別ROIで共用
    label_kind = {label: kind for _, label, kind in SPORTS}
    sport_disp = {"soccer": ("⚽ サッカー", "⚽ Soccer"), "mlb": ("⚾ MLB", "⚾ MLB")}

    def _sport_of(r):
        kind = label_kind.get(r["league"] or "", "soccer")  # 旧行(league空)はサッカー
        return kind if kind in sport_disp else (r["league"] or "その他")

    def _sport_sorted(rows_):
        return sorted({_sport_of(r) for r in rows_},
                      key=lambda s: {"soccer": 0, "mlb": 1}.get(s, 2))

    # キャリブレーション(確率帯別・5%刻み)。diff=実績-予測平均(pt)。
    # 全体(calib)に加えスポーツ別(calib_sport)も同じ関数で集計する(一元化)
    def _calib_of(grp_settled):
        out = []
        for lo, hi in CALIB_BINS:
            grp = [r for r in grp_settled if lo <= int(float(r["prob"])) < hi]
            if grp:
                a = _agg(grp, [])
                pred = sum(int(float(r["prob"])) for r in grp) / len(grp)
                out.append({"bin": f"{lo}-{hi - 1}%" if hi < 101 else f"{lo}%+",
                            "pred": pred,
                            "diff": a["hit"] - pred if a["hit"] is not None else None,
                            **a})
        return out

    calib = _calib_of(settled)
    calib_sport = []
    for sport in _sport_sorted(settled):
        bins = _calib_of([r for r in settled if _sport_of(r) == sport])
        if bins:
            ja, en = sport_disp.get(sport, (sport, sport))
            calib_sport.append({"sport": sport, "ja": ja, "en": en, "bins": bins})

    mroi = []
    for sport in _sport_sorted(settled + pushes):
        s_set = [r for r in settled if _sport_of(r) == sport]
        s_push = [r for r in pushes if _sport_of(r) == sport]
        ja, en = sport_disp.get(sport, (sport, sport))
        markets = []
        mkts = sorted({r["market"] for r in s_set + s_push})

        # O/U(合計得点/ゴール)はライン別だと1行あたりの件数が少なく判断不能なため、
        # 「全ライン計」の集約行を先頭に置き、ライン別は折りたたみ内の詳細(lines)に格下げ
        ou_mkts = [m for m in mkts if m.startswith("O/U ")]
        if len(ou_mkts) >= 2:
            ou_set = [r for r in s_set if r["market"].startswith("O/U ")]
            ou_push = [r for r in s_push if r["market"].startswith("O/U ")]
            lines = [{"market": mk,
                      **_agg([r for r in ou_set if r["market"] == mk],
                             [r for r in ou_push if r["market"] == mk])}
                     for mk in ou_mkts]
            markets.append({"market": "O/U", "agg_ou": True, "lines": lines,
                            **_agg(ou_set, ou_push)})
            mkts = [m for m in mkts if not m.startswith("O/U ")]

        for mk in mkts:
            entry = {"market": mk,
                     **_agg([r for r in s_set if r["market"] == mk],
                            [r for r in s_push if r["market"] == mk])}
            # ランラインは予想確率帯別(50-59%/60%+)の内訳を付ける
            if mk == M_RUNLINE:
                bands = []
                for lo, hi, label in ((0, 60, "50-59%"), (60, 101, "60%+")):
                    g = [r for r in s_set if r["market"] == mk
                         and lo <= int(float(r["prob"])) < hi]
                    gp = [r for r in s_push if r["market"] == mk
                          and lo <= int(float(r["prob"])) < hi]
                    if g or gp:
                        bands.append({"band": label, **_agg(g, gp)})
                if bands:
                    entry["bands"] = bands
            markets.append(entry)
        mroi.append({"sport": sport, "ja": ja, "en": en, "markets": markets})

    return {"tiers": tiers, "calib": calib, "calib_sport": calib_sport, "mroi": mroi,
            "overall": _agg(settled, pushes, pendings)}


def main():
    odds_key = os.environ["ODDS_API_KEY"]
    ai_key = os.environ["ANTHROPIC_API_KEY"]

    rows = load_history()
    ai_calls = 0
    mlb_ai_calls = 0          # MLB専用エンジンのAI呼び出し回数(費用レポート用)
    mlb_odds_requests = 0     # MLBに紐づくThe Odds APIリクエスト数(slate + scores)

    # 統計モデル用レーティング(週1回再計算、data/ratings.jsonにキャッシュ)
    try:
        ratings = stats_model.load_or_build(
            [k for k, _, kind in SPORTS if kind == "soccer"])
    except Exception as e:
        print(f"[warn] stats ratings unavailable: {e}", file=sys.stderr)
        ratings = {}

    # デイリーレビュー用: 今回の答え合わせで確定した行(=昨日分)を特定する
    unsettled_before = {r["id"] for r in rows if not r["result"]}
    for sport_key, _, kind in SPORTS:
        try:
            settle(rows, odds_api.get_scores(odds_key, sport_key))
            if kind == "mlb":
                mlb_odds_requests += 1
        except Exception as e:
            print(f"[warn] settle failed for {sport_key}: {e}", file=sys.stderr)
    newly_settled = [r for r in rows if r["id"] in unsettled_before and r["result"]]

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=DAYS_AHEAD)                       # オッズ取得の対象期間
    analyze_horizon = now + timedelta(hours=ANALYZE_HOURS_BEFORE)    # AI分析・予想記録の対象期間
    # 予想済みキーは(試合名, 試合日, マーケット)。試合名だけだとMLBの連戦(同一カードが
    # 連日対戦)で前日の予想が今日の試合の予想をブロックしてしまう
    predicted_keys = {(r["match"], (r["kickoff_utc"] or "")[:10], r["market"]) for r in rows}
    display = []
    match_notes = {}   # 試合ごとの補足表示(MLBの先発投手など)
    league_state = _load_league_state()
    league_status = []  # リーグ別の次回試合日(全リーグ0件の日の案内パネル用)

    for sport_key, sport_label, kind in SPORTS:
        try:
            if kind == "mlb":
                events = odds_api.get_upcoming(odds_key, sport_key, MLB_REGIONS, "h2h,spreads,totals")
                mlb_odds_requests += 1
            else:
                events = odds_api.get_upcoming(odds_key, sport_key, REGIONS)
        except Exception as e:
            print(f"[warn] upcoming failed for {sport_key}: {e}", file=sys.stderr)
            league_status.append({"label": sport_label, "kind": kind, "next": None})
            continue
        nxt_ko = _next_kickoff(events, now)
        league_status.append({"label": sport_label, "kind": kind,
                              "next": nxt_ko.isoformat() if nxt_ko else None})
        # オフシーズン/ブレイク検知用: The Odds APIが返した今後の試合数を毎回ログに残す
        print(f"[info] {sport_key}: {len(events)} upcoming events"
              + (f", next={nxt_ko:%Y-%m-%d %H:%M}Z" if nxt_ko else " (none)"), file=sys.stderr)

        # リーグ開幕検知(オフシーズン→試合出現でTelegram等に1回通知)
        upcoming_in_horizon = []
        for ev in events:
            try:
                ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if now <= ko <= horizon:
                upcoming_in_horizon.append((ko, ev))
        _update_league_state(league_state, sport_key, sport_label, upcoming_in_horizon, now)

        # 費用ガード: 1リーグ・1日あたりのAI分析上限(人気=ブックメーカー数の順で上位のみ)
        max_per_day = {"mlb": MLB_MAX_GAMES_PER_DAY,
                       "soccer": SOCCER_MAX_GAMES_PER_DAY}.get(kind, GENERIC_MAX_GAMES_PER_DAY)
        cand = [e for e in events
                if now <= datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) <= analyze_horizon]
        cand.sort(key=lambda e: (-len(e.get("bookmakers", [])), e["commence_time"]))
        eligible = {e["id"] for e in cand[:max_per_day]}
        if len(cand) > max_per_day:
            print(f"[info] cost guard {sport_key}: {len(cand)} games within "
                  f"{ANALYZE_HOURS_BEFORE}h -> analyze top {max_per_day} by liquidity",
                  file=sys.stderr)

        # MLB: 当日〜48hのslateを1回取得
        mlb_slate = []
        if kind == "mlb":
            mlb_slate = mlb.load_slate(days=3)
        mlb_eligible = eligible if kind == "mlb" else set()

        for ev in events:
            kickoff = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if not (now <= kickoff <= horizon):
                continue
            home, away = ev["home_team"], ev["away_team"]
            match = f"{home} vs {away}"
            kdate = ev["commence_time"][:10]   # 連戦区別用の試合日
            best = odds_api.best_odds(ev)
            if not best["h2h"]:
                continue

            corner_card = None
            score_card = None
            # AI分析・予想記録はキックオフ48時間以内の試合のみ（それ以外は既存予想の表示のみ）
            within_analysis = kickoff <= analyze_horizon

            if kind == "soccer":
                analyzable = within_analysis and ev["id"] in eligible  # 費用ガード込み
                needed = ([m for m in SOCCER_TRACKED if (match, kdate, m) not in predicted_keys]
                          if analyzable else [])
                # ハンディはラインが実行時に決まる(例「ハンディ -1.5」)ため、prefix一致で未予想判定
                if analyzable and not any(
                        mt == match and kd == kdate and mk.startswith(f"{M_AH} ")
                        for mt, kd, mk in predicted_keys):
                    needed = needed + [M_AH]
                analysis, sg, sxg = None, None, None
                extra = {"totals": {}, "btts": {}, "dnb": {},
                         "team_totals": {}, "corners": {}, "spreads": {}, "spread_n": {}}
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
                    facts = analysis.get("facts", []) or []
                    mv = analysis.get("market_verdicts", {}) or {}
                    # マーケットごとに対応するverdict+共通factsで根拠を組み立てる
                    # (旧形式のreasonキーにもフォールバックして頑健に)
                    hr, hre = _reason_text(
                        h2h.get("verdict_ja") or h2h.get("reason", ""),
                        h2h.get("verdict_en") or h2h.get("reason_en", ""), facts)
                    # 合計ゴールのAI見解(market_verdicts.ou)は結論には使わず、
                    # 「試合展開の背景説明」として事実欄(折りたたみ内)へ回す。
                    # 結論はライン別にコード生成(_ou_verdict)する
                    ou_v = mv.get("ou", {}) or {}
                    ou_bg = ou_v.get("ja") or xg.get("reason", "")
                    ou_facts = ([{"ja": f"試合展開の見解: {ou_bg}",
                                  "en": "Game-flow view: "
                                        + (ou_v.get("en") or xg.get("reason_en", "") or ou_bg)}]
                                if ou_bg else []) + facts
                    xg_total = (float(xg.get("home", 0)) + float(xg.get("away", 0))) if xg else None
                    low_scoring = (xg_total < 2.5) if xg_total else None
                    btts_v = mv.get("btts", {}) or {}
                    btts_r, btts_re = _reason_text(
                        btts_v.get("ja") or xg.get("reason", ""),
                        btts_v.get("en") or xg.get("reason_en", ""), facts)

                    if M_H2H in needed and h2h:
                        cands = [(home, h2h["home"] / 100, sg and sg["home_win"], best["h2h"].get(home)),
                                 ("引き分け", h2h["draw"] / 100, sg and sg["draw"], best["h2h"].get("Draw")),
                                 (away, h2h["away"] / 100, sg and sg["away_win"], best["h2h"].get(away))]
                        c = _pick_side(cands)
                        if c:
                            rj, rje = _blend_reason(c, cands, hr, hre, facts)
                            rj, rje = _verify_reason(ai_key, match, M_H2H, c[0], rj, rje, facts)
                            rows.append(_mk_row(ev, sport_label, M_H2H, *c, rj, rje, now))
                            predicted_keys.add((match, kdate, M_H2H))

                    if M_DNB in needed and h2h and extra["dnb"]:
                        ph, pa = h2h["home"], h2h["away"]
                        st_h = (sg["home_win"] / (sg["home_win"] + sg["away_win"])
                                if sg and (sg["home_win"] + sg["away_win"]) > 0 else None)
                        if (ph + pa) > 0:
                            cands = [(home, ph / (ph + pa), st_h, extra["dnb"].get(home)),
                                     (away, pa / (ph + pa),
                                      1 - st_h if st_h is not None else None,
                                      extra["dnb"].get(away))]
                            c = _pick_side(cands)
                            if c:
                                rj, rje = _blend_reason(c, cands, hr, hre, facts)
                                rj, rje = _verify_reason(ai_key, match, M_DNB, c[0], rj, rje, facts)
                                rows.append(_mk_row(ev, sport_label, M_DNB, *c, rj, rje, now))
                                predicted_keys.add((match, kdate, M_DNB))

                    if g:
                        for mname, line, ko, ku in ((M_OU15, 1.5, "over15", "under15"),
                                                    (M_OU25, 2.5, "over25", "under25"),
                                                    (M_OU35, 3.5, "over35", "under35")):
                            if mname in needed:
                                cands = [(f"オーバー{line}", g[ko], sg and sg[ko], extra["totals"].get(f"Over {line}")),
                                         (f"アンダー{line}", g[ku], sg and sg[ku], extra["totals"].get(f"Under {line}"))]
                                c = _pick_side(cands)
                                if c:
                                    # 結論はライン別コード生成(AI文の使い回しをやめ、
                                    # 定義上ピックと矛盾しない。AI見解はou_factsで背景表示)
                                    v_ja, v_en = _ou_verdict(c[0], c[1], low_scoring)
                                    rj, rje = _reason_text(v_ja, v_en, ou_facts)
                                    rows.append(_mk_row(ev, sport_label, mname, *c, rj, rje, now))
                                    predicted_keys.add((match, kdate, mname))

                        if M_BTTS in needed and extra["btts"]:
                            cands = [("あり", g["btts_yes"], sg and sg["btts_yes"], extra["btts"].get("Yes")),
                                     ("なし", g["btts_no"], sg and sg["btts_no"], extra["btts"].get("No"))]
                            c = _pick_side(cands)
                            if c:
                                rj, rje = _blend_reason(c, cands, btts_r, btts_re, facts)
                                rj, rje = _verify_reason(ai_key, match, M_BTTS, c[0], rj, rje, facts)
                                rows.append(_mk_row(ev, sport_label, M_BTTS, *c, rj, rje, now))
                                predicted_keys.add((match, kdate, M_BTTS))

                        if M_TEAM in needed and extra["team_totals"]:
                            for team, ko, ku in ((home, "home_over15", "home_under15"),
                                                 (away, "away_over15", "away_under15")):
                                cands = [(f"{team} オーバー1.5", g[ko], sg and sg[ko], extra["team_totals"].get((team, "Over"))),
                                         (f"{team} アンダー1.5", g[ku], sg and sg[ku], extra["team_totals"].get((team, "Under")))]
                                c = _pick_side(cands)
                                if c:
                                    # チーム得点の結論はコード生成(1つのAI verdictを2行に使い回さない)
                                    v_ja, v_en = _team_total_verdict(c[0], c[1])
                                    rj, rje = _reason_text(v_ja, v_en, facts)
                                    rows.append(_mk_row(ev, sport_label, M_TEAM, *c, rj, rje, now))
                            predicted_keys.add((match, kdate, M_TEAM))

                    if M_AH in needed and xg and extra.get("spreads"):
                        # ホーム視点のライン一覧(両サイドのオッズが揃うものだけ)。
                        # 主要ライン=提供ブックメーカー数が最多のもの(同数なら0に近い方)
                        ah_lines = {}
                        for (nm, pt), price in extra["spreads"].items():
                            if nm != home:
                                continue
                            a_price = extra["spreads"].get((away, -pt))
                            if a_price:
                                ah_lines[pt] = (price, a_price,
                                                extra.get("spread_n", {}).get((nm, pt), 0))
                        if ah_lines:
                            line = max(ah_lines, key=lambda p: (ah_lines[p][2], -abs(p)))
                            h_price, a_price, _n = ah_lines[line]
                            hp = model.handicap_probs(float(xg.get("home", 1.3)),
                                                      float(xg.get("away", 1.3)), line)
                            shp = model.handicap_probs(*sxg, line) if sxg else None
                            m_ah = f"{M_AH} {line:+g}"
                            c = _pick_side([(f"{home} {line:+g}", hp["cover"],
                                             shp and shp["cover"], h_price),
                                            (f"{away} {-line:+g}", hp["no_cover"],
                                             shp and shp["no_cover"], a_price)])
                            if c:
                                # ハンディの結論は予想内容からコード生成(AI verdictは流用しない)
                                v_ja, v_en = _ah_verdict(c[0], c[1])
                                rj, rje = _reason_text(v_ja, v_en, facts)
                                rows.append(_mk_row(ev, sport_label, m_ah, *c, rj, rje, now))
                                predicted_keys.add((match, kdate, m_ah))

                    # スコア予想(参考): 期待ゴールから最有力スコア上位3つ。
                    # オッズ・期待値・推奨なし、history.csvには記録しない(コーナーと同様)
                    if xg:
                        tops = model.top_scores(float(xg.get("home", 1.3)),
                                                float(xg.get("away", 1.3)), 3)
                        picks = [(f"{hh_}-{aa_}", round(p_ * 100)) for (hh_, aa_), p_ in tops]
                        score_card = dict(kickoff=ev["commence_time"], match=match, rule="90",
                                          market=M_SCORE, score_card=True,
                                          pick=" / ".join(f"{s} ({pr}%)" for s, pr in picks),
                                          picks=picks, prob=0, recommended=False,
                                          league=sport_label)

                    cn = analysis.get("corners", {})
                    cn_r, cn_re = _reason_text(
                        cn.get("verdict_ja") or cn.get("reason", ""),
                        cn.get("verdict_en") or cn.get("reason_en", ""),
                        facts) if cn else ("", "")
                    if cn and extra["corners"]:
                        lines = sorted({float(k.split(" ")[1]) for k in extra["corners"]})
                        if lines:
                            line = min(lines, key=lambda x: abs(x - float(cn.get("total", 9.5))))
                            cp = model.corner_probs(float(cn.get("total", 9.5)), line)
                            cands = [(f"オーバー{line}", cp["over"], None, extra["corners"].get(f"Over {line}")),
                                     (f"アンダー{line}", cp["under"], None, extra["corners"].get(f"Under {line}"))]
                            c = _pick_side(cands)
                            if c:
                                cn_r, cn_re = _blend_reason(c, cands, cn_r, cn_re, facts)
                                cn_r, cn_re = _verify_reason(ai_key, match, M_CORNER,
                                                             c[0], cn_r, cn_re, facts)
                                pick, prob, odd, p_ai, p_mkt, _ = c
                                corner_card = dict(kickoff=ev["commence_time"], match=match, rule="90",
                                                   market=M_CORNER, pick=pick, prob=round(prob * 100),
                                                   prob_ai=round(p_ai * 100),
                                                   prob_market=round(p_mkt * 100) if p_mkt is not None else "",
                                                   prob_stat="",
                                                   odds=odd, ev=prob * odd - 1,
                                                   reason=cn_r, reason_en=cn_re,
                                                   recommended=False, league=sport_label, cur=None)

            elif kind == "mlb":
                # 先発投手表示ノートは(slateにマッチすれば)予想の有無に関わらず出す
                match_notes[match] = mlb.note(mlb_slate, home, away, ev["commence_time"])

                if within_analysis and ev["id"] in mlb_eligible:
                    hh = best["h2h"]
                    # 合計得点: 主要ライン(最頻point)とO/Uベストオッズ
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
                    # ランライン(spreads ±1.5): 本命は-1.5側
                    sp = {}
                    for bm in ev.get("bookmakers", []):
                        for mk in bm.get("markets", []):
                            if mk["key"] == "spreads":
                                for o in mk["outcomes"]:
                                    pt = o.get("point")
                                    if pt is not None:
                                        sp.setdefault(o["name"], {})[pt] = max(
                                            sp.get(o["name"], {}).get(pt, 0), o["price"])
                    fav = dog = fav_price = dog_price = None
                    for nm, pts in sp.items():
                        if -1.5 in pts:
                            fav, fav_price = nm, pts[-1.5]
                        if 1.5 in pts:
                            dog, dog_price = nm, pts[1.5]
                    fav_team = fav or min((home, away), key=lambda t: hh.get(t) or 999)

                    need_win = (match, kdate, M_WIN) not in predicted_keys and hh.get(home) and hh.get(away)
                    need_tot = m_ou and (match, kdate, m_ou) not in predicted_keys
                    need_rl = ((match, kdate, M_RUNLINE) not in predicted_keys
                               and fav and dog and fav_price and dog_price)

                    if need_win or need_tot or need_rl:
                        ctx = mlb.build_context(mlb_slate, home, away, ev["commence_time"])
                        if ctx is None:
                            print(f"[warn] MLB: Stats APIにマッチせずスキップ {match}", file=sys.stderr)
                            # スキップの見える化: 静かに消えるのではなく理由をカード表示
                            display.append(dict(
                                info_card=True, kind="mlb", kickoff=ev["commence_time"],
                                match=match, league=sport_label, prob=0,
                                market="情報", pick=f"skip:{match}",
                                tag_ja="⚠️ 分析スキップ", tag_en="⚠️ Analysis skipped",
                                text_ja="分析スキップ: MLB Stats APIと試合を照合できませんでした(チーム名マッピング失敗または先発情報なし)",
                                text_en="Skipped: could not match this game to the MLB Stats API"))
                        else:
                            try:
                                analysis = ai.analyze_mlb(ai_key, ctx, line or 0, fav_team)
                                ai_calls += 1
                                mlb_ai_calls += 1
                            except Exception as e:
                                analysis = None
                                print(f"[warn] MLB AI failed for {match}: {e}", file=sys.stderr)
                                display.append(dict(
                                    info_card=True, kind="mlb", kickoff=ev["commence_time"],
                                    match=match, league=sport_label, prob=0,
                                    market="情報", pick=f"skip:{match}",
                                    tag_ja="⚠️ 分析スキップ", tag_en="⚠️ Analysis skipped",
                                    text_ja="分析スキップ: AI分析でエラーが発生しました(次回実行で再試行)",
                                    text_en="Skipped: AI analysis failed (will retry next run)"))

                            if analysis:
                                facts = analysis.get("facts", []) or []
                                win = analysis.get("win", {})
                                if need_win and win:
                                    wr, wre = _reason_text(win.get("verdict_ja", ""),
                                                           win.get("verdict_en", ""), facts)
                                    cands = [(home, win.get("home", 50) / 100, None, hh.get(home)),
                                             (away, win.get("away", 50) / 100, None, hh.get(away))]
                                    c = _pick_side(cands)
                                    if c:
                                        wr, wre = _blend_reason(c, cands, wr, wre, facts)
                                        wr, wre = _verify_reason(ai_key, match, M_WIN, c[0], wr, wre, facts)
                                        rows.append(_mk_row(ev, sport_label, M_WIN, *c, wr, wre, now))
                                        predicted_keys.add((match, kdate, M_WIN))

                                tot = analysis.get("total", {})
                                if need_tot and tot and line is not None:
                                    tr, tre = _reason_text(tot.get("verdict_ja", ""),
                                                           tot.get("verdict_en", ""), facts)
                                    tp = model.total_probs(float(tot.get("expected", line)), line)
                                    cands = [(f"オーバー{line}", tp["over"], None, tot_lines[line].get("Over")),
                                             (f"アンダー{line}", tp["under"], None, tot_lines[line].get("Under"))]
                                    c = _pick_side(cands)
                                    if c:
                                        tr, tre = _blend_reason(c, cands, tr, tre, facts)
                                        tr, tre = _verify_reason(ai_key, match, m_ou, c[0], tr, tre, facts)
                                        rows.append(_mk_row(ev, sport_label, m_ou, *c, tr, tre, now))
                                        predicted_keys.add((match, kdate, m_ou))

                                rl = analysis.get("runline", {})
                                if need_rl and rl:
                                    fc = rl.get("fav_cover", 50) / 100
                                    cands = [(f"{fav} -1.5", fc, None, fav_price),
                                             (f"{dog} +1.5", 1 - fc, None, dog_price)]
                                    c = _pick_side(cands)
                                    if c:
                                        # ランラインもハンディ同様、結論を予想内容からコード生成
                                        v_ja, v_en = _ah_verdict(c[0], c[1])
                                        rr, rre = _reason_text(v_ja, v_en, facts)
                                        rows.append(_mk_row(ev, sport_label, M_RUNLINE, *c, rr, rre, now))
                                        predicted_keys.add((match, kdate, M_RUNLINE))

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
                needed = ([m for m in ([M_WIN] + ([m_ou] if m_ou else []))
                           if (match, kdate, m) not in predicted_keys]
                          if within_analysis and ev["id"] in eligible else [])

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
                                wr2, wre2 = _verify_reason(ai_key, match, M_WIN, c[0], wr, wre, [])
                                rows.append(_mk_row(ev, sport_label, M_WIN, *c, wr2, wre2, now))
                                predicted_keys.add((match, kdate, M_WIN))

                        tot = analysis.get("total", {})
                        if m_ou and m_ou in needed and tot and line is not None:
                            tp = model.total_probs(float(tot.get("expected", line)), line)
                            c = _pick_side([(f"オーバー{line}", tp["over"], None, tot_lines[line].get("Over")),
                                            (f"アンダー{line}", tp["under"], None, tot_lines[line].get("Under"))])
                            if c:
                                tr2, tre2 = _verify_reason(ai_key, match, m_ou, c[0],
                                                           tot.get("reason", ""),
                                                           tot.get("reason_en", ""), [])
                                rows.append(_mk_row(ev, sport_label, m_ou, *c, tr2, tre2, now))
                                predicted_keys.add((match, kdate, m_ou))

            # 表示 (現在オッズとの変動: h2h/主要O/Uのみ再取得可能)
            # 判定ルール表示: サッカーは90分(延長・PK含まず)、MLB等は延長込み
            rule = "90" if kind == "soccer" else "ext"
            match_display_idx = []   # この試合の表示エントリ(MLB代表カード選定用)
            for r in rows:
                if r["match"] == match and r["kickoff_utc"][:10] == kdate and not r["result"]:
                    prob, odd = int(r["prob"]), float(r["odds"])
                    cur = None
                    if r["market"] in (M_H2H, M_WIN, M_DNB):
                        cur = best["h2h"].get(r["pick"]) or (best["h2h"].get("Draw") if r["pick"] == "引き分け" else None)
                    elif r["market"].startswith("O/U ") and r["market"] in (M_OU15, M_OU25, M_OU35):
                        en = r["pick"].replace("オーバー", "Over ").replace("アンダー", "Under ")
                        cur = best["totals"].get(en)
                    hint_ja = hint_en = ""
                    reason_d, reason_en_d = r["reason"], r.get("reason_en", "")
                    if r["market"].startswith(f"{M_AH} ") or r["market"] == M_RUNLINE:
                        hint_ja, hint_en = _ah_hint(r["pick"])
                        # 過去に記録されたAI流用の矛盾verdictも、表示時のみテンプレート生成で
                        # 上書きする(history.csvの記録自体は改変しない)
                        v_ja, v_en = _ah_verdict(r["pick"], prob / 100)
                        reason_d, reason_en_d = _replace_verdict(reason_d, reason_en_d, v_ja, v_en)
                    elif r["market"] in (M_OU15, M_OU25, M_OU35):
                        # サッカーO/Uも同方針: 過去記録分を含め、表示時はライン別の
                        # コード生成結論に差し替える(xG情報は記録時のみ利用可のためNone)
                        v_ja, v_en = _ou_verdict(r["pick"], prob / 100)
                        reason_d, reason_en_d = _replace_verdict(reason_d, reason_en_d, v_ja, v_en)
                    display.append(dict(kickoff=r["kickoff_utc"], match=match, market=r["market"],
                                        pick=r["pick"], prob=prob, odds=odd,
                                        prob_ai=r.get("prob_ai", ""),
                                        prob_market=r.get("prob_market", ""),
                                        prob_stat=r.get("prob_stat", ""),
                                        cur=cur if (cur and abs(cur - odd) >= 0.01) else None,
                                        ev=prob / 100 * odd - 1, reason=reason_d,
                                        reason_en=reason_en_d,
                                        # 通知(notify.send)の選定は表示ラベルと同基準
                                        # (55〜59%帯は表示格下げのため通知しない)
                                        recommended=prob >= PROB_SUISHO_DISPLAY,
                                        note=match_notes.get(match, ""),
                                        hint_ja=hint_ja, hint_en=hint_en, rule=rule,
                                        kind=kind,
                                        league=r.get("league") or sport_label))
                    match_display_idx.append(len(display) - 1)
            # MLB代表カード: 何も表示されない状態を避けるため、試合ごとの最有力
            # マーケット1件は確率に関わらず表示する(repフラグ)
            if kind == "mlb" and match_display_idx:
                best = max(match_display_idx, key=lambda i: display[i]["prob"])
                display[best]["rep"] = True
            if corner_card:
                display.append(corner_card)
            if score_card:
                display.append(score_card)

        # MLB: 表示できる予想が1件もない日(オールスターブレイク・オフシーズン等)は
        # タブは残したまま「現在試合がありません」カードを表示する。
        # 次の試合日は既に取得済みのイベント一覧から取る(追加APIリクエストなし)。
        # 取得できない(0件)場合は日付なしの文言のみ
        if kind == "mlb":
            has_mlb = any(d.get("kind") == "mlb" and not d.get("info_card") for d in display)
            if not has_mlb:
                nxt = _next_kickoff(events, now)
                if nxt:
                    t = nxt.astimezone(timezone(timedelta(hours=8)))  # フィリピン時間
                    wd = "月火水木金土日"[t.weekday()]
                    text_ja = (f"現在試合がありません(オールスターブレイク等)。"
                               f"次の試合: {t.month}/{t.day}({wd})")
                    text_en = (f"No games right now (All-Star break etc.). "
                               f"Next game: {t.month}/{t.day}")
                    kick = nxt.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    text_ja = "現在試合がありません"
                    text_en = "No games right now"
                    kick = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                display.append(dict(
                    info_card=True, kind="mlb", kickoff=kick,
                    match="MLB", league=sport_label, prob=0, market="情報", pick="mlb-next",
                    tag_ja="⚾ お知らせ", tag_en="⚾ Notice",
                    text_ja=text_ja, text_en=text_en))

    outrights = []
    for key, label in OUTRIGHTS:
        lst = odds_api.get_outrights(odds_key, key, REGIONS)
        if lst:
            total_inv = sum(1 / o for _, o in lst)
            outrights.append({"label": label,
                              "entries": [(nm, o, (1 / o) / total_inv) for nm, o in lst[:10]]})

    save_history(rows)  # 記録・答え合わせは全予想を対象（表示フィルタとは独立）
    _save_league_state(league_state)
    seen, uniq = set(), []
    for d in display:
        # 連戦(同一カードの連日試合)を区別するため試合日もキーに含める
        k = (d["match"], d["market"], d.get("pick"), (d.get("kickoff") or "")[:10])
        if k not in seen:
            seen.add(k)
            uniq.append(d)

    # 表示フィルタ: 閾値「以下」は非表示(history.csvには残す)。リーグ種別ごとに設定:
    # サッカー・汎用=50%、MLB=52%(接戦が本質のため。52%あれば野球では十分な傾き)。
    # 例外: スコア予想(参考)/お知らせカード/MLB代表カード(各試合の最有力1件)は常に表示
    def _min_disp(d):
        return PROB_DISPLAY_MIN_MLB if d.get("kind") == "mlb" else PROB_DISPLAY_MIN
    uniq = [d for d in uniq if d.get("score_card") or d.get("info_card")
            or d.get("rep") or d["prob"] > _min_disp(d)]
    uniq.sort(key=lambda d: -d["prob"])

    # デイリーレビュー&改善提案(ゲート付き)。答え合わせ完了後に1回だけ。
    # 提案は表示・通知のみで自動実装は絶対にしない。失敗しても本体は止めない
    review_data = None
    try:
        review_data = review.build_review(rows, newly_settled, ai_key)
        review.save(review_data)
        if review_data.get("ai_called"):
            ai_calls += 1
    except Exception as e:
        print(f"[warn] daily review failed: {e}", file=sys.stderr)

    meta = {"odds_remaining": odds_api.QUOTA["remaining"],
            "odds_used": odds_api.QUOTA["used"], "ai_calls": ai_calls}
    dashboard.build(rows, uniq, outrights, meta, analytics(rows), review=review_data,
                    league_status=league_status)
    notify.send([d for d in uniq if d.get("recommended") and d["market"] != M_CORNER])
    notify.post(review.notify_text(review_data))
    print(f"done: {len(rows)} rows, {len(uniq)} predictions, {ai_calls} AI calls, "
          f"quota remaining={meta['odds_remaining']}")
    print(f"reason consistency: {VERIFY_STATS['checks']} checks "
          f"({ai.MODEL_LIGHT}), {VERIFY_STATS['regens']} rewrites, "
          f"{VERIFY_STATS['dropped']} published without verdict")
    print(f"MLB: {mlb_ai_calls} games analyzed, {mlb_ai_calls} AI calls, "
          f"{mlb_odds_requests} Odds API requests, {mlb.CALLS['count']} MLB StatsAPI calls")


if __name__ == "__main__":
    main()
