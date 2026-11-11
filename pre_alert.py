"""
Smart pre-alert for JAY GOLD MASTER (v2).

Runs at :55. It previews the real signal engine against the hour that is
about to close. A pre-alert is sent ONLY when a qualified setup is forming
and the open-trade limit has not been reached. If nothing qualifies the
channel stays quiet (or sends a short market-watch line, see NEUTRAL_MODE).
"""

from datetime import datetime, timezone, timedelta

from common import send_text, load_json, save_json, PRE_ALERT_FILE, is_market_open
from radar import save_forming, clear_forming
from main import get_signal, trade_limit_reached

# "silent" = say nothing when no setup is forming
# "watch"  = send a short market-watch line (max once every 3 hours)
NEUTRAL_MODE = "silent"
WATCH_EVERY_HOURS = 3

FORMING_MESSAGES = [
    "⏳ *GOLD setup forming*\n{direction} bias is lining up on the 1H and 4H.\nConfirmation due at {hour} UTC. No confirmation, no trade.",
    "👀 *Eyes on GOLD*\nA {direction} setup is taking shape.\nThe {hour} UTC candle close decides it.",
    "📡 *GOLD radar*\nConditions favour a {direction} entry.\nWe confirm at {hour} UTC. Patience first.",
    "🎯 *Setup watch: GOLD*\n{direction} conditions are aligning.\nSignal drops at {hour} UTC only if the close holds.",
]


def top_of_next_hour(now):
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def main():
    now = datetime.now(timezone.utc)
    target = top_of_next_hour(now)

    if not is_market_open(target):
        print("Market closed at next signal time - no pre-alert.")
        clear_forming()
        return

    # Only stay quiet when the open-trade limit is reached.
    if trade_limit_reached(now):
        print("Open-trade limit reached - no pre-alert.")
        clear_forming()
        return

    # Preview the real strategy as if the forming candle had closed now.
    try:
        preview = get_signal(target)
    except Exception as error:
        print(f"Pre-alert preview failed, staying silent: {error}")
        clear_forming()
        return

    state = load_json(PRE_ALERT_FILE, {})

    if preview is None:
        clear_forming()
        print("No qualified setup forming.")
        if NEUTRAL_MODE == "watch":
            last = state.get("last_watch_ts")
            due = True
            if last:
                age = now - datetime.fromisoformat(last)
                due = age >= timedelta(hours=WATCH_EVERY_HOURS)
            if due:
                send_text(
                    "🧭 *GOLD market watch*\n"
                    "No qualified setup right now. We wait for quality."
                )
                state = load_json(PRE_ALERT_FILE, {})
                state["last_watch_ts"] = now.isoformat()
                save_json(PRE_ALERT_FILE, state)
        return

    idx = int(state.get("pre_alert_index", 0)) % len(FORMING_MESSAGES)
    send_text(
        FORMING_MESSAGES[idx].format(
            direction=preview["direction"],
            hour=f"{target:%H:%M}",
        )
    )

    save_forming(target, preview["direction"], preview["score"])
    state = load_json(PRE_ALERT_FILE, {})
    state["pre_alert_index"] = (idx + 1) % len(FORMING_MESSAGES)
    save_json(PRE_ALERT_FILE, state)


if __name__ == "__main__":
    main()
