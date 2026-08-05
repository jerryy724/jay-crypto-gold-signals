from datetime import datetime, timezone, timedelta
from common import send_text, is_market_open

def main():
    now = datetime.now(timezone.utc)
    upcoming_hour = (now + timedelta(minutes=10)).replace(minute=0, second=0, microsecond=0)

    if not is_market_open(upcoming_hour):
        print("Market will be closed at the next signal time — no pre-alert.")
        return

    send_text("⏳ *Heads up!* A new GOLD signal is expected in the next 5 minutes. Stay ready!")

if __name__ == "__main__":
    main()
