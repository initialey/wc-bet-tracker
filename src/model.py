"""ポアソンモデル: AIが推定したxGから全ゴール系マーケットの確率を一括計算"""
from math import exp

MAX_G = 9


def _pois(lam: float, k: int) -> float:
    p = exp(-lam)
    for i in range(1, k + 1):
        p *= lam / i
    return p


def goal_probs(xg_home: float, xg_away: float) -> dict:
    """合計O/U各ライン, BTTS, チーム別O/U1.5 の確率を返す"""
    ph = [_pois(xg_home, k) for k in range(MAX_G + 1)]
    pa = [_pois(xg_away, k) for k in range(MAX_G + 1)]

    total = {}  # 合計得点の分布
    for h in range(MAX_G + 1):
        for a in range(MAX_G + 1):
            total[h + a] = total.get(h + a, 0) + ph[h] * pa[a]

    def over(line):
        return sum(p for g, p in total.items() if g > line)

    return {
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
