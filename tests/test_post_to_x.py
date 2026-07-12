"""X自動投稿の単体テスト(tweepy・実APIキー不要、純粋関数と生成ロジックのみ)。
python tests/test_post_to_x.py で実行"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.post_to_x import (weighted_len, contains_url, fit_tweet,  # noqa: E402
                           build_prediction_posts, build_result_post,
                           build_weekly_post, MAX_WEIGHTED_LEN)
from src.main import FIELDS  # noqa: E402

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
EMPTY_LOG = {"predictions": {}, "results": {}, "weekly": {}, "daily_counts": {}}


def _row(**kw):
    r = {k: "" for k in FIELDS}
    r.update(id="ev123|勝敗", created_utc="2026-07-12T08:00",
             kickoff_utc="2026-07-12T18:00:00Z", league="MLB",
             match="New York Yankees vs Boston Red Sox", market="勝敗",
             pick="New York Yankees", prob="62", odds="1.80", ev="0.12")
    r.update(kw)
    return r


def test_weighted_len():
    assert weighted_len("abc") == 3          # 半角=1
    assert weighted_len("あいう") == 6        # 全角=2
    assert weighted_len("🔮") == 2            # 絵文字=2
    assert weighted_len("a\nあ") == 4         # 改行=1


def test_contains_url():
    assert contains_url("詳細は https://example.com で")
    assert contains_url("see www.example.jp")
    assert contains_url("dashboard: example.github.io")  # 裸ドメイン(自動リンク化)
    assert not contains_url("オッズ @1.85 で的中")        # 小数はURLではない
    assert not contains_url("全予測はGitHubで自動記録")   # 単語GitHubはOK
    assert not contains_url("St. Louis Cardinals vs Atlanta Braves")


def test_fit_tweet_trims_in_order():
    long_body = "あ" * 130  # 260 weighted
    text = fit_tweet([("タイトル", None), (long_body, None),
                      ("ハイライト行", 2), ("#タグ", 1)])
    assert weighted_len(text) <= MAX_WEIGHTED_LEN
    assert "#タグ" not in text          # まずハッシュタグが削られる
    assert "ハイライト行" not in text    # 次にハイライト
    assert "タイトル" in text            # 必須段落は残る

    short = fit_tweet([("タイトル", None), ("本文", None), ("#タグ", 1)])
    assert "#タグ" in short              # 収まる場合は削らない


def test_fit_tweet_truncates_required():
    text = fit_tweet([("あ" * 200, None)])  # 400 weighted の必須段落
    assert weighted_len(text) <= MAX_WEIGHTED_LEN
    assert text.endswith("…")


def test_prediction_posts():
    hist = [
        _row(id="a|勝敗", ev="0.12"),
        _row(id="b|勝敗", ev="0.30"),
        _row(id="c|勝敗", ev="0.05"),
        _row(id="d|勝敗", ev="0.08"),
        _row(id="e|勝敗", ev="0.01"),                        # EV<MIN_EV → 除外
        _row(id="f|勝敗", ev="0.50", result="win"),           # 確定済み → 除外
        _row(id="g|勝敗", ev="0.50", kickoff_utc="2026-07-12T10:00:00Z"),  # 開始済み → 除外
        _row(id="h|勝敗", ev="0.50", created_utc="2026-07-01T08:00"),      # 古い予測 → 除外
    ]
    posts = build_prediction_posts(hist, EMPTY_LOG, NOW)
    assert [i for i, _ in posts] == ["b|勝敗", "a|勝敗", "d|勝敗"]  # EV上位3件のみ
    for _, text in posts:
        assert weighted_len(text) <= MAX_WEIGHTED_LEN
        assert not contains_url(text)
        assert "🔮 本日の予測" in text and "バリュー" in text

    log = {**EMPTY_LOG, "predictions": {"b|勝敗": "2026-07-12"}}
    assert [i for i, _ in build_prediction_posts(hist, log, NOW)] == [
        "a|勝敗", "d|勝敗", "c|勝敗"]  # 投稿済みは重複しない


def test_result_post():
    hist = [
        _row(id="w1|勝敗", result="win", odds="1.90", profit="0.90",
             kickoff_utc="2026-07-12T02:00:00Z", pick="Real Madrid"),
        _row(id="w2|勝敗", result="win", odds="1.60", profit="0.60",
             kickoff_utc="2026-07-12T02:00:00Z"),
        _row(id="l1|勝敗", result="lose", profit="-1.00",
             kickoff_utc="2026-07-12T02:00:00Z"),
        _row(id="p1|勝敗", result="push", profit="0.00",
             kickoff_utc="2026-07-12T02:00:00Z"),
        _row(id="old|勝敗", result="win", profit="0.90",
             kickoff_utc="2026-07-01T02:00:00Z"),  # 窓の外(初回実行ガード) → 除外
    ]
    ids, text = build_result_post(hist, EMPTY_LOG, NOW)
    assert set(ids) == {"w1|勝敗", "w2|勝敗", "l1|勝敗", "p1|勝敗"}
    assert "✅ 2勝 ❌ 1敗 ➖ 1分" in text
    assert "+0.5 ユニット" in text           # 0.90+0.60-1.00+0.00
    assert "Real Madrid" in text             # 最高オッズの的中がハイライト
    assert weighted_len(text) <= MAX_WEIGHTED_LEN and not contains_url(text)

    # 全敗の日も必ず投稿する(スキップしない)
    ids2, text2 = build_result_post(
        [_row(id="l9|勝敗", result="lose", profit="-1.00",
              kickoff_utc="2026-07-12T02:00:00Z")], EMPTY_LOG, NOW)
    assert "✅ 0勝 ❌ 1敗" in text2

    # 投稿済みIDのみなら投稿しない
    log = {**EMPTY_LOG, "results": {i: "2026-07-12" for i in ids}}
    assert build_result_post(hist[:4], log, NOW) is None


def test_weekly_post():
    hist = [
        _row(id="w1|勝敗", result="win", profit="0.85",
             kickoff_utc="2026-07-10T02:00:00Z", prob="62"),
        _row(id="l1|勝敗", result="lose", profit="-1.00",
             kickoff_utc="2026-07-11T02:00:00Z", prob="58"),
    ]
    week_key, text = build_weekly_post(hist, EMPTY_LOG, NOW)
    assert week_key == "2026-W28"
    assert "📈 週間サマリー" in text and "今週: 1勝1敗" in text
    assert weighted_len(text) <= MAX_WEIGHTED_LEN and not contains_url(text)

    log = {**EMPTY_LOG, "weekly": {"2026-W28": "2026-07-12"}}
    assert build_weekly_post(hist, log, NOW) is None  # 同一週は重複しない


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
