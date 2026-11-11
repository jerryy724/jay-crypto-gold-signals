"""
JAY GOLD MASTER - Signal Engine v2.1

- Three take-profit ladder: TP1 1R, TP2 2R, TP3 3R (R = 1.5 ATR),
  closing 40% / 30% / 30%, stop to entry after TP1.
- New signals are allowed while other trades are open, up to
  MAX_OPEN_TRADES at the same time (risk cap).
- 72 market-hour time stop (handled by watch.py).
- Entry rules live in PARAMS so they can be changed in one place after a
  backtest (see freq_test.py).
- Private admin diagnostics: you are told WHY the engine is waiting, and
  immediately if a run fails (needs the ADMIN_CHAT_ID secret).
"""

import time
from datetime import datetime, timezone, timedelta

from common import (
    send_photo,
    load_json,
    save_json,
    DATA_DIR,
    OPEN_TRADES_FILE,
    STATE_FILE,
    is_market_open,
)

from indicators import (
    SYMBOL,
    parse_bar_time,
    fetch_bars,
    closed_bars,
    ema_series,
    ema,
    wilder_average,
    rsi,
    true_ranges,
    atr,
    adx,
    macd_histogram,
    find_swing,
    fib_confluence,
    detect_recent_fvg,
    fvg_confluence,
)

from cards import make_signal_card
from radar import resolve_forming
from admin import notify
from tradeutil import market_hours_between, MAX_HOLD_MARKET_HOURS


# ============================================================
# CONFIGURATION
# ============================================================

LABEL = "GOLD (XAU/USD)"

DIVIDER = "━━━━━━━━━━━━━━━"

STRATEGY_VERSION = "v2.1"

ENTRY_ZONE_ATR = 0.15

# R (one unit of risk) = SL_ATR x ATR.
SL_ATR = 1.5

# Targets in ATR multiples: 1R, 2R, 3R.
TP_MULT = (1.5, 3.0, 4.5)

# Share of the position closed at each target.
TP_WEIGHTS = (0.4, 0.3, 0.3)

# Entry rules. Change these only after a backtest says so.
#   trend_mode:   "STRICT"    price > 4H EMA50 > EMA200 (or the mirror)
#                 "STRUCTURE" 4H EMA50 > EMA200 only (allows pullback resumes)
#   rsi_buy / rsi_sell: 1H RSI thresholds
#   adx_min:      minimum 1H trend strength
#   momentum_min: how many of (RSI, MACD, DI) must agree: 3 or 2
PARAMS = {
    "trend_mode": "STRICT",
    "rsi_buy": 52,
    "rsi_sell": 48,
    "adx_min": 14.0,
    "momentum_min": 3,
}

# Several trades may be open at once. This caps the total risk.
ALLOW_CONCURRENT_TRADES = True
MAX_OPEN_TRADES = 100

# Evidence for skipping the Asian session is weak, so it stays off.
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


def max_open_trades():
    return MAX_OPEN_TRADES if ALLOW_CONCURRENT_TRADES else 1


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

    if ema200_4h is not None and PARAMS["trend_mode"] == "STRUCTURE":
        bullish_trend = ema50_4h > ema200_4h
        bearish_trend = ema50_4h < ema200_4h
    elif ema200_4h is not None:
        bullish_trend = trend_price > ema50_4h and ema50_4h > ema200_4h
        bearish_trend = trend_price < ema50_4h and ema50_4h < ema200_4h
    else:
        bullish_trend = trend_price > ema50_4h
        bearish_trend = trend_price < ema50_4h

    # Momentum confirmation (how many of RSI / MACD / DI must agree).

    need = PARAMS["momentum_min"]

    buy_votes = sum([
        rsi_1h >= PARAMS["rsi_buy"],
        macd_hist > 0,
        plus_di > minus_di,
    ])

    sell_votes = sum([
        rsi_1h <= PARAMS["rsi_sell"],
        macd_hist < 0,
        minus_di > plus_di,
    ])

    bullish_momentum = buy_votes >= need
    bearish_momentum = sell_votes >= need

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

    if adx_1h < PARAMS["adx_min"]:
        note(
            "WEAK_TREND",
            f"WAIT: {label}, momentum agrees but trend strength is too "
            f"low (ADX {adx_1h:.1f}, needs {PARAMS['adx_min']:.0f}).",
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
# OPEN TRADE LIMIT (risk cap, not a one-at-a-time rule)
# ============================================================

def open_gold_trades(now):
    """
    Gold trades that are still open. A trade older than the time stop plus
    a safety margin is treated as stuck and ignored, so one broken record
    can never silence the system.
    """

    trades = load_json(OPEN_TRADES_FILE, [])

    if not isinstance(trades, list):
        return []

    limit = MAX_HOLD_MARKET_HOURS + 6
    active = []

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
            active.append(trade)

    return active


def trade_limit_reached(now):
    return len(open_gold_trades(now)) >= max_open_trades()


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

def caption(signal, now, signal_no, open_count=0):
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

    if open_count:
        lines.append(f"📂 Other gold trades still open: {open_count}")

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
        "⚠️ Trade responsibly. Each signal is its own trade, so size every "
        "one small.",
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

    # Risk cap: only stop when the maximum number of trades is already open.
    open_now = open_gold_trades(now)

    if len(open_now) >= max_open_trades():
        reason = (
            f"WAIT: {len(open_now)} gold trades are open, which is the "
            f"limit ({max_open_trades()}). New signals resume as trades close."
        )
        print(reason)
        log_scan(now, "LIMIT", reason)
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

    send_photo(
        "/tmp/card.png",
        caption(signal, now, signal_number, len(open_now)),
    )

    record_published_signal(signal["analysis"]["last_closed_1h"])

    trades = load_json(OPEN_TRADES_FILE, [])

    trades.append({
        "id": f"gold-{int(now.timestamp())}",
        "signal_no": signal_number,
        "symbol": SYMBOL,
        # The label is shown on every tracker update, so each trade can be
        # told apart when several are open.
        "label": f"{LABEL} · #{signal_number:03d} {signal['direction']}",
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
        f"{signal['direction']} at {signal['price']:.2f} "
        f"({len(open_now) + 1} open)\n{DECISION['text']}"
    )

    print(
        f"Published {signal['direction']} {SYMBOL} "
        f"signal #{signal_number:03d}."
    )


if __name__ == "__main__":
    main()
