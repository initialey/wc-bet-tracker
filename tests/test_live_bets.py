"""🎯 実弾候補(ライブベット候補)フィルタのモックテスト。
python tests/test_live_bets.py で実行"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import dashboard, notify  # noqa: E402
from src.config import is_live_bet, live_bet_lines, LIVE_BET_FILTERS  # noqa: E402
from src.main import FIELDS, analytics  # noqa: E402

SCRATCH = os.environ.get("TMPDIR", "/tmp")


def test_is_live_bet_matrix():
    """該当判定はLIVE_BET_FILTERSのみ参照(EV>=3%、かつexclude_markets非該当)。
    確率ベース(旧)と違い、対象は全スポーツ・全マーケットに拡大している"""
    assert is_live_bet("MLB", "ランライン", 0.03)           # 下限ちょうど
    assert is_live_bet("MLB", "ランライン", 0.05)
    assert not is_live_bet("MLB", "ランライン", 0.02)       # EV不足
    assert is_live_bet("MLB", "勝敗", 0.05)                 # 旧: 対象外マーケット→新: 対象
    assert is_live_bet("プレミア", "ランライン", 0.05)       # 旧: 対象外スポーツ→新: 対象
    assert not is_live_bet("プレミア", "90分勝敗", 0.05)     # 除外マーケット(サッカー90分勝敗)
    assert not is_live_bet("プレミア", "ハンディ +0.5", 0.05)  # 除外マーケット(サッカーハンディ+0.5)
    assert is_live_bet("プレミア", "ハンディ -0.5", 0.05)    # 除外は+0.5のみ、他ラインは対象
    assert not is_live_bet("プレミア", "コーナー(参考)", 0.05)  # 除外マーケット(未記録の参考市場)
    assert not is_live_bet("", "", None)                    # evなしでも落ちない
    # 条件はconfigの定義と一致(コード直書きしていないことの確認)
    assert LIVE_BET_FILTERS["min_ev"] == 0.03
    assert ("soccer", "90分勝敗") in LIVE_BET_FILTERS["exclude_markets"]
    assert ("soccer", "ハンディ +0.5") in LIVE_BET_FILTERS["exclude_markets"]


def test_is_live_bet_min_ev_env_override():
    """LIVE_BET_MIN_EV環境変数で閾値を上書きできる(閾値調整用)"""
    import importlib
    from src import config as config_mod
    old = os.environ.get("LIVE_BET_MIN_EV")
    os.environ["LIVE_BET_MIN_EV"] = "0.05"
    try:
        importlib.reload(config_mod)
        assert config_mod.LIVE_BET_FILTERS["min_ev"] == 0.05
        assert not config_mod.is_live_bet("MLB", "勝敗", 0.04)
        assert config_mod.is_live_bet("MLB", "勝敗", 0.05)
    finally:
        if old is None:
            os.environ.pop("LIVE_BET_MIN_EV", None)
        else:
            os.environ["LIVE_BET_MIN_EV"] = old
        importlib.reload(config_mod)   # 既定値(0.03)に戻す(他テストへの影響防止)


def test_live_bet_lines():
    """損益分岐 = 100÷補正後確率、合格ライン = ×1.02を小数2桁に切り上げ"""
    be, ok = live_bet_lines(62)
    assert abs(be - 1.6129) < 0.001    # 100/62
    assert ok == 1.65                  # 1.6129×1.02=1.6452 → 切り上げ1.65
    be60, ok60 = live_bet_lines(60)
    assert abs(be60 - 1.6667) < 0.001
    assert ok60 == 1.70                # 浮動小数の誤差で1.71に繰り上がらない
    _, ok70 = live_bet_lines(70)
    assert ok70 == 1.46                # 1.4286×1.02=1.4571 → 1.46


def _pred(**kw):
    p = {"kickoff": "2026-07-21T19:00:00Z", "match": "Yankees vs Red Sox",
         "league": "MLB", "market": "ランライン", "pick": "Yankees -1.5",
         "prob": 62, "odds": 1.65, "ev": 0.05, "reason": "理由",
         "reason_en": "reason", "kind": "mlb"}
    p.update(kw)
    return p


def _render(preds, hist=None):
    path = os.path.join(SCRATCH, "test_live_dash.html")
    hist = hist or []
    dashboard.build(hist, preds, stats=analytics(hist), path=path)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    os.remove(path)
    return page


def test_card_live_block_and_badge():
    """該当カードに損益分岐/合格ライン表示。取得オッズが合格ライン以上なら✅買い候補"""
    page = _render([_pred(odds=1.65)])   # 合格ライン1.65ちょうど → 買い候補
    assert '" data-live="1">' in page    # カード属性(JS内のセレクタ文字列と区別)
    assert "損益分岐 @1.61 / 合格ライン @1.65" in page
    assert "この値以上でのみベット" in page
    assert "✅" in page and "買い候補" in page
    assert "要オッズ確認" not in page

    page2 = _render([_pred(odds=1.64)])  # 境界の1つ下 → 要オッズ確認
    assert "要オッズ確認(取得時点では合格ライン未満)" in page2
    assert "✅" not in page2

    # オッズ変動があれば現在値(cur)で判定: 記録1.60でも現在1.66なら買い候補
    page3 = _render([_pred(odds=1.60, cur=1.66)])
    assert "買い候補" in page3 and "要オッズ確認" not in page3


def test_tab_and_empty_state():
    """実弾候補タブが先頭にあり、非該当のみの日は空メッセージを内蔵する"""
    page = _render([_pred(league="プレミア", market="90分勝敗", kind="soccer")])
    assert 'data-v="live"' in page
    gtabs = page[page.find('id="gtabs"'):]
    assert gtabs.find('data-v="live"') < gtabs.find('data-v="all"')  # カテゴリタブの先頭
    assert "🎯 実弾候補" in page and "🎯 Live Bets" in page
    assert "本日の実弾候補はありません" in page      # liveEmpty(JSでタブ選択時のみ表示)
    assert '" data-live="1">' not in page            # 非該当カードには付かない


def _hist(i, ev="", league="MLB", market="ランライン", **kw):
    r = {k: "" for k in FIELDS}
    r.update(id=f"lb{i}|{market}", created_utc="2026-07-01T00:00",
             kickoff_utc="2026-07-01T23:00:00Z", league=league,
             match=f"Team{i} vs TeamX", market=market,
             pick=f"Team{i} -1.5", prob="62", odds="1.80", ev=ev)
    r.update(kw)
    return r


def test_analytics_retroactive():
    """LIVE_BET_FILTERSを過去分に遡及適用して検証成績を集計する。
    対象は全スポーツ・全マーケットに拡大(EV>=3%)、exclude_markets該当のみ除外"""
    hist = [
        _hist(1, ev="0.05", result="win", profit="0.80"),        # MLBランライン、対象
        _hist(2, ev="0.05", result="lose", profit="-1.00"),      # MLBランライン、対象
        _hist(3, ev="0.05"),                                     # 待ち、対象
        _hist(4, ev="0.02", result="win", profit="0.80"),        # EV不足 → 対象外
        _hist(5, market="勝敗", ev="0.05",
              result="win", profit="0.80"),                      # 旧:対象外マーケット→新:対象
        _hist(6, league="プレミア", market="ランライン", ev="0.05",
              result="win", profit="0.80"),                      # 旧:対象外スポーツ→新:対象
        _hist(7, league="プレミア", market="90分勝敗", ev="0.05",
              result="lose", profit="-1.00"),                    # 除外マーケット → 対象外
        _hist(8, league="プレミア", market="ハンディ +0.5", ev="0.05",
              result="win", profit="0.80"),                      # 除外マーケット → 対象外
        _hist(9, league="プレミア", market="コーナー(参考)", ev="0.05",
              result="win", profit="0.80"),                      # 除外マーケット → 対象外
    ]
    lb = analytics(hist)["live_bets"]
    # 対象: 1,2,3,5,6 (n=4件確定=win3/lose1、pending1)
    assert lb["n"] == 4 and lb["win"] == 3 and lb["lose"] == 1 and lb["pending"] == 1
    assert lb["total"] == 5
    assert abs(lb["profit"] - 1.40) < 1e-9
    # 該当0件の履歴でも落ちない(evなし=対象外)
    lb0 = analytics([_hist(9, market="勝敗")])["live_bets"]
    assert lb0["total"] == 0


def test_dashboard_mroi_live_row():
    """マーケット別成績の先頭に実弾候補条件該当分(遡及)の行が出る"""
    hist = [_hist(i, ev="0.05", result="win", profit="0.80") for i in range(3)]
    page = _render([], hist=hist)
    assert "実弾候補条件該当分" in page
    assert "EV3%以上" in page
    assert "コーナーは除く" in page
    # 該当0件なら行を出さない(evなし=対象外)
    page0 = _render([], hist=[_hist(9, market="勝敗", result="win", profit="0.80")])
    assert "実弾候補条件該当分" not in page0


def test_notify_live_section():
    """通知冒頭に実弾候補セクション(件数・合格ライン)、0件日は1行だけ"""
    sent = []
    orig = notify.post
    notify.post = lambda t: sent.append(t)
    try:
        cand = {"match": "Yankees vs Red Sox", "pick": "Yankees -1.5", "prob": 62}
        pick = {"league": "MLB", "match": "A vs B", "pick": "A", "prob": 65,
                "odds": 1.70}
        notify.send([pick], live=[cand])
        assert "🎯 実弾候補 1件" in sent[-1]
        assert "合格ライン@1.65" in sent[-1]
        assert "補正後62%" in sent[-1]
        assert "本日のAIベット予想" in sent[-1]      # 既存セクションは維持

        notify.send([pick], live=[])
        assert "本日の実弾候補なし" in sent[-1]
        assert "実弾候補 " not in sent[-1].split("\n")[0] or "なし" in sent[-1]

        n_before = len(sent)
        notify.send([], live=[])                     # 両方空 → 通知なし
        assert len(sent) == n_before

        notify.send([pick])                          # live未指定 → 従来通り
        assert "実弾候補" not in sent[-1]
    finally:
        notify.post = orig


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(fns)} tests passed")
