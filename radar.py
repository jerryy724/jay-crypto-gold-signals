"""
radar.py - shared memory between the :55 pre-alert and the top-of-hour signal job.

The pre-alert records what it saw ("forming"). The hourly signal job calls
resolve_forming() so subscribers are told if a flagged setup did not confirm.
This file must NOT import main.py (main.py imports this file).
"""

from datetime import datetime, timezone, timedelta

from common import send_text, load_json, save_json, PRE_ALERT_FILE

CONFIRM_WINDOW_MINUTES = 25


def save_forming(target_hour, direction, score):
    state = load_json(PRE_ALERT_FILE, {})
    state["forming"] = {
        "target_hour": target_hour.isoformat(),
        "direction": direction,
        "score": score,
        "flagged_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json(PRE_ALERT_FILE, state)


def clear_forming():
    state = load_json(PRE_ALERT_FILE, {})
    if "forming" in state:
        state.pop("forming")
        save_json(PRE_ALERT_FILE, state)


def resolve_forming(now, signal):
    """Call from main.py once the hourly decision is known (signal may be None)."""
    try:
        state = load_json(PRE_ALERT_FILE, {})
        forming = state.get("forming")
        if not forming:
            return

        target = datetime.fromisoformat(forming["target_hour"])
        gap_minutes = abs((now - target).total_seconds()) / 60

        # Stale flag from some earlier hour: drop it quietly.
        if gap_minutes > CONFIRM_WINDOW_MINUTES:
            clear_forming()
            return

        confirmed = signal is not None and signal["direction"] == forming["direction"]

        if not confirmed and signal is None:
            send_text(
                "🧭 *GOLD update*\n"
                f"The {forming['direction']} setup flagged before {target:%H:%M} UTC "
                "did not confirm at the candle close.\n"
                "No trade. We stand aside and scan again next hour."
            )

        # If a signal dropped, the signal card is the confirmation.
        clear_forming()

    except Exception as error:
        print(f"resolve_forming skipped: {error}")
