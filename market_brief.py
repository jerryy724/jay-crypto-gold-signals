from datetime import datetime, timezone
from common import send_photo, is_market_open
from cards import make_brief_card
from main import fetch_bars, resample_4h, ema, atr, session_tag

def classify_volatility(atr_value):
    if atr_value < 8:
        return "🟢 LOW", "Calm conditions — tighter ranges expected."
    if atr_value < 15:
        return "🟡 MODERATE", "Normal trading conditions."
    return "🔴 HIGH", "Elevated volatility — expect wider swings. Manage risk accordingly."

def brief_type(now):
    if now.hour == 6:
        return "MORNING BRIEF"
    if now.hour == 12:
        return "MIDDAY BRIEF"
    return "MARKET CLOSE BRIEF"

def main():
    now = datetime.now(timezone.utc)
    label = brief_type(now)

    if label == "MARKET CLOSE BRIEF":
        if now.weekday() == 5:
            print("Saturday — no close event, skipping.")
            return
    else:
        if not is_market_open(now):
            print("Market closed — skipping this brief.")
            return

    try:
        bars_1h = fetch_bars()
        price = bars_1h[-1]["close"]
        bars_4h = resample_4h(bars_1h)
        ema_4h = ema([b["close"] for b in bars_4h], 50)
        atr_1h = atr(bars_1h, 14)
    except Exception as e:
        print(f"Brief generation failed: {e}")
        return

    trend = "Bullish bias (above 4H trend)" if price > ema_4h else "Bearish bias (below 4H trend)"
    vol_tag, vol_note = classify_volatility(atr_1h)

    make_brief_card(label, "/tmp/brief.png")
    caption = (
        f"📰 *JAY GOLD MASTER — {label}*\n\n"
        f"🌍 Session: {session_tag(now)}\n"
        f"📈 Current Price: `{price:.2f}`\n"
        f"🧭 Trend Read: {trend}\n"
        f"📊 Volatility: {vol_tag}\n"
        f"💡 {vol_note}\n\n"
        f"⚠️ Market-condition summary from live price data — not a news feed. Confirm major economic releases independently."
    )
    send_photo("/tmp/brief.png", caption)

if __name__ == "__main__":
    main()
