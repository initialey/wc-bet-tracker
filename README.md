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

## 注意
本ツールは参考情報であり的中を保証しません。ベッティングの合法性は国・地域により異なります。余剰資金の範囲で自己責任にてご利用ください。
