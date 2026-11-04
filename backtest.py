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
import re
import html
import math
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
        "risk_usd": risk,
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

    mean = sum(rs) / n

    if n > 1:
        variance = sum((r - mean) ** 2 for r in rs) / (n - 1)
        stderr = math.sqrt(variance) / math.sqrt(n)
    else:
        stderr = 0.0

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
        "avg_r": mean,
        "stderr": stderr,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "total_r": sum(rs),
        "pf": profit_factor,
        "max_dd": drawdown,
        "avg_hold": sum(t["hold"] for t in trades) / n,
    }


def stress_avg(trades, extra_cost):
    """Average R if the per-trade cost were `extra_cost` dollars instead."""
    if not trades:
        return 0.0

    delta = extra_cost - COST_USD

    return sum(
        t["r"] - delta / t["risk_usd"] for t in trades
    ) / len(trades)


# ============================================================
# MESSAGE FORMATTING (Telegram HTML)
# ============================================================

RULE = "━━━━━━━━━━━━━━━━━━"


def esc(text):
    return html.escape(str(text), quote=False)


def light(avg_r):
    if avg_r >= 0.10:
        return "🟢"
    if avg_r >= 0.0:
        return "🟡"
    return "🔴"


def pf_text(s):
    return "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"


def block(label, s, icon=None):
    """Three-line card for one strategy profile."""
    if not s:
        return f"{icon or '▫️'} <b>{esc(label)}</b>\n   No closed trades"

    icon = icon or light(s["avg_r"])

    return (
        f"{icon} <b>{esc(label)}</b>\n"
        f"   Trades <b>{s['n']}</b>  ·  Win {s['win_pct']:.0f}%  ·  "
        f"Loss {s['loss_pct']:.0f}%  ·  BE {s['flat_pct']:.0f}%\n"
        f"   Avg <b>{s['avg_r']:+.2f}R</b> (±{s['stderr']:.2f})  ·  "
        f"PF {pf_text(s)}\n"
        f"   Total {s['total_r']:+.0f}R  ·  Worst dip {s['max_dd']:.0f}R"
    )


def small_row(label, s):
    if not s:
        return f"▫️ {esc(label)}: no trades"

    return (
        f"{light(s['avg_r'])} <b>{esc(label)}</b>\n"
        f"   {s['n']} trades  ·  Win {s['win_pct']:.0f}%  ·  "
        f"Avg {s['avg_r']:+.2f}R  ·  PF {pf_text(s)}"
    )


def breakdown(trades, key_fn):
    groups = defaultdict(list)

    for trade in trades:
        groups[key_fn(trade)].append(trade)

    return [small_row(key, stats(groups[key])) for key in sorted(groups)]


def adx_bucket(trade):
    if trade["adx"] < 20:
        return "Trend ADX under 20"
    if trade["adx"] < 30:
        return "Trend ADX 20 to 30"
    return "Trend ADX 30+"


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text))


# ============================================================
# REPORT
# ============================================================

MEDALS = ["🥇", "🥈", "🥉"]


def build_sections(bars_1h, signals, evaluated):
    first = bars_1h[0]["datetime"][:10]
    last = bars_1h[-1]["datetime"][:10]

    days = max(
        1,
        (
            engine.parse_bar_time(bars_1h[-1]["datetime"])
            - engine.parse_bar_time(bars_1h[0]["datetime"])
        ).days,
    )

    per_day = len(signals) / max(1, days * 5 / 7)

    results = {}

    for profile in PROFILES:
        trades = []

        for signal in signals:
            outcome = simulate(profile, signal, bars_1h)
            if outcome:
                trades.append(outcome)

        results[profile["name"]] = trades

    live_name = PROFILES[0]["name"]
    live_trades = results[live_name]
    live = stats(live_trades)

    sections = []

    # ---- Header -------------------------------------------------
    sections.append(
        "📊 <b>JAY GOLD BACKTEST</b>  🔒 private\n"
        f"{RULE}\n"
        f"🗓 Data: {first} → {last}\n"
        f"🕯 {len(bars_1h):,} hourly candles checked\n"
        f"📡 Signals the live engine fires: <b>{len(signals)}</b> "
        f"({per_day:.1f} per trading day)\n"
        f"💵 Cost assumed: ${COST_USD:.2f} per trade"
    )

    # ---- Plain English ------------------------------------------
    if live:
        big_loss_small_win = live["avg_win"] < abs(live["avg_loss"])

        if live["win_pct"] >= 60 and big_loss_small_win:
            win_note = (
                f"Win rate looks great at <b>{live['win_pct']:.0f}%</b>, "
                f"but the average win is only <b>{live['avg_win']:+.2f}R</b> "
                f"while the average loss is <b>{live['avg_loss']:+.2f}R</b>."
            )
        else:
            win_note = (
                f"Win rate is <b>{live['win_pct']:.0f}%</b>. Average win "
                f"<b>{live['avg_win']:+.2f}R</b>, average loss "
                f"<b>{live['avg_loss']:+.2f}R</b>."
            )

        if live["avg_r"] <= 0:
            verdict_line = "👉 No edge after costs. Do not trade this as is."
        elif live["avg_r"] < 0.10 and big_loss_small_win:
            verdict_line = (
                "👉 Many small wins, fewer big losses. The edge exists "
                "but is thin."
            )
        elif live["avg_r"] < 0.10:
            verdict_line = "👉 A small edge. Worth improving, not scaling."
        else:
            verdict_line = "👉 A meaningful edge, pending the checks below."

        sections.append(
            f"🧠 <b>1. THE CURRENT SYSTEM IN PLAIN ENGLISH</b>\n"
            f"{RULE}\n"
            f"{win_note}\n\n"
            f"{light(live['avg_r'])} Net result per trade: "
            f"<b>{live['avg_r']:+.2f}R</b> (±{live['stderr']:.2f})\n"
            f"📈 Total over the period: <b>{live['total_r']:+.0f}R</b> "
            f"across {live['n']} signals\n"
            f"📉 Worst dip: {live['max_dd']:.0f}R\n\n"
            f"{verdict_line}"
        )

    # ---- All signals --------------------------------------------
    rows = [block(p["name"], stats(results[p["name"]])) for p in PROFILES]

    sections.append(
        "⚙️ <b>2. EVERY SIGNAL TRADED (like live)</b>\n"
        f"{RULE}\n" + "\n\n".join(rows)
    )

    # ---- One at a time ------------------------------------------
    single = {}

    for profile in PROFILES:
        single[profile["name"]] = one_at_a_time(results[profile["name"]])

    ranked = sorted(
        [p["name"] for p in PROFILES if single[p["name"]]],
        key=lambda name: stats(single[name])["avg_r"],
        reverse=True,
    )

    rows = []

    for place, name in enumerate(ranked):
        medal = MEDALS[place] if place < 3 else "▫️"
        rows.append(block(name, stats(single[name]), icon=medal))

    sections.append(
        "🎯 <b>3. ONE TRADE AT A TIME (ranked)</b>\n"
        "No overlapping trades, closer to what a person can follow.\n"
        f"{RULE}\n" + "\n\n".join(rows)
    )

    # ---- Hold-up test -------------------------------------------
    split_index = len(bars_1h) // 2

    rows = []
    consistent = []

    for name in ranked:
        trades = single[name]
        first_half = [t for t in trades if t["entry_index"] < split_index]
        second_half = [t for t in trades if t["entry_index"] >= split_index]

        a = stats(first_half)
        b = stats(second_half)

        if not a or not b:
            continue

        ok = a["avg_r"] > 0 and b["avg_r"] > 0

        if ok:
            consistent.append(name)

        rows.append(
            f"{'✅' if ok else '⚠️'} <b>{esc(name)}</b>\n"
            f"   1st half {a['avg_r']:+.2f}R ({a['n']})  ·  "
            f"2nd half {b['avg_r']:+.2f}R ({b['n']})"
        )

    sections.append(
        "🔬 <b>4. HOLD-UP TEST</b>\n"
        "Does the result survive in both halves of the data? "
        "✅ = positive in both.\n"
        f"{RULE}\n" + "\n".join(rows)
    )

    # ---- Cost stress --------------------------------------------
    rows = []

    for name in [live_name] + [n for n in ranked[:1] if n != live_name]:
        trades = single[name]

        rows.append(
            f"💵 <b>{esc(name)}</b>\n"
            f"   $0.30 → {stress_avg(trades, 0.30):+.2f}R  ·  "
            f"$0.60 → {stress_avg(trades, 0.60):+.2f}R  ·  "
            f"$1.00 → {stress_avg(trades, 1.00):+.2f}R"
        )

    sections.append(
        "🧾 <b>5. COST STRESS</b>\n"
        "Average R per trade if spread and slippage are worse than assumed.\n"
        f"{RULE}\n" + "\n".join(rows)
    )

    # ---- Where the edge lives -----------------------------------
    parts = ["By direction"]
    parts += breakdown(live_trades, lambda t: t["direction"])
    parts.append("")
    parts.append("By session")
    parts += breakdown(live_trades, lambda t: t["session"])
    parts.append("")
    parts.append("By trend strength")
    parts += breakdown(live_trades, adx_bucket)

    sections.append(
        "🧭 <b>6. WHERE THE CURRENT EDGE LIVES</b>\n"
        "Live rules, all signals.\n"
        f"{RULE}\n" + "\n".join(parts)
    )

    # ---- Verdict -----------------------------------------------
    best = ranked[0] if ranked else None
    notes = []

    if best:
        s = stats(single[best])
        notes.append(
            f"🏆 Highest average R, one at a time: <b>{esc(best)}</b> "
            f"({s['avg_r']:+.2f}R)"
        )

        if best in consistent:
            notes.append("✅ It stayed positive in both halves of the data.")
        else:
            notes.append("⚠️ It did NOT stay positive in both halves.")

    notes.append(
        "📏 The ± figure is the statistical noise. When two strategies "
        "differ by less than about twice that, treat them as equal."
    )
    notes.append(
        "🔎 Winning sessions or trend buckets may be luck at these sample "
        "sizes. Do not filter on them without more data."
    )
    notes.append(
        "🧪 Backtests are evidence, not a promise. A change is only real "
        "if it also holds on data it was not tuned on."
    )

    sections.append(
        "✅ <b>7. VERDICT</b>\n" + f"{RULE}\n" + "\n\n".join(notes)
    )

    return sections


def build_report(bars_1h, signals, evaluated):
    return build_sections(bars_1h, signals, evaluated)


# ============================================================
# DELIVERY
# ============================================================

def pack_messages(sections, limit=3800):
    messages = []
    current = ""

    for section in sections:
        candidate = f"{current}\n\n{section}" if current else section

        if len(candidate) > limit and current:
            messages.append(current)
            current = section
        else:
            current = candidate

    if current:
        messages.append(current)

    return messages


def send_admin(sections):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("ADMIN_CHAT_ID", "").strip()

    if not token or not chat:
        print("ADMIN_CHAT_ID not set: report printed in the log only.")
        return

    for part in pack_messages(sections):
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        response = requests.post(
            url,
            json={
                "chat_id": chat,
                "text": part,
                "parse_mode": "HTML",
            },
            timeout=20,
        )

        if not response.ok:
            print("HTML send failed, retrying as plain text:", response.text)

            requests.post(
                url,
                json={"chat_id": chat, "text": strip_tags(part)},
                timeout=20,
            )


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

    sections = run(bars_1h, bars_4h)

    plain = "\n\n".join(strip_tags(section) for section in sections)

    print(plain)

    with open("backtest_report.txt", "w") as handle:
        handle.write(plain)

    send_admin(sections)


if __name__ == "__main__":
    main()
