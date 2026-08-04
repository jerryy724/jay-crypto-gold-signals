from datetime import datetime, timezone, timedelta
from common import send_text, load_json, OPEN_TRADES_FILE, is_market_open

SYMBOL = "XAU/USD"
MAX_OPEN_TRADES = 3

def main():
    now = datetime.now(timezone.utc)
    upcoming_hour = (now + timedelta(minutes=10)).replace(minute=0, second=0, microsecond=0)

    if not is_market_open(upcoming_hour):
        print("Market will be closed at the next signal time — no pre-alert.")
        return

    trades = load_json(OPEN_TRADES_FILE, [])
    open_count = sum(1 for t in trades if t.get("symbol") == SYMBOL)

    if open_count >= MAX_OPEN_TRADES:
        send_text(
            f"⏸️ *{open_count} trades currently open* (max reached)\n"
            f"Next GOLD signal is paused until one closes. Hang tight!"
        )
    else:
        send_text("⏳ *Heads up!* A new GOLD signal is expected in the next 5 minutes. Stay ready!")

if __name__ == "__main__":
    main()
