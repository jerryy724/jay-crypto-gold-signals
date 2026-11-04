"""
JAY GOLD MASTER - Backtester (Phase 1, step 1)

Replays the LIVE signal engine (main.get_signal) over historical Twelve Data
candles, then simulates how each signal would have played out under several
trade-management profiles. Purpose: measure the real expectancy of the
current system and test alternatives BEFORE changing live behaviour.

Run from GitHub Actions (workflow: Backtest). Needs the same secrets as the
signal job. Optional secret ADMIN_CHAT_ID sends the report privately to you.

Honest limits (read these):
- Uses 1H candles. If a candle touches both stop and target, the stop is
  assumed to hit first (conservative).
- Break-even stop applies from the candle AFTER TP1 is reached.
- One fixed spread/slippage cost per trade (COST_USD below).
- Results are historical. They are evidence, not a promise.
"""

import io
import os
import bisect
from collections import defaultdict
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta

import requests

import main as engine
from common import is_market_open


# ============================================================
# SETTINGS
# ============================================================

HISTORY_1H = 5000
HISTORY_4H = 2000

# Round-trip cost per trade in gold dollars (spread + slippage assumption).
COST_USD = 0.30

# A trade within +/- this many R counts as breakeven in the win/loss split.
BE_BAND_R = 0.05

# SL = sl x ATR, targets = multiples x ATR, weights = share closed at each.
PROFILES = [
    {
        "name": "LIVE (current rules)",
        "sl": 1.5,
        "tps": (0.4, 1.0, 1.8, 2.8),
        "weights": (0.25, 0.25, 0.25, 0.25),
        "be_after": 0,
        "max_hold": None,
    },
    {
        "name": "LIVE, no breakeven",
        "sl": 1.5,
        "tps": (0.4, 1.0, 1.8, 2.8),
        "weights": (0.25, 0.25, 0.25, 0.25),
        "be_after": None,
        "max_hold": None,
    },
    {
        "name": "LIVE, 48h time stop",
        "sl": 1.5,
        "tps": (0.4, 1.0, 1.8, 2.8),
        "weights": (0.25, 0.25, 0.25, 0.25),
        "be_after": 0,
        "max_hold": 48,
    },
    {
        "name": "Single 1.5R target",
        "sl": 1.5,
        "tps": (2.25,),
        "weights": (1.0,),
        "be_after": None,
        "max_hold": 48,
    },
    {
        "name": "Split 1R/2R + BE",
        "sl": 1.5,
        "tps": (1.5, 3.0),
        "weights": (0.5, 0.5),
        "be_after": 0,
        "max_hold": 72,
    },
    {
        "name": "Runner 1R/2R/3R + BE",
        "sl": 1.5,
        "tps": (1.5, 3.0, 4.5),
        "weights": (0.4, 0.3, 0.3),
        "be_after": 0,
        "max_hold": 72,
    },
    {
        "name": "Tight stop 1.0 ATR",
        "sl": 1.0,
        "tps": (1.0, 2.0, 3.0),
        "weights": (0.4, 0.3, 0.3),
        "be_after": 0,
        "max_hold": 72,
    },
]


# ============================================================
# REPLAY THE LIVE ENGINE WITHOUT LOOK-AHEAD
# ============================================================

def prepare(bars):
    return [engine.parse_bar_time(bar["datetime"]) for bar in bars]


def replay_signals(bars_1h, bars_4h):
    """
    For every closed 1H candle, ask the live engine what it would have done.
    Data is cut so the engine only sees candles that were closed at that time.
    """

    starts_1h = prepare(bars_1h)
    starts_4h = prepare(bars_4h)

    clock = {"now": None}
    original_fetch = engine.fetch_bars

    def fake_fetch(interval="1h", outputsize=220):
        if interval == "1h":
            source, starts, hours = bars_1h, starts_1h, 1
        else:
            source, starts, hours = bars_4h, starts_4h, 4

        cut = bisect.bisect_right(
            starts,
            clock["now"] - timedelta(hours=hours),
        )

        return source[max(0, cut - 219):cut]

    engine.fetch_bars = fake_fetch

    signals = []
    evaluated = 0

    try:
        for i in range(len(bars_1h)):
            now = starts_1h[i] + timedelta(hours=1)

            if not is_market_open(now):
                continue

            if i < 230:
                continue

            cut_4h = bisect.bisect_right(
                starts_4h,
                now - timedelta(hours=4),
            )

            if cut_4h < 205:
                continue

            clock["now"] = now

            try:
                with redirect_stdout(io.StringIO()):
                    result = engine.get_signal(now)
            except Exception:
                continue

            evaluated += 1

            if result is None:
                continue

            analysis = result["analysis"]

            signals.append({
                "index": i,
                "time": now,
                "direction": result["direction"],
                "entry": result["price"],
                "atr": analysis["atr14_1h"],
                "adx": analysis["adx14_1h"],
                "rsi": analysis["rsi14_1h"],
                "score": result["score"],
                "session": engine.session_tag(now),
            })

    finally:
        engine.fetch_bars = original_fetch

    return signals, evaluated


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate(profile, signal, bars):
    sign = 1 if signal["direction"] == "BUY" else -1
    entry = signal["entry"]
    atr = signal["atr"]

    risk = profile["sl"] * atr
    stop = entry - sign * risk
    targets = [entry + sign * m * atr for m in profile["tps"]]
    weights = profile["weights"]

    hit = [False] * len(targets)
    remaining = 1.0
    realized = 0.0

    exit_index = None
    reason = None

    i = signal["index"]

    for j in range(i + 1, len(bars)):
        high = bars[j]["high"]
        low = bars[j]["low"]

        stop_hit = low <= stop if sign == 1 else high >= stop

        # Conservative: the stop is checked before targets in the same candle.
        if stop_hit:
            realized += remaining * sign * (stop - entry)
            remaining = 0.0
            exit_index = j
            reason = "BE" if abs(stop - entry) < 1e-9 else "SL"
            break

        for k, target in enumerate(targets):
            reached = high >= target if sign == 1 else low <= target

            if not hit[k] and reached:
                hit[k] = True
                realized += weights[k] * sign * (target - entry)
                remaining -= weights[k]

        if remaining <= 1e-9:
            exit_index = j
            reason = "TP"
            break

        be_after = profile["be_after"]

        if be_after is not None and hit[be_after]:
            stop = entry

        max_hold = profile["max_hold"]

        if max_hold and (j - i) >= max_hold:
            realized += remaining * sign * (bars[j]["close"] - entry)
            remaining = 0.0
            exit_index = j
            reason = "TIME"
            break

    if exit_index is None:
        return None

    realized -= COST_USD

    return {
        "entry_index": i,
        "exit_index": exit_index,
        "hold": exit_index - i,
        "r": realized / risk,
        "reason": reason,
        "tps_hit": sum(hit),
        "direction": signal["direction"],
        "session": signal["session"],
        "adx": signal["adx"],
        "rsi": signal["rsi"],
        "score": signal["score"],
        "time": signal["time"],
    }


def one_at_a_time(trades):
    kept = []
    free_from = -1

    for trade in sorted(trades, key=lambda t: t["entry_index"]):
        if trade["entry_index"] >= free_from:
            kept.append(trade)
            free_from = trade["exit_index"]

    return kept


# ============================================================
# STATISTICS
# ============================================================

def stats(trades):
    n = len(trades)

    if n == 0:
        return None

    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > BE_BAND_R]
    losses = [r for r in rs if r < -BE_BAND_R]
    flat = n - len(wins) - len(losses)

    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)

    profit_factor = (
        gross_win / gross_loss if gross_loss > 0 else float("inf")
    )

    equity = 0.0
    peak = 0.0
    drawdown = 0.0

    for trade in sorted(trades, key=lambda t: t["exit_index"]):
        equity += trade["r"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)

    return {
        "n": n,
        "win_pct": 100.0 * len(wins) / n,
        "loss_pct": 100.0 * len(losses) / n,
        "flat_pct": 100.0 * flat / n,
        "avg_r": sum(rs) / n,
        "total_r": sum(rs),
        "pf": profit_factor,
        "max_dd": drawdown,
        "avg_hold": sum(t["hold"] for t in trades) / n,
    }


def line(label, s):
    if not s:
        return f"{label}: no trades"

    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"

    return (
        f"{label}: n={s['n']} win={s['win_pct']:.0f}% "
        f"loss={s['loss_pct']:.0f}% be={s['flat_pct']:.0f}% "
        f"avg={s['avg_r']:+.2f}R PF={pf} "
        f"total={s['total_r']:+.0f}R DD={s['max_dd']:.0f}R"
    )


def breakdown(trades, key_fn):
    groups = defaultdict(list)

    for trade in trades:
        groups[key_fn(trade)].append(trade)

    rows = []

    for key in sorted(groups):
        rows.append(line(f"  {key}", stats(groups[key])))

    return rows


def adx_bucket(trade):
    if trade["adx"] < 20:
        return "ADX <20"
    if trade["adx"] < 30:
        return "ADX 20-30"
    return "ADX 30+"


# ============================================================
# REPORT
# ============================================================

def build_report(bars_1h, signals, evaluated):
    first = bars_1h[0]["datetime"][:10]
    last = bars_1h[-1]["datetime"][:10]

    days = max(
        1,
        (
            engine.parse_bar_time(bars_1h[-1]["datetime"])
            - engine.parse_bar_time(bars_1h[0]["datetime"])
        ).days,
    )

    lines = [
        "JAY GOLD BACKTEST (private)",
        f"Data: {first} to {last}, {len(bars_1h)} 1H candles",
        f"Hours evaluated: {evaluated}",
        f"Signals the live engine would fire: {len(signals)} "
        f"({len(signals) / max(1, days * 5 / 7):.1f} per trading day)",
        f"Cost assumed: ${COST_USD:.2f} per trade",
        "",
    ]

    results = {}

    for profile in PROFILES:
        trades = []

        for signal in signals:
            outcome = simulate(profile, signal, bars_1h)
            if outcome:
                trades.append(outcome)

        results[profile["name"]] = trades

    lines.append("ALL SIGNALS (every signal traded, like live)")

    for profile in PROFILES:
        name = profile["name"]
        lines.append(line(name, stats(results[name])))

    lines.append("")
    lines.append("ONE TRADE AT A TIME (no overlapping trades)")

    best_name = None
    best_avg = None

    for profile in PROFILES:
        name = profile["name"]
        single = stats(one_at_a_time(results[name]))
        lines.append(line(name, single))

        if single and single["n"] >= 30:
            if best_avg is None or single["avg_r"] > best_avg:
                best_avg = single["avg_r"]
                best_name = name

    live_trades = results[PROFILES[0]["name"]]

    lines.append("")
    lines.append("LIVE RULES BREAKDOWN (all signals)")
    lines.append("By direction")
    lines += breakdown(live_trades, lambda t: t["direction"])
    lines.append("By session")
    lines += breakdown(live_trades, lambda t: t["session"])
    lines.append("By trend strength")
    lines += breakdown(live_trades, adx_bucket)

    lines.append("")

    if best_name:
        lines.append(f"Best expectancy (one at a time, n>=30): {best_name}")
    else:
        lines.append("Not enough closed trades to rank profiles.")

    lines.append(
        "Reminder: backtests are evidence, not a promise. "
        "Judge a change only if it also holds on data it was not tuned on."
    )

    return "\n".join(lines)


# ============================================================
# DELIVERY
# ============================================================

def send_admin(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("ADMIN_CHAT_ID", "").strip()

    if not token or not chat:
        print("ADMIN_CHAT_ID not set: report printed in the log only.")
        return

    chunk = ""
    chunks = []

    for row in text.split("\n"):
        if len(chunk) + len(row) + 1 > 3800:
            chunks.append(chunk)
            chunk = ""
        chunk += row + "\n"

    if chunk:
        chunks.append(chunk)

    for part in chunks:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": part},
            timeout=20,
        )

        if not response.ok:
            print("Telegram admin send failed:", response.text)


def run(bars_1h, bars_4h):
    signals, evaluated = replay_signals(bars_1h, bars_4h)
    return build_report(bars_1h, signals, evaluated)


def main():
    print("Downloading history from Twelve Data...")

    bars_1h = engine.fetch_bars("1h", HISTORY_1H)
    bars_4h = engine.fetch_bars("4h", HISTORY_4H)

    print(f"Got {len(bars_1h)} 1H and {len(bars_4h)} 4H candles.")

    if len(bars_1h) < 600 or len(bars_4h) < 300:
        raise RuntimeError("Not enough history returned to backtest.")

    report = run(bars_1h, bars_4h)

    print(report)

    with open("backtest_report.txt", "w") as handle:
        handle.write(report)

    send_admin(report)


if __name__ == "__main__":
    main()
