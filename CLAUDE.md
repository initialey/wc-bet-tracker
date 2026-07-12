# AI Bet Tracker - プロジェクト概要 (for Claude Code)

## 何をするシステムか
毎朝9時JST(cron)にGitHub Actionsで実行。The Odds APIから試合とオッズを取得し、
Claude API(ウェブ検索付き)で各試合を分析、ポアソン/正規モデルで各マーケットの確率を計算。
予想をdata/history.csvに記録し、確定試合を自動で答え合わせ。docs/index.htmlを生成しGitHub Pagesで公開。

## 構成
- .github/workflows/analyze.yml : cron + 手動実行。Secrets: ODDS_API_KEY, ANTHROPIC_API_KEY, (任意)SLACK_WEBHOOK_URL, DISCORD_WEBHOOK_URL
- src/config.py : 対象リーグ(SPORTS: 種別"soccer"/"2way"/"3way"/"mlb")、アウトライト、閾値、MLB_REGIONS/MLB_MAX_GAMES_PER_DAY
- src/odds_api.py : The Odds API (h2h/totals/spreads/btts/dnb/team_totals/corners/outrights/scores + 残クォータ)。get_upcomingはmarkets引数で取得マーケットを指定(MLBはh2h,spreads,totals)
- src/ai.py : Claude API分析。サッカー用(analyze_match)・汎用(analyze_generic)・MLB用(analyze_mlb: Stats API構造化データを埋込+検索は補助max_uses=4)。根拠は日英
- src/mlb.py : MLB Stats API(statsapi.mlb.com, キー不要)。先発投手の発表/今季成績/直近3登板、両チーム直近10試合の得点力、球場を取得。The Odds APIとのチーム名マッピング(表記揺れ吸収、連戦はkickoff時刻で特定)。未マッチ試合はNoneでスキップ
- src/model.py : ポアソン(goal_probs, corner_probs)と正規近似(total_probs)、devig(市場確率)/blend(重み付き平均)
- src/stats_model.py : football-data.co.ukの過去結果→攻守レーティング(data/ratings.jsonに週1キャッシュ、STATS_REFRESH=1で強制再計算)。市場・AIに次ぐ第3の確率ソース。代表戦(W杯等)やデータ不足チームは自動で市場+AIにフォールバック
- src/main.py : オーケストレーション。settle=答え合わせ(push対応)、analytics=キャリブレーション/マーケット別ROI
- src/dashboard.py : 静的HTML生成(日英切替、タブ、優勝オッズ、オッズ変動、実績分析)
- src/notify.py : Slack/Discord通知(webhook Secretがある時のみ)
- data/history.csv : 全予想の記録(答え合わせ済み含む)。スキーマ変更時は互換性に注意
- data/league_state.json : リーグ開幕検知の状態。オフシーズン→試合出現でTelegram等に1回通知(14日未満の空白は通知しない)。費用ガード: 1リーグ1日あたりのAI分析上限はconfig.pyのSOCCER/GENERIC/MLB_MAX_GAMES_PER_DAY

## 規約
- 予想は一度記録したら変更しない(検証の公正性のため)。オッズ変動は表示のみ
- コーナーとスコア予想(参考)はhistory.csvに記録しない(表示のみ)。ハンディ(サッカーのspreads)は0.5刻みライン限定で記録・答え合わせ対象
- MLB: 種別"mlb"はsrc/mlb.pyの構造化データを土台にanalyze_mlbで分析。マーケットは勝敗(引分なし)/合計得点(O/U)/ランライン(±1.5)。答え合わせは延長込み最終スコア。費用ガードでブックメーカー数(人気)上位MLB_MAX_GAMES_PER_DAY試合のみAI分析
- 日本の法規制に配慮し、免責文言をダッシュボードから削除しない
- テスト: モックでmain.main()を通す(実APIキー不要)。実API疎通はActionsログで確認

## よくあるタスク
- リーグ追加: config.pyのSPORTSに1行(The Odds APIのsport key)
- 閾値調整: config.pyのPROB_HONMEI/PROB_SUISHO
- 新マーケット: odds_api取得→main予想生成→settle答え合わせ→dashboard表示の4点セットで追加
