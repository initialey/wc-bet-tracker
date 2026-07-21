"""API費用削減(プロンプトキャッシング・二段階スクリーニング・検索絞り込み・コストレポート)の
モックテスト(実APIキー・ネットワーク不要)。
python tests/test_cost_reduction.py で実行"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import ai, main  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")


class _FakeResp:
    def __init__(self, payload: dict, usage: dict = None):
        self._data = {"content": [{"type": "text", "text": json.dumps(payload)}],
                      "usage": usage or {"input_tokens": 100, "output_tokens": 50,
                                        "cache_creation_input_tokens": 0,
                                        "cache_read_input_tokens": 0}}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _patch_post(fn):
    """ai.requests.post を差し替え、呼び出しごとのbody(kwargs["json"])を記録する"""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return fn(json)

    orig = ai.requests.post
    ai.requests.post = fake_post
    return calls, lambda: setattr(ai.requests, "post", orig)


def _reset_usage_log():
    ai.USAGE_LOG.clear()


# ---------- 1. プロンプトキャッシング ----------

def test_analyze_match_caches_static_rules_only():
    """analyze_matchはsystemブロック(cache_control付き)に固定ルールを分離し、
    チーム名・キックオフなど動的な部分はuserメッセージ側にのみ含める"""
    _reset_usage_log()
    calls, restore = _patch_post(lambda body: _FakeResp(
        {"facts": [], "h2h": {"home": 50, "draw": 25, "away": 25,
                              "verdict_ja": "v", "verdict_en": "v"},
         "xg": {"home": 1.3, "away": 1.2}, "market_verdicts": {}, "corners": {}, "news": ""}))
    try:
        ai.analyze_match("key", "Arsenal", "Chelsea", "2026-07-21T19:00:00Z")
    finally:
        restore()
    assert len(calls) == 1
    body = calls[0]
    assert "system" in body and len(body["system"]) == 1
    sys_block = body["system"][0]
    assert sys_block["cache_control"] == {"type": "ephemeral"}
    assert "Arsenal" not in sys_block["text"] and "Chelsea" not in sys_block["text"]
    user_msg = body["messages"][0]["content"]
    assert "Arsenal" in user_msg and "Chelsea" in user_msg
    assert body["tools"][0]["max_uses"] == 5   # 検索絞り込みでmax_usesを引き下げ


def test_analyze_mlb_caches_static_rules_only():
    """analyze_mlbもStats APIデータ(試合ごとに変わる)はuserメッセージ側、
    ルール・出力形式はsystem(キャッシュ対象)に分離する"""
    _reset_usage_log()
    ctx = {"home": "Yankees", "away": "Red Sox", "venue": "Yankee Stadium",
           "home_pitcher": {"name": "Cole"}, "away_pitcher": {"name": "Crawford"},
           "home_form": {}, "away_form": {}}
    calls, restore = _patch_post(lambda body: _FakeResp(
        {"facts": [], "win": {"home": 55, "away": 45, "verdict_ja": "v", "verdict_en": "v"},
         "total": {"expected": 8.5, "verdict_ja": "v", "verdict_en": "v"},
         "runline": {"fav_cover": 50, "verdict_ja": "v", "verdict_en": "v"}, "news": ""}))
    try:
        ai.analyze_mlb("key", ctx, 8.5, "Yankees")
    finally:
        restore()
    body = calls[0]
    sys_text = body["system"][0]["text"]
    assert "Yankees" not in sys_text and "Cole" not in sys_text
    assert "Yankees" in body["messages"][0]["content"]
    assert "Cole" in body["messages"][0]["content"]
    assert body["tools"][0]["max_uses"] == 3


def test_analyze_generic_system_varies_by_three_way():
    """引分の有無(three_way)でJSON形状が変わるため、system文言もそれに応じて変わる
    (同じ組み合わせの試合が連続する間はキャッシュがヒットする設計)"""
    _reset_usage_log()
    calls, restore = _patch_post(lambda body: _FakeResp(
        {"win": {"home": 55, "away": 45, "reason": "r", "reason_en": "r"},
         "total": {"expected": 220, "reason": "r", "reason_en": "r"}, "news": ""}))
    try:
        ai.analyze_generic("key", "NBA", "Lakers", "Celtics", "2026-07-21T19:00:00Z", False, 220)
        ai.analyze_generic("key", "NBA", "Lakers", "Celtics", "2026-07-21T19:00:00Z", True, 220)
    finally:
        restore()
    two_way_sys = calls[0]["system"][0]["text"]
    three_way_sys = calls[1]["system"][0]["text"]
    assert '"draw"' not in two_way_sys.replace(" ", "") or "draw" not in two_way_sys
    assert '"draw"' in three_way_sys.replace(" ", "")
    assert "Lakers" not in two_way_sys   # チーム名は動的部分(userメッセージ)のみ
    assert "Lakers" in calls[0]["messages"][0]["content"]


def test_usage_logged_and_summarized():
    """API呼び出しのusage(cache_read/write含む)がUSAGE_LOGに蓄積され、
    label×model単位で集計できる(コストレポート用)"""
    _reset_usage_log()
    usages = [
        {"input_tokens": 50, "output_tokens": 20,
         "cache_creation_input_tokens": 800, "cache_read_input_tokens": 0},
        {"input_tokens": 50, "output_tokens": 20,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 800},
    ]
    calls, restore = _patch_post(lambda body: _FakeResp(
        {"facts": [], "win": {"home": 55, "away": 45, "verdict_ja": "v", "verdict_en": "v"},
         "total": {"expected": 8, "verdict_ja": "v", "verdict_en": "v"},
         "runline": {"fav_cover": 50, "verdict_ja": "v", "verdict_en": "v"}, "news": ""},
        usage=usages.pop(0)))
    ctx = {"home": "A", "away": "B", "home_pitcher": {}, "away_pitcher": {},
           "home_form": {}, "away_form": {}}
    try:
        ai.analyze_mlb("key", ctx, 8, "A")
        ai.analyze_mlb("key", ctx, 8, "A")
    finally:
        restore()
    summary = ai.usage_summary()
    key = ("analyze_mlb", ai.MODEL if hasattr(ai, "MODEL") else None)
    # labelでの集計を確認(モデル名はconfig.MODELそのもの)
    matches = [v for (label, _), v in summary.items() if label == "analyze_mlb"]
    assert len(matches) == 1
    agg = matches[0]
    assert agg["calls"] == 2
    assert agg["cache_creation_input_tokens"] == 800
    assert agg["cache_read_input_tokens"] == 800   # 2件目でキャッシュヒット


# ---------- 2. 二段階スクリーニング ----------

def test_screen_match_proceed_false_on_lopsided_market():
    """市場が一方的(暗示勝率97%以上)と判定されればproceed=falseが返る"""
    _reset_usage_log()
    calls, restore = _patch_post(lambda body: _FakeResp(
        {"proceed": False, "reason_ja": "市場が一方的", "reason_en": "lopsided market"}))
    try:
        out = ai.screen_match("key", "MLB", "A vs B", 98, 12)
    finally:
        restore()
    assert out == {"proceed": False, "reason_ja": "市場が一方的", "reason_en": "lopsided market"}
    # 検索なし(Haikuの軽量呼び出し)
    assert "tools" not in calls[0]
    assert calls[0]["model"] == ai.MODEL_LIGHT


def test_screen_match_defaults_to_proceed_on_missing_key():
    """AIの応答にproceedキーが欠けていても安全側(proceed=True=分析継続)にフォールバックする"""
    _reset_usage_log()
    calls, restore = _patch_post(lambda body: _FakeResp({"reason_ja": "判定不能"}))
    try:
        out = ai.screen_match("key", "MLB", "A vs B", None, 5)
    finally:
        restore()
    assert out["proceed"] is True


def test_screen_wrapper_fails_open_on_error():
    """main._screen: screen_match自体が例外を投げても分析を継続する(カバレッジを失わない)"""
    orig = main.ai.screen_match

    def boom(*a, **kw):
        raise RuntimeError("network down")
    main.ai.screen_match = boom
    try:
        out = main._screen("key", "MLB", "A vs B", 90, 10)
    finally:
        main.ai.screen_match = orig
    assert out == {"proceed": True, "reason_ja": "", "reason_en": ""}


def test_fav_prob_devigs_best_odds():
    """_fav_probは市場最良オッズをdevigしてfavorite側の暗示勝率(%)を返す"""
    # 1.50/2.60/4.00 の暗示確率(正規化後)のうち最大値が返るはず: 1/1.5=0.667を
    # 合計(0.667+0.385+0.25=1.301)で正規化 → 約51.2%
    p = main._fav_prob({"A": 1.50, "B": 2.60, "Draw": 4.00})
    assert p is not None and abs(p - 51.2) < 0.5
    assert main._fav_prob({"A": 1.80}) is None      # 1択のみ(devig不可)
    assert main._fav_prob({}) is None


# ---------- 3. スクリーニング除外ログ(理由付き追跡) ----------

def test_screening_log_roundtrip_and_pruning():
    """一次スクリーニングで除外した試合を理由付きで記録し、keep_daysより古い分は自動整理する。
    history.csv(答え合わせ対象の記録)とは別ファイルに保持する"""
    path = os.path.join(SCRATCH, "test_screening_log.json")
    if os.path.exists(path):
        os.remove(path)
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    old = now - timedelta(days=40)
    entries = [
        {"checked_utc": old.strftime("%Y-%m-%dT%H:%M"), "league": "MLB",
         "match": "Old vs Game", "kind": "mlb", "reason": "古い記録"},
        {"checked_utc": now.strftime("%Y-%m-%dT%H:%M"), "league": "MLB",
         "match": "A vs B", "kind": "mlb", "reason": "市場が一方的"},
    ]
    main._save_screening_log(entries, now, path=path, keep_days=30)
    loaded = main._load_screening_log(path=path)
    assert len(loaded) == 1 and loaded[0]["match"] == "A vs B"
    os.remove(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
