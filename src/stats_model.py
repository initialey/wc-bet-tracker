"""統計モデル(第3の予測ソース): football-data.co.uk の過去試合結果から
チームの攻撃力・守備力レーティングを構築し、期待ゴールを推定する。

手法: 「平均得点/失点ベース + 直近重み付け」の簡易法
- 各試合に経過日数の指数減衰重み w = 0.5 ** (days_ago / HALF_LIFE_DAYS) を付与
- 攻撃力 att = チームの加重平均得点 / リーグ全体の加重平均得点 (1より大=強力)
- 守備力 dfn = チームの加重平均失点 / リーグ全体の加重平均得点 (1より小=堅守)
- 期待ゴール: xg_home = リーグ平均ホーム得点 * att(home) * dfn(away)
              xg_away = リーグ平均アウェー得点 * att(away) * dfn(home)

限界(ポアソン回帰/ディクソン=コールズとの差):
- 対戦相手の強さで個々の試合を補正しない(強豪相手の失点も同じ重み)ため、
  日程の偏りがあるシーズン序盤はレーティングが歪みやすい
- ホーム/アウェー別の攻守は分離せず、H/A差はリーグ平均側でのみ補正
- 低スコア(0-0, 1-0, 1-1)の相関補正(Dixon-Coles tau)なし
- 昇格チーム等は対象データが少なく、MIN_MATCHES 未満なら統計をスキップ
"""
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# The Odds API の sport key -> football-data.co.uk のリーグコード
FD_LEAGUE = {
    "soccer_epl": "E0",
    "soccer_spain_la_liga": "SP1",
    "soccer_spain_segunda_division": "SP2",
    "soccer_italy_serie_a": "I1",
    "soccer_germany_bundesliga": "D1",
    "soccer_france_ligue_one": "F1",
    # Jリーグはfootball-data.co.ukの主要CSV(mmz4281)に無いため対象外
    # (統計ソースなしで市場+AIの2ソースに自動フォールバック)
}

# The Odds API のチーム名 -> football-data.co.uk のチーム名 (表記揺れ対策)
# 同名のチームは記載不要。ここにもCSVにも無い場合は警告を出し統計をスキップする
TEAM_ALIAS = {
    # プレミア
    "Manchester United": "Man United", "Manchester City": "Man City",
    "Tottenham Hotspur": "Tottenham", "Wolverhampton Wanderers": "Wolves",
    "West Ham United": "West Ham", "Brighton and Hove Albion": "Brighton",
    "Newcastle United": "Newcastle", "Nottingham Forest": "Nott'm Forest",
    "Leeds United": "Leeds", "Leicester City": "Leicester",
    "AFC Bournemouth": "Bournemouth", "Luton Town": "Luton",
    "Ipswich Town": "Ipswich",
    # ラ・リーガ
    "Atletico Madrid": "Ath Madrid", "Athletic Bilbao": "Ath Bilbao",
    "Real Sociedad": "Sociedad", "Real Betis": "Betis",
    "Celta Vigo": "Celta", "Rayo Vallecano": "Vallecano",
    "Espanyol": "Espanol", "Deportivo Alaves": "Alaves",
    "Real Valladolid": "Valladolid", "CA Osasuna": "Osasuna",
    "Real Oviedo": "Oviedo",
    # セリエA
    "Inter Milan": "Inter", "AC Milan": "Milan", "AS Roma": "Roma",
    "Hellas Verona": "Verona", "SSC Napoli": "Napoli",
    # ブンデス
    "Borussia Dortmund": "Dortmund", "Bayer Leverkusen": "Leverkusen",
    "Borussia Monchengladbach": "M'gladbach",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "FC Cologne": "FC Koln", "1. FC Koln": "FC Koln",
    "TSG Hoffenheim": "Hoffenheim", "1899 Hoffenheim": "Hoffenheim",
    "FSV Mainz 05": "Mainz", "1. FSV Mainz 05": "Mainz",
    "VfB Stuttgart": "Stuttgart", "VfL Wolfsburg": "Wolfsburg",
    "SC Freiburg": "Freiburg", "FC Augsburg": "Augsburg",
    "FC St. Pauli": "St Pauli", "SV Werder Bremen": "Werder Bremen",
    "Hamburger SV": "Hamburg", "1. FC Heidenheim": "Heidenheim",
    "1. FC Union Berlin": "Union Berlin",
    # リーグアン
    "Paris Saint Germain": "Paris SG", "Olympique Lyonnais": "Lyon",
    "Olympique Marseille": "Marseille", "AS Monaco": "Monaco",
    "LOSC Lille": "Lille", "Stade Rennais": "Rennes",
}

RATINGS_PATH = "data/ratings.json"
CACHE_DAYS = 7          # 週1回再計算 (環境変数 STATS_REFRESH=1 で強制)
HALF_LIFE_DAYS = 180    # 半年前の試合は重み0.5
MIN_MATCHES = 10        # これ未満のチームはレーティング対象外(昇格直後など)
XG_MIN, XG_MAX = 0.2, 4.0  # 期待ゴールのクランプ(異常値対策)


def _seasons(n=2, now=None):
    """直近nシーズンのfootball-dataシーズンコード(例: 2526)。欧州は8月開幕基準"""
    now = now or datetime.now(timezone.utc)
    start = now.year if now.month >= 8 else now.year - 1
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(start, start - n, -1)]


def _download_csv(url: str) -> str:
    r = requests.get(url, timeout=30,
                     headers={"User-Agent": "wc-bet-tracker (weekly ratings cache)"})
    r.raise_for_status()
    return r.text


def _parse_matches(text: str) -> list:
    """CSVから (日付, ホーム, アウェー, ホーム得点, アウェー得点) を抽出"""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        d = None
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                d = datetime.strptime((r.get("Date") or "").strip(), fmt)
                break
            except ValueError:
                continue
        if not d:
            continue
        try:
            out.append((d, r["HomeTeam"].strip(), r["AwayTeam"].strip(),
                        int(float(r["FTHG"])), int(float(r["FTAG"]))))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def _build_league(code: str) -> dict | None:
    """1リーグ分のレーティングを構築(直近2シーズンのCSVをダウンロード)"""
    matches = []
    for season in _seasons():
        url = f"{BASE_URL}/{season}/{code}.csv"
        try:
            matches += _parse_matches(_download_csv(url))
        except Exception as e:
            print(f"[warn] stats: download failed {code} {season}: {e}", file=sys.stderr)
    if not matches:
        return None

    latest = max(d for d, *_ in matches)
    sum_w = sum_hg = sum_ag = 0.0
    teams = {}  # name -> [重み計, 加重得点, 加重失点, 試合数]
    for d, h, a, hg, ag in matches:
        w = 0.5 ** ((latest - d).days / HALF_LIFE_DAYS)
        sum_w += w
        sum_hg += hg * w
        sum_ag += ag * w
        for name, gf, ga in ((h, hg, ag), (a, ag, hg)):
            t = teams.setdefault(name, [0.0, 0.0, 0.0, 0])
            t[0] += w
            t[1] += gf * w
            t[2] += ga * w
            t[3] += 1

    avg_goals = (sum_hg + sum_ag) / (2 * sum_w)  # 1チームあたり平均得点
    rated = {name: {"att": (gf / w) / avg_goals, "def": (ga / w) / avg_goals, "n": n}
             for name, (w, gf, ga, n) in teams.items() if n >= MIN_MATCHES}
    return {"avg_home": sum_hg / sum_w, "avg_away": sum_ag / sum_w, "teams": rated}


def load_or_build(sport_keys: list, path=RATINGS_PATH, force=False) -> dict:
    """data/ratings.json をキャッシュとして使い、CACHE_DAYS より古い場合のみ再計算。
    force または環境変数 STATS_REFRESH=1 で強制再計算。
    ダウンロード失敗したリーグは旧キャッシュを温存する"""
    force = force or os.environ.get("STATS_REFRESH") == "1"
    cached = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
        except (OSError, ValueError):
            cached = {}
    now = datetime.now(timezone.utc)
    fresh = False
    try:
        fresh = (now - datetime.fromisoformat(cached["built_utc"])).days < CACHE_DAYS
    except (KeyError, ValueError):
        pass

    leagues = dict(cached.get("leagues", {}))
    wanted = [k for k in sport_keys if k in FD_LEAGUE]
    if not force and fresh and all(k in leagues for k in wanted):
        return leagues

    for k in wanted:
        built = _build_league(FD_LEAGUE[k])
        if built:
            leagues[k] = built
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"built_utc": now.isoformat(), "leagues": leagues},
                  f, ensure_ascii=False)
    return leagues


def _resolve(teams: dict, name: str):
    if name in teams:
        return teams[name]
    alias = TEAM_ALIAS.get(name)
    return teams.get(alias) if alias else None


def predict(league: dict | None, home: str, away: str):
    """期待ゴール (xg_home, xg_away) を返す。
    リーグ未対応(代表戦など)は静かに、チーム名不一致・データ不足は警告を出して None"""
    if not league:
        return None
    h, a = _resolve(league["teams"], home), _resolve(league["teams"], away)
    if not h or not a:
        missing = [n for n, t in ((home, h), (away, a)) if not t]
        print(f"[warn] stats: no rating for {missing} - skip stat source", file=sys.stderr)
        return None
    xh = league["avg_home"] * h["att"] * a["def"]
    xa = league["avg_away"] * a["att"] * h["def"]
    return (max(XG_MIN, min(XG_MAX, xh)), max(XG_MIN, min(XG_MAX, xa)))
