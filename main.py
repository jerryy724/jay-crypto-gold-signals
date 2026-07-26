from datetime import datetime, timezone
from common import get_price, td_get, send_photo, load_json, save_json, OPEN_TRADES_FILE, is_market_open
from cards import make_signal_card

SYMBOL = "XAU/USD"
LABEL = "GOLD (XAU/USD)"

def get_signal():
    price = get_price(SYMBOL)
    atr = float(td_get("atr", SYMBOL, interval="1h", time_period=14)["values"][0]["atr"])
    rsi = float(td_get("rsi", SYMBOL, interval="1h", time_period=14)["values"][0]["rsi"])
    direction = "BUY" if rsi >= 50 else "SELL"
    sign = 1 if direction == "BUY" else -1
    sl = price - sign * 1.5 * atr
    tps = [price + sign * m * atr for m in (1, 2, 3, 4)]
    zone_buffer = 0.15 * atr
    return direction, price, sl, tps, price - zone_buffer, price + zone_buffer

def caption(direction, entry_low, entry_high, sl, tps):
    emoji = "🟢" if direction == "BUY" else "🔴"
    lines = [f"{emoji} {direction} — {LABEL}", "", f"Entry Zone: `{entry_high:.2f}` - `{entry_low:.2f}`"]
    for i, tp in enumerate(tps, 1):
        lines.append(f"🎯 TP{i}: `{tp:.2f}`")
    lines.append(f"🛑 SL: `{sl:.2f}`")
    lines += ["", "⚠️ Trade responsibly. Not financial advice."]
    return "\n".join(lines)

def main():
    now = datetime.now(timezone.utc)
    if not is_market_open(now):
        print("Gold market is closed — skipping this run.")
        return

    direction, entry, sl, tps, zone_low, zone_high = get_signal()
    make_signal_card(direction, LABEL, "/tmp/card.png")
    send_photo("/tmp/card.png", caption(direction, zone_low, zone_high, sl, tps))

    trades = load_json(OPEN_TRADES_FILE, [])
    trades.append({
        "id": f"gold-{int(now.timestamp())}", "symbol": SYMBOL, "label": LABEL,
        "direction": direction, "entry": entry, "sl": sl, "tps": tps,
        "tp_hit": [False] * 4, "opened_at": now.isoformat(),
    })
    save_json(OPEN_TRADES_FILE, trades)

if __name__ == "__main__":
    main()
