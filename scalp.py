"""
JAY GOLD SCALP - 5-minute scalping module (runs every 5 minutes)

What it does
- Scans 5M gold candles in the liquid hours (London and New York).
- Looks for two setups in the direction of the 1H bias:
    PULLBACK: price dips to the 5M EMA20 inside a trend and bounces.
    BREAKOUT: price breaks the recent 12-candle range with trend strength.
- Posts an entry ZONE, a stop, an optional quick target and a validity window.
- One scalp at a time, a short cooldown, and a daily cap. It never forces
  signals to hit a number.
- Tracks each scalp on the 5M candles and reports target / stop / expiry.
- Sends a daily scalp recap.

SCALP_MODE
- "shadow": everything goes to YOUR private admin chat only (safe testing).
- "live":   signals and updates go to the main channel.

Run scalp_backtest.py first. Change SCALP_MODE to "live" only when the
backtest supports it.
"""

from datetime import datetime, timezone, timedelta

from common import send_text, load_json, save_json, DATA_DIR, is_market_open
from main import (
    fetch_bars,
    closed_bars,
    parse_bar_time,
    ema,
    ema_series,
    rsi,
    atr,
    adx,
)
from admin import notify


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "XAU/USD"
LABEL = "GOLD (XAU/USD)"
DIVIDER = "━━━━━━━━━━━━━━━"

SCALP_MODE = "shadow"          # "shadow" or "live"

LIQUID_START_HOUR = 7          # UTC, inclusive
LIQUID_END_HOUR = 17           # UTC, exclusive

SL_ATR = 1.2                   # stop distance in 5M ATRs (this is 1R)
TP_R = 1.0                     # optional quick target in R. None = no target
EXPIRY_MINUTES = 30            # validity window

MIN_ATR = 0.8                  # skip dead markets (gold dollars)
MAX_CANDLE_ATR = 3.0           # skip news spikes

MAX_SCALPS_PER_DAY = 15
COOLDOWN_MINUTES = 5           # wait after a scalp closes

COST_USD = 0.30                # spread/slippage assumption for estimates
MAX_DATA_AGE_MINUTES = 12

RECAP_HOUR = 17                # UTC, recap sent once after this hour

OPEN_FILE = DATA_DIR / "scalp_open.json"
HISTORY_FILE = DATA_DIR / "scalp_history.json"
STATE_FILE = DATA_DIR / "scalp_state.json"

FIVE_MIN_HOURS = 5.0 / 60.0


# ============================================================
# OUTPUT (shadow = admin chat, live = main channel)
# ============================================================

def strip_markdown(text):
    return text.replace("*", "").replace("`", "")


def publish(text):
    if SCALP_MODE == "live":
        send_text(text)
    else:
        notify("[SCALP SHADOW]\n" + strip_markdown(text))


# ============================================================
# PURE ANALYSIS (used by the live job AND the backtester)
# ============================================================

def in_liquid_hours(now):
    return LIQUID_START_HOUR <= now.hour < LIQUID_END_HOUR


def build_levels(direction, entry, atr_value, sl_atr=SL_ATR, tp_r=TP_R):
    sign = 1 if direction == "BUY" else -1
    risk = sl_atr * atr_value

    stop = entry - sign * risk
    target = None if tp_r is None else entry + sign * tp_r * risk

    return stop, target, risk


def analyse(bars5, bars1h, now):
    """
    bars5 / bars1h: CLOSED candles, oldest first.
    Returns (signal_dict_or_None, reason_text).
    """

    if len(bars5) < 80 or len(bars1h) < 60:
        return None, "Not enough candles."

    if not in_liquid_hours(now):
        return None, "Outside London and New York hours."

    closes5 = [b["close"] for b in bars5]
    closes1h = [b["close"] for b in bars1h]

    price = closes5[-1]
    last = bars5[-1]
    previous_close = closes5[-2]

    e20 = ema_series(closes5, 20)[-1]
    e50 = ema_series(closes5, 50)[-1]

    atr_value = atr(bars5, 14)
    rsi5 = rsi(closes5, 14)
    adx5, plus_di, minus_di = adx(bars5, 14)

    if atr_value < MIN_ATR:
        return None, f"Market too quiet (5M ATR {atr_value:.2f})."

    if (last["high"] - last["low"]) > MAX_CANDLE_ATR * atr_value:
        return None, "News-style spike candle, standing aside."

    bias_up = closes1h[-1] > ema(closes1h, 50)

    uptrend = e20 > e50 and price > e50
    downtrend = e20 < e50 and price < e50

    bullish_candle = last["close"] > last["open"]
    bearish_candle = last["close"] < last["open"]

    recent = bars5[-4:]
    prior = bars5[-13:-1]

    signal = None

    # ---- Setup A: pullback to the 5M EMA20 inside a trend -------------
    if uptrend and bias_up:
        touched = min(b["low"] for b in recent) <= e20 + 0.15 * atr_value
        if (
            touched
            and bullish_candle
            and price > e20
            and price > previous_close
            and 45 <= rsi5 <= 68
            and (price - e20) <= 1.2 * atr_value
        ):
            signal = ("BUY", "Pullback in uptrend")

    elif downtrend and not bias_up:
        touched = max(b["high"] for b in recent) >= e20 - 0.15 * atr_value
        if (
            touched
            and bearish_candle
            and price < e20
            and price < previous_close
            and 32 <= rsi5 <= 55
            and (e20 - price) <= 1.2 * atr_value
        ):
            signal = ("SELL", "Pullback in downtrend")

    # ---- Setup B: breakout of the recent 12-candle range --------------
    if signal is None:
        if uptrend and bias_up:
            if (
                price > max(b["high"] for b in prior)
                and adx5 >= 20
                and 55 <= rsi5 <= 75
                and (price - e20) <= 2.0 * atr_value
            ):
                signal = ("BUY", "Range breakout up")

        elif downtrend and not bias_up:
            if (
                price < min(b["low"] for b in prior)
                and adx5 >= 20
                and 25 <= rsi5 <= 45
                and (e20 - price) <= 2.0 * atr_value
            ):
                signal = ("SELL", "Range breakout down")

    if signal is None:
        trend = "up" if uptrend else "down" if downtrend else "mixed"
        return None, (
            f"No scalp setup (5M trend {trend}, 1H bias "
            f"{'up' if bias_up else 'down'}, RSI {rsi5:.0f})."
        )

    direction, label = signal

    return {
        "direction": direction,
        "setup": label,
        "entry": price,
        "atr": atr_value,
        "rsi": rsi5,
        "adx": adx5,
    }, f"{label} qualified."


# ============================================================
# TRACKING A SCALP ON 5M CANDLES
# ============================================================

def check_open(scalp, bars5):
    """
    Walk the closed 5M candles after the signal.
    Returns a result dict when the scalp ended, else None.
    The stop is checked before the target inside the same candle.
    """

    sign = 1 if scalp["direction"] == "BUY" else -1
    entry = scalp["entry"]
    stop = scalp["sl"]
    target = scalp.get("tp")
    risk = abs(entry - stop)

    start_after = parse_bar_time(scalp["bar_end"])
    expires = parse_bar_time(scalp["expires_at"])

    for bar in bars5:
        start = parse_bar_time(bar["datetime"])

        if start < start_after:
            continue

        end = start + timedelta(minutes=5)

        if sign == 1:
            stop_hit = bar["low"] <= stop
            target_hit = target is not None and bar["high"] >= target
        else:
            stop_hit = bar["high"] >= stop
            target_hit = target is not None and bar["low"] <= target

        if stop_hit:
            exit_price, reason = stop, "SL"
        elif target_hit:
            exit_price, reason = target, "TP"
        elif end >= expires:
            exit_price, reason = bar["close"], "EXPIRED"
        else:
            continue

        pnl = sign * (exit_price - entry) - COST_USD

        return {
            "exit_price": exit_price,
            "reason": reason,
            "r": pnl / risk,
            "closed_at": end.isoformat(),
        }

    return None


# ============================================================
# MESSAGES
# ============================================================

def signal_message(scalp, number):
    emoji = "🟢" if scalp["direction"] == "BUY" else "🔴"
    issued = parse_bar_time(scalp["created_at"])
    expires = parse_bar_time(scalp["expires_at"])

    lines = [
        "⚡ *JAY GOLD SCALP*",
        DIVIDER,
        f"{emoji} *{scalp['direction']}* — {LABEL}",
        f"Scalp #{number:02d} · {scalp['setup']}",
        (
            f"🕐 Issued {issued:%H:%M} UTC · "
            f"⏱ Valid until {expires:%H:%M} UTC ({EXPIRY_MINUTES} min)"
        ),
        "",
        f"📍 Entry zone: `{scalp['zone_low']:.2f}` - `{scalp['zone_high']:.2f}`",
        f"🛑 SL: `{scalp['sl']:.2f}`",
    ]

    if scalp.get("tp") is not None:
        lines.append(
            f"🎯 Quick target: `{scalp['tp']:.2f}` (optional, +{TP_R:.1f}R)"
        )
    else:
        lines.append("🎯 No fixed target. Close it yourself inside the window.")

    lines += [
        "",
        "📌 Enter only inside the zone. If price already left it, skip.",
        "🏃 Close early whenever you like. This is a short-term scalp.",
        "⚠️ Scalping is high risk. Use small size and always use the stop.",
    ]

    return "\n".join(lines)


def result_message(scalp, result):
    base = (
        f"📌 {LABEL} · {scalp['direction']} scalp "
        f"#{scalp.get('number', 0):02d}\n"
        f"📉 Exit: `{result['exit_price']:.2f}`\n"
        f"📐 Estimated result: {result['r']:+.2f}R"
    )

    if result["reason"] == "TP":
        return f"🎯 *SCALP TARGET HIT*\n\n{base}"

    if result["reason"] == "SL":
        return f"🛑 *SCALP STOPPED*\n\n{base}"

    return (
        f"⏱️ *SCALP WINDOW CLOSED*\n\n{base}\n"
        "If you are still in this trade, close it."
    )


def recap_message(trades, day_label):
    n = len(trades)

    if n == 0:
        return f"📊 *SCALP RECAP*\n🗓️ {day_label}\nNo scalps today."

    wins = sum(1 for t in trades if t["r"] > 0.05)
    losses = sum(1 for t in trades if t["r"] < -0.05)
    flat = n - wins - losses
    total_r = sum(t["r"] for t in trades)
    hit_tp = sum(1 for t in trades if t["reason"] == "TP")
    stopped = sum(1 for t in trades if t["reason"] == "SL")

    return (
        f"📊 *SCALP RECAP*\n"
        f"🗓️ {day_label}\n"
        f"Scalps: {n}\n"
        f"🎯 Target hit: {hit_tp}\n"
        f"🛑 Stopped: {stopped}\n"
        f"✅ Wins: {wins} · 🛑 Losses: {losses} · 🟡 Flat: {flat}\n"
        f"📐 Estimated result: {total_r:+.2f}R"
    )


# ============================================================
# LIVE RUN
# ============================================================

def load_bars(now):
    bars5 = closed_bars(fetch_bars("5min", 150), FIVE_MIN_HOURS, now)
    bars1h = closed_bars(fetch_bars("1h", 80), 1, now)

    return bars5, bars1h


def main():
    now = datetime.now(timezone.utc)

    if not is_market_open(now):
        print("Market closed.")
        return

    state = load_json(STATE_FILE, {})
    open_list = load_json(OPEN_FILE, [])
    history = load_json(HISTORY_FILE, [])

    today = now.date().isoformat()

    if state.get("date") != today:
        state["date"] = today
        state["count_today"] = 0

    try:
        bars5, bars1h = load_bars(now)
    except Exception as error:
        print(f"Scalp data failed: {error}")

        last_error = state.get("last_error_notice")
        if not last_error or now - datetime.fromisoformat(last_error) > timedelta(hours=1):
            notify(f"❌ Scalp run failed at {now:%H:%M} UTC\n{error}")
            state["last_error_notice"] = now.isoformat()
            save_json(STATE_FILE, state)

        raise

    if len(bars5) < 80:
        print("Not enough 5M candles.")
        return

    last_start = parse_bar_time(bars5[-1]["datetime"])
    age_minutes = (now - (last_start + timedelta(minutes=5))).total_seconds() / 60

    if age_minutes > MAX_DATA_AGE_MINUTES:
        print(f"5M data is stale ({age_minutes:.0f} min). Skipping.")
        return

    changed = False

    # ---- 1. Track the open scalp ------------------------------------
    still_open = []

    for scalp in open_list:
        result = check_open(scalp, bars5)

        if result is None:
            still_open.append(scalp)
            continue

        publish(result_message(scalp, result))

        record = dict(scalp)
        record.update(result)
        history.append(record)

        state["last_closed_at"] = result["closed_at"]
        changed = True

    open_list = still_open

    # ---- 2. Daily recap ---------------------------------------------
    if now.hour >= RECAP_HOUR and state.get("last_recap_date") != today:
        todays = [
            t for t in history
            if str(t.get("closed_at", ""))[:10] == today
        ]

        publish(recap_message(todays, now.strftime("%d %b %Y")))
        state["last_recap_date"] = today
        changed = True

    # ---- 3. Look for a new scalp ------------------------------------
    can_scan = (
        not open_list
        and in_liquid_hours(now)
        and state["count_today"] < MAX_SCALPS_PER_DAY
    )

    if can_scan and state.get("last_closed_at"):
        closed_at = parse_bar_time(state["last_closed_at"])

        if now - closed_at < timedelta(minutes=COOLDOWN_MINUTES):
            can_scan = False

    if can_scan:
        signal, reason = analyse(bars5, bars1h, now)
        print(reason)

        if signal:
            stop, target, risk = build_levels(
                signal["direction"], signal["entry"], signal["atr"]
            )

            zone = 0.1 * signal["atr"]
            bar_end = parse_bar_time(bars5[-1]["datetime"]) + timedelta(minutes=5)

            number = int(state.get("count_total", 0)) + 1

            scalp = {
                "id": f"scalp-{int(now.timestamp())}",
                "number": number,
                "symbol": SYMBOL,
                "direction": signal["direction"],
                "setup": signal["setup"],
                "entry": signal["entry"],
                "zone_low": signal["entry"] - zone,
                "zone_high": signal["entry"] + zone,
                "sl": stop,
                "tp": target,
                "atr": signal["atr"],
                "created_at": now.isoformat(),
                "bar_end": bar_end.isoformat(),
                "expires_at": (now + timedelta(minutes=EXPIRY_MINUTES)).isoformat(),
                "mode": SCALP_MODE,
            }

            publish(signal_message(scalp, number))

            open_list.append(scalp)
            state["count_today"] += 1
            state["count_total"] = number
            state["last_signal_at"] = now.isoformat()
            changed = True

    if changed:
        save_json(OPEN_FILE, open_list)
        save_json(HISTORY_FILE, history)
        save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
