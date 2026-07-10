SPORT = "soccer_fifa_world_cup"   # 8月以降は soccer_epl 等に変更可
REGIONS = "eu"                     # 参照するブックメーカー地域
DAYS_AHEAD = 7                     # 何日先の試合まで分析するか
STAKE = 1.0                        # ROI計算用の仮想ベット額(1単位固定)
MODEL = "claude-sonnet-4-6"

# --- 当たりやすさ基準 ---
PROB_HONMEI = 65    # これ以上 = 🟢本命（当たりやすい）
PROB_SUISHO = 55    # これ以上 = 🟡有力（✓推奨ライン）
MIN_EV = 0.03       # EV参考表示用の閾値
