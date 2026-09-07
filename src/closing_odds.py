"""締切オッズ取得(CLV計測用)。analyze.ymlのメイン分析ジョブとは完全に分離した専用ワークフロー
(.github/workflows/closing_odds.yml)から実行する。

背景: GitHub Actionsはこのリポジトリの5分cronを数時間おき(実測で中央値2.4時間、最長11時間)に
しか起動しないため、「cronで直前だけ狙い撃ち」では試合の大半を取り逃していた(稼働後の取得率16%)。
そこでワークフロー側は1回の実行を最大約5.7時間の常駐ループにし、5分ごとにこのモジュールを
1回呼ぶ(capture_once)。取得窓(キックオフまでCAPTURE_LEAD_MIN分以内)に入った未取得の予想だけを
イベント単位でピンポイント取得するため、対象がない反復はAPI呼び出しゼロ。

記録する列: closing_odds(締切オッズ)・closing_odds_at(取得UTC時刻)・closing_lead_min(取得時点で
キックオフまでの分数)・closing_source(live=このジョブ / backfill=historical APIで遡及 /
missed=取り逃し)。「賭け時オッズ」(odds)・「賭けた時刻」(created_utc)・「試合開始時刻」(kickoff_utc)は
記録時に必ず入る既存列。

取得失敗・取り逃しはTelegram(notify.post。closing_odds.ymlはTELEGRAM_*のみ渡す)へ通知する。
同じ試合の失敗を5分ごとに繰り返し通知しないよう、実行(ランナー)内の通知済み状態を
CLOSING_ERR_STATEファイルに持つ。取り逃しはclosing_source=missedで記録されるため二重通知しない。

遡及取得(--backfill): The Odds APIのhistoricalエンドポイント(有料プランのみ、
1リクエスト=10クレジット×市場数×リージョン数)でキックオフ直前スナップショットを取得する。
クレジット上限(--budget)で止まり、新しい試合から優先。closing_source=backfill"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

from . import odds_api, notify
from .config import SPORTS, REGIONS, MLB_REGIONS
from .main import load_history, save_history, _closing_odds_for

CAPTURE_LEAD_MIN = 15    # キックオフまでこの分数以内に入ったら取得(=締切オッズとして扱う最終観測)
MISSED_GRACE_MIN = 5     # キックオフからこの分数を過ぎても未取得なら「取り逃し」確定
MISSED_LOOKBACK_H = 12   # 取り逃し検出の遡り範囲。より古い未取得分は遡及取得(backfill)の対象
BACKFILL_SNAPSHOT_MIN = 5   # 遡及取得で参照するスナップショット: キックオフのこの分数前
HISTORICAL_CREDITS_PER_MARKET = 10   # historicalエンドポイントのコスト(通常の10倍)

KIND_BY_LEAGUE = {label: (key, kind) for key, label, kind in SPORTS}
ERR_STATE = os.environ.get("CLOSING_ERR_STATE", "/tmp/closing_odds_notified.json")


def _regions_for(kind: str) -> str:
    return MLB_REGIONS if kind == "mlb" else REGIONS


def _kickoff(r):
    try:
        return datetime.fromisoformat(r["kickoff_utc"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None


def _event_key(r):
    """(event_id, sport_key, kind) を返す。リーグ不明・ID欠損はNone"""
    key_kind = KIND_BY_LEAGUE.get(r["league"])
    ev_id = (r["id"] or "").split("|")[0]
    if not key_kind or not ev_id:
        return None
    return (ev_id,) + key_kind


def _load_err_state() -> set:
    try:
        with open(ERR_STATE, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def _save_err_state(keys: set):
    try:
        with open(ERR_STATE, "w", encoding="utf-8") as f:
            json.dump(sorted(keys), f)
    except OSError:
        pass


def _apply(r, co, now, kickoff, source):
    r["closing_odds"] = f"{co:.2f}"
    r["closing_odds_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    r["closing_lead_min"] = str(int(round((kickoff - now).total_seconds() / 60)))
    r["closing_source"] = source


def capture_once(now=None, notify_fn=notify.post) -> dict:
    """取得窓に入った未取得の予想の締切オッズを取得し、取り逃しを検出してhistory.csvを更新する。
    戻り値は集計(captured/targets/events/errors/missed)。ワークフローのループから5分ごとに呼ばれる"""
    odds_key = os.environ["ODDS_API_KEY"]
    now = now or datetime.now(timezone.utc)
    rows = load_history()
    deadline = now + timedelta(minutes=CAPTURE_LEAD_MIN)
    missed_from = now - timedelta(hours=MISSED_LOOKBACK_H)
    missed_until = now - timedelta(minutes=MISSED_GRACE_MIN)

    targets, missed = {}, []
    for r in rows:
        if r.get("closing_odds") or r.get("closing_source"):
            continue
        ko = _kickoff(r)
        if ko is None:
            continue
        if now <= ko <= deadline:
            key = _event_key(r)
            if key is None:
                print(f"[warn] closing_odds: unknown league '{r['league']}' for {r['match']}",
                      file=sys.stderr)
                continue
            targets.setdefault(key, []).append(r)
        elif missed_from <= ko <= missed_until:
            r["closing_source"] = "missed"
            missed.append(r)

    stats = {"captured": 0, "targets": sum(len(g) for g in targets.values()),
             "events": len(targets), "errors": [], "missed": len(missed)}
    for (ev_id, sport_key, kind), grp in targets.items():
        ko = _kickoff(grp[0])
        try:
            ev, used = odds_api.get_closing_event_odds(odds_key, sport_key, ev_id,
                                                       _regions_for(kind), kind)
        except Exception as e:
            print(f"[warn] closing_odds: fetch failed for {ev_id} ({sport_key}): {e}",
                  file=sys.stderr)
            stats["errors"].append((ev_id, grp[0]["match"], str(e)[:120]))
            continue
        for r in grp:
            co = _closing_odds_for(r, ev)
            if co:
                _apply(r, co, now, ko, "live")
                stats["captured"] += 1
            else:
                print(f"[warn] closing_odds: no matching price for {r['match']} "
                      f"{r['market']} {r['pick']} (markets={used})", file=sys.stderr)

    if stats["captured"] or missed:
        save_history(rows)

    # 通知: 取り逃しは1回(missedとして記録済み)、取得失敗は同じ試合を繰り返さない
    lines = []
    if missed:
        ev_names = sorted({f"{r['league']} {r['match']}" for r in missed})
        lines.append(f"⚠️ 締切オッズ取り逃し {len(missed)}件({len(ev_names)}試合)")
        lines += [f"・{n}" for n in ev_names[:8]]
    if stats["errors"]:
        seen = _load_err_state()
        new = [e for e in stats["errors"] if e[0] not in seen]
        if new:
            lines.append(f"❌ 締切オッズ取得失敗 {len(new)}試合")
            lines += [f"・{m}: {err}" for _, m, err in new[:5]]
            _save_err_state(seen | {e[0] for e in new})
    if lines:
        notify_fn("\n".join(lines))

    print(f"[info] closing_odds: captured {stats['captured']}/{stats['targets']} rows "
          f"across {stats['events']} events, errors={len(stats['errors'])}, "
          f"missed={stats['missed']}, quota_remaining={odds_api.QUOTA['remaining']}")
    return stats


def backfill(budget: int, days: int, markets: str = "core", now=None,
             notify_fn=notify.post) -> dict:
    """過去分の締切オッズをhistoricalエンドポイントで遡及取得する(手動実行)。
    対象: closing_odds未取得・キックオフ済み・days日以内。新しい試合から順に、
    推定クレジット消費がbudgetに達したら停止。失敗した試合はclosing_source=backfill_failedで
    記録し、再実行時に同じ試合でクレジットを浪費しない"""
    odds_key = os.environ["ODDS_API_KEY"]
    now = now or datetime.now(timezone.utc)
    rows = load_history()
    since = now - timedelta(days=days)
    groups = {}
    for r in rows:
        if r.get("closing_odds") or r.get("closing_source") in ("backfill_failed",):
            continue
        ko = _kickoff(r)
        if ko is None or not (since <= ko < now):
            continue
        key = _event_key(r)
        if key:
            groups.setdefault(key, []).append(r)
    ordered = sorted(groups.items(), key=lambda kv: _kickoff(kv[1][0]), reverse=True)

    stats = {"events": 0, "captured": 0, "spent": 0, "failed": 0, "skipped_budget": 0}
    for (ev_id, sport_key, kind), grp in ordered:
        mk = (odds_api.CLOSING_MARKETS_CORE if markets == "core"
              else odds_api.CLOSING_MARKETS.get(kind, odds_api.CLOSING_MARKETS_CORE))
        cost = HISTORICAL_CREDITS_PER_MARKET * len(mk.split(","))
        if stats["spent"] + cost > budget:
            stats["skipped_budget"] += 1
            continue
        ko = _kickoff(grp[0])
        snap_at = ko - timedelta(minutes=BACKFILL_SNAPSHOT_MIN)
        stats["events"] += 1
        stats["spent"] += cost
        try:
            ev, snap_ts = odds_api.get_historical_event_odds(
                odds_key, sport_key, ev_id, _regions_for(kind), mk,
                snap_at.strftime("%Y-%m-%dT%H:%M:%SZ"))
        except Exception as e:
            print(f"[warn] backfill: fetch failed for {ev_id} ({sport_key}): {e}", file=sys.stderr)
            stats["failed"] += 1
            for r in grp:
                r["closing_source"] = "backfill_failed"
            continue
        at = snap_ts or snap_at
        for r in grp:
            co = _closing_odds_for(r, ev)
            if co:
                _apply(r, co, at, ko, "backfill")
                stats["captured"] += 1
    save_history(rows)
    msg = (f"📚 締切オッズ遡及取得: {stats['captured']}件取得 / {stats['events']}試合照会 / "
           f"失敗{stats['failed']} / 推定{stats['spent']}クレジット消費"
           f"(予算超過で見送り{stats['skipped_budget']}試合)")
    print("[info] " + msg)
    if stats["events"]:
        notify_fn(msg)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description="締切オッズ取得(1回分)/遡及取得")
    ap.add_argument("--backfill", action="store_true", help="historical APIで過去分を遡及取得")
    ap.add_argument("--budget", type=int, default=2000, help="遡及取得の推定クレジット上限")
    ap.add_argument("--days", type=int, default=30, help="遡及取得の対象期間(日)")
    ap.add_argument("--markets", choices=["core", "full"], default="core",
                    help="遡及取得の市場: core=h2h,totals,spreads(30クレジット/試合) / full=種別の全市場")
    a = ap.parse_args(argv)
    if a.backfill:
        backfill(a.budget, a.days, a.markets)
    else:
        capture_once()


if __name__ == "__main__":
    main()
