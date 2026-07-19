"""検証データに基づく確率補正層。

history.csvの検証済み(win/lose)データから確率帯(5%刻み)ごとの実績的中率を計算し、
市場ブレンド後の最終確率をベイズ縮小で補正する:

    補正後確率 = (実績的中率 × n + 予測確率 × k) / (n + k)
    n = その帯の検証件数, k = K_SHRINK(縮小定数)

件数が少ない帯はほぼ無補正、多い帯ほど実績側に寄る設計。
スポーツ別(サッカー/MLB)のテーブルを優先し、該当スポーツの帯あたり検証件数が
MIN_SPORT_N件未満の場合は全スポーツ合算のテーブルにフォールバックする。
ラベル判定・期待値計算・表示はすべて補正後の値を使い、補正前の値は
history.csvのprob_raw列に記録して補正の効果自体を後から検証できるようにする。
"""
from .config import SPORTS, CALIB_BINS

K_SHRINK = 50      # 縮小定数k(この件数分だけ予測側に錘を置く)
MIN_SPORT_N = 20   # スポーツ別テーブルを使う最低検証件数(帯あたり)

_LABEL_KIND = {label: kind for _, label, kind in SPORTS}


def _kind_of(row) -> str:
    """行のリーグ表示名からスポーツ種別を返す(analytics._sport_ofと同じ規則)。
    旧行(league空)はサッカー、soccer/mlb以外はotherとして全体テーブルのみに寄与"""
    kind = _LABEL_KIND.get(row.get("league") or "", "soccer")
    return kind if kind in ("soccer", "mlb") else "other"


def _band(p: float):
    for lo, hi in CALIB_BINS:
        if lo <= p < hi:
            return (lo, hi)
    return None


def build_tables(history: list) -> dict:
    """検証済み行から補正テーブルを構築:
    {"all"|"soccer"|"mlb": {(lo,hi): (n, 実績的中率%)}}"""
    buckets = {}
    for r in history:
        if r.get("result") not in ("win", "lose"):
            continue
        try:
            p = int(float(r["prob"]))
        except (TypeError, ValueError):
            continue
        band = _band(p)
        if not band:
            continue
        kind = _kind_of(r)
        keys = ("all", kind) if kind in ("soccer", "mlb") else ("all",)
        for key in keys:
            n, w = buckets.get((key, band), (0, 0))
            buckets[(key, band)] = (n + 1, w + (1 if r["result"] == "win" else 0))

    tables = {"all": {}, "soccer": {}, "mlb": {}}
    for (key, band), (n, w) in buckets.items():
        tables[key][band] = (n, w / n * 100)
    return tables


def correct(tables: dict, kind: str, prob: float) -> float:
    """ブレンド後の最終確率(0-1)に補正を適用して0-1で返す。
    kind: "soccer"/"mlb"はスポーツ別テーブル優先(帯の件数不足時はallへ
    フォールバック)、その他("2way"等)は最初からall。データが無ければ無補正"""
    if not tables:
        return prob
    p = prob * 100
    band = _band(p)
    if not band:
        return prob   # 50%未満は検証データの帯が無いため無補正
    entry = None
    if kind in ("soccer", "mlb"):
        entry = tables.get(kind, {}).get(band)
        if entry and entry[0] < MIN_SPORT_N:
            entry = None   # スポーツ別の件数不足 → 全スポーツ合算へ
    if entry is None:
        entry = tables.get("all", {}).get(band)
    if not entry:
        return prob
    n, hit = entry
    adj = (hit * n + p * K_SHRINK) / (n + K_SHRINK)
    return min(max(adj, 1.0), 99.0) / 100
