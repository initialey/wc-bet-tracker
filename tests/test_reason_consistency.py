"""理由文とピックの整合性ガードのモックテスト(実APIキー・ネットワーク不要)。
python tests/test_reason_consistency.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import main  # noqa: E402

FACTS = [{"ja": "事実1", "en": "fact1"}, {"ja": "事実2", "en": "fact2"}]
MATCH = "England vs Argentina"


class _FakeAI:
    """main.ai を差し替えるスタブ。verdictsは check_verdict が返す値の列"""

    MODEL_LIGHT = "claude-haiku-4-5"

    def __init__(self, check_results, rewrite_ja="選択を支持する新しい結論",
                 rewrite_en="new supporting verdict", check_error=None):
        self.check_results = list(check_results)
        self.rewrite_ja, self.rewrite_en = rewrite_ja, rewrite_en
        self.check_error = check_error
        self.checked, self.rewrites = [], 0

    def check_verdict(self, key, match, market, pick, v_ja):
        if self.check_error:
            raise self.check_error
        self.checked.append(v_ja)
        return self.check_results.pop(0)

    def rewrite_verdict(self, key, match, market, pick, facts):
        self.rewrites += 1
        return {"ja": self.rewrite_ja, "en": self.rewrite_en}


def _run(fake, rj="堅い試合になりやすく合計2ゴール以下が有力／事実1／事実2",
         rje="low-scoring likely / fact1 / fact2", pick="オーバー1.5"):
    orig = main.ai
    main.ai = fake
    main.VERIFY_STATS.update(checks=0, regens=0, dropped=0)
    try:
        return main._verify_reason("key", MATCH, "O/U 1.5", pick, rj, rje, FACTS)
    finally:
        main.ai = orig


def test_consistent_reason_unchanged():
    """整合していれば理由文はそのまま(チェック1回のみ)"""
    fake = _FakeAI([True])
    rj, rje = _run(fake, rj="打ち合いが濃厚で2点以上が有力／事実1")
    assert rj == "打ち合いが濃厚で2点以上が有力／事実1"
    assert len(fake.checked) == 1 and fake.rewrites == 0


def test_inconsistent_rewritten():
    """矛盾検出 → 選択を明示した再生成が成功 → 新しい結論+事実で差し替え"""
    fake = _FakeAI([False, True])  # 元の文=矛盾 / 再生成文=整合
    rj, rje = _run(fake)
    assert rj == "選択を支持する新しい結論／事実1／事実2"
    assert rje.startswith("new supporting verdict")
    assert fake.rewrites == 1 and len(fake.checked) == 2
    assert main.VERIFY_STATS["dropped"] == 0


def test_still_inconsistent_published_without_verdict():
    """再生成2回でも矛盾 → 結論なし(事実のみ)で掲載"""
    fake = _FakeAI([False, False, False])
    rj, rje = _run(fake)
    assert rj == "事実1／事実2"          # 結論セグメントが落ちる
    assert rje == "fact1／fact2"
    assert fake.rewrites == 2 and main.VERIFY_STATS["dropped"] == 1


def test_check_failure_keeps_original():
    """検証API自体の失敗 → 元の理由文を維持(理由文が消えない安全側)"""
    fake = _FakeAI([], check_error=RuntimeError("api down"))
    rj, rje = _run(fake, rj="元の結論／事実1")
    assert rj == "元の結論／事実1"


def test_blend_guard_text_skipped():
    """_blend_reasonのコード生成文(定義上矛盾しない)はチェックせずスキップ"""
    fake = _FakeAI([])
    rj, _ = _run(fake, rj="AI単独では逆サイド寄りだが、市場オッズと統計を含めた"
                          "総合評価ではオーバー1.5が優勢の見立て／事実1")
    assert "AI単独では逆サイド寄り" in rj
    assert len(fake.checked) == 0 and main.VERIFY_STATS["checks"] == 0


def test_empty_reason_skipped():
    fake = _FakeAI([])
    assert _run(fake, rj="", rje="") == ("", "")
    assert len(fake.checked) == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
