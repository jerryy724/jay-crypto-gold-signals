from datetime import datetime, timezone, timedelta
from common import send_text, load_json, save_json, STATE_FILE, is_market_open

MESSAGES = [
    "⏳ Heads up! A new GOLD signal is expected in the next 5 minutes. Stay ready!",
    "🚨 5 minutes to go — the next GOLD setup is loading. Don't miss it!",
    "👀 Eyes on the charts — a fresh GOLD signal drops in 5 minutes.",
    "⏰ Almost time! Next GOLD entry incoming in 5 minutes.",
    "🔔 5-minute warning — GOLD signal loading now.",
    "📡 Scanning the market... next signal lands in 5 minutes. Stay tuned!",
    "💡 Get ready — a new GOLD opportunity drops in 5 minutes.",
    "⚡ 5 minutes out. Next GOLD signal is on the way.",
    "🎯 Setup forming — next GOLD signal in 5 minutes. Stay sharp!",
    "🕐 T-minus 5 minutes to the next GOLD signal drop.",
    "📊 Market's speaking — next GOLD signal in 5 minutes.",
    "🔥 Next GOLD signal locked and loading — 5 minutes to go!",
]

def main():
    now = datetime.now(timezone.utc)
    upcoming_hour = (now + timedelta(minutes=10)).replace(minute=0, second=0, microsecond=0)

    if not is_market_open(upcoming_hour):
        print("Market will be closed at the next signal time — no pre-alert.")
        return

    state = load_json(STATE_FILE, {})
    idx = state.get("pre_alert_index", 0) % len(MESSAGES)
    send_text(MESSAGES[idx])
    state["pre_alert_index"] = (idx + 1) % len(MESSAGES)
    save_json(STATE_FILE, state)

if __name__ == "__main__":
    main()
