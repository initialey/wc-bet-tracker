# W杯 AIベット予想トラッカー

毎朝9時(JST)に自動で: 過去予想の答え合わせ → 今後の試合をAI分析 → EV計算 → ダッシュボード更新。

## セットアップ（初回のみ・約30分）

### 1. APIキーを2つ取得
- **The Odds API**: https://the-odds-api.com → 無料登録 → キーをコピー
- **Anthropic API**: https://console.anthropic.com → キー発行（$5チャージ推奨）

### 2. リポジトリ作成
1. GitHubで新規リポジトリ作成（例: `wc-bet-tracker`、Private推奨）
2. このフォルダの中身をすべてアップロード（git push または Web画面でドラッグ&ドロップ）

### 3. Secrets登録
リポジトリの Settings → Secrets and variables → Actions → New repository secret で2つ登録:
- `ODDS_API_KEY` = The Odds APIのキー
- `ANTHROPIC_API_KEY` = Anthropicのキー

### 4. GitHub Pages有効化
Settings → Pages → Source: **Deploy from a branch** → Branch: `main` / フォルダ: `/docs` → Save

### 5. 初回実行
Actions タブ → `analyze` → **Run workflow** → 2〜3分待つ
→ `https://ユーザー名.github.io/wc-bet-tracker/` にダッシュボードが表示されます

## 日常の使い方
- 何もしなくても毎朝9時に自動更新
- 今すぐ更新したい時: ダッシュボードの「⚡ 再分析を実行」→ GitHubのActions画面で Run workflow

## 設定変更 (`src/config.py`)
- `SPORT`: W杯終了後は `soccer_epl`(プレミア) `soccer_spain_la_liga` 等に変更
- `MIN_EV`: 推奨表示の閾値（デフォルト3%）

## X(Twitter)自動投稿(任意・マーケティング用)
予測・決済結果を X へ自動投稿できます(`.github/workflows/post-to-x.yml`)。Secrets 未登録なら何もしません。

### セットアップ
1. https://developer.x.com でアプリ作成 → **User authentication settings** で Read and write を有効化(OAuth 1.0a)
2. Keys and tokens から4つの値を取得し、Settings → Secrets and variables → Actions に登録:
   - `X_API_KEY` = API Key (Consumer Key)
   - `X_API_SECRET` = API Key Secret
   - `X_ACCESS_TOKEN` = Access Token
   - `X_ACCESS_TOKEN_SECRET` = Access Token Secret
3. ダッシュボードURLは**プロフィール(bio)に記載**して運用(X APIは本文にURLを含む投稿が $0.20/件 と高額なため、本文へのURL混入はコード側で禁止している)

### 投稿の種類
- **試合前予測**: analyze 完了後、バリュー(EV)上位 最大3件(1件ずつ投稿)
- **本日の結果**: analyze 完了後、新たに確定した予想の勝敗・損益・的中ハイライト(負けた日も必ず投稿)
- **週間サマリー**: 毎週月曜 09:00 JST。週間/累計ROI・的中率+累積損益グラフ画像
- 1日の投稿上限はデフォルト6件(環境変数 `X_MAX_POSTS_PER_DAY` で変更可)。`data/posted_log.json` で重複投稿を防止

### テスト手順
1. Actions → `post-to-x` → Run workflow → **dry_run=true**(デフォルト)で実行し、ログで生成本文と文字数を確認
2. 文字数超過ケースは `python tests/test_post_to_x.py` の削減ロジックのテストで確認(全角2/半角1の weighted length、超過時はハッシュタグ→ハイライトの順に自動削除)
3. dry_run=false・mode=result などで1件だけ実投稿し、Xでの表示を確認
4. もう一度同じモードで実行し、`data/posted_log.json` により重複投稿されない(no newly settled... とログに出る)ことを確認

## 注意
本ツールは参考情報であり的中を保証しません。ベッティングの合法性は国・地域により異なります。余剰資金の範囲で自己責任にてご利用ください。
