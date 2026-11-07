"""
JAY GOLD MASTER - Signal Engine v2

Entry logic is the same engine that was backtested over two years.
What changed in v2 (all backed by the backtests):
- Three take-profit ladder: TP1 1R, TP2 2R, TP3 3R (R = 1.5 ATR),
  closing 40% / 30% / 30%, stop to entry after TP1.
- One trade at a time: no new signal while a gold trade is still open.
- 72 market-hour time stop (handled by watch.py).
- No more made-up setup score on the card.
- Private admin diagnostics: you are told WHY the engine is waiting, and
  immediately if a run fails (needs the ADMIN_CHAT_ID secret).
"""

import time
from datetime import datetime, timezone, timedelta

from common import (
    td_get,
    send_photo,
    load_json,
    save_json,
    DATA_DIR,
    OPEN_TRADES_FILE,
    STATE_FILE,
    is_market_open,
)

from cards import make_signal_card
from radar import resolve_forming
from admin import notify
from tradeutil import market_hours_between, MAX_HOLD_MARKET_HOURS


# ============================================================
# CONFIGURATION
# ============================================================

SYMBOL = "XAU/USD"
LABEL = "GOLD (XAU/USD)"

DIVIDER = "━━━━━━━━━━━━━━━"

STRATEGY_VERSION = "v2.0"

ENTRY_ZONE_ATR = 0.15

# R (one unit of risk) = SL_ATR x ATR.
SL_ATR = 1.5

# Targets in ATR multiples: 1R, 2R, 3R.
TP_MULT = (1.5, 3.0, 4.5)

# Share of the position closed at each target.
TP_WEIGHTS = (0.4, 0.3, 0.3)

MIN_ADX = 14.0

# Evidence for skipping the Asian session is weak, so it stays off.
# Set to True to stop signals during the Sydney/Asian session.
SKIP_ASIAN = False

# Last closed 1H candle must be at most this old (hours).
MAX_DATA_AGE_HOURS = 1.5

# If data looks stale, wait and retry before giving up.
STALE_RETRIES = 2
STALE_RETRY_SECONDS = 40

# Private "still scanning" update while no signal (hours between updates).
HEARTBEAT_HOURS = 3

SCAN_LOG_FILE = DATA_DIR / "scan_log.json"
ADMIN_STATE_FILE = DATA_DIR / "admin_state.json"
SCAN_LOG_LIMIT = 300

# Why the engine last said WAIT (used for diagnostics).
DECISION = {"code": "", "text": ""}


def note(code, text):
    DECISION["code"] = code
    DECISION["text"] = text
    print(text)


class StaleDataError(ValueError):
    """Raised when the newest closed candle is too old to trust."""


# ============================================================
# SESSION INFORMATION
# ============================================================

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


def in_reopen_window(now):
    """Sunday and the first hours of Monday: candles are still catching up."""
    return now.weekday() == 6 or (now.weekday() == 0 and now.hour < 2)


# ============================================================
# TIME AND MARKET DATA
# ============================================================

def parse_bar_time(value):
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def fetch_bars(interval="1h", outputsize=220):
    data = td_get(
        "time_series",
        SYMBOL,
        interval=interval,
        outputsize=outputsize,
        order="ASC",
        timezone="UTC",
        format="JSON",
    )

    if not isinstance(data, dict) or "values" not in data:
        raise RuntimeError(
            f"Unexpected Twelve Data response for {interval}: {data}"
        )

    bars = []

    for row in data.get("values", []):
        try:
            bar = {
                "datetime": parse_bar_time(row["datetime"]).isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }

            if bar["high"] < bar["low"]:
                continue
            if not (bar["low"] <= bar["open"] <= bar["high"]):
                continue
            if not (bar["low"] <= bar["close"] <= bar["high"]):
                continue

            bars.append(bar)

        except (KeyError, TypeError, ValueError):
            continue

    unique = {bar["datetime"]: bar for bar in bars}

    return [unique[key] for key in sorted(unique)]


def closed_bars(bars, interval_hours, now=None):
    now = now or datetime.now(timezone.utc)
    result = []

    for bar in bars:
        try:
            start = parse_bar_time(bar["datetime"])
            if start + timedelta(hours=interval_hours) <= now:
                result.append(bar)
        except (KeyError, TypeError, ValueError):
            continue

    return result


# ============================================================
# EMA
# ============================================================

def ema_series(values, period):
    if period <= 0:
        raise ValueError("EMA period must be positive")

    result = [None] * len(values)

    if len(values) < period:
        return result

    multiplier = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    result[period - 1] = current

    for i in range(period, len(values)):
        current = values[i] * multiplier + current * (1.0 - multiplier)
        result[i] = current

    return result


def ema(values, period):
    series = ema_series(values, period)
    if not series or series[-1] is None:
        raise ValueError(f"Not enough data for EMA{period}")
    return float(series[-1])


# ============================================================
# WILDER AVERAGE, RSI, ATR
# ============================================================

def wilder_average(values, period):
    if len(values) < period:
        raise ValueError(f"Not enough data for Wilder average {period}")

    current = sum(values[:period]) / period

    for value in values[period:]:
        current = ((current * (period - 1)) + value) / period

    return current


def rsi(closes, period=14):
    if len(closes) <= period:
        raise ValueError("Not enough data for RSI")

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    avg_gain = wilder_average(gains, period)
    avg_loss = wilder_average(losses, period)

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def true_ranges(bars):
    ranges = []

    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        previous_close = bars[i - 1]["close"]

        ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    return ranges


def atr(bars, period=14):
    return wilder_average(true_ranges(bars), period)


# ============================================================
# ADX AND DIRECTIONAL INDICATORS
# ============================================================

def adx(bars, period=14):
    if len(bars) < (period * 2 + 1):
        raise ValueError("Not enough data for ADX")

    plus_dm = []
    minus_dm = []
    tr_values = []

    for i in range(1, len(bars)):
        up_move = bars[i]["high"] - bars[i - 1]["high"]
        down_move = bars[i - 1]["low"] - bars[i]["low"]

        plus_dm.append(
            up_move if up_move > down_move and up_move > 0 else 0.0
        )
        minus_dm.append(
            down_move if down_move > up_move and down_move > 0 else 0.0
        )

        tr_values.append(
            max(
                bars[i]["high"] - bars[i]["low"],
                abs(bars[i]["high"] - bars[i - 1]["close"]),
                abs(bars[i]["low"] - bars[i - 1]["close"]),
            )
        )

    def wilder_series(values, length):
        out = [None] * len(values)

        if len(values) < length:
            return out

        current = sum(values[:length])
        out[length - 1] = current

        for j in range(length, len(values)):
            current = current - (current / length) + values[j]
            out[j] = current

        return out

    sm_tr = wilder_series(tr_values, period)
    sm_plus = wilder_series(plus_dm, period)
    sm_minus = wilder_series(minus_dm, period)

    dx_values = []
    plus_di_latest = 0.0
    minus_di_latest = 0.0

    for i in range(len(tr_values)):
        if (
            sm_tr[i] is None
            or sm_tr[i] == 0
            or sm_plus[i] is None
            or sm_minus[i] is None
        ):
            continue

        pdi = 100.0 * sm_plus[i] / sm_tr[i]
        mdi = 100.0 * sm_minus[i] / sm_tr[i]

        denominator = pdi + mdi
        dx = 0.0 if denominator == 0 else 100.0 * abs(pdi - mdi) / denominator

        dx_values.append(dx)
        plus_di_latest = pdi
        minus_di_latest = mdi

    if len(dx_values) < period:
        raise ValueError("Not enough data to calculate ADX")

    adx_value = wilder_average(dx_values, period)

    return adx_value, plus_di_latest, minus_di_latest


# ============================================================
# MACD
# ============================================================

def macd_histogram(closes, fast=12, slow=26, signal=9):
    fast_series = ema_series(closes, fast)
    slow_series = ema_series(closes, slow)

    macd_values = []

    for fast_value, slow_value in zip(fast_series, slow_series):
        if fast_value is not None and slow_value is not None:
            macd_values.append(fast_value - slow_value)

    if len(macd_values) < signal:
        raise ValueError("Not enough data for MACD")

    signal_value = ema(macd_values, signal)

    return macd_values[-1] - signal_value


# ============================================================
# MARKET STRUCTURE
# ============================================================

def find_swing(bars, lookback=50):
    recent = bars[-lookback:]
    return (
        max(bar["high"] for bar in recent),
        min(bar["low"] for bar in recent),
    )


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


# ============================================================
# FAIR VALUE GAP
# ============================================================

def detect_recent_fvg(bars, lookback=30):
    recent = bars[-lookback:]

    for i in range(len(recent) - 1, 1, -1):
        first = recent[i - 2]
        third = recent[i]

        if first["high"] < third["low"]:
            return {
                "type": "Bullish",
                "top": third["low"],
                "bottom": first["high"],
            }

        if first["low"] > third["high"]:
            return {
                "type": "Bearish",
                "top": first["low"],
                "bottom": third["high"],
            }

    return None


def fvg_confluence(price, fvg, direction):
    if not fvg:
        return None

    in_zone = fvg["bottom"] <= price <= fvg["top"]

    aligned = (
        (fvg["type"] == "Bullish" and direction == "BUY")
        or (fvg["type"] == "Bearish" and direction == "SELL")
    )

    if in_zone and aligned:
        return (
            f"{fvg['type']} FVG "
            f"({fvg['bottom']:.2f}-{fvg['top']:.2f})"
        )

    return None


# ============================================================
# MAIN CANDIDATE ANALYSIS
# ============================================================

def trend_label(price, ema50, ema200):
    if ema200 is None:
        return "4H price above EMA50" if price > ema50 else "4H price below EMA50"

    if price > ema50 > ema200:
        return "4H uptrend"

    if price < ema50 < ema200:
        return "4H downtrend"

    if ema50 > ema200:
        return (
            f"4H pullback inside an uptrend "
            f"(price {price:.0f} is below the 4H EMA50 at {ema50:.0f})"
        )

    return (
        f"4H bounce inside a downtrend "
        f"(price {price:.0f} is above the 4H EMA50 at {ema50:.0f})"
    )


def get_signal(now=None):
    """
    Returns a signal dict, or None when conditions do not qualify.
    The smart pre-alert calls this with the NEXT top-of-hour as `now`
    to preview the forming candle. It has no side effects (it only records
    the reason in DECISION).
    """

    now = now or datetime.now(timezone.utc)

    bars_1h = closed_bars(fetch_bars("1h", 220), 1, now)
    bars_4h = closed_bars(fetch_bars("4h", 220), 4, now)

    if len(bars_1h) < 60:
        raise ValueError(f"Insufficient closed 1H candles: {len(bars_1h)}")

    if len(bars_4h) < 60:
        raise ValueError(f"Insufficient closed 4H candles: {len(bars_4h)}")

    # Validate market data freshness.

    last_bar_time = parse_bar_time(bars_1h[-1]["datetime"])

    data_age_hours = (
        now - (last_bar_time + timedelta(hours=1))
    ).total_seconds() / 3600

    if data_age_hours > MAX_DATA_AGE_HOURS:
        raise StaleDataError(
            f"Market data is stale ({data_age_hours:.1f} hours old)"
        )

    closes_1h = [bar["close"] for bar in bars_1h]
    closes_4h = [bar["close"] for bar in bars_4h]

    price = closes_1h[-1]
    trend_price = closes_4h[-1]

    # Indicators.

    ema50_4h = ema(closes_4h, 50)
    ema200_4h = ema(closes_4h, 200) if len(closes_4h) >= 200 else None

    rsi_1h = rsi(closes_1h, 14)
    atr_1h = atr(bars_1h, 14)
    adx_1h, plus_di, minus_di = adx(bars_1h, 14)
    macd_hist = macd_histogram(closes_1h)

    # Higher-timeframe trend.

    if ema200_4h is not None:
        bullish_trend = trend_price > ema50_4h and ema50_4h > ema200_4h
        bearish_trend = trend_price < ema50_4h and ema50_4h < ema200_4h
    else:
        bullish_trend = trend_price > ema50_4h
        bearish_trend = trend_price < ema50_4h

    # Momentum confirmation.

    bullish_momentum = (
        rsi_1h >= 52 and macd_hist > 0 and plus_di > minus_di
    )

    bearish_momentum = (
        rsi_1h <= 48 and macd_hist < 0 and minus_di > plus_di
    )

    label = trend_label(trend_price, ema50_4h, ema200_4h)

    momentum_text = (
        f"1H RSI {rsi_1h:.0f}, MACD "
        f"{'rising' if macd_hist > 0 else 'falling'}, "
        f"{'buyers' if plus_di > minus_di else 'sellers'} ahead"
    )

    # Direction.

    if bullish_trend and bullish_momentum:
        direction = "BUY"
    elif bearish_trend and bearish_momentum:
        direction = "SELL"
    elif bullish_trend or bearish_trend:
        note(
            "NO_MOMENTUM",
            f"WAIT: {label}, but 1H momentum does not confirm yet "
            f"({momentum_text}).",
        )
        return None
    else:
        note(
            "NO_TREND",
            f"WAIT: {label}. No entry until the trend resumes "
            f"({momentum_text}).",
        )
        return None

    # Trend strength filter.

    if adx_1h < MIN_ADX:
        note(
            "WEAK_TREND",
            f"WAIT: {label}, momentum agrees but trend strength is too "
            f"low (ADX {adx_1h:.1f}, needs {MIN_ADX:.0f}).",
        )
        return None

    # Optional session filter.

    if SKIP_ASIAN and session_tag(now) == "Sydney/Asian Session":
        note("ASIAN_SKIP", f"WAIT: {direction} setup skipped in the Asian session.")
        return None

    # Avoid chasing extreme momentum.

    risk_flag = None

    if direction == "BUY" and rsi_1h >= 72:
        note("OVERBOUGHT", "WAIT: BUY setup rejected, gold is overbought.")
        return None

    if direction == "SELL" and rsi_1h <= 28:
        note("OVERSOLD", "WAIT: SELL setup rejected, gold is oversold.")
        return None

    if direction == "BUY" and rsi_1h >= 68:
        risk_flag = "⚠️ Momentum extended — Exercise Caution"
    elif direction == "SELL" and rsi_1h <= 32:
        risk_flag = "⚠️ Momentum extended — Exercise Caution"

    # Structure (confluence notes shown on the card).

    swing_high, swing_low = find_swing(bars_1h)
    fib_note = fib_confluence(price, swing_high, swing_low, atr_1h)
    fvg = detect_recent_fvg(bars_1h)
    fvg_note = fvg_confluence(price, fvg, direction)

    # Trade levels.

    sign = 1 if direction == "BUY" else -1

    sl = price - sign * SL_ATR * atr_1h
    tps = [price + sign * multiple * atr_1h for multiple in TP_MULT]

    zone_buffer = ENTRY_ZONE_ATR * atr_1h
    rr = TP_MULT[-1] / SL_ATR

    latest = bars_1h[-1]

    note("SIGNAL", f"{direction} setup qualified. {label}.")

    return {
        "direction": direction,
        "price": price,
        "sl": sl,
        "tps": tps,
        "zone_low": price - zone_buffer,
        "zone_high": price + zone_buffer,
        "rr": rr,
        "score": None,
        "risk_flag": risk_flag,
        "fib_note": fib_note,
        "fvg_note": fvg_note,
        "killzone": killzone_tag(now),
        "analysis": {
            "signal_timeframe": "1H",
            "trend_timeframe": "4H",
            "last_closed_1h": latest["datetime"],
            "price": round(price, 5),
            "ema50_4h": round(ema50_4h, 5),
            "ema200_4h": (
                round(ema200_4h, 5) if ema200_4h is not None else None
            ),
            "rsi14_1h": round(rsi_1h, 3),
            "atr14_1h": round(atr_1h, 5),
            "adx14_1h": round(adx_1h, 3),
            "plus_di_1h": round(plus_di, 3),
            "minus_di_1h": round(minus_di, 3),
            "macd_hist_1h": round(macd_hist, 6),
            "market_regime": "TRENDING" if adx_1h >= 20 else "WEAK_TREND",
            "fib_confluence": fib_note,
            "fvg_confluence": fvg_note,
            "trend_state": label,
            "data_age_hours": round(data_age_hours, 3),
        },
    }


# ============================================================
# OPEN TRADE GUARD (one trade at a time)
# ============================================================

def blocking_trade(now):
    """
    Returns an open gold trade that should block a new signal, or None.
    A trade older than the time stop plus a safety margin is treated as
    stuck and ignored, so one broken record can never silence the system.
    """

    trades = load_json(OPEN_TRADES_FILE, [])

    if not isinstance(trades, list):
        return None

    limit = MAX_HOLD_MARKET_HOURS + 6

    for trade in trades:
        if not isinstance(trade, dict) or trade.get("symbol") != SYMBOL:
            continue

        try:
            opened = datetime.fromisoformat(
                str(trade.get("opened_at")).replace("Z", "+00:00")
            )

            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)

        except (TypeError, ValueError):
            continue

        if market_hours_between(opened, now) < limit:
            return trade

    return None


# ============================================================
# SIGNAL NUMBERING AND DUPLICATE PROTECTION
# ============================================================

def next_signal_number():
    state = load_json(STATE_FILE, {})
    return int(state.get("signal_count", 0)) + 1


def already_processed_candle(signal):
    state = load_json(STATE_FILE, {})
    candle_key = signal["analysis"]["last_closed_1h"]

    if state.get("last_signal_candle") == candle_key:
        print(f"Signal decision already processed for candle {candle_key}")
        return True

    return False


def record_published_signal(candle_key):
    state = load_json(STATE_FILE, {})
    state["signal_count"] = int(state.get("signal_count", 0)) + 1
    state["last_signal_candle"] = candle_key
    save_json(STATE_FILE, state)


# ============================================================
# TELEGRAM CAPTION
# ============================================================

def caption(signal, now, signal_no):
    direction = signal["direction"]
    emoji = "🟢" if direction == "BUY" else "🔴"
    issued_str = now.strftime("%d %b %Y, %H:%M UTC")

    lines = [
        "JAY GOLD MASTER",
        DIVIDER,
        f"{emoji} {direction} — {LABEL}",
        f"Signal #{signal_no:03d} | {session_tag(now)}",
        f"🕐 {signal['killzone']}",
    ]

    if signal.get("risk_flag"):
        lines.append(signal["risk_flag"])

    if signal.get("fib_note"):
        lines.append(f"📐 Fib Confluence: {signal['fib_note']} retracement")

    if signal.get("fvg_note"):
        lines.append(f"🔲 {signal['fvg_note']}")

    lines += [
        f"Issued: {issued_str}",
        DIVIDER,
        "",
        (
            f"Entry Zone: `{signal['zone_low']:.2f}` - "
            f"`{signal['zone_high']:.2f}`"
        ),
        "",
    ]

    for index, target in enumerate(signal["tps"], 1):
        share = int(round(TP_WEIGHTS[index - 1] * 100))
        lines.append(f"🎯 TP{index}: `{target:.2f}`  ({share}% of position)")

    lines += [
        "",
        f"🛑 SL: `{signal['sl']:.2f}`",
        f"⚖️ Risk:Reward — 1:{signal['rr']:.1f} to TP3",
        "🛡️ Stop moves to entry after TP1",
        "",
        "⚠️ Trade responsibly. Use lower position sizes to avoid high risk.",
    ]

    return "\n".join(lines)


# ============================================================
# ADMIN DIAGNOSTICS
# ============================================================

def log_scan(now, status, reason):
    try:
        log = load_json(SCAN_LOG_FILE, [])

        if not isinstance(log, list):
            log = []

        log.append({
            "time": now.isoformat(),
            "status": status,
            "reason": reason,
        })

        save_json(SCAN_LOG_FILE, log[-SCAN_LOG_LIMIT:])

    except Exception as error:
        print(f"Scan log skipped: {error}")


def heartbeat(now, reason):
    """Private 'still scanning, here is why' message, at most every few hours."""

    try:
        state = load_json(ADMIN_STATE_FILE, {})
        last = state.get("last_heartbeat")

        if last:
            age = now - datetime.fromisoformat(last)

            if age < timedelta(hours=HEARTBEAT_HOURS):
                return

        sent = notify(
            f"🧭 Gold scan {now:%H:%M} UTC\n{reason}\n\n"
            f"Next update in about {HEARTBEAT_HOURS}h if still waiting."
        )

        if sent:
            state["last_heartbeat"] = now.isoformat()
            save_json(ADMIN_STATE_FILE, state)

    except Exception as error:
        print(f"Heartbeat skipped: {error}")


# ============================================================
# MAIN EXECUTION
# ============================================================

def evaluate_with_retry():
    """
    Runs get_signal(). If candle data is stale (provider lag), waits and
    retries. Returns (signal_or_None, now). Raises if data stays stale.
    """

    attempt = 0

    while True:
        now = datetime.now(timezone.utc)

        try:
            return get_signal(now), now

        except StaleDataError as error:
            if in_reopen_window(now):
                note("REOPENING", f"Market reopening, candles not ready yet: {error}")
                return None, now

            if attempt >= STALE_RETRIES:
                print(f"Data still stale after retries: {error}")
                raise

            attempt += 1
            print(
                f"Stale data ({error}). Retry {attempt}/{STALE_RETRIES} "
                f"in {STALE_RETRY_SECONDS}s."
            )
            time.sleep(STALE_RETRY_SECONDS)


def main():
    now = datetime.now(timezone.utc)

    if not is_market_open(now):
        print("Gold market is closed — skipping this run.")
        return

    # One trade at a time: do not even analyse while a trade is open.
    open_trade = blocking_trade(now)

    if open_trade:
        reason = (
            f"WAIT: {open_trade.get('direction', '?')} trade "
            f"{open_trade.get('id', '')} is still open. "
            "A new signal comes after it closes."
        )
        print(reason)
        log_scan(now, "BLOCKED", reason)
        heartbeat(now, reason)
        return

    try:
        signal, now = evaluate_with_retry()
    except Exception as error:
        print(f"Signal generation failed: {error}")
        notify(f"❌ Gold signal run FAILED at {now:%H:%M} UTC\n{error}")
        log_scan(now, "ERROR", str(error))
        raise

    # Tell the channel if a flagged pre-alert setup did not confirm.
    resolve_forming(now, signal)

    if signal is None:
        reason = DECISION["text"] or "WAIT: no qualified setup."
        print("No publishable Gold candidate this run.")
        log_scan(now, "WAIT", reason)
        heartbeat(now, reason)
        return

    if already_processed_candle(signal):
        return

    signal_number = next_signal_number()

    make_signal_card(signal["direction"], LABEL, "/tmp/card.png")

    send_photo("/tmp/card.png", caption(signal, now, signal_number))

    record_published_signal(signal["analysis"]["last_closed_1h"])

    trades = load_json(OPEN_TRADES_FILE, [])

    trades.append({
        "id": f"gold-{int(now.timestamp())}",
        "symbol": SYMBOL,
        "label": LABEL,
        "direction": signal["direction"],
        "entry": signal["price"],
        "sl": signal["sl"],
        "initial_sl": signal["sl"],
        "tps": signal["tps"],
        "weights": list(TP_WEIGHTS),
        "tp_hit": [False] * len(TP_MULT),
        "opened_at": now.isoformat(),
        "entry_candle": signal["analysis"]["last_closed_1h"],
        "max_hold_hours": MAX_HOLD_MARKET_HOURS,
        "strategy_version": STRATEGY_VERSION,
        "analysis": signal["analysis"],
        "tracking_version": 3,
    })

    save_json(OPEN_TRADES_FILE, trades)

    log_scan(now, "SIGNAL", DECISION["text"])

    notify(
        f"📡 Gold signal #{signal_number:03d} published: "
        f"{signal['direction']} at {signal['price']:.2f}\n{DECISION['text']}"
    )

    print(
        f"Published {signal['direction']} {SYMBOL} "
        f"signal #{signal_number:03d}."
    )


if __name__ == "__main__":
    main()
