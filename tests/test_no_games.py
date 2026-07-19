"""試合がない日の表示のモックテスト(3パターン: 全リーグ0件/一部のみ0件/待ちあり)。
python tests/test_no_games.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import dashboard  # noqa: E402
from src.main import analytics  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")
STATUS = [
    {"label": "プレミア", "kind": "soccer", "next": "2026-08-15T14:00:00+00:00"},
    {"label": "MLB", "kind": "mlb", "next": "2026-07-17T00:11:00+00:00"},
    {"label": "NBA", "kind": "2way", "next": None},
]


def _build(predictions, league_status):
    path = os.path.join(SCRATCH, "test_no_games.html")
    dashboard.build([], predictions, stats=analytics([]), league_status=league_status, path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    return page


def _pred_card(**kw):
    p = dict(kickoff="2026-07-16T03:00:00Z", match="England vs Argentina", league="W杯 2026",
             market="O/U 1.5", pick="オーバー1.5", prob=73, odds=1.43, ev=0.044,
             reason="結論／事実", reason_en="verdict／fact", kind="soccer")
    p.update(kw)
    return p


def test_all_leagues_no_games():
    """全リーグ0件 → 案内パネル(日英)+リーグ別の次の試合日。実績セクションは通常表示"""
    page = _build([], STATUS)
    assert "本日は分析対象の試合がありません" in page
    assert "No games to analyze today" in page
    # 次の試合日(PHT変換: 8/15 22:00 PHT, 7/17 08:11 PHT)
    assert "⚽ プレミア" in page and "次の試合: 8/15" in page
    assert "⚾ MLB" in page and "次の試合: 7/17" in page
    assert "🏀 NBA" in page and "オフシーズン/日程未取得" in page
    # 実績セクションは通常どおり表示される
    assert "区分別成績" in page and "確率のキャリブレーション検証" in page and "予想履歴" in page


def test_partial_league_no_games():
    """一部リーグのみ0件 → パネルは出さず、従来どおりカード+リーグ内お知らせ表示"""
    preds = [_pred_card(),
             dict(info_card=True, kind="mlb", kickoff="2026-07-17T00:11:00Z", match="MLB",
                  league="MLB", prob=0, market="情報", pick="mlb-next",
                  tag_ja="⚾ お知らせ", tag_en="⚾ Notice",
                  text_ja="現在試合がありません(オールスターブレイク等)。次の試合: 7/17(金)",
                  text_en="No games right now (All-Star break etc.). Next game: 7/17")]
    page = _build(preds, STATUS)
    assert "本日は分析対象の試合がありません" not in page
    assert "England vs Argentina" in page                     # 通常カード
    assert "現在試合がありません(オールスターブレイク等)" in page  # MLBタブ内のお知らせ


def test_pending_predictions_not_treated_as_no_games():
    """「待ち」の予想(分析済み・未確定)がある日は0件扱いにしない"""
    page = _build([_pred_card()], STATUS)
    assert "本日は分析対象の試合がありません" not in page
    assert "England vs Argentina" in page


def test_info_only_shows_panel():
    """お知らせカードしかない日(実予想0件)はパネルに切り替える"""
    only_info = [dict(info_card=True, kind="mlb", kickoff="2026-07-17T00:11:00Z", match="MLB",
                      league="MLB", prob=0, market="情報", pick="mlb-next",
                      tag_ja="⚾ お知らせ", tag_en="⚾ Notice",
                      text_ja="現在試合がありません", text_en="No games right now")]
    page = _build(only_info, STATUS)
    assert "本日は分析対象の試合がありません" in page


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
