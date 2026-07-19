# --- 対象リーグ (キー, 表示名, 種別) ---
# 種別: "soccer"=フル分析(9マーケット) / "2way"=勝敗+合計O/U / "3way"=引分あり勝敗+合計O/U
SPORTS = [
    ("soccer_fifa_world_cup", "W杯 2026", "soccer"),
    ("soccer_epl", "プレミア", "soccer"),
    ("soccer_spain_la_liga", "ラ・リーガ", "soccer"),
    ("soccer_spain_segunda_division", "ラ・リーガ2部", "soccer"),
    ("soccer_italy_serie_a", "セリエA", "soccer"),
    ("soccer_germany_bundesliga", "ブンデス", "soccer"),
    ("soccer_uefa_champs_league", "CL", "soccer"),
    ("soccer_japan_j_league", "Jリーグ", "soccer"),
    ("baseball_mlb", "MLB", "mlb"),   # 種別"mlb": 先発投手中心のMLB専用エンジン(src/mlb.py)
    ("basketball_nba", "NBA", "2way"),
    ("americanfootball_nfl", "NFL", "2way"),
    ("icehockey_nhl", "NHL", "2way"),
]
# オフシーズンのリーグは試合が無いだけでエラーにはならない。
# 開幕(対象試合の出現)を検知したらnotify経由でTelegram等に通知する(data/league_state.json)

# --- アウトライト(優勝予想など) ---
OUTRIGHTS = [
    ("soccer_fifa_world_cup_winner", "W杯2026 優勝"),
    # ("soccer_epl_winner", "プレミア 優勝"),
]

REGIONS = "eu"
DAYS_AHEAD = 7            # オッズ取得の対象期間（この範囲の試合のオッズを取得）
ANALYZE_HOURS_BEFORE = 48  # AI分析・予想記録はキックオフまでこの時間以内の試合のみ（費用/API消費削減）
STAKE = 1.0
MODEL = "claude-sonnet-4-6"

# --- MLB専用エンジン ---
MLB_REGIONS = "us"           # MLBは米ブックメーカーの流動性が高いのでusリージョンを使う
MLB_MAX_GAMES_PER_DAY = 15   # 1日にAI分析するMLB試合の上限。超過分はオッズの人気順(主要試合)で上位のみ

# --- 費用ガード(1リーグ・1日あたりのAI分析上限。人気=ブックメーカー数順で上位のみ) ---
SOCCER_MAX_GAMES_PER_DAY = 10   # サッカー各リーグ(週末のフル節でも上位10試合まで)
GENERIC_MAX_GAMES_PER_DAY = 8   # NBA/NFL/NHL等の汎用スポーツ

# キャリブレーションの確率帯(5%刻み、最終帯は70%以上)。集計・表示・補正・テストで共用
CALIB_BINS = [(50, 55), (55, 60), (60, 65), (65, 70), (70, 101)]

PROB_HONMEI = 65
PROB_SUISHO = 55           # 集計(検証)区分の下限。記録・検証・キャリブレーションはこの値のまま
PROB_SUISHO_DISPLAY = 60   # 表示ラベル「有力」の下限。55〜59%帯はキャリブレーションで
                           # 過大評価傾向(予実差マイナス)が観察されたため表示上は「参考」に格下げ。
                           # ※データ蓄積は止めない: 記録・検証・集計はPROB_SUISHOの区分を継続


def _prob_int(prob) -> int:
    try:
        return int(float(prob))
    except (TypeError, ValueError):
        return 0


def tier_of(prob) -> str:
    """確率(数値/文字列)から集計用の区分キー hon/sui/ref を返す唯一の判定関数。
    集計(analytics)・検証はこの関数だけを参照し、区分判定を二重に持たない。"""
    p = _prob_int(prob)
    return "hon" if p >= PROB_HONMEI else "sui" if p >= PROB_SUISHO else "ref"


def tier_of_display(prob) -> str:
    """表示ラベル用の区分キー。「有力」の下限だけPROB_SUISHO_DISPLAYに引き上げ、
    55〜59%帯を表示上「参考」に格下げする(ダッシュボードのバッジ・タブ・通知の選定用)。
    集計・検証・キャリブレーションはtier_of(従来区分)を使い続けること。"""
    p = _prob_int(prob)
    return "hon" if p >= PROB_HONMEI else "sui" if p >= PROB_SUISHO_DISPLAY else "ref"


PROB_DISPLAY_MIN = 50      # サッカー・汎用: この値「以下」の予想は非表示（記録・答え合わせは継続）
PROB_DISPLAY_MIN_MLB = 52  # MLB: 接戦が本質で確率が50%台前半に密集するため少し高め。
                           # 52%あれば野球では十分な傾き。ただし試合ごとの最有力1件(代表カード)は常に表示
MIN_EV = 0.03

# --- 3ソースブレンド: 最終確率 = 市場0.5 + AI0.3 + 統計0.2 の重み付き平均 ---
# 統計モデルが使えない試合(W杯などの代表戦・データ不足チーム)は市場+AIで、
# 市場確率も計算できない場合はAIのみで自動フォールバック(残った重みを再正規化)
WEIGHT_MARKET = 0.5
WEIGHT_AI = 0.3
WEIGHT_STAT = 0.2
