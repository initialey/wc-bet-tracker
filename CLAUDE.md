# AI Bet Tracker - プロジェクト概要 (for Claude Code)

## 何をするシステムか
毎朝9時JST(cron)にGitHub Actionsで実行。The Odds APIから試合とオッズを取得し、
Claude API(ウェブ検索付き)で各試合を分析、ポアソン/正規モデルで各マーケットの確率を計算。
予想をdata/history.csvに記録し、確定試合を自動で答え合わせ。docs/index.htmlを生成しGitHub Pagesで公開。

## 構成
- .github/workflows/analyze.yml : cron + 手動実行。Secrets: ODDS_API_KEY, ANTHROPIC_API_KEY, (任意)SLACK_WEBHOOK_URL, DISCORD_WEBHOOK_URL
- src/config.py : 対象リーグ(SPORTS)、アウトライト、閾値
- src/odds_api.py : The Odds API (h2h/totals/btts/dnb/team_totals/corners/outrights/scores + 残クォータ)
- src/ai.py : Claude API分析。サッカー用(analyze_match: h2h+xG+corners)と汎用(analyze_generic: 勝敗+合計)。根拠は日英
- src/model.py : ポアソン(goal_probs, corner_probs)と正規近似(total_probs)
- src/main.py : オーケストレーション。settle=答え合わせ(push対応)、analytics=キャリブレーション/マーケット別ROI
- src/dashboard.py : 静的HTML生成(日英切替、タブ、優勝オッズ、オッズ変動、実績分析)
- src/notify.py : Slack/Discord通知(webhook Secretがある時のみ)
- data/history.csv : 全予想の記録(答え合わせ済み含む)。スキーマ変更時は互換性に注意

## 規約
- 予想は一度記録したら変更しない(検証の公正性のため)。オッズ変動は表示のみ
- コーナーは結果を自動取得できないためhistory.csvに記録しない(表示のみ)
- 日本の法規制に配慮し、免責文言をダッシュボードから削除しない
- テスト: モックでmain.main()を通す(実APIキー不要)。実API疎通はActionsログで確認

## よくあるタスク
- リーグ追加: config.pyのSPORTSに1行(The Odds APIのsport key)
- 閾値調整: config.pyのPROB_HONMEI/PROB_SUISHO
- 新マーケット: odds_api取得→main予想生成→settle答え合わせ→dashboard表示の4点セットで追加
