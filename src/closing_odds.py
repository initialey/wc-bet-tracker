"""精密な締切オッズ取得(CLV計測精度向上用)。
analyze.ymlのメイン分析ジョブとは完全に分離した専用ワークフロー(.github/workflows/closing_odds.yml)
から高頻度(5分間隔)で実行し、キックオフまで残り僅か(既定10分以内)の未確定予想だけを
イベント単位でピンポイント取得してhistory.csvのclosing_odds列に書き込む。

既存の近似値(analyze.yml側が毎回の実行で上書きしていた「直近観測オッズ」)は
approx_closing_odds列に温存されており、このジョブとは独立して動き続ける
(cron欠落等でこちらが取りこぼした試合の予備データとして機能する)。

注意: GitHub Actionsの5分cronは混雑時に数分〜十数分遅延することがあるため
(analyze.ymlが00:00 UTCを避けているのと同じ理由)、「ちょうど5分前」は保証できない。
WINDOW_MINUTESを10分に広げてcronの実行タイミングのゆらぎを吸収している。
それでも取れなかった試合はclosing_odds空欄のままとなり、CLV集計から除外される
(近似値にフォールバックはしない=精密値のみを対象とする仕様のため)"""
import os
import sys
from datetime import datetime, timezone, timedelta

from . import odds_api
from .config import SPORTS, REGIONS, MLB_REGIONS
from .main import load_history, save_history, _closing_odds_for

WINDOW_MINUTES = 10  # キックオフまでこの分数以内の未確定予想を対象にする(cronの揺らぎを吸収)

KIND_BY_LEAGUE = {label: (key, kind) for key, label, kind in SPORTS}


def _regions_for(kind: str) -> str:
    return MLB_REGIONS if kind == "mlb" else REGIONS


def main():
    odds_key = os.environ["ODDS_API_KEY"]
    rows = load_history()
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(minutes=WINDOW_MINUTES)

    # (event_id, sport_key) ごとにまとめて、1試合1回のAPI呼び出しで済ませる
    targets = {}
    for r in rows:
        if r["result"] or r.get("closing_odds"):
            continue  # 確定済み、またはすでに精密値を取得済み
        try:
            kickoff = datetime.fromisoformat(r["kickoff_utc"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if not (now <= kickoff <= deadline):
            continue
        key_kind = KIND_BY_LEAGUE.get(r["league"])
        if not key_kind:
            print(f"[warn] closing_odds: unknown league '{r['league']}' for {r['match']}",
                  file=sys.stderr)
            continue
        sport_key, kind = key_kind
        ev_id = (r["id"] or "").split("|")[0]
        if not ev_id:
            continue
        targets.setdefault((ev_id, sport_key, kind), []).append(r)

    if not targets:
        print("[info] closing_odds: no pending predictions entering kickoff window")
        return

    updated = 0
    for (ev_id, sport_key, kind), grp in targets.items():
        try:
            ev = odds_api.get_event_odds(odds_key, sport_key, ev_id, _regions_for(kind))
        except Exception as e:
            print(f"[warn] closing_odds: fetch failed for {ev_id} ({sport_key}): {e}",
                  file=sys.stderr)
            continue
        for r in grp:
            co = _closing_odds_for(r, ev)
            if co:
                r["closing_odds"] = f"{co:.2f}"
                updated += 1
            else:
                print(f"[warn] closing_odds: no matching price for {r['match']} "
                      f"{r['market']} {r['pick']}", file=sys.stderr)

    save_history(rows)
    print(f"[info] closing_odds: captured for {updated}/{sum(len(g) for g in targets.values())} "
          f"pending rows across {len(targets)} events")


if __name__ == "__main__":
    main()
