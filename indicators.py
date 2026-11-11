"""
indicators.py - market data download and indicator maths shared by every
JAY system (Gold now, FX next). No trading decisions live here.
"""

from datetime import datetime, timezone, timedelta

from common import td_get

SYMBOL = "XAU/USD"


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
