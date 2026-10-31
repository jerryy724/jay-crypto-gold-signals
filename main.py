"""
JAY GOLD MASTER
Phase 1: Improved Gold Signal Engine

Features:
- Native 1H and 4H market candles
- EMA 50 and EMA 200
- Wilder RSI and ATR
- ADX and directional indicators
- MACD momentum confirmation
- Fibonacci confluence
- Fair Value Gap detection
- Weighted candidate scoring
- Market data freshness validation
- Duplicate signal protection
- Historical analysis recording
"""

from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from common import (
    td_get,
    send_photo,
    load_json,
    save_json,
    OPEN_TRADES_FILE,
    STATE_FILE,
    is_market_open,
)

from cards import make_signal_card


# ============================================================
# CONFIGURATION
# ============================================================

SYMBOL = "XAU/USD"
LABEL = "GOLD (XAU/USD)"

DIVIDER = "━━━━━━━━━━━━━━━"

ENTRY_ZONE_ATR = 0.15
SL_ATR = 1.5

TP_MULT = (0.4, 1.0, 1.8, 2.8)

MIN_SETUP_SCORE = 6
MIN_ADX = 14.0

MAX_DATA_AGE_HOURS = 6


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


# ============================================================
# TIME AND MARKET DATA
# ============================================================

def parse_bar_time(value):

    ts = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )

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
                "datetime": parse_bar_time(
                    row["datetime"]
                ).isoformat(),

                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }

            if bar["high"] < bar["low"]:
                continue

            if not (
                bar["low"] <= bar["open"] <= bar["high"]
            ):
                continue

            if not (
                bar["low"] <= bar["close"] <= bar["high"]
            ):
                continue

            bars.append(bar)

        except (KeyError, TypeError, ValueError):
            continue

    bars.sort(key=lambda item: item["datetime"])

    unique = {
        bar["datetime"]: bar
        for bar in bars
    }

    return [
        unique[key]
        for key in sorted(unique)
    ]


def closed_bars(
    bars,
    interval_hours,
    now=None
):

    now = now or datetime.now(timezone.utc)

    result = []

    for bar in bars:

        try:

            start = parse_bar_time(
                bar["datetime"]
            )

            if start + timedelta(
                hours=interval_hours
            ) <= now:

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

        current = (
            values[i] * multiplier
            + current * (1.0 - multiplier)
        )

        result[i] = current

    return result


def ema(values, period):

    series = ema_series(values, period)

    if not series or series[-1] is None:
        raise ValueError(
            f"Not enough data for EMA{period}"
        )

    return float(series[-1])


# ============================================================
# WILDER AVERAGE
# ============================================================

def wilder_average(values, period):

    if len(values) < period:
        raise ValueError(
            f"Not enough data for Wilder average {period}"
        )

    current = sum(values[:period]) / period

    for value in values[period:]:

        current = (
            (current * (period - 1)) + value
        ) / period

    return current


# ============================================================
# RSI
# ============================================================

def rsi(closes, period=14):

    if len(closes) <= period:
        raise ValueError("Not enough data for RSI")

    changes = [
        closes[i] - closes[i - 1]
        for i in range(1, len(closes))
    ]

    gains = [
        max(change, 0.0)
        for change in changes
    ]

    losses = [
        max(-change, 0.0)
        for change in changes
    ]

    avg_gain = wilder_average(
        gains,
        period
    )

    avg_loss = wilder_average(
        losses,
        period
    )

    if avg_loss == 0:

        return (
            100.0
            if avg_gain > 0
            else 50.0
        )

    rs = avg_gain / avg_loss

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


# ============================================================
# ATR
# ============================================================

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

    ranges = true_ranges(bars)

    return wilder_average(
        ranges,
        period
    )


# ============================================================
# ADX AND DIRECTIONAL INDICATORS
# ============================================================

def adx(bars, period=14):

    if len(bars) < (period * 2 + 1):
        raise ValueError(
            "Not enough data for ADX"
        )

    plus_dm = []
    minus_dm = []
    tr_values = []

    for i in range(1, len(bars)):

        up_move = (
            bars[i]["high"]
            - bars[i - 1]["high"]
        )

        down_move = (
            bars[i - 1]["low"]
            - bars[i]["low"]
        )

        plus_dm.append(
            up_move
            if up_move > down_move and up_move > 0
            else 0.0
        )

        minus_dm.append(
            down_move
            if down_move > up_move and down_move > 0
            else 0.0
        )

        tr_values.append(
            max(
                bars[i]["high"] - bars[i]["low"],
                abs(
                    bars[i]["high"]
                    - bars[i - 1]["close"]
                ),
                abs(
                    bars[i]["low"]
                    - bars[i - 1]["close"]
                ),
            )
        )

    def wilder_series(values, length):

        out = [None] * len(values)

        if len(values) < length:
            return out

        current = sum(values[:length])

        out[length - 1] = current

        for j in range(length, len(values)):

            current = (
                current
                - (current / length)
                + values[j]
            )

            out[j] = current

        return out

    sm_tr = wilder_series(
        tr_values,
        period
    )

    sm_plus = wilder_series(
        plus_dm,
        period
    )

    sm_minus = wilder_series(
        minus_dm,
        period
    )

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

        pdi = (
            100.0 * sm_plus[i] / sm_tr[i]
        )

        mdi = (
            100.0 * sm_minus[i] / sm_tr[i]
        )

        denominator = pdi + mdi

        dx = (
            0.0
            if denominator == 0
            else 100.0 * abs(pdi - mdi) / denominator
        )

        dx_values.append(dx)

        plus_di_latest = pdi
        minus_di_latest = mdi

    if len(dx_values) < period:
        raise ValueError(
            "Not enough data to calculate ADX"
        )

    adx_value = wilder_average(
        dx_values,
        period
    )

    return (
        adx_value,
        plus_di_latest,
        minus_di_latest,
    )


# ============================================================
# MACD
# ============================================================

def macd_histogram(
    closes,
    fast=12,
    slow=26,
    signal=9
):

    fast_series = ema_series(
        closes,
        fast
    )

    slow_series = ema_series(
        closes,
        slow
    )

    macd_values = []

    for fast_value, slow_value in zip(
        fast_series,
        slow_series
    ):

        if (
            fast_value is not None
            and slow_value is not None
        ):

            macd_values.append(
                fast_value - slow_value
            )

    if len(macd_values) < signal:
        raise ValueError(
            "Not enough data for MACD"
        )

    signal_value = ema(
        macd_values,
        signal
    )

    return (
        macd_values[-1] - signal_value
    )


# ============================================================
# MARKET STRUCTURE
# ============================================================

def find_swing(bars, lookback=50):

    recent = bars[-lookback:]

    return (
        max(bar["high"] for bar in recent),
        min(bar["low"] for bar in recent),
    )


def fib_confluence(
    price,
    swing_high,
    swing_low,
    atr_value
):

    diff = swing_high - swing_low

    if diff <= 0:
        return None

    levels = {

        "38.2%": (
            swing_high - 0.382 * diff
        ),

        "50.0%": (
            swing_high - 0.5 * diff
        ),

        "61.8%": (
            swing_high - 0.618 * diff
        ),

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

    for i in range(
        len(recent) - 1,
        1,
        -1
    ):

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


def fvg_confluence(
    price,
    fvg,
    direction
):

    if not fvg:
        return None

    in_zone = (
        fvg["bottom"]
        <= price
        <= fvg["top"]
    )

    aligned = (
        (
            fvg["type"] == "Bullish"
            and direction == "BUY"
        )
        or
        (
            fvg["type"] == "Bearish"
            and direction == "SELL"
        )
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

def get_signal(now=None):

    now = now or datetime.now(timezone.utc)

    # Fetch native 1H and 4H candles.

    bars_1h = closed_bars(
        fetch_bars("1h", 220),
        1,
        now
    )

    bars_4h = closed_bars(
        fetch_bars("4h", 220),
        4,
        now
    )

    if len(bars_1h) < 60:

        raise ValueError(
            f"Insufficient closed 1H candles: {len(bars_1h)}"
        )

    if len(bars_4h) < 60:

        raise ValueError(
            f"Insufficient closed 4H candles: {len(bars_4h)}"
        )

    # Validate market data freshness.

    last_bar_time = parse_bar_time(
        bars_1h[-1]["datetime"]
    )

    data_age_hours = (
        now - (
            last_bar_time
            + timedelta(hours=1)
        )
    ).total_seconds() / 3600

    if data_age_hours > MAX_DATA_AGE_HOURS:

        raise ValueError(
            f"Market data is stale "
            f"({data_age_hours:.1f} hours old)"
        )

    # Prepare prices.

    closes_1h = [
        bar["close"]
        for bar in bars_1h
    ]

    closes_4h = [
        bar["close"]
        for bar in bars_4h
    ]

    price = closes_1h[-1]

    trend_price = closes_4h[-1]

    # Calculate indicators.

    ema50_4h = ema(
        closes_4h,
        50
    )

    ema200_4h = (
        ema(closes_4h, 200)
        if len(closes_4h) >= 200
        else None
    )

    rsi_1h = rsi(
        closes_1h,
        14
    )

    atr_1h = atr(
        bars_1h,
        14
    )

    adx_1h, plus_di, minus_di = adx(
        bars_1h,
        14
    )

    macd_hist = macd_histogram(
        closes_1h
    )

    # Determine higher-timeframe trend.

    if ema200_4h is not None:

        bullish_trend = (
            trend_price > ema50_4h
            and ema50_4h > ema200_4h
        )

        bearish_trend = (
            trend_price < ema50_4h
            and ema50_4h < ema200_4h
        )

    else:

        bullish_trend = (
            trend_price > ema50_4h
        )

        bearish_trend = (
            trend_price < ema50_4h
        )

    # Momentum confirmation.

    bullish_momentum = (
        rsi_1h >= 52
        and macd_hist > 0
        and plus_di > minus_di
    )

    bearish_momentum = (
        rsi_1h <= 48
        and macd_hist < 0
        and minus_di > plus_di
    )

    # Determine direction.

    if bullish_trend and bullish_momentum:

        direction = "BUY"

    elif bearish_trend and bearish_momentum:

        direction = "SELL"

    else:

        print(
            "No signal: trend and momentum are not aligned. "
            f"RSI={rsi_1h:.1f}, "
            f"ADX={adx_1h:.1f}, "
            f"MACD={macd_hist:.4f}"
        )

        return None

    # Trend strength filter.

    if adx_1h < MIN_ADX:

        print(
            f"No signal: weak trend strength. "
            f"ADX={adx_1h:.1f}"
        )

        return None

    # Structure analysis.

    swing_high, swing_low = find_swing(
        bars_1h
    )

    fib_note = fib_confluence(
        price,
        swing_high,
        swing_low,
        atr_1h
    )

    fvg = detect_recent_fvg(
        bars_1h
    )

    fvg_note = fvg_confluence(
        price,
        fvg,
        direction
    )

    # Weighted candidate scoring.

    score = 0

    score += 3

    score += 2

    score += 2

    if adx_1h >= 20:
        score += 1

    if fib_note:
        score += 1

    if fvg_note:
        score += 1

    if score < MIN_SETUP_SCORE:

        print(
            f"No signal: score {score}/10 "
            f"is below {MIN_SETUP_SCORE}/10"
        )

        return None

    # Avoid chasing extreme momentum.

    risk_flag = None

    if direction == "BUY" and rsi_1h >= 72:

        print("BUY rejected: excessively overbought.")

        return None

    if direction == "SELL" and rsi_1h <= 28:

        print("SELL rejected: excessively oversold.")

        return None

    if direction == "BUY" and rsi_1h >= 68:

        risk_flag = (
            "⚠️ Momentum extended — Exercise Caution"
        )

    elif direction == "SELL" and rsi_1h <= 32:

        risk_flag = (
            "⚠️ Momentum extended — Exercise Caution"
        )

    # Calculate trade levels.

    sign = (
        1
        if direction == "BUY"
        else -1
    )

    sl = (
        price
        - sign * SL_ATR * atr_1h
    )

    tps = [
        price + sign * multiple * atr_1h
        for multiple in TP_MULT
    ]

    zone_buffer = (
        ENTRY_ZONE_ATR * atr_1h
    )

    rr = (
        TP_MULT[-1] / SL_ATR
    )

    latest = bars_1h[-1]

    # Return signal and historical analysis features.

    return {

        "direction": direction,

        "price": price,

        "sl": sl,

        "tps": tps,

        "zone_low": price - zone_buffer,

        "zone_high": price + zone_buffer,

        "rr": rr,

        "conviction": (
            "🔥 A-Grade Setup"
            if score >= 8
            else "⚡ Standard Setup"
        ),

        "score": score,

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
                round(ema200_4h, 5)
                if ema200_4h is not None
                else None
            ),

            "rsi14_1h": round(rsi_1h, 3),

            "atr14_1h": round(atr_1h, 5),

            "adx14_1h": round(adx_1h, 3),

            "plus_di_1h": round(plus_di, 3),

            "minus_di_1h": round(minus_di, 3),

            "macd_hist_1h": round(macd_hist, 6),

            "market_regime": (
                "TRENDING"
                if adx_1h >= 20
                else "WEAK_TREND"
            ),

            "fib_confluence": fib_note,

            "fvg_confluence": fvg_note,

            "setup_score": score,

            "data_age_hours": round(
                data_age_hours,
                3
            ),

        },

    }


# ============================================================
# SIGNAL NUMBERING AND DUPLICATE PROTECTION
# ============================================================

def next_signal_number():

    state = load_json(
        STATE_FILE,
        {}
    )

    return int(
        state.get("signal_count", 0)
    ) + 1


def already_processed_candle(signal):

    state = load_json(
        STATE_FILE,
        {}
    )

    candle_key = signal["analysis"]["last_closed_1h"]

    if state.get("last_signal_candle") == candle_key:

        print(
            "Signal decision already processed "
            f"for candle {candle_key}"
        )

        return True

    return False


def record_published_signal(candle_key):

    state = load_json(
        STATE_FILE,
        {}
    )

    state["signal_count"] = (
        int(state.get("signal_count", 0)) + 1
    )

    state["last_signal_candle"] = candle_key

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# TELEGRAM CAPTION
# ============================================================

def caption(signal, now, signal_no):

    direction = signal["direction"]

    emoji = (
        "🟢"
        if direction == "BUY"
        else "🔴"
    )

    issued_str = now.strftime(
        "%d %b %Y, %H:%M UTC"
    )

    lines = [

        "JAY GOLD MASTER",

        DIVIDER,

        f"{emoji} {direction} — {LABEL}",

        f"Signal #{signal_no:03d} | {session_tag(now)}",

        signal["conviction"],

        f"📊 Setup Score: {signal['score']}/10",

        f"🕐 {signal['killzone']}",

    ]

    if signal.get("risk_flag"):

        lines.append(
            signal["risk_flag"]
        )

    if signal.get("fib_note"):

        lines.append(
            f"📐 Fib Confluence: "
            f"{signal['fib_note']} retracement"
        )

    if signal.get("fvg_note"):

        lines.append(
            f"🔲 {signal['fvg_note']}"
        )

    lines += [

        f"Issued: {issued_str}",

        DIVIDER,

        "",

        (
            f"Entry Zone: "
            f"`{signal['zone_low']:.2f}` - "
            f"`{signal['zone_high']:.2f}`"
        ),

        "",

    ]

    for index, target in enumerate(
        signal["tps"],
        1
    ):

        lines.append(
            f"🎯 TP{index}: `{target:.2f}`"
        )

    lines += [

        "",

        f"🛑 SL: `{signal['sl']:.2f}`",

        (
            f"⚖️ Risk:Reward — "
            f"1:{signal['rr']:.1f}"
        ),

        "",

        "⚠️ Trade responsibly. Use lower position sizes to avoid high risk.",

    ]

    return "\n".join(lines)


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():

    now = datetime.now(timezone.utc)

    if not is_market_open(now):

        print(
            "Gold market is closed — skipping this run."
        )

        return

    try:

        signal = get_signal(now)

    except Exception as error:

        print(
            f"Signal generation failed: {error}"
        )

        raise

    if signal is None:

        print(
            "No publishable Gold candidate this run."
        )

        return

    if already_processed_candle(signal):

        return

    signal_number = next_signal_number()

    # Generate existing signal card.

    make_signal_card(
        signal["direction"],
        LABEL,
        "/tmp/card.png"
    )

    # Publish using existing Telegram integration.

    send_photo(
        "/tmp/card.png",
        caption(
            signal,
            now,
            signal_number
        )
    )

    # Record successful publication.

    record_published_signal(
        signal["analysis"]["last_closed_1h"]
    )

    # Save trade for existing tracking system.

    trades = load_json(
        OPEN_TRADES_FILE,
        []
    )

    trade_id = (
        f"gold-{int(now.timestamp())}"
    )

    trades.append({

        "id": trade_id,

        "symbol": SYMBOL,

        "label": LABEL,

        "direction": signal["direction"],

        "entry": signal["price"],

        "sl": signal["sl"],

        "initial_sl": signal["sl"],

        "tps": signal["tps"],

        "tp_hit": [False] * 4,

        "opened_at": now.isoformat(),

        "entry_candle": signal["analysis"]["last_closed_1h"],

        "setup_score": signal["score"],

        "analysis": signal["analysis"],

        "tracking_version": 2,

    })

    save_json(
        OPEN_TRADES_FILE,
        trades
    )

    print(
        f"Published {signal['direction']} "
        f"{SYMBOL} signal #{signal_number:03d} "
        f"({signal['score']}/10)."
    )


if __name__ == "__main__":

    main()
