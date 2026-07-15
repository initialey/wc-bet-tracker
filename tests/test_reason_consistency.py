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


def test_ou_verdict_all_lines_consistent():
    """O/U結論のライン別コード生成: 全ライン×全確率帯×全展開でピックと矛盾しない"""
    for line in (1.5, 2.5, 3.5):
        for prob in (0.73, 0.65, 0.57, 0.52):   # 70%+/60-69/55-59/僅差
            for low in (True, False, None):
                k_o, k_u = int(line + 0.5), int(line - 0.5)
                ja, en = main._ou_verdict(f"オーバー{line}", prob, low)
                assert f"合計{k_o}点には届く" in ja and "以下に収まる" not in ja, (line, prob, low, ja)
                assert f"{k_o}+" in en and "fewer" not in en
                ja_u, en_u = main._ou_verdict(f"アンダー{line}", prob, low)
                assert f"合計{k_u}点以下に収まる" in ja_u and "届く" not in ja_u, (line, prob, low, ja_u)
                assert "fewer" in en_u and "+" not in en_u.replace("Very close, but ", "")


def test_ou_verdict_reported_case():
    """報告事例の再現: オーバー1.5(73%)×ロースコア見込み。
    旧実装は「合計2ゴール以下が有力」(アンダー側)を流用していた"""
    ja, en = main._ou_verdict("オーバー1.5", 0.73, True)
    assert ja == "点は少なめの見込みだが、合計2点には届く確率が高い見立て"
    assert en == "A low-scoring game is expected, but the total reaching 2+ goals looks likely"
    # アンダー2.5×ロースコアはアンダー側の文になる
    ja2, _ = main._ou_verdict("アンダー2.5", 0.65, True)
    assert ja2 == "ロースコア想定で、合計2点以下に収まる見込み"


def test_replace_verdict_keeps_facts():
    """表示時の結論差し替え: 結論セグメントのみ交換し事実欄は維持"""
    rj, rje = main._replace_verdict("矛盾した旧結論／事実1／事実2",
                                    "old verdict／fact1／fact2", "新結論", "new verdict")
    assert rj == "新結論／事実1／事実2"
    assert rje == "new verdict／fact1／fact2"
    assert main._replace_verdict("", "", "v", "ve") == ("v", "ve")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
