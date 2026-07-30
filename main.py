from datetime import datetime, timezone
from common import get_price, td_get, send_photo, load_json, save_json, OPEN_TRADES_FILE, is_market_open
from cards import make_signal_card

SYMBOL = "XAU/USD"
LABEL = "GOLD (XAU/USD)"

def get_signal():
    price = get_price(SYMBOL)
    ema = float(td_get("ema", SYMBOL, interval="4h", time_period=50)["values"][0]["ema"])
    atr = float(td_get("atr", SYMBOL, interval="1h", time_period=14)["values"][0]["atr"])
    rsi = float(td_get("rsi", SYMBOL, interval="1h", time_period=14)["values"][0]["rsi"])

    trend = "BUY" if price > ema else "SELL"
    momentum = "BUY" if rsi >= 50 else "SELL"
    if trend != momentum:
        return None  # no aligned edge this hour

    direction = trend
    sign = 1 if direction == "BUY" else -1
    sl = price - sign * 1.5 * atr
    tps = [price + sign * m * atr for m in (0.7, 1.5, 2.5, 3.5)]
    zone_buffer = 0.15 * atr
    rr = 3.5 / 1.5
    return direction, price, sl, tps, price - zone_buffer, price + zone_buffer, rr

def caption(direction, entry_low, entry_high, sl, tps, rr):
    emoji = "🟢" if direction == "BUY" else "🔴"
    lines = [f"{emoji} {direction} — {LABEL}", "", f"Entry Zone: `{entry_high:.2f}` - `{entry_low:.2f}`"]
    for i, tp in enumerate(tps, 1):
        lines.append(f"🎯 TP{i}: `{tp:.2f}`")
    lines.append(f"🛑 SL: `{sl:.2f}`")
    lines.append(f"⚖️ Risk:Reward — 1:{rr:.1f}")
    lines += ["", "⚠️ Trade responsibly. Not financial advice."]
    return "\n".join(lines)

def main():
    now = datetime.now(timezone.utc)
    if not is_market_open(now):
        print("Gold market is closed — skipping this run.")
        return

    result = get_signal()
    if result is None:
        print("Trend and momentum disagree this hour — skipping signal.")
        return
    direction, entry, sl, tps, zone_low, zone_high, rr = result

    make_signal_card(direction, LABEL, "/tmp/card.png")
    send_photo("/tmp/card.png", caption(direction, zone_low, zone_high, sl, tps, rr))

    trades = load_json(OPEN_TRADES_FILE, [])
    trades.append({
        "id": f"gold-{int(now.timestamp())}", "symbol": SYMBOL, "label": LABEL,
        "direction": direction, "entry": entry, "sl": sl, "tps": tps,
        "tp_hit": [False] * 4, "opened_at": now.isoformat(),
    })
    save_json(OPEN_TRADES_FILE, trades)

if __name__ == "__main__":
    main()
