from datetime import datetime, timezone, timedelta
from common import td_get, send_photo, load_json, save_json, OPEN_TRADES_FILE, STATE_FILE, is_market_open
from cards import make_signal_card

SYMBOL = "XAU/USD"
LABEL = "GOLD (XAU/USD)"
DIVIDER = "━━━━━━━━━━━━━━━"
ENTRY_ZONE_ATR = 0.20
SL_ATR = 1.50
TP_R = (1.00, 1.80, 2.60, 3.50)
TP_WEIGHTS = (0.30, 0.30, 0.25, 0.15)
SIGNAL_VALID_HOURS = 1


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


def fetch_bars(interval="1h", outputsize=220):
    data = td_get("time_series", SYMBOL, interval=interval, outputsize=outputsize, order="ASC", timezone="UTC")
    values = data.get("values", [])
    return [
        {"datetime": v.get("datetime"), "open": float(v["open"]), "high": float(v["high"]),
         "low": float(v["low"]), "close": float(v["close"])}
        for v in values
    ]


def closed_bars(bars, now=None, interval_hours=1):
    now = now or datetime.now(timezone.utc)
    out = []
    for b in bars:
        try:
            ts = datetime.fromisoformat(str(b["datetime"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts + timedelta(hours=interval_hours) <= now:
                out.append(b)
        except Exception:
            # If the provider does not return a parseable timestamp, retain the
            # original bar set rather than silently changing the old workflow.
            out.append(b)
    return out


def ema(values, period):
    if len(values) < period:
        raise ValueError(f"Not enough data for EMA{period}")
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, period=14):
    if len(closes) <= period:
        raise ValueError("Not enough data for RSI")
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
    if len(bars) <= period:
        raise ValueError("Not enough data for ATR")
    trs = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


def find_swing(bars, lookback=50):
    recent = bars[-lookback:]
    return max(b["high"] for b in recent), min(b["low"] for b in recent)


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


def build_entry_zone(signal_candle, direction, atr_value):
    """Build a next-candle pullback zone from the CLOSED signal candle.

    The zone is intentionally narrow. The signal is not considered filled merely
    because it was published; the tracker must observe price entering the zone.
    """
    o, c = signal_candle["open"], signal_candle["close"]
    body_low, body_high = min(o, c), max(o, c)
    buffer = ENTRY_ZONE_ATR * atr_value

    if direction == "BUY":
        anchor = c
        low = max(body_low, anchor - buffer)
        high = min(body_high, anchor - buffer * 0.25)
        if low >= high:
            low, high = anchor - buffer, anchor
    else:
        anchor = c
        low = max(body_low, anchor + buffer * 0.25)
        high = min(body_high, anchor + buffer)
        if low >= high:
            low, high = anchor, anchor + buffer

    return min(low, high), max(low, high)


def get_signal(now=None):
    now = now or datetime.now(timezone.utc)
    bars_1h = closed_bars(fetch_bars("1h", 220), now, 1)
    bars_4h = closed_bars(fetch_bars("4h", 120), now, 4)
    if len(bars_1h) < 60 or len(bars_4h) < 55:
        raise ValueError("Insufficient closed 1H/4H candles")

    signal_candle = bars_1h[-1]
    price = signal_candle["close"]
    closes_1h = [b["close"] for b in bars_1h]
    closes_4h = [b["close"] for b in bars_4h]
    ema_4h = ema(closes_4h, 50)
    ema_1h = ema(closes_1h, 20)
    ema_1h_50 = ema(closes_1h, 50)
    rsi_1h = rsi(closes_1h, 14)
    atr_1h = atr(bars_1h, 14)

    trend = "BUY" if price > ema_4h else "SELL"
    momentum = "BUY" if ema_1h > ema_1h_50 and rsi_1h >= 50 else "SELL" if ema_1h < ema_1h_50 and rsi_1h < 50 else "NEUTRAL"
    direction = trend

    candle_range = max(signal_candle["high"] - signal_candle["low"], 1e-9)
    body = abs(signal_candle["close"] - signal_candle["open"])
    body_ratio = body / candle_range
    candle_aligned = (direction == "BUY" and signal_candle["close"] > signal_candle["open"]) or (direction == "SELL" and signal_candle["close"] < signal_candle["open"])

    score = 0
    score += 2 if trend == direction else 0
    score += 1 if momentum == direction else 0
    score += 1 if candle_aligned else 0
    score += 1 if body_ratio >= 0.50 else 0
    score += 1 if (50 <= rsi_1h <= 68 if direction == "BUY" else 32 <= rsi_1h <= 50) else 0
    conviction = "🔥 A-Grade Setup" if score >= 5 else "⚡ Standard Setup"

    risk_flag = None
    if direction == "BUY" and rsi_1h > 70:
        risk_flag = "⚠️ Overbought — entry requires pullback"
    elif direction == "SELL" and rsi_1h < 30:
        risk_flag = "⚠️ Oversold — entry requires pullback"
    elif score < 3:
        risk_flag = "⚠️ Mixed conditions — lower confidence"

    swing_high, swing_low = find_swing(bars_1h)
    fib_note = fib_confluence(price, swing_high, swing_low, atr_1h)
    fvg = detect_recent_fvg(bars_1h)
    fvg_note = fvg_confluence(price, fvg, direction)

    zone_low, zone_high = build_entry_zone(signal_candle, direction, atr_1h)
    model_entry = (zone_low + zone_high) / 2

    # Use the model entry for all published levels. The tracker records the actual
    # zone-touch price separately, so signal levels remain stable and auditable.
    sign = 1 if direction == "BUY" else -1
    sl = model_entry - sign * SL_ATR * atr_1h
    risk_distance = abs(model_entry - sl)
    tps = [model_entry + sign * r * risk_distance for r in TP_R]
    rr = TP_R[-1]

    return {
        "direction": direction,
        "model_entry": model_entry,
        "sl": sl,
        "tps": tps,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "rr": rr,
        "conviction": conviction,
        "risk_flag": risk_flag,
        "fib_note": fib_note,
        "fvg_note": fvg_note,
        "score": score,
        "rsi": rsi_1h,
        "atr": atr_1h,
        "signal_candle_time": signal_candle.get("datetime"),
    }


def caption(signal, now, signal_no):
    direction = signal["direction"]
    emoji = "🟢" if direction == "BUY" else "🔴"
    issued_str = now.strftime("%d %b %Y, %H:%M UTC")
    lines = [
        "JAY GOLD MASTER", DIVIDER,
        f"{emoji} {direction} — {LABEL}",
        f"Signal #{signal_no:03d} | {session_tag(now)}",
        signal["conviction"],
        f"🕐 {killzone_tag(now)}",
        f"📊 Setup Score: {signal['score']}/6",
    ]
    if signal["risk_flag"]:
        lines.append(signal["risk_flag"])
    if signal["fib_note"]:
        lines.append(f"📐 Fib Confluence: {signal['fib_note']} retracement")
    if signal["fvg_note"]:
        lines.append(f"🔲 {signal['fvg_note']}")
    lines += [
        f"Issued: {issued_str}", DIVIDER, "",
        f"🎯 ENTRY ZONE: `{signal['zone_high']:.2f}` - `{signal['zone_low']:.2f}`",
        "⚠️ Entry is valid only when price reaches this zone during the next hour.", "",
    ]
    for i, tp in enumerate(signal["tps"], 1):
        lines.append(f"🎯 TP{i}: `{tp:.2f}` ({TP_R[i-1]:.1f}R)")
    lines += ["", f"🛑 SL: `{signal['sl']:.2f}`", f"⚖️ Max planned R:R — 1:{signal['rr']:.1f}", "",
              "⚠️ Trade responsibly. Risk a fixed percentage, not a fixed lot size."]
    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc)
    if not is_market_open(now):
        print("Gold market is closed — skipping this run.")
        return

    try:
        signal = get_signal(now)
    except Exception as e:
        print(f"Signal generation failed this hour: {e}")
        return

    signal_no = next_signal_number()
    make_signal_card(signal["direction"], LABEL, "/tmp/card.png")
    send_photo("/tmp/card.png", caption(signal, now, signal_no))

    trades = load_json(OPEN_TRADES_FILE, [])
    trades.append({
        "id": f"gold-{int(now.timestamp())}", "symbol": SYMBOL, "label": LABEL,
        "direction": signal["direction"], "entry": signal["model_entry"],
        "model_entry": signal["model_entry"], "entry_low": signal["zone_low"],
        "entry_high": signal["zone_high"], "sl": signal["sl"], "initial_sl": signal["sl"],
        "tps": signal["tps"], "tp_hit": [False] * 4,
        "tp_weights": list(TP_WEIGHTS), "tp_r": list(TP_R),
        "status": "PENDING", "entry_filled": False,
        "opened_at": now.isoformat(), "entry_deadline": (now + timedelta(hours=SIGNAL_VALID_HOURS)).isoformat(),
        "signal_candle_time": signal["signal_candle_time"], "score": signal["score"],
        "atr": signal["atr"], "rsi": signal["rsi"], "realized_r": 0.0,
    })
    save_json(OPEN_TRADES_FILE, trades)

if __name__ == "__main__":
    main()
