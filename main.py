from datetime import datetime, timezone
from common import td_get, send_photo, load_json, save_json, OPEN_TRADES_FILE, STATE_FILE, is_market_open
from cards import make_signal_card

SYMBOL = "XAU/USD"
LABEL = "GOLD (XAU/USD)"
DIVIDER = "━━━━━━━━━━━━━━━"

def session_tag(now):
    h = now.hour
    if h >= 21 or h < 6:
        return "Sydney/Asian Session"
    if h < 12:
        return "London Session"
    if h < 16:
        return "London/NY Overlap"
    return "New York Session"

def killzone_tag(now):
    h = now.hour
    if 2 <= h < 5:
        return "🟢 London Killzone — High Liquidity"
    if 7 <= h < 10:
        return "🟢 New York Killzone — High Liquidity"
    if 10 <= h < 12:
        return "🟡 London Close — Reversal Watch"
    if 19 <= h or h < 2:
        return "🔴 Asian Range — Thin Liquidity"
    return "⚪ Standard Hours"

def next_signal_number():
    state = load_json(STATE_FILE, {})
    n = state.get("signal_count", 0) + 1
    state["signal_count"] = n
    save_json(STATE_FILE, state)
    return n

def fetch_bars():
    data = td_get("time_series", SYMBOL, interval="1h", outputsize=220, order="ASC")
    return [
        {"open": float(v["open"]), "high": float(v["high"]),
         "low": float(v["low"]), "close": float(v["close"])}
        for v in data["values"]
    ]

def resample_4h(bars_1h):
    bars_4h = []
    for i in range(0, len(bars_1h) - 3, 4):
        g = bars_1h[i:i + 4]
        bars_4h.append({"close": g[-1]["close"]})
    return bars_4h

def ema(values, period):
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e

def rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(bars, period=14):
    trs = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period

def find_swing(bars, lookback=50):
    recent = bars[-lookback:]
    swing_high = max(b["high"] for b in recent)
    swing_low = min(b["low"] for b in recent)
    return swing_high, swing_low

def fib_confluence(price, swing_high, swing_low, atr_value):
    diff = swing_high - swing_low
    if diff <= 0:
        return None
    levels = {
        "38.2%": swing_high - 0.382 * diff,
        "50.0%": swing_high - 0.5 * diff,
        "61.8%": swing_high - 0.618 * diff,
    }
    tolerance = 0.25 * atr_value
    for name, level in levels.items():
        if abs(price - level) <= tolerance:
            return name
    return None

def detect_recent_fvg(bars, lookback=30):
    recent = bars[-lookback:]
    fvgs = []
    for i in range(2, len(recent)):
        c1, c3 = recent[i - 2], recent[i]
        if c1["high"] < c3["low"]:
            fvgs.append({"type": "Bullish", "top": c3["low"], "bottom": c1["high"]})
        elif c1["low"] > c3["high"]:
            fvgs.append({"type": "Bearish", "top": c1["low"], "bottom": c3["high"]})
    return fvgs[-1] if fvgs else None

def fvg_confluence(price, fvg, direction):
    if not fvg:
        return None
    in_zone = fvg["bottom"] <= price <= fvg["top"]
    aligned = (fvg["type"] == "Bullish" and direction == "BUY") or (fvg["type"] == "Bearish" and direction == "SELL")
    if in_zone and aligned:
        return f"{fvg['type']} FVG ({fvg['bottom']:.2f}-{fvg['top']:.2f})"
    return None

def get_signal():
    bars_1h = fetch_bars()
    price = bars_1h[-1]["close"]
    closes_1h = [b["close"] for b in bars_1h]
    bars_4h = resample_4h(bars_1h)
    ema_4h = ema([b["close"] for b in bars_4h], 50)
    rsi_1h = rsi(closes_1h, 14)
    atr_1h = atr(bars_1h, 14)

    trend = "BUY" if price > ema_4h else "SELL"
    momentum = "BUY" if rsi_1h >= 50 else "SELL"
    direction = trend
    conviction = "🔥 High Conviction" if trend == momentum else "⚡ Standard Setup"

    risk_flag = None
    if direction == "BUY" and rsi_1h > 70:
        risk_flag = "⚠️ Overbought — Exercise Caution"
    elif direction == "SELL" and rsi_1h < 30:
        risk_flag = "⚠️ Oversold — Exercise Caution"

    swing_high, swing_low = find_swing(bars_1h)
    fib_note = fib_confluence(price, swing_high, swing_low, atr_1h)

    fvg = detect_recent_fvg(bars_1h)
    fvg_note = fvg_confluence(price, fvg, direction)

    sign = 1 if direction == "BUY" else -1
    sl = price - sign * 1.5 * atr_1h
    tps = [price + sign * m * atr_1h for m in (0.4, 1.0, 1.8, 2.8)]
    zone_buffer = 0.15 * atr_1h
    rr = 2.8 / 1.5
    return direction, price, sl, tps, price - zone_buffer, price + zone_buffer, rr, conviction, risk_flag, fib_note, fvg_note

def caption(direction, entry_low, entry_high, sl, tps, rr, now, signal_no, conviction, risk_flag, fib_note, fvg_note):
    emoji = "🟢" if direction == "BUY" else "🔴"
    issued_str = now.strftime("%d %b %Y, %H:%M UTC")
    lines = [
        "JAY GOLD MASTER",
        DIVIDER,
        f"{emoji} {direction} — {LABEL}",
        f"Signal #{signal_no:03d} | {session_tag(now)}",
        conviction,
        f"🕐 {killzone_tag(now)}",
    ]
    if risk_flag:
        lines.append(risk_flag)
    if fib_note:
        lines.append(f"📐 Fib Confluence: {fib_note} retracement")
    if fvg_note:
        lines.append(f"🔲 {fvg_note}")
    lines += [
        f"Issued: {issued_str}",
        DIVIDER,
        "",
        f"Entry Zone: `{entry_high:.2f}` - `{entry_low:.2f}`",
        "",
    ]
    for i, tp in enumerate(tps, 1):
        lines.append(f"🎯 TP{i}: `{tp:.2f}`")
    lines.append("")
    lines.append(f"🛑 SL: `{sl:.2f}`")
    lines.append(f"⚖️ Risk:Reward — 1:{rr:.1f}")
    lines += ["", "⚠️ Trade responsibly. Use lower position sizes to avoid high risk."]
    return "\n".join(lines)

def main():
    now = datetime.now(timezone.utc)
    if not is_market_open(now):
        print("Gold market is closed — skipping this run.")
        return

    try:
        direction, entry, sl, tps, zone_low, zone_high, rr, conviction, risk_flag, fib_note, fvg_note = get_signal()
    except Exception as e:
        print(f"Signal generation failed this hour: {e}")
        return

    signal_no = next_signal_number()
    make_signal_card(direction, LABEL, "/tmp/card.png")
    send_photo("/tmp/card.png", caption(direction, zone_low, zone_high, sl, tps, rr, now, signal_no, conviction, risk_flag, fib_note, fvg_note))

    trades = load_json(OPEN_TRADES_FILE, [])
    trades.append({
        "id": f"gold-{int(now.timestamp())}", "symbol": SYMBOL, "label": LABEL,
        "direction": direction, "entry": entry, "sl": sl, "tps": tps,
        "tp_hit": [False] * 4, "opened_at": now.isoformat(),
    })
    save_json(OPEN_TRADES_FILE, trades)

if __name__ == "__main__":
    main()
