"""根拠となる事実の上限(最大3点・各1行・平易な日本語)のモックテスト。
python tests/test_facts_limit.py で実行"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import ai, dashboard, main  # noqa: E402
from src.main import analytics  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")


def test_reason_text_caps_at_three():
    """AIが4点以上返してもコード側で3点に切り詰める(結論+事実3点)"""
    facts = [{"ja": f"事実{i}", "en": f"fact{i}"} for i in range(1, 6)]
    ja, en = main._reason_text("結論", "verdict", facts)
    assert ja == "結論／事実1／事実2／事実3"
    assert en == "verdict／fact1／fact2／fact3"
    # 3点以下はそのまま
    ja2, _ = main._reason_text("結論", "verdict", facts[:2])
    assert ja2 == "結論／事実1／事実2"
    assert main.MAX_FACTS == 3


def test_reason_html_displays_max_three():
    """過去に長く記録された予想も表示上は事実3点まで"""
    ja = "結論／" + "／".join(f"事実{i}" for i in range(1, 7))
    en = "verdict／" + "／".join(f"fact{i}" for i in range(1, 7))
    out = dashboard._reason_html(ja, en)
    assert out.count("<li") == 3
    assert "事実3" in out and "事実4" not in out
    assert 'data-en="fact3"' in out and "fact4" not in out
    # 結論は従来通り表示される
    assert "結論" in out and "根拠となる事実" in out


def test_card_render_old_long_record():
    """実カード描画でも過去記録由来の長い根拠が3点に丸まる(記録自体は不変)"""
    pred = {"kickoff": "2026-07-21T19:00:00Z", "match": "A vs B", "league": "MLB",
            "market": "勝敗", "pick": "A", "prob": 60, "odds": 1.80, "ev": 0.05,
            "kind": "mlb",
            "reason": "Aが有力／" + "／".join(f"詳細データ{i}" for i in range(1, 9)),
            "reason_en": "A likely／" + "／".join(f"detail{i}" for i in range(1, 9))}
    path = os.path.join(SCRATCH, "test_facts_dash.html")
    dashboard.build([], [pred], stats=analytics([]), path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    assert "詳細データ3" in page and "詳細データ4" not in page


def test_prompts_instruct_plain_three_facts():
    """全分析プロンプトが「最大3点・平易な表現」を指示している"""
    assert "最大3つ" in ai.RULES and "誰でも分かる" in ai.RULES
    # サッカー/MLBの固定ルールはプロンプトキャッシング用にSOCCER_SYSTEM/MLB_SYSTEM
    # (システムブロック側)に分離されているため、そちらを検査する
    for text in (ai.SOCCER_SYSTEM, ai.MLB_SYSTEM):
        assert "最大3個" in text
        assert "誰でも分かる" in text
        assert "専門用語" in text
    # 旧指示(5〜8個)が残っていないこと
    full = inspect.getsource(ai)
    assert "5〜8個" not in full


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
