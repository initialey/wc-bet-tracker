"""Slack/Discord Webhook通知（環境変数が設定されている場合のみ動作）"""
import os
import requests


def post(text: str):
    """任意テキストを設定済みのSlack/Discord/Telegramへ送信する。"""
    if not text:
        return
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

    # Telegram: TELEGRAM_BOT_TOKEN と TELEGRAM_CHAT_ID の両方が設定されている場合のみ
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat, "text": text.replace("**", ""),
                      "disable_web_page_preview": True},
                timeout=15)
        except Exception as e:
            print(f"[warn] telegram notify failed: {e}")


def send(picks: list):
    """picks: 本命・有力の予想リスト（毎日の分析後にまとめて通知）"""
    if not picks:
        return
    lines = ["🎯 本日のAIベット予想（本命・有力のみ）"]
    for p in picks[:12]:
        lines.append(f"・[{p['league']}] {p['match']} → {p['pick']} "
                     f"({p['prob']}% @{p['odds']:.2f})")
    post("\n".join(lines))
