"""ポアソンモデル: AIが推定したxGから全ゴール系マーケットの確率を一括計算"""
from math import exp

MAX_G = 9


def _pois(lam: float, k: int) -> float:
    p = exp(-lam)
    for i in range(1, k + 1):
        p *= lam / i
    return p


def _score_matrix(xg_home: float, xg_away: float) -> list:
    """独立ポアソンのスコア行列 m[h][a] = P(ホームh点, アウェーa点)"""
    ph = [_pois(xg_home, k) for k in range(MAX_G + 1)]
    pa = [_pois(xg_away, k) for k in range(MAX_G + 1)]
    return [[ph[h] * pa[a] for a in range(MAX_G + 1)] for h in range(MAX_G + 1)]


def handicap_probs(xg_home: float, xg_away: float, line: float) -> dict:
    """ホームのハンディキャップライン(0.5刻み: ±0.5/±1.5/±2.5のみ)のカバー確率。
    cover = P(ホーム得点 + line > アウェー得点)。0.5刻み限定なのでpush(返金)は生じない。
    0.25/0.75などのクォーターラインは答え合わせが複雑になるため対象外(呼び出し側で除外)"""
    m = _score_matrix(xg_home, xg_away)
    cover = sum(m[h][a] for h in range(MAX_G + 1) for a in range(MAX_G + 1)
                if h + line > a)
    return {"cover": cover, "no_cover": 1 - cover}


def top_scores(xg_home: float, xg_away: float, n: int = 3) -> list:
    """最有力スコア上位n件 [((home_goals, away_goals), 確率)] を確率降順で返す"""
    m = _score_matrix(xg_home, xg_away)
    flat = [((h, a), m[h][a]) for h in range(MAX_G + 1) for a in range(MAX_G + 1)]
    return sorted(flat, key=lambda x: -x[1])[:n]


def goal_probs(xg_home: float, xg_away: float) -> dict:
    """勝敗(1x2), 合計O/U各ライン, BTTS, チーム別O/U1.5 の確率を返す"""
    ph = [_pois(xg_home, k) for k in range(MAX_G + 1)]
    pa = [_pois(xg_away, k) for k in range(MAX_G + 1)]

    total = {}  # 合計得点の分布
    home_win = draw = away_win = 0.0
    for h in range(MAX_G + 1):
        for a in range(MAX_G + 1):
            p = ph[h] * pa[a]
            total[h + a] = total.get(h + a, 0) + p
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p

    def over(line):
        return sum(p for g, p in total.items() if g > line)

    return {
        "home_win": home_win, "draw": draw, "away_win": away_win,
        "over15": over(1.5), "under15": 1 - over(1.5),
        "over25": over(2.5), "under25": 1 - over(2.5),
        "over35": over(3.5), "under35": 1 - over(3.5),
        "btts_yes": (1 - ph[0]) * (1 - pa[0]),
        "btts_no": 1 - (1 - ph[0]) * (1 - pa[0]),
        "home_over15": 1 - ph[0] - ph[1],
        "home_under15": ph[0] + ph[1],
        "away_over15": 1 - pa[0] - pa[1],
        "away_under15": pa[0] + pa[1],
    }


def corner_probs(expected_total: float, line: float = 9.5) -> dict:
    """コーナー合計のO/U確率（ポアソン近似）"""
    dist = [_pois(expected_total, k) for k in range(25)]
    over = sum(p for k, p in enumerate(dist) if k > line)
    return {"over": over, "under": 1 - over, "line": line}


def total_probs(expected: float, line: float) -> dict:
    """汎用スポーツの合計スコアO/U確率（正規近似）"""
    from math import erf, sqrt
    sd = max(1.0, 1.2 * (expected ** 0.5))
    z = (line - expected) / sd
    under = 0.5 * (1 + erf(z / sqrt(2)))
    return {"over": 1 - under, "under": under}


def devig(odds_dict: dict) -> dict:
    """同一マーケット内の全選択肢のオッズから市場暗示確率を計算する。
    1/odds を合計1に正規化することでブックメーカーのマージンを除去する。
    オッズが欠損・不正(<=1)な選択肢がある場合は {} を返す(全選択肢が揃っている前提のため)"""
    if not odds_dict or len(odds_dict) < 2:
        return {}
    try:
        inv = {k: 1 / float(o) for k, o in odds_dict.items() if o and float(o) > 1}
    except (TypeError, ValueError):
        return {}
    if len(inv) != len(odds_dict):
        return {}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()}


def blend(probs: list, weights: list) -> float:
    """複数の確率ソースを重み付き平均する(将来3ソース以上への拡張用)。
    Noneのソースはその重みごと除外する"""
    pairs = [(p, w) for p, w in zip(probs, weights) if p is not None]
    if not pairs:
        raise ValueError("blend: no valid probability sources")
    total_w = sum(w for _, w in pairs)
    return sum(p * w for p, w in pairs) / total_w
