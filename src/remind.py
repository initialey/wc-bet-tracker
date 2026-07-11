"""試合開始 約1時間前の通知。

data/history.csv の未確定予想から「開始まで LEAD_MIN 分以内」の有力予想(本命・有力)を
試合ごとにまとめ、Slack/Discord へ通知する。GitHub Actions で30分ごとに実行する想定。
data/notified.json に通知済みキーを記録して二重通知を防ぐ(過去分は自動整理)。
時刻表示はフィリピン時間(PHT, UTC+8)。
"""
import csv
import json
import os
from datetime import datetime, timezone, timedelta

from . import notify
from .config import PROB_SUISHO

HISTORY = "data/history.csv"
STATE = "data/notified.json"
PHT = timezone(timedelta(hours=8))
LEAD_MIN = 90   # 開始まで何分以内で通知するか(30分間隔cron想定で≒1時間前)


def _load_notified() -> set:
    try:
        with open(STATE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_notified(ids: set):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=0)


def _fmt_pht(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PHT).strftime("%m/%d %H:%M")
    except Exception:
        return iso


def _prune(notified: set, now: datetime) -> set:
    """キックオフが1日以上過去のキーは破棄してファイルの肥大を防ぐ。"""
    keep = set()
    for k in notified:
        iso = k.rsplit("|", 1)[-1]
        try:
            ko = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if ko > now - timedelta(days=1):
                keep.add(k)
        except Exception:
            pass
    return keep


def main():
    # Webhook未設定なら何もしない(通知済みマークも付けない→後で設定した時に取りこぼさない)
    if not (os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
            or os.environ.get("SLACK_WEBHOOK_URL", "").strip()):
        print("remind: no webhook configured, skip")
        return
    if not os.path.exists(HISTORY):
        print("remind: no history")
        return
    with open(HISTORY, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    now = datetime.now(timezone.utc)
    notified = _load_notified()

    groups = {}   # (match, kickoff_iso) -> [picks]
    for r in rows:
        if r.get("result"):            # 確定済みは対象外
            continue
        try:
            ko = datetime.fromisoformat(r["kickoff_utc"].replace("Z", "+00:00"))
        except Exception:
            continue
        mins = (ko - now).total_seconds() / 60
        if not (0 < mins <= LEAD_MIN):  # 開始前かつ LEAD_MIN 分以内
            continue
        try:
            if int(r.get("prob") or 0) < PROB_SUISHO:   # 有力(55%)以上のみ通知
                continue
        except ValueError:
            continue
        groups.setdefault((r["match"], r["kickoff_utc"]), []).append(r)

    fired = 0
    for (match, ko_iso), picks in sorted(groups.items(), key=lambda kv: kv[0][1]):
        key = f"{match}|{ko_iso}"
        if key in notified:
            continue
        lg = picks[0].get("league", "")
        lines = [f"⏰ まもなく開始（約1時間前）: [{lg}] {match}  {_fmt_pht(ko_iso)} PHT"]
        for p in sorted(picks, key=lambda x: -int(x["prob"] or 0))[:6]:
            try:
                odd = f"@{float(p['odds']):.2f}"
            except (TypeError, ValueError):
                odd = ""
            lines.append(f"・{p['market']}: {p['pick']}  {p['prob']}% {odd}")
        notify.post("\n".join(lines))
        notified.add(key)
        fired += 1

    if fired:
        _save_notified(_prune(notified, now))
    print(f"remind: {fired} matches notified ({len(rows)} rows scanned)")


if __name__ == "__main__":
    main()
