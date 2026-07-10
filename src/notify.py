"""Slack/Discord Webhook通知（環境変数が設定されている場合のみ動作）"""
import os
import requests


def send(picks: list):
    """picks: 本命・有力の予想リスト"""
    if not picks:
        return
    lines = ["🎯 本日のAIベット予想（本命・有力のみ）"]
    for p in picks[:12]:
        lines.append(f"・[{p['league']}] {p['match']} → {p['pick']} "
                     f"({p['prob']}% @{p['odds']:.2f})")
    text = "\n".join(lines)

    slack = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if slack:
        try:
            requests.post(slack, json={"text": text}, timeout=15)
        except Exception as e:
            print(f"[warn] slack notify failed: {e}")

    discord = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if discord:
        try:
            requests.post(discord, json={"content": text}, timeout=15)
        except Exception as e:
            print(f"[warn] discord notify failed: {e}")
