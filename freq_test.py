"""
JAY GOLD - Signal frequency test

Question: can we get more signals per day WITHOUT destroying the edge?

Each candidate relaxes the entry rules in main.PARAMS. For each one it
replays the live engine over about 2.4 years of hourly gold, then simulates
the live exit ladder (1R / 2R / 3R, 40/30/30, stop to entry after TP1,
72 market-hour time stop) with several trades allowed open at once (the
same cap as live).

A candidate only passes when:
- it is positive on the first 60% (practice) AND the last 40% (unseen),
- it has at least 150 unseen trades,
- and its edge is statistically visible on a DAILY basis (z >= 1.5), which
  corrects for overlapping trades moving together.

Needs history.py, backtest.py, main.py. Sends the report to ADMIN_CHAT_ID.
"""

import math

import backtest as bt
import history
import main as engine

PAGES_1H = 3
PAGES_4H = 1

SPLIT_FRACTION = 0.60
MIN_UNSEEN_TRADES = 150
MIN_Z = 1.5

EXIT = next(p for p in bt.PROFILES if p["name"] == "Runner 1R/2R/3R + BE")

CANDIDATES = [
    ("A. Current rules", "What runs live today", {}),
    (
        "B. Pullback resumes",
        "4H structure only (EMA50 above EMA200), no price-above-EMA50 gate",
        {"trend_mode": "STRUCTURE"},
    ),
    (
        "C. 2 of 3 momentum",
        "Any two of RSI, MACD, DI must agree",
        {"momentum_min": 2},
    ),
    (
        "D. Softer RSI",
        "RSI 50 instead of 52/48",
        {"rsi_buy": 50, "rsi_sell": 50},
    ),
    (
        "E. Lower ADX floor",
        "Trend strength minimum 10 instead of 14",
        {"adx_min": 10.0},
    ),
    (
        "F. Pullbacks + 2 of 3",
        "Candidates B and C together",
        {"trend_mode": "STRUCTURE", "momentum_min": 2},
    ),
    (
        "G. Pullbacks + 2 of 3 + softer RSI",
        "B, C and D together",
        {
            "trend_mode": "STRUCTURE",
            "momentum_min": 2,
            "rsi_buy": 50,
            "rsi_sell": 50,
        },
    ),
    (
        "H. Loosest",
        "B, C, D and ADX floor 10",
        {
            "trend_mode": "STRUCTURE",
            "momentum_min": 2,
            "rsi_buy": 50,
            "rsi_sell": 50,
            "adx_min": 10.0,
        },
    ),
]


# ============================================================
# RUN ONE CANDIDATE
# ============================================================

def cap_open(trades, cap):
    """Allow a trade only if fewer than `cap` trades are open at its entry."""

    kept = []
    open_exits = []

    for trade in sorted(trades, key=lambda t: t["entry_index"]):
        open_exits = [x for x in open_exits if x > trade["entry_index"]]

        if len(open_exits) < cap:
            kept.append(trade)
            open_exits.append(trade["exit_index"])

    return kept


def run_candidate(params, bars_1h, bars_4h):
    saved = dict(engine.PARAMS)
    engine.PARAMS.update(params)

    try:
        signals, _ = bt.replay_signals(bars_1h, bars_4h)
    finally:
        engine.PARAMS.clear()
        engine.PARAMS.update(saved)

    trades = []

    for signal in signals:
        trade = bt.simulate(EXIT, signal, bars_1h)

        if trade:
            trades.append(trade)

    return cap_open(trades, engine.max_open_trades())


def daily_z(trades):
    """Edge strength using one number per day (handles overlapping trades)."""

    by_day = {}

    for trade in trades:
        day = trade["time"].date()
        by_day[day] = by_day.get(day, 0.0) + trade["r"]

    values = list(by_day.values())

    if len(values) < 20:
        return 0.0

    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)

    if var <= 0:
        return 0.0

    return mean / (math.sqrt(var) / math.sqrt(len(values)))


def max_overlap(trades):
    events = []

    for trade in trades:
        events.append((trade["entry_index"], 1))
        events.append((trade["exit_index"], -1))

    events.sort(key=lambda e: (e[0], e[1]))

    current = 0
    peak = 0

    for _, step in events:
        current += step
        peak = max(peak, current)

    return peak


def trading_day_set(bars_1h):
    days = set()

    for bar in bars_1h:
        stamp = engine.parse_bar_time(bar["datetime"])

        if stamp.weekday() < 5:
            days.add(stamp.date())

    return days


# ============================================================
# REPORT
# ============================================================

def build_sections(bars_1h, results):
    split_index = int(len(bars_1h) * SPLIT_FRACTION)
    all_days = trading_day_set(bars_1h)
    days = max(1, len(all_days))

    first = bars_1h[0]["datetime"][:10]
    last = bars_1h[-1]["datetime"][:10]
    split_date = bars_1h[split_index]["datetime"][:10]

    sections = [
        "📡 <b>JAY GOLD SIGNAL FREQUENCY TEST</b>  🔒 private\n"
        f"{bt.RULE}\n"
        f"🗓 {first} → {last}\n"
        f"🔵 Practice: {first} → {split_date}\n"
        f"🟣 Unseen: {split_date} → {last}\n"
        f"🎯 Exit: live ladder 1R/2R/3R (40/30/30), stop to entry after TP1\n"
        f"📂 Up to {engine.max_open_trades()} trades open at once\n"
        f"💵 Cost ${bt.COST_USD:.2f} per trade"
    ]

    cards = []
    passing = []

    for name, description, params, trades in results:
        practice = [t for t in trades if t["entry_index"] < split_index]
        unseen = [t for t in trades if t["entry_index"] >= split_index]

        p = bt.stats(practice)
        u = bt.stats(unseen)
        a = bt.stats(trades)

        if not p or not u or not a:
            cards.append(f"⚪ <b>{bt.esc(name)}</b>\n   Too few trades")
            continue

        z = daily_z(trades)
        per_day = len(trades) / days
        active_days = len({t["time"].date() for t in trades})

        icon = "⚠️"

        if u["n"] < MIN_UNSEEN_TRADES:
            icon = "⚪"
        elif p["avg_r"] > 0 and u["avg_r"] > 0 and z >= MIN_Z:
            icon = "✅"
            passing.append((name, params, per_day, a, z))

        cards.append(
            f"{icon} <b>{bt.esc(name)}</b>\n"
            f"   📝 {bt.esc(description)}\n"
            f"   📡 <b>{per_day:.1f} signals per trading day</b> · "
            f"signals on {100 * active_days / days:.0f}% of days\n"
            f"   🔵 Practice {p['avg_r']:+.2f}R ({p['n']}) · "
            f"🟣 Unseen {u['avg_r']:+.2f}R ({u['n']})\n"
            f"   📈 Total {a['total_r']:+.0f}R · Worst dip {a['max_dd']:.0f}R · "
            f"Edge z-score {z:.1f}\n"
            f"   📂 Most trades open at once: {max_overlap(trades)}"
        )

    sections.append(
        "🏁 <b>1. EVERY CANDIDATE</b>\n"
        "✅ passes all checks · ⚠️ fails · ⚪ too few trades\n"
        f"{bt.RULE}\n" + "\n\n".join(cards)
    )

    notes = []

    if passing:
        passing.sort(key=lambda item: item[2], reverse=True)

        name, params, per_day, a, z = passing[0]

        notes.append(
            f"🏆 Most signals that still pass: <b>{bt.esc(name)}</b> "
            f"at {per_day:.1f} signals per trading day."
        )

        shown = ", ".join(
            f'"{key}": {value!r}' for key, value in params.items()
        ) or "no change from today"

        notes.append(
            "🔧 To use it, set these values inside PARAMS in main.py:\n"
            f"{bt.esc(shown)}"
        )

        if per_day < 7:
            notes.append(
                "ℹ️ That is below your 7 to 10 target. We do not lower "
                "quality just to reach a number."
            )
    else:
        notes.append(
            "🔴 No candidate passed every check. The current rules stay. "
            "Frequency cannot be raised honestly with these filters."
        )

    notes.append(
        f"⚠️ {len(results)} candidates were tested, so one can look good by "
        "luck. Run any new setting for 2 weeks and compare live results "
        "with this report."
    )
    notes.append(
        "📐 Stacking: several signals open at once means several risks at "
        "once. Size each signal small."
    )

    sections.append("✅ <b>2. VERDICT</b>\n" + f"{bt.RULE}\n" + "\n\n".join(notes))

    return sections


def run(bars_1h, bars_4h):
    results = []

    for name, description, params in CANDIDATES:
        trades = run_candidate(params, bars_1h, bars_4h)
        results.append((name, description, params, trades))

    return build_sections(bars_1h, results)


def main():
    print("Downloading long history from Twelve Data...")

    bars_1h = history.load("1h", PAGES_1H)
    bars_4h = history.load("4h", PAGES_4H)

    print(f"Got {len(bars_1h)} 1H and {len(bars_4h)} 4H candles.")

    if len(bars_1h) < 600 or len(bars_4h) < 300:
        raise RuntimeError("Not enough history returned to backtest.")

    sections = run(bars_1h, bars_4h)

    plain = "\n\n".join(bt.strip_tags(section) for section in sections)

    print(plain)

    with open("freq_test_report.txt", "w") as handle:
        handle.write(plain)

    bt.send_admin(sections)


if __name__ == "__main__":
    main()
