# --- 対象リーグ (キー, 表示名, 種別) ---
# 種別: "soccer"=フル分析(9マーケット) / "2way"=勝敗+合計O/U / "3way"=引分あり勝敗+合計O/U
SPORTS = [
    ("soccer_fifa_world_cup", "W杯 2026", "soccer"),
    ("soccer_epl", "プレミア", "soccer"),
    ("soccer_spain_la_liga", "ラ・リーガ", "soccer"),
    ("soccer_italy_serie_a", "セリエA", "soccer"),
    ("soccer_germany_bundesliga", "ブンデス", "soccer"),
    ("soccer_uefa_champs_league", "CL", "soccer"),
    # ↓ 有効化するときは行頭の # を外す（試合数が多くAPI消費と費用が増える点に注意）
    # ("basketball_nba", "NBA", "2way"),
    # ("americanfootball_nfl", "NFL", "2way"),
    # ("icehockey_nhl", "NHL", "2way"),
    # ("baseball_mlb", "MLB", "2way"),
]

# --- アウトライト(優勝予想など) ---
OUTRIGHTS = [
    ("soccer_fifa_world_cup_winner", "W杯2026 優勝"),
    # ("soccer_epl_winner", "プレミア 優勝"),
]

REGIONS = "eu"
DAYS_AHEAD = 7
STAKE = 1.0
MODEL = "claude-sonnet-4-6"

PROB_HONMEI = 65
PROB_SUISHO = 55
MIN_EV = 0.03

# --- 3ソースブレンド: 最終確率 = 市場0.5 + AI0.3 + 統計0.2 の重み付き平均 ---
# 統計モデルが使えない試合(W杯などの代表戦・データ不足チーム)は市場+AIで、
# 市場確率も計算できない場合はAIのみで自動フォールバック(残った重みを再正規化)
WEIGHT_MARKET = 0.5
WEIGHT_AI = 0.3
WEIGHT_STAT = 0.2
