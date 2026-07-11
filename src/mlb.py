"""MLB Stats API クライアント (https://statsapi.mlb.com, キー不要)

野球は先発投手が最大の変数。当日〜48時間以内の試合について、先発投手の発表情報・
今季成績・直近3登板、両チームの直近10試合の成績と得点力、球場情報を構造化して返す。
The Odds APIのチーム名との表記揺れは _match_side() のエイリアス/正規化で吸収し、
マッチしない試合は build_context() が None を返す(呼び出し側で警告してスキップ)。
"""
import sys
from datetime import datetime, timezone, timedelta

import requests

BASE = "https://statsapi.mlb.com/api/v1"

# このモジュールが行ったMLB Stats APIリクエスト数(費用レポート用。無料・キー不要)
CALLS = {"count": 0}


def _get(path: str, params: dict = None) -> dict:
    CALLS["count"] += 1
    r = requests.get(f"{BASE}{path}", params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def _season() -> int:
    return datetime.now(timezone.utc).year


def _norm(s: str) -> str:
    """比較用の正規化: 英数字のみ小文字。"St. Louis Cardinals" → "stlouiscardinals" """
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


# The Odds APIとMLB Stats APIで表記が食い違う既知チームの別名(正規化後キー)
TEAM_ALIASES = {
    "dbacks": "arizonadiamondbacks",
    "azdbacks": "arizonadiamondbacks",
    "arizonadbacks": "arizonadiamondbacks",
    "oaklandathletics": "athletics",
    "athleticsoakland": "athletics",
}


def _alias(n: str) -> str:
    return TEAM_ALIASES.get(n, n)


def _pitcher(pid: int) -> dict:
    """先発投手の今季成績(防御率・WHIP・イニング・先発数)と直近3登板"""
    out = {"id": pid, "name": "", "era": None, "whip": None, "ip": None,
           "gs": None, "so": None, "recent": []}
    season = _season()
    try:
        d = _get(f"/people/{pid}/stats", {"stats": "season", "group": "pitching",
                                          "season": season})
        sp = (d.get("stats") or [{}])[0].get("splits") or []
        if sp:
            st = sp[0]["stat"]
            out.update(era=st.get("era"), whip=st.get("whip"),
                       ip=st.get("inningsPitched"), gs=st.get("gamesStarted"),
                       so=st.get("strikeOuts"))
    except Exception as e:
        print(f"[warn] mlb pitcher season {pid}: {e}", file=sys.stderr)
    try:
        d = _get(f"/people/{pid}/stats", {"stats": "gameLog", "group": "pitching",
                                          "season": season})
        sp = (d.get("stats") or [{}])[0].get("splits") or []
        for g in sp[-3:]:
            st = g.get("stat", {})
            out["recent"].append({"date": g.get("date"), "ip": st.get("inningsPitched"),
                                  "er": st.get("earnedRuns"), "so": st.get("strikeOuts"),
                                  "h": st.get("hits")})
    except Exception as e:
        print(f"[warn] mlb pitcher gamelog {pid}: {e}", file=sys.stderr)
    return out


def _team_form(team_id: int, ref: datetime) -> dict:
    """refより前の直近10試合の成績(勝敗)と得点力(得点/失点の平均)"""
    start = (ref - timedelta(days=25)).strftime("%Y-%m-%d")
    end = (ref - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        d = _get("/schedule", {"sportId": 1, "teamId": team_id,
                               "startDate": start, "endDate": end, "hydrate": "linescore"})
    except Exception as e:
        print(f"[warn] mlb team form {team_id}: {e}", file=sys.stderr)
        return {}
    games = []
    for dt in d.get("dates", []):
        for g in dt.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            ls = g.get("linescore", {}).get("teams", {})
            hr, ar = ls.get("home", {}).get("runs"), ls.get("away", {}).get("runs")
            if hr is None or ar is None:
                continue
            side = "home" if g["teams"]["home"]["team"]["id"] == team_id else "away"
            rf, ra = (hr, ar) if side == "home" else (ar, hr)
            games.append((g.get("gameDate", ""), rf, ra, bool(g["teams"][side].get("isWinner"))))
    games.sort()
    last = games[-10:]
    if not last:
        return {}
    w = sum(1 for *_, win in last if win)
    return {"last10": f"{w}-{len(last) - w}",
            "rpg": round(sum(x[1] for x in last) / len(last), 1),
            "rapg": round(sum(x[2] for x in last) / len(last), 1),
            "n": len(last)}


def load_slate(days: int = 3) -> list:
    """今日から days 日ぶんのMLB試合一覧(先発投手・球場をhydrate)。呼び出し1回。"""
    today = datetime.now(timezone.utc).date()
    start = today.strftime("%Y-%m-%d")
    end = (today + timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        d = _get("/schedule", {"sportId": 1, "startDate": start, "endDate": end,
                               "hydrate": "probablePitcher,team,venue"})
    except Exception as e:
        print(f"[warn] mlb slate fetch failed: {e}", file=sys.stderr)
        return []
    slate = []
    for dt in d.get("dates", []):
        for g in dt.get("games", []):
            h, a = g["teams"]["home"], g["teams"]["away"]
            slate.append({
                "gamePk": g.get("gamePk"), "gameDate": g.get("gameDate"),
                "venue": g.get("venue", {}).get("name", ""),
                "home_name": h["team"].get("name", ""), "away_name": a["team"].get("name", ""),
                "home_id": h["team"].get("id"), "away_id": a["team"].get("id"),
                "home_nick": h["team"].get("teamName", ""), "away_nick": a["team"].get("teamName", ""),
                "home_pp": h.get("probablePitcher") or {}, "away_pp": a.get("probablePitcher") or {},
            })
    return slate


def _match_side(odds_norm: str, game: dict) -> str:
    """odds_norm がその試合のどちら側か("home"/"away")。判定できなければ ""。"""
    o = _alias(odds_norm)
    for side, pre in (("home", "home"), ("away", "away")):
        full = _alias(_norm(game[f"{pre}_name"]))
        nick = _norm(game[f"{pre}_nick"])
        if o == full or (nick and (o == nick or o.endswith(nick) or nick in o)):
            return side
    return ""


def _game_date(g: dict):
    try:
        return datetime.fromisoformat((g.get("gameDate") or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _find_game(slate: list, oh: str, oa: str, kickoff_iso: str = None):
    """両チームがマッチする試合を探す。連戦(同一カードが複数日)対策として、
    kickoffが与えられれば試合日時が最も近い試合を選ぶ。無ければ先発発表済みを優先。"""
    matches = [(g, _match_side(oh, g)) for g in slate]
    matches = [(g, sh) for g, sh in matches if sh and _match_side(oa, g) and _match_side(oa, g) != sh]
    if not matches:
        return None, None
    k = None
    if kickoff_iso:
        try:
            k = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
        except Exception:
            k = None
    if k:
        matches.sort(key=lambda gs: abs(((_game_date(gs[0]) or k) - k).total_seconds()))
    else:  # kickoff不明なら先発が両方発表済みの試合を優先
        matches.sort(key=lambda gs: 0 if (gs[0]["home_pp"].get("id") and gs[0]["away_pp"].get("id")) else 1)
    return matches[0]


def build_context(slate: list, odds_home: str, odds_away: str, kickoff_iso: str = None) -> dict:
    """The Odds APIのチーム名から該当試合を特定し、先発・成績・球場を構造化して返す。
    どのMLB試合ともマッチしなければ None(呼び出し側で警告してスキップ)。"""
    oh, oa = _norm(odds_home), _norm(odds_away)
    g, sh = _find_game(slate, oh, oa, kickoff_iso)
    if not g:
        return None
    # odds_home 側の先発/チームIDを home に揃える
    hp_raw = g["home_pp"] if sh == "home" else g["away_pp"]
    ap_raw = g["away_pp"] if sh == "home" else g["home_pp"]
    hid = g["home_id"] if sh == "home" else g["away_id"]
    aid = g["away_id"] if sh == "home" else g["home_id"]
    ref = datetime.now(timezone.utc)
    hp = _pitcher(hp_raw["id"]) if hp_raw.get("id") else {"name": "未定", "recent": []}
    if hp_raw.get("fullName"):
        hp["name"] = hp_raw["fullName"]
    ap = _pitcher(ap_raw["id"]) if ap_raw.get("id") else {"name": "未定", "recent": []}
    if ap_raw.get("fullName"):
        ap["name"] = ap_raw["fullName"]
    return {
        "matched": True, "gamePk": g["gamePk"], "venue": g["venue"],
        "home": odds_home, "away": odds_away,
        "home_pitcher": hp, "away_pitcher": ap,
        "home_form": _team_form(hid, ref) if hid else {},
        "away_form": _team_form(aid, ref) if aid else {},
    }


def pitcher_last(name: str) -> str:
    """表示用の姓(先発ラベル用)。'Yoshinobu Yamamoto' → 'Yamamoto'"""
    return (name or "未定").split()[-1] if name else "未定"


def note(slate: list, odds_home: str, odds_away: str, kickoff_iso: str = None) -> str:
    """表示用の先発ラベル「先発: Cole vs Yamamoto」。slateの発表情報のみ使用(追加API呼び出しなし)。
    未マッチ・未発表なら空文字。"""
    g, sh = _find_game(slate, _norm(odds_home), _norm(odds_away), kickoff_iso)
    if not g:
        return ""
    hp = g["home_pp"] if sh == "home" else g["away_pp"]
    ap = g["away_pp"] if sh == "home" else g["home_pp"]
    if not (hp.get("fullName") and ap.get("fullName")):
        return ""
    return f"先発: {pitcher_last(hp.get('fullName'))} vs {pitcher_last(ap.get('fullName'))}"
