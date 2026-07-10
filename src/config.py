# --- 対象リーグ (キー, 表示名) : 複数追加可 ---
SPORTS = [
    ("soccer_fifa_world_cup", "W杯 2026"),
    # 8月以降の追加例:
    # ("soccer_epl", "プレミアリーグ"),
    # ("soccer_spain_la_liga", "ラ・リーガ"),
    # ("soccer_uefa_champs_league", "CL"),
]

# --- アウトライト(優勝予想など) (キー, 表示名) ---
OUTRIGHTS = [
    ("soccer_fifa_world_cup_winner", "W杯2026 優勝"),
]

REGIONS = "eu"
DAYS_AHEAD = 7
STAKE = 1.0
MODEL = "claude-sonnet-4-6"

PROB_HONMEI = 65    # 🟢本命
PROB_SUISHO = 55    # 🟡有力
MIN_EV = 0.03
