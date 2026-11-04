"""
JAY GOLD MASTER - Strategy v2 test lane (Phase 1, step 2)

Tests candidate rule changes on top of the live engine's signals.
Method (this is what keeps the test honest):
- Rules are chosen and ranked on the FIRST 60% of history ("practice").
- Every rule is then checked on the LAST 40% ("unseen"). A rule only counts
  if it also works there.
- Every rule uses the same exit (single 1.5R target) and the same costs, and
  one trade at a time, so rules are compared fairly.
- A new 0-10 setup score is built from real conditions and checked against
  actual outcomes, to see whether higher scores really do better.

Reuses backtest.py (do not delete it). Sends the report to ADMIN_CHAT_ID.
"""

import os

import backtest as bt

engine = bt.engine

SPLIT_FRACTION = 0.60
MIN_TRADES_UNSEEN = 40
EXIT = next(p for p in bt.PROFILES if p["name"] == "Single 1.5R target")

GOOD_SESSIONS = (
    "London Session",
    "London/NY Overlap",
    "New York Session",
)


# ============================================================
# COLLECT LIVE-ENGINE SIGNALS WITH THEIR FULL ANALYSIS
# ============================================================

def collect(bars_1h, bars_4h):
    seen = {}
    original = engine.get_signal

    def spy(now=None):
        result = original(now)
        if result is not None:
            seen[now] = result
        return result

    engine.get_signal = spy

    try:
        signals, evaluated = bt.replay_signals(bars_1h, bars_4h)
    finally:
        engine.get_signal = original

    usable = []

    for signal in signals:
        result = seen.get(signal["time"])

        if not result:
            continue

        signal["an"] = result["analysis"]
        signal["fib"] = bool(result.get("fib_note"))
        signal["fvg"] = bool(result.get("fvg_note"))
        usable.append(signal)

    return usable, evaluated


# ============================================================
# FEATURES AND THE NEW SETUP SCORE
# ============================================================

def distance_atr(signal):
    a = signal["an"]
    return abs(a["price"] - a["ema50_4h"]) / a["atr14_1h"]


def di_gap(signal):
    a = signal["an"]
    return abs(a["plus_di_1h"] - a["minus_di_1h"])


def rsi_points(signal):
    rsi = signal["rsi"]

    if signal["direction"] == "BUY":
        if 55 <= rsi <= 68:
            return 2
        if 52 <= rsi < 55:
            return 1
        return 0

    if 32 <= rsi <= 45:
        return 2
    if 45 < rsi <= 48:
        return 1
    return 0


def session_good(signal):
    return signal["session"] in GOOD_SESSIONS


def score_v2(signal, ctx):
    """
    0-10, every point is a real, checkable condition:
      trend strength (ADX)          0-2
      directional gap (DI)          0-1
      healthy RSI zone              0-2
      fib / FVG confluence          0-2
      liquid session                0-1
      distance from 4H EMA50        0-2 (closer to value = more points)
    """

    points = 0

    if signal["adx"] >= 30:
        points += 2
    elif signal["adx"] >= 20:
        points += 1

    if di_gap(signal) >= 10:
        points += 1

    points += rsi_points(signal)

    if signal["fib"]:
        points += 1
    if signal["fvg"]:
        points += 1

    if session_good(signal):
        points += 1

    distance = distance_atr(signal)

    if distance <= ctx["t1"]:
        points += 2
    elif distance <= ctx["t2"]:
        points += 1

    return points


def percentile(values, fraction):
    ordered = sorted(values)

    if not ordered:
        return 0.0

    index = min(len(ordered) - 1, int(fraction * len(ordered)))

    return ordered[index]


# ============================================================
# CANDIDATE RULES (fixed in advance, not mined from results)
# ============================================================

def build_candidates():
    return [
        (
            "Baseline: every live signal",
            "No extra filter",
            lambda s, c: True,
        ),
        (
            "BUY needs strong trend",
            "BUYs only when ADX is 25+",
            lambda s, c: s["direction"] == "SELL" or s["adx"] >= 25,
        ),
        (
            "Healthy RSI zone",
            "Momentum strong but not stretched",
            lambda s, c: rsi_points(s) == 2,
        ),
        (
            "Not chasing price",
            "Entry close to the 4H EMA50",
            lambda s, c: distance_atr(s) <= c["t2"],
        ),
        (
            "Real confluence",
            "Fib level or Fair Value Gap present",
            lambda s, c: s["fib"] or s["fvg"],
        ),
        (
            "London and New York only",
            "Skip thin Asian hours",
            lambda s, c: session_good(s),
        ),
        (
            "Score 5 or more",
            "New 0-10 setup score at least 5",
            lambda s, c: score_v2(s, c) >= 5,
        ),
        (
            "Score 6 or more",
            "New 0-10 setup score at least 6",
            lambda s, c: score_v2(s, c) >= 6,
        ),
        (
            "Score 7 or more",
            "New 0-10 setup score at least 7",
            lambda s, c: score_v2(s, c) >= 7,
        ),
        (
            "Combo filter",
            "ADX 20+, healthy RSI and not chasing",
            lambda s, c: (
                s["adx"] >= 20
                and rsi_points(s) == 2
                and distance_atr(s) <= c["t2"]
            ),
        ),
    ]


# ============================================================
# EVALUATION
# ============================================================

def evaluate(predicate, signals, bars, ctx, split_index):
    chosen = [s for s in signals if predicate(s, ctx)]

    trades = []

    for signal in chosen:
        trade = bt.simulate(EXIT, signal, bars)

        if trade:
            trade["signal"] = signal
            trades.append(trade)

    single = bt.one_at_a_time(trades)

    practice = [t for t in single if t["entry_index"] < split_index]
    unseen = [t for t in single if t["entry_index"] >= split_index]

    return {
        "signals": len(chosen),
        "all": single,
        "practice": bt.stats(practice),
        "unseen": bt.stats(unseen),
    }


def verdict(result, baseline):
    p = result["practice"]
    u = result["unseen"]

    if not p or not u:
        return "⚪", "Too few trades"

    if u["n"] < MIN_TRADES_UNSEEN:
        return "⚪", f"Only {u['n']} unseen trades"

    if p["avg_r"] <= 0 or u["avg_r"] <= 0:
        return "⚠️", "Failed on unseen data" if u["avg_r"] <= 0 else (
            "Not profitable on practice data"
        )

    base_unseen = baseline["unseen"]["avg_r"] if baseline["unseen"] else 0.0

    if u["avg_r"] > base_unseen:
        return "✅", "Holds up and beats the baseline"

    return "🟡", "Holds up but no better than baseline"


# ============================================================
# REPORT
# ============================================================

def card(rank_icon, name, description, result, note, days):
    p = result["practice"]
    u = result["unseen"]

    if not p or not u:
        return f"{rank_icon} <b>{bt.esc(name)}</b>\n   Too few trades"

    per_day = len(result["all"]) / max(1.0, days)

    return (
        f"{rank_icon} <b>{bt.esc(name)}</b>\n"
        f"   📝 {bt.esc(description)}\n"
        f"   🔵 Practice: {p['n']} trades · "
        f"<b>{p['avg_r']:+.2f}R</b> (±{p['stderr']:.2f})\n"
        f"   🟣 Unseen: {u['n']} trades · "
        f"<b>{u['avg_r']:+.2f}R</b> (±{u['stderr']:.2f}) · "
        f"PF {bt.pf_text(u)} · Win {u['win_pct']:.0f}%\n"
        f"   📡 About {per_day:.1f} signals per trading day\n"
        f"   {bt.esc(note)}"
    )


def score_table(signals, bars, ctx, split_index):
    buckets = [("0-3", 0, 3), ("4-5", 4, 5), ("6-7", 6, 7), ("8-10", 8, 10)]

    rows = []
    unseen_avgs = []

    for label, low, high in buckets:
        row = [f"<b>Score {label}</b>"]

        for part_name, in_part in (
            ("Practice", lambda i: i < split_index),
            ("Unseen", lambda i: i >= split_index),
        ):
            trades = []

            for signal in signals:
                if not in_part(signal["index"]):
                    continue

                if not (low <= score_v2(signal, ctx) <= high):
                    continue

                trade = bt.simulate(EXIT, signal, bars)

                if trade:
                    trades.append(trade)

            s = bt.stats(trades)

            if s and part_name == "Unseen" and s["n"] >= 15:
                unseen_avgs.append(s["avg_r"])

            if s:
                row.append(
                    f"   {part_name}: {s['n']} trades · "
                    f"{bt.light(s['avg_r'])} {s['avg_r']:+.2f}R"
                )
            else:
                row.append(f"   {part_name}: no trades")

        rows.append("\n".join(row))

    return rows, unseen_avgs


def build_sections(bars_1h, signals, evaluated):
    split_index = int(len(bars_1h) * SPLIT_FRACTION)

    split_date = bars_1h[split_index]["datetime"][:10]
    first = bars_1h[0]["datetime"][:10]
    last = bars_1h[-1]["datetime"][:10]

    days = max(
        1.0,
        (
            engine.parse_bar_time(bars_1h[-1]["datetime"])
            - engine.parse_bar_time(bars_1h[0]["datetime"])
        ).days * 5 / 7,
    )

    practice_signals = [s for s in signals if s["index"] < split_index]

    ctx = {
        "t1": percentile([distance_atr(s) for s in practice_signals], 0.33),
        "t2": percentile([distance_atr(s) for s in practice_signals], 0.66),
    }

    candidates = build_candidates()

    results = []

    for name, description, predicate in candidates:
        results.append(
            (
                name,
                description,
                evaluate(predicate, signals, bars_1h, ctx, split_index),
            )
        )

    baseline = results[0][2]

    sections = []

    sections.append(
        "🧪 <b>JAY GOLD STRATEGY v2 TEST</b>  🔒 private\n"
        f"{bt.RULE}\n"
        f"🗓 Data: {first} → {last}\n"
        f"🔵 Practice (rules judged here): {first} → {split_date}\n"
        f"🟣 Unseen (rules must hold here): {split_date} → {last}\n"
        f"📡 Live-engine signals tested: {len(signals)}\n"
        f"🎯 Exit used for every rule: single 1.5R target, one trade at a time\n"
        f"💵 Cost: ${bt.COST_USD:.2f} per trade"
    )

    ranked = sorted(
        results[1:],
        key=lambda item: (
            item[2]["practice"]["avg_r"] if item[2]["practice"] else -9
        ),
        reverse=True,
    )

    rows = [
        card(
            "📍",
            results[0][0],
            results[0][1],
            baseline,
            "Reference point for every rule below",
            days,
        )
    ]

    passing = []

    for name, description, result in ranked:
        icon, note = verdict(result, baseline)

        if icon == "✅":
            passing.append((name, result))

        rows.append(card(icon, name, description, result, note, days))

    sections.append(
        "🏁 <b>1. RULES, RANKED ON PRACTICE DATA</b>\n"
        "✅ holds up and beats baseline · 🟡 holds up, no gain · "
        "⚠️ fails · ⚪ too few trades\n"
        f"{bt.RULE}\n" + "\n\n".join(rows)
    )

    score_rows, unseen_avgs = score_table(signals, bars_1h, ctx, split_index)

    rising = (
        len(unseen_avgs) >= 3
        and all(b >= a for a, b in zip(unseen_avgs, unseen_avgs[1:]))
    )

    score_note = (
        "✅ Higher scores did better on unseen data. The score carries "
        "real information."
        if rising
        else "⚠️ Higher scores did NOT reliably do better on unseen data. "
        "Do not show this score on public cards yet."
    )

    sections.append(
        "🎚 <b>2. DOES THE NEW SETUP SCORE MEAN ANYTHING?</b>\n"
        "A good score should mean better results. Look for the average R "
        "rising as the score rises, on BOTH practice and unseen data.\n"
        f"{bt.RULE}\n"
        + "\n\n".join(score_rows)
        + f"\n\n{score_note}"
    )

    notes = []

    if passing:
        names = ", ".join(bt.esc(name) for name, _ in passing)
        notes.append(f"✅ Shortlist for the v2 build: {names}")
        notes.append(
            f"⚠️ {len(results) - 1} rules were tested, so one or two can look "
            "good by luck. A shortlisted rule must now prove itself live in shadow "
            "mode before it replaces anything."
        )
    else:
        notes.append(
            "🟡 No rule clearly beat the baseline on unseen data. That is "
            "a useful result: it means the extra filters add noise, and "
            "the gains are in exit rules and trade selection, not more "
            "indicators."
        )

    notes.append(
        "🧪 Nothing here changes your live signals. This is evidence "
        "for the next build."
    )

    sections.append("✅ <b>3. VERDICT</b>\n" + f"{bt.RULE}\n" + "\n\n".join(notes))

    return sections


def run(bars_1h, bars_4h):
    signals, evaluated = collect(bars_1h, bars_4h)
    return build_sections(bars_1h, signals, evaluated)


def main():
    print("Downloading history from Twelve Data...")

    bars_1h = engine.fetch_bars("1h", bt.HISTORY_1H)
    bars_4h = engine.fetch_bars("4h", bt.HISTORY_4H)

    print(f"Got {len(bars_1h)} 1H and {len(bars_4h)} 4H candles.")

    if len(bars_1h) < 600 or len(bars_4h) < 300:
        raise RuntimeError("Not enough history returned to backtest.")

    sections = run(bars_1h, bars_4h)

    plain = "\n\n".join(bt.strip_tags(section) for section in sections)

    print(plain)

    with open("backtest_v2_report.txt", "w") as handle:
        handle.write(plain)

    bt.send_admin(sections)


if __name__ == "__main__":
    main()
