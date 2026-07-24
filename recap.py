import os
from datetime import datetime, timezone, timedelta
from common import send_text, load_json, HISTORY_FILE

def in_period(dt, period, now):
    if period == "daily":
        return dt.date() == now.date()
    if period == "weekly":
        return dt.isocalendar()[1] == now.isocalendar()[1] and dt.year == now.year
    return dt.month == now.month and dt.year == now.year

def recap(period, label):
    now = datetime.now(timezone.utc)
    trades = [t for t in load_json(HISTORY_FILE, []) if in_period(datetime.fromisoformat(t["closed_at"]), period, now)]
    if not trades:
        send_text(f"📊 {label} RECAP\nNo trades closed this period.")
        return
    wins = sum(1 for t in trades if t["outcome"] == "win")
    losses = sum(1 for t in trades if t["outcome"] == "loss")
    send_text(f"📊 {label} RECAP\nTrades: {len(trades)}\n✅ Wins: {wins}  🛑 Losses: {losses}")

def main():
    period = os.environ["RECAP_PERIOD"]
    force = os.environ.get("FORCE") == "true"
    now = datetime.now(timezone.utc)
    if period == "monthly" and (force or (now + timedelta(days=1)).month != now.month):
        recap("monthly", "MONTHLY")
    elif period == "weekly" and (force or now.weekday() == 6):
        recap("weekly", "WEEKLY")
    elif period == "daily":
        recap("daily", "DAILY")

if __name__ == "__main__":
    main()
