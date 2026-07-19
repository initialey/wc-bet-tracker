# AI Bet Tracker - プロジェクト概要 (for Claude Code)

## 何をするシステムか
毎朝7:40フィリピン時間(23:40 UTC cron、9:20 PHTにフォールバックcron)にGitHub Actionsで実行。
00:00 UTCはActionsの混雑で欠落しやすいため半端な分を使用。The Odds APIから試合とオッズを取得し、
Claude API(ウェブ検索付き)で各試合を分析、ポアソン/正規モデルで各マーケットの確率を計算。
予想をdata/history.csvに記録し、確定試合を自動で答え合わせ。docs/index.htmlを生成しGitHub Pagesで公開。

## 構成
- .github/workflows/analyze.yml : cron + 手動実行。Secrets: ODDS_API_KEY, ANTHROPIC_API_KEY, (任意)SLACK_WEBHOOK_URL, DISCORD_WEBHOOK_URL
- src/config.py : 対象リーグ(SPORTS: 種別"soccer"/"2way"/"3way"/"mlb")、アウトライト、閾値、MLB_REGIONS/MLB_MAX_GAMES_PER_DAY
- src/odds_api.py : The Odds API (h2h/totals/spreads/btts/dnb/team_totals/corners/outrights/scores + 残クォータ)。get_upcomingはmarkets引数で取得マーケットを指定(MLBはh2h,spreads,totals)
- src/ai.py : Claude API分析。サッカー用(analyze_match)・汎用(analyze_generic)・MLB用(analyze_mlb: Stats API構造化データを埋込+検索は補助max_uses=4)。根拠は日英。check_verdict/rewrite_verdict=理由文とピックの整合性ガード用の軽量呼び出し(MODEL_LIGHT=haiku)
- 理由文整合性ガード(main._verify_reason): ピックは分析後にブレンドで決まるため、AI由来の結論文が選択と矛盾しうる。AI文を流用するマーケット(h2h/DNB/BTTS/コーナー/MLB勝敗・合計/汎用)で生成後に軽量モデル検証→矛盾なら選択を明示して再生成(最大2回)→それでも矛盾なら結論なし(事実のみ)で掲載。ハンディ/ランライン/チーム得点/サッカーO-Uはコード生成(_ah_verdict/_team_total_verdict/_ou_verdict)で対象外。サッカーO-UはAI見解を「試合展開の見解」として事実欄に回し、結論はライン別テンプレート(過去記録分も表示時に差し替え)
- 試合がない日の表示: 全リーグ0件はカード欄に案内パネル(リーグ別の次の試合日、取得済みオッズ結果を再利用しleague_statusで受け渡し)。MLB等の個別リーグ0件はタブ内お知らせカード。各リーグの取得件数は毎回[info]ログに出力
- src/mlb.py : MLB Stats API(statsapi.mlb.com, キー不要)。先発投手の発表/今季成績/直近3登板、両チーム直近10試合の得点力、球場を取得。The Odds APIとのチーム名マッピング(表記揺れ吸収、連戦はkickoff時刻で特定)。未マッチ試合はNoneでスキップ
- src/model.py : ポアソン(goal_probs, corner_probs)と正規近似(total_probs)、devig(市場確率)/blend(重み付き平均)
- src/calibration.py : 検証データに基づく確率補正層。確率帯(5%刻み)ごとの実績的中率でブレンド後の最終確率をベイズ縮小補正(補正後=(実績×n+予測×k)/(n+k), k=50)。スポーツ別テーブル優先・帯20件未満は全体にフォールバック。ラベル・EV・表示は補正後、補正前はhistory.csvのprob_raw列に記録(効果検証用)。カード内訳に「補正前 xx%」表示
- data/proposals.json : デイリーレビューで表示済みの改善提案の記録。同じ提案はROIが±5pt以上変化した時だけ再表示(毎日の重複抑制)
- src/stats_model.py : football-data.co.ukの過去結果→攻守レーティング(data/ratings.jsonに週1キャッシュ、STATS_REFRESH=1で強制再計算)。市場・AIに次ぐ第3の確率ソース。代表戦(W杯等)やデータ不足チームは自動で市場+AIにフォールバック
- src/main.py : オーケストレーション。settle=答え合わせ(push対応)、analytics=キャリブレーション/マーケット別ROI
- src/review.py : デイリーレビュー(答え合わせ後に実行)。昨日確定分のAI短評(1日1回・確定0件ならスキップ)+ゲート付き改善提案(検証15件以上かつROI±15%超の区分のみ、コード側で定型生成)。data/review.jsonに保存しダッシュボード表示とSlack/Discord通知。提案の自動実装は絶対にしない
- src/dashboard.py : 静的HTML生成(日英切替、タブ、優勝オッズ、オッズ変動、実績分析)
- src/notify.py : Slack/Discord通知(webhook Secretがある時のみ)
- src/post_to_x.py : X(Twitter)自動投稿(マーケティング用)。--mode prediction/result/weekly/all。本文URL禁止(URL入り投稿は$0.20/件と高額。bio誘導運用)、weighted length 280(全角2/半角1)検証+超過時はハッシュタグ→ハイライト順に自動削減、日次上限X_MAX_POSTS_PER_DAY(デフォルト6)、DRY_RUN=1で表示のみ。Secrets: X_API_KEY/X_API_SECRET/X_ACCESS_TOKEN/X_ACCESS_TOKEN_SECRET(OAuth 1.0a User Context, tweepy)
- .github/workflows/post-to-x.yml : analyze完了後(workflow_run)に結果→予測を投稿、月曜09:00 JST cronで週次サマリー(matplotlib累積損益グラフ添付)。analyzeとは完全分離で失敗しても本体に影響しない。tweepy/matplotlibはこのワークフローのみでinstall(requirements.txtに入れない)
- data/posted_log.json : 投稿済みID・日次投稿数の管理(重複投稿防止、90日で自動整理)。post-to-xワークフローがコミット
- data/history.csv : 全予想の記録(答え合わせ済み含む)。スキーマ変更時は互換性に注意。prob_raw=補正前確率、bookmaker=最良オッズの提供ブックメーカー名(カードのオッズ横に併記、ダッシュボードに提供回数ランキング表示)、closing_odds=締切オッズの近似(毎回の実行で未確定予想の直近観測オッズを上書きし、キックオフ前最後の観測値が残る。追加APIなし。対応はh2h/totals/spreadsのみ)。CLV=記録時オッズ÷締切オッズ−1をマーケット別・スポーツ別にダッシュボード表示、平均CLV+2%以上の区分はレビューで「市場に先行」と評価
- 🎯実弾候補: 実弾テスト用の絞り込み(現行はMLB×ランライン×補正後60%以上)。条件はconfig.pyのLIVE_BET_FILTERSだけを参照(拡張時はここに追記)。ダッシュボードのカテゴリタブ先頭に「🎯 実弾候補」タブ(該当0件はタブ内に案内)、該当カードに損益分岐オッズ(100÷補正後確率)と合格ライン(×LIVE_BET_MARGIN=1.02、2桁切り上げ)を表示し、取得済み最良オッズが合格ライン以上なら✅買い候補/未満なら⚠️要オッズ確認。Slack/Discord通知の冒頭にセクション(0件日は「本日の実弾候補なし」1行)、実績分析のマーケット別成績の先頭に条件を過去分へ遡及適用した集計行
- data/league_state.json : リーグ開幕検知の状態。オフシーズン→試合出現でTelegram等に1回通知(14日未満の空白は通知しない)。費用ガード: 1リーグ1日あたりのAI分析上限はconfig.pyのSOCCER/GENERIC/MLB_MAX_GAMES_PER_DAY

## 規約
- 予想は一度記録したら変更しない(検証の公正性のため)。オッズ変動は表示のみ
- コーナーとスコア予想(参考)はhistory.csvに記録しない(表示のみ)。ハンディ(サッカーのspreads)は0.5刻みライン限定で記録・答え合わせ対象
- MLB: 種別"mlb"はsrc/mlb.pyの構造化データを土台にanalyze_mlbで分析。マーケットは勝敗(引分なし)/合計得点(O/U)/ランライン(±1.5)。答え合わせは延長込み最終スコア。費用ガードでブックメーカー数(人気)上位MLB_MAX_GAMES_PER_DAY試合のみAI分析
- 日本の法規制に配慮し、免責文言をダッシュボードから削除しない
- レビューの改善提案(src/review.py)は表示・通知のみ。config等への自動反映は実装しない
- テスト: モックでmain.main()を通す(実APIキー不要)。実API疎通はActionsログで確認

## よくあるタスク
- リーグ追加: config.pyのSPORTSに1行(The Odds APIのsport key)
- 閾値調整: config.pyのPROB_HONMEI/PROB_SUISHO(集計・検証区分)とPROB_SUISHO_DISPLAY(表示ラベル「有力」の下限。55〜59%帯は表示上「参考」格下げ中だが記録・検証・キャリブレーション集計はPROB_SUISHOの区分を継続)。表示ラベル・通知はtier_of_display、集計はtier_ofを使う
- 新マーケット: odds_api取得→main予想生成→settle答え合わせ→dashboard表示の4点セットで追加
