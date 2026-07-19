"""X(Twitter)自動投稿(マーケティング用)。

data/history.csv の予測・決済結果を読み、X API(従量課金)へ自動投稿する。
- テキスト投稿 $0.015/件、URL入り投稿は $0.20/件のため本文へのURL混入は禁止
  (投稿前にバリデーションで拒否。ダッシュボード誘導はプロフィールbio運用)
- 文字数は weighted length(全角2/半角1)で280以内。超過時はハッシュタグ→
  ハイライト→フッターの順に削り、それでも超過なら末尾を切り詰める
- 1日の投稿上限 X_MAX_POSTS_PER_DAY(デフォルト6件、PHT日付基準)。超過分はスキップしてログ
- data/posted_log.json で投稿済みIDを管理し重複投稿を防止(過去分は自動整理)
- 投稿失敗は exponential backoff で最大3回リトライ、それでも失敗ならログを残して
  正常終了(exit 0)。既存の予測・決済パイプラインを止めない
- DRY_RUN=1 なら投稿せず本文を標準出力に表示(認証情報・tweepy不要)

使い方: python -m src.post_to_x --mode prediction|result|weekly|all
認証: OAuth 1.0a User Context (Secrets: X_API_KEY, X_API_SECRET,
      X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET)
"""
import argparse
import json
import os
import sys
import re
import time
import unicodedata
from datetime import datetime, timezone, timedelta

from .config import MIN_EV, SPORTS
from .main import load_history, analytics

POSTED_LOG = "data/posted_log.json"
MAX_WEIGHTED_LEN = 280          # X の weighted length 上限(全角2/半角1)
MAX_PREDICTION_POSTS = 3        # 試合前予測はバリュー上位のみ(全件投稿しない)
PREDICTION_WINDOW_H = 36        # この時間内に生成された予測のみ「本日の予測」対象
RESULT_WINDOW_DAYS = 3          # この日数内キックオフの確定分のみ「本日の結果」対象
LOG_KEEP_DAYS = 90              # posted_log の保持期間(古いエントリは自動削除)
PHT = timezone(timedelta(hours=8))  # 既存システムに合わせフィリピン時間基準

# 将来の英語アカウント対応のためテンプレート文字列は分離しておく(実装は日本語のみ)
TEMPLATES = {
    "ja": {
        "pred_title": "🔮 本日の予測",
        "pred_body": "{emoji} {match}\n📊 モデル確率 {prob}% / オッズ換算 {imp}%\n💎 バリュー {pt:+d}pt → {market}: {pick}",
        "pred_footer": "全予測はGitHubで自動記録・後出しなし\nプロフィールのダッシュボードで公開中",
        "pred_tags": "#ブックメーカー #スポーツベッティング",
        "res_title": "📋 本日の結果",
        "res_body": "✅ {win}勝 ❌ {lose}敗{push}\n💰 {profit:+.1f} ユニット",
        "res_push": " ➖ {n}分",
        "res_highlight": "的中: {pick} @{odds}",
        "res_footer": "累計成績はダッシュボードで全公開",
        "res_tags": "#ブックメーカー",
        "wk_title": "📈 週間サマリー ({start}〜{end})",
        "wk_week": "今週: {win}勝{lose}敗 / 収支 {profit:+.1f}u / ROI {roi:+.1f}%",
        "wk_total": "累計: {win}勝{lose}敗 / ROI {roi:+.1f}% / 的中率 {hit:.1f}%",
        "wk_footer": "全履歴はプロフィールのダッシュボードから",
        "wk_tags": "#ブックメーカー #スポーツベッティング",
    },
}
T = TEMPLATES["ja"]

# リーグ表示名 → 投稿の絵文字(SPORTSの種別からサッカー/MLBを判定、他は個別対応)
_EMOJI_BY_LABEL = {"NBA": "🏀", "NFL": "🏈", "NHL": "🏒"}
_KIND_BY_LABEL = {label: kind for _, label, kind in SPORTS}


def _sport_emoji(league: str) -> str:
    if league in _EMOJI_BY_LABEL:
        return _EMOJI_BY_LABEL[league]
    return "⚾" if _KIND_BY_LABEL.get(league) == "mlb" else "⚽"


# ---------- 文字数・URL検証 ----------

def weighted_len(text: str) -> int:
    """Xのweighted length。全角(F/W)と絵文字は2、他は1。
    曖昧幅(A)も2で数える(過小評価して投稿拒否されるより安全側に倒す)"""
    total = 0
    for ch in text:
        if ch == "\n":
            total += 1
            continue
        if ord(ch) >= 0x1F000 or unicodedata.east_asian_width(ch) in "FWA":
            total += 2
        else:
            total += 1
    return total


# URLスキーム・www・裸ドメイン(Xが自動リンク化し$0.20課金になるもの)を検出
_URL_RE = re.compile(
    r"(?i)(?:https?://|www\.|\b[\w-]+\.(?:com|net|org|io|jp|me|dev|app|ai|gg|tv|xyz|info|co)\b)")


def contains_url(text: str) -> bool:
    return bool(_URL_RE.search(text))


def fit_tweet(paragraphs: list) -> str:
    """paragraphs: [(text, drop_order)]。drop_orderがNoneの段落は必須、
    数値の段落は280超過時に小さい順(ハッシュタグ→ハイライト→フッター)で削除。
    全部削っても超過する場合は末尾を切り詰めて「…」を付ける"""
    paras = [(t, d) for t, d in paragraphs if t]

    def render(ps):
        return "\n\n".join(t for t, _ in ps)

    text = render(paras)
    for order in sorted({d for _, d in paras if d is not None}):
        if weighted_len(text) <= MAX_WEIGHTED_LEN:
            break
        paras = [(t, d) for t, d in paras if d != order]
        text = render(paras)
    if weighted_len(text) > MAX_WEIGHTED_LEN:
        while text and weighted_len(text + "…") > MAX_WEIGHTED_LEN:
            text = text[:-1]
        text = text.rstrip() + "…"
    return text


# ---------- 投稿済みログ ----------

def load_log() -> dict:
    log = {}
    if os.path.exists(POSTED_LOG):
        with open(POSTED_LOG, encoding="utf-8") as f:
            log = json.load(f) or {}
    for k in ("predictions", "results", "weekly", "daily_counts"):
        log.setdefault(k, {})
    return log


def save_log(log: dict):
    # 古いエントリを整理してファイル肥大を防ぐ
    cutoff = (datetime.now(PHT) - timedelta(days=LOG_KEEP_DAYS)).strftime("%Y-%m-%d")
    for k in ("predictions", "results", "weekly", "daily_counts"):
        log[k] = {i: d for i, d in log[k].items() if d >= cutoff}
    with open(POSTED_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
        f.write("\n")


def _today(now) -> str:
    return now.astimezone(PHT).strftime("%Y-%m-%d")


def _daily_limit() -> int:
    return int(os.environ.get("X_MAX_POSTS_PER_DAY", "6"))


# ---------- 投稿文の生成 ----------

def _parse_dt(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def build_prediction_posts(history: list, log: dict, now) -> list:
    """未投稿の新しい予測からバリュー(EV)上位を選び [(row_id, text)] を返す。
    対象: 直近PREDICTION_WINDOW_H時間に生成され、未確定でキックオフ前、EV>=MIN_EV"""
    cands = []
    for r in history:
        if r["result"] or r["id"] in log["predictions"]:
            continue
        created = _parse_dt(r["created_utc"])
        kickoff = _parse_dt(r["kickoff_utc"])
        if not created or not kickoff:
            continue
        if kickoff <= now or now - created > timedelta(hours=PREDICTION_WINDOW_H):
            continue
        try:
            ev, prob, odds = float(r["ev"]), int(float(r["prob"])), float(r["odds"])
        except (TypeError, ValueError):
            continue
        if ev < MIN_EV:
            continue
        cands.append((ev, prob, odds, r))

    posts = []
    for ev, prob, odds, r in sorted(cands, key=lambda c: c[0], reverse=True)[:MAX_PREDICTION_POSTS]:
        imp = round(100 / odds)
        body = T["pred_body"].format(emoji=_sport_emoji(r["league"]), match=r["match"],
                                     prob=prob, imp=imp, pt=prob - imp,
                                     market=r["market"], pick=r["pick"])
        text = fit_tweet([(T["pred_title"], None), (body, None),
                          (T["pred_footer"], 2), (T["pred_tags"], 1)])
        posts.append((r["id"], text))
    return posts


def _pick_label(r) -> str:
    """ハイライト用のピック表記。勝敗系はチーム名だけだと文脈が無いので「勝利」を
    付け、O/U系は試合名を前置する。ハンディ等はピック自体にチーム名が入っている"""
    home_away = r["match"].split(" vs ")
    if r["pick"] in home_away:
        return f"{r['pick']} 勝利"
    if r["pick"].startswith(("オーバー", "アンダー")):
        return f"{r['match']} {r['pick']}"
    return r["pick"]


def build_result_post(history: list, log: dict, now):
    """新たに確定した予測をまとめて (row_ids, text) を返す。対象がなければNone。
    負けた日も必ず投稿する(信頼性が商品なのでスキップしない)"""
    rows = []
    for r in history:
        if r["result"] not in ("win", "lose", "push") or r["id"] in log["results"]:
            continue
        kickoff = _parse_dt(r["kickoff_utc"])
        if not kickoff or now - kickoff > timedelta(days=RESULT_WINDOW_DAYS):
            continue  # 初回実行時に過去の全確定分を遡って投稿しないためのガード
        rows.append(r)
    if not rows:
        return None

    win = [r for r in rows if r["result"] == "win"]
    lose = [r for r in rows if r["result"] == "lose"]
    push = [r for r in rows if r["result"] == "push"]
    profit = sum(float(r["profit"] or 0) for r in rows)
    body = T["res_body"].format(
        win=len(win), lose=len(lose), profit=profit,
        push=T["res_push"].format(n=len(push)) if push else "")

    highlight = ""
    if win:
        best = max(win, key=lambda r: float(r["odds"] or 0))
        highlight = T["res_highlight"].format(pick=_pick_label(best), odds=best["odds"])
    text = fit_tweet([(T["res_title"], None), (body, None),
                      (highlight, 2), (T["res_footer"], 3), (T["res_tags"], 1)])
    return [r["id"] for r in rows], text


def build_weekly_post(history: list, log: dict, now):
    """週次サマリー (week_key, text) を返す。投稿済み週ならNone。
    週の範囲は実行時点から遡って7日間(月曜朝JST実行想定=前週分)"""
    week_key = "{}-W{:02d}".format(*now.astimezone(PHT).isocalendar()[:2])
    if week_key in log["weekly"]:
        return None
    start = now - timedelta(days=7)

    week_rows = []
    for r in history:
        if r["result"] not in ("win", "lose", "push"):
            continue
        kickoff = _parse_dt(r["kickoff_utc"])
        if kickoff and start <= kickoff < now:
            week_rows.append(r)

    w_settled = [r for r in week_rows if r["result"] in ("win", "lose")]
    w_win = sum(1 for r in w_settled if r["result"] == "win")
    w_profit = sum(float(r["profit"] or 0) for r in week_rows)
    w_roi = w_profit / len(w_settled) * 100 if w_settled else 0.0

    overall = analytics(history)["overall"]
    s, e = start.astimezone(PHT), now.astimezone(PHT)
    text = fit_tweet([
        (T["wk_title"].format(start=f"{s.month}/{s.day}", end=f"{e.month}/{e.day}"), None),
        (T["wk_week"].format(win=w_win, lose=len(w_settled) - w_win,
                             profit=w_profit, roi=w_roi), None),
        (T["wk_total"].format(win=overall["win"], lose=overall["lose"],
                              roi=overall["roi"] or 0.0, hit=overall["hit"] or 0.0), None),
        (T["wk_footer"], 2), (T["wk_tags"], 1)])
    return week_key, text


def make_weekly_chart(history: list, path: str) -> bool:
    """全確定予想の累積損益(ユニット)の推移グラフPNGを生成。
    matplotlib不在や失敗時はFalseを返しテキストのみ投稿にフォールバック"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # 日単位(PHT)で集計して日次終値の累積を描く(同時刻の複数予想で
        # 縦ギザギザにならないように)
        by_day = {}
        for r in history:
            if r["result"] not in ("win", "lose", "push"):
                continue
            kickoff = _parse_dt(r["kickoff_utc"])
            if kickoff:
                day = kickoff.astimezone(PHT).date()
                by_day[day] = by_day.get(day, 0.0) + float(r["profit"] or 0)
        if len(by_day) < 2:
            return False
        dates, cum, total = [], [], 0.0
        for day in sorted(by_day):
            total += by_day[day]
            dates.append(day)
            cum.append(total)

        # 単系列ライン: 凡例なし(タイトルが系列名)、控えめグリッド、ゼロ基準線、
        # 終端の値を直接ラベル。日本語フォントがCIに無いためラベルは英語
        fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
        fig.patch.set_facecolor("#fcfcfb")
        ax.set_facecolor("#fcfcfb")
        ax.axhline(0, color="#c3c2b7", linewidth=1, zorder=1)
        ax.plot(dates, cum, color="#2a78d6", linewidth=2, zorder=3,
                marker="o", markersize=4 if len(dates) > 30 else 6)
        ax.annotate(f"{cum[-1]:+.1f}u", (dates[-1], cum[-1]),
                    xytext=(6, 0), textcoords="offset points",
                    color="#0b0b0b", fontsize=11, fontweight="bold", va="center")
        ax.set_title("Cumulative Units (all settled picks)",
                     color="#0b0b0b", fontsize=12, loc="left", pad=12)
        ax.grid(axis="y", color="#e8e7e2", linewidth=0.8, zorder=0)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#c3c2b7")
        ax.tick_params(colors="#52514e", labelsize=9, length=0)
        ax.margins(x=0.02)
        import matplotlib.dates as mdates
        ax.xaxis.set_major_locator(
            mdates.DayLocator(interval=max(1, (dates[-1] - dates[0]).days // 8)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        fig.autofmt_xdate(rotation=0, ha="center")
        fig.tight_layout()
        fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        return True
    except Exception as e:  # noqa: BLE001 - グラフ生成失敗は投稿自体を止めない
        print(f"[post_to_x] chart generation failed (text-only fallback): {e}")
        return False


# ---------- X API ----------

def _dry_run() -> bool:
    return os.environ.get("DRY_RUN", "").lower() not in ("", "0", "false")


def _credentials():
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    vals = [os.environ.get(k, "") for k in keys]
    return vals if all(vals) else None


def post_tweet(text: str, media_path: str = None) -> bool:
    """1件投稿。本文URL検証→(DRY_RUNなら表示のみ)→リトライ付き投稿。
    Trueなら投稿成功(=ログに記録してよい)"""
    if contains_url(text):
        print(f"[post_to_x] ERROR: URL detected in tweet body; skipped:\n{text}")
        return False
    if weighted_len(text) > MAX_WEIGHTED_LEN:  # fit_tweet後は起きないはずの最終ガード
        print(f"[post_to_x] ERROR: tweet exceeds weighted length; skipped:\n{text}")
        return False

    if _dry_run():
        print(f"[post_to_x] DRY_RUN (weighted_len={weighted_len(text)}"
              + (f", media={media_path}" if media_path else "") + "):\n"
              + "-" * 40 + f"\n{text}\n" + "-" * 40)
        return True

    creds = _credentials()
    if not creds:
        print("[post_to_x] X API secrets not configured; skipped")
        return False

    import tweepy
    k, s, at, ats = creds
    client = tweepy.Client(consumer_key=k, consumer_secret=s,
                           access_token=at, access_token_secret=ats)
    media_ids = None
    if media_path and os.path.exists(media_path):
        try:  # 画像添付はv1.1のmedia/uploadが必要(tweepy.APIを併用)
            api = tweepy.API(tweepy.OAuth1UserHandler(k, s, at, ats))
            media_ids = [api.media_upload(media_path).media_id]
        except Exception as e:  # noqa: BLE001
            print(f"[post_to_x] media upload failed (text-only fallback): {e}")

    for attempt in range(3):
        try:
            resp = client.create_tweet(text=text, media_ids=media_ids)
            print(f"[post_to_x] posted tweet id={resp.data['id']}")
            return True
        except Exception as e:  # noqa: BLE001
            wait = 2 ** (attempt + 1)
            print(f"[post_to_x] post failed (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(wait)
    print("[post_to_x] giving up after 3 attempts; will retry on next run")
    return False


# ---------- メイン ----------

def _cap_reached(log: dict, today: str) -> bool:
    if log["daily_counts"].get(today, 0) >= _daily_limit():
        print(f"[post_to_x] daily post limit ({_daily_limit()}) reached; skipping the rest")
        return True
    return False


def run(modes: list, now=None):
    now = now or datetime.now(timezone.utc)
    today = _today(now)
    history = load_history()
    log = load_log()
    changed = False

    for mode in modes:
        if mode == "prediction":
            posts = build_prediction_posts(history, log, now)
            if not posts:
                print("[post_to_x] no new value predictions; nothing to post")
            for row_id, text in posts:
                if _cap_reached(log, today):
                    break
                if post_tweet(text):
                    log["predictions"][row_id] = today
                    log["daily_counts"][today] = log["daily_counts"].get(today, 0) + 1
                    changed = True

        elif mode == "result":
            built = build_result_post(history, log, now)
            if not built:
                print("[post_to_x] no newly settled picks; nothing to post")
                continue
            row_ids, text = built
            if _cap_reached(log, today):
                continue
            if post_tweet(text):
                for i in row_ids:
                    log["results"][i] = today
                log["daily_counts"][today] = log["daily_counts"].get(today, 0) + 1
                changed = True

        elif mode == "weekly":
            built = build_weekly_post(history, log, now)
            if not built:
                print("[post_to_x] weekly summary already posted for this week")
                continue
            week_key, text = built
            if _cap_reached(log, today):
                continue
            chart = "weekly_roi.png"  # 一時ファイル(コミットされない)
            media = chart if make_weekly_chart(history, chart) else None
            if post_tweet(text, media_path=media):
                log["weekly"][week_key] = today
                log["daily_counts"][today] = log["daily_counts"].get(today, 0) + 1
                changed = True
            if media and os.path.exists(chart):
                os.remove(chart)

    if changed and not _dry_run():
        save_log(log)
        print(f"[post_to_x] updated {POSTED_LOG}")


def main():
    p = argparse.ArgumentParser(description="Post predictions/results to X")
    p.add_argument("--mode", required=True,
                   choices=["prediction", "result", "weekly", "all"])
    args = p.parse_args()
    modes = ["result", "prediction", "weekly"] if args.mode == "all" else [args.mode]
    try:
        run(modes)
    except Exception as e:  # noqa: BLE001 - 投稿処理の失敗でパイプラインを落とさない
        print(f"[post_to_x] ERROR: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
