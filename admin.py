"""
admin.py - private messages to the administrator (never the public channel).
Set the ADMIN_CHAT_ID secret. If it is missing, nothing is sent and nothing
breaks.
"""

import os

import requests


def notify(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("ADMIN_CHAT_ID", "").strip()

    if not token or not chat:
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": str(text)[:3900]},
            timeout=15,
        )
        return response.ok

    except Exception as error:
        print(f"Admin notify failed: {error}")
        return False
