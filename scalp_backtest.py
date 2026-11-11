"""
JAY GOLD SCALP - Backtest

Replays the scalp engine (scalp.analyse) over months of 5-minute gold
candles and simulates every scalp the way it would be posted:
- entry is filled at the NEXT candle's open (you cannot trade the close),
- the stop is checked before the target inside a candle,
- one scalp at a time, a short cooldown and the daily cap, like live,
- spread/slippage cost on every trade.

Rules are judged on the first 60% of the data ("practice") and must hold
on the last 40% ("unseen"). Results are evidence, not a promise.

Needs history.py, backtest.py, scalp.py. Sends the report to ADMIN_CHAT_ID.
"""

import bisect
import io
from contextlib import redirect_stdout
from datetime import timedelta

import backtest as bt
import history
import scalp
import main as engine

PAGES_5M = 6
PAGES_1H = 1

SPLIT_FRACTION = 0.60
MIN_UNSEEN_TRADES = 100

# (stop in ATR, target in R or None, expiry in 5M candles)
SL_CHOICES = (1.0, 1.5)
TP_CHOICES = (0.8, 1.2, None)
EXPIRY_CHOICES = (6, 12)


# ============================================================
# 1. FIND EVERY RAW SETUP (no trade management yet)
# ============================================================

def find_setups(bars5, bars1h):
    starts5 = [engine.parse_bar_time(b["datetime"]) for b in bars5]
    starts1h = [engine.parse_bar_time(b["datetime"]) for b in bars1h]

    setups = []
    evaluated = 0

    for i in range(100, len(bars5) - 14):
        now = starts5[i] + timedelta(minutes=5)

        if not scalp.in_liquid_hours(now):
            continue

        # Skip gaps: the last 100 candles must be contiguous enough.
        if (starts5[i] - starts5[i - 99]).total_seconds() > 99 * 300 * 1.6:
            continue

        cut = bisect.bisect_right(starts1h, now - timedelta(hours=1))

        if cut < 70:
            continue

        window5 = bars5[i - 99:i + 1]
        window1h = bars1h[cut - 79:cut] if cut >= 79 else bars1h[:cut]

        try:
            with redirect_stdout(io.StringIO()):
                signal, _ = scalp.analyse(window5, window1h, now)
        except Exception:
            continue

        evaluated += 1

        if signal:
            signal["index"] = i
            signal["time"] = now
            setups.append(signal)

    return setups, evaluated


# ============================================================
# 2. SIMULATE ONE SCALP
# ============================================================

def simulate(signal, profile, bars5):
    sl_atr, tp_r, expiry = profile
    sign = 1 if signal["direction"] == "BUY" else -1

    stop, target, risk = scalp.build_levels(
        signal["direction"], signal["entry"], signal["atr"], sl_atr, tp_r
    )

    i = signal["index"]

    if i + 1 >= len(bars5):
        return None

    fill = bars5[i + 1]["open"]

    last_index = min(i + expiry, len(bars5) - 1)

    exit_index = None
    exit_price = None
    reason = None

    for j in range(i + 1, last_index + 1):
        bar = bars5[j]

        if sign == 1:
            stop_hit = bar["low"] <= stop
            target_hit = target is not None and bar["high"] >= target
        else:
            stop_hit = bar["high"] >= stop
            target_hit = target is not None and bar["low"] <= target

        if stop_hit:
            exit_index, exit_price, reason = j, stop, "SL"
            break

        if target_hit:
            exit_index, exit_price, reason = j, target, "TP"
            break

    if exit_index is None:
        exit_index = last_index
        exit_price = bars5[last_index]["close"]
        reason = "TIME"

    pnl = sign * (exit_price - fill) - bt.COST_USD

    return {
        "entry_index": i,
        "exit_index": exit_index,
        "hold": exit_index - i,
        "r": pnl / risk,
        "risk_usd": risk,
        "reason": reason,
        "direction": signal["direction"],
        "setup": signal["setup"],
        "time": signal["time"],
    }


def run_profile(setups, profile, bars5):
    """One scalp at a time, cooldown, daily cap: same rules as live."""

    trades = []
    free_from = -1
    per_day = {}

    cooldown_bars = max(1, scalp.COOLDOWN_MINUTES // 5)

    for signal in setups:
        if signal["index"] < free_from:
            continue

        day = signal["time"].date()

        if per_day.get(day, 0) >= scalp.MAX_SCALPS_PER_DAY:
            continue

        trade = simulate(signal, profile, bars5)

        if not trade:
            continue

        trades.append(trade)
        per_day[day] = per_day.get(day, 0) + 1
        free_from = trade["exit_index"] + cooldown_bars

    return trades


def profile_name(profile):
    sl_atr, tp_r, expiry = profile
    target = "no target" if tp_r is None else f"target {tp_r:.1f}R"
    return f"SL {sl_atr:.1f} ATR · {target} · {expiry * 5} min"


# ============================================================
# 3. REPORT
# ============================================================

def split_trades(trades, split_index):
    practice = [t for t in trades if t["entry_index"] < split_index]
    unseen = [t for t in trades if t["entry_index"] >= split_index]

    return bt.stats(practice), bt.stats(unseen)


def trading_days(bars5):
    first = engine.parse_bar_time(bars5[0]["datetime"])
    last = engine.parse_bar_time(bars5[-1]["datetime"])

    return max(1.0, (last - first).days * 5 / 7)


def profile_card(icon, name, trades, split_index, days):
    p, u = split_trades(trades, split_index)

    if not p or not u:
        return f"{icon} <b>{bt.esc(name)}</b>\n   Too few trades"

    per_day = len(trades) / days

    return (
        f"{icon} <b>{bt.esc(name)}</b>\n"
        f"   📡 About {per_day:.1f} scalps per trading day\n"
        f"   🔵 Practice: {p['n']} trades · <b>{p['avg_r']:+.2f}R</b> "
        f"(±{p['stderr']:.2f})\n"
        f"   🟣 Unseen: {u['n']} trades · <b>{u['avg_r']:+.2f}R</b> "
        f"(±{u['stderr']:.2f}) · PF {bt.pf_text(u)} · Win {u['win_pct']:.0f}%"
    )


def build_sections(bars5, setups, evaluated):
    split_index = int(len(bars5) * SPLIT_FRACTION)
    days = trading_days(bars5)

    first = bars5[0]["datetime"][:10]
    last = bars5[-1]["datetime"][:10]
    split_date = bars5[split_index]["datetime"][:10]

    sections = []

    by_setup = {}

    for s in setups:
        by_setup[s["setup"]] = by_setup.get(s["setup"], 0) + 1

    setup_lines = "\n".join(
        f"   • {bt.esc(name)}: {count}"
        for name, count in sorted(by_setup.items())
    )

    sections.append(
        "⚡ <b>JAY GOLD SCALP BACKTEST</b>  🔒 private\n"
        f"{bt.RULE}\n"
        f"🗓 {first} → {last}\n"
        f"🔵 Practice: {first} → {split_date}\n"
        f"🟣 Unseen: {split_date} → {last}\n"
        f"🕯 {len(bars5):,} five-minute candles\n"
        f"🕐 Hours scanned: {scalp.LIQUID_START_HOUR}:00 to "
        f"{scalp.LIQUID_END_HOUR}:00 UTC\n"
        f"💵 Cost: ${bt.COST_USD:.2f} per trade · entry at next candle open\n"
        f"🔎 Raw setups found: {len(setups)}\n{setup_lines}"
    )

    profiles = [
        (sl, tp, ex)
        for sl in SL_CHOICES
        for tp in TP_CHOICES
        for ex in EXPIRY_CHOICES
    ]

    results = []

    for profile in profiles:
        trades = run_profile(setups, profile, bars5)
        p, u = split_trades(trades, split_index)
        results.append((profile, trades, p, u))

    def practice_key(item):
        return item[2]["avg_r"] if item[2] else -9

    ranked = sorted(results, key=practice_key, reverse=True)

    cards = []
    passing = []

    for place, (profile, trades, p, u) in enumerate(ranked):
        icon = "⚪"

        if p and u and u["n"] >= MIN_UNSEEN_TRADES:
            if p["avg_r"] > 0 and u["avg_r"] > 0:
                icon = "✅"
                passing.append((profile, trades, p, u))
            else:
                icon = "⚠️"

        cards.append(
            profile_card(icon, profile_name(profile), trades, split_index, days)
        )

    sections.append(
        "🏁 <b>1. ALL EXIT SETTINGS, RANKED ON PRACTICE DATA</b>\n"
        "✅ positive on both parts · ⚠️ fails · ⚪ too few trades\n"
        f"{bt.RULE}\n" + "\n\n".join(cards)
    )

    # ---- Best passing profile in detail -----------------------------
    if passing:
        passing.sort(key=lambda item: item[3]["avg_r"], reverse=True)
        profile, trades, p, u = passing[0]

        rows = [
            f"💵 ${cost:.2f} → {bt.stress_avg(trades, cost):+.2f}R"
            for cost in (0.30, 0.60, 1.00)
        ]

        setup_rows = []

        for name in sorted(by_setup):
            subset = [t for t in trades if t["setup"] == name]
            sp, su = split_trades(subset, split_index)

            if sp and su:
                setup_rows.append(
                    f"<b>{bt.esc(name)}</b>\n"
                    f"   Practice {sp['avg_r']:+.2f}R ({sp['n']}) · "
                    f"Unseen {su['avg_r']:+.2f}R ({su['n']})"
                )

        sections.append(
            "🏆 <b>2. BEST SETTING THAT HOLDS UP</b>\n"
            f"{bt.RULE}\n"
            f"<b>{bt.esc(profile_name(profile))}</b>\n\n"
            "Cost stress (average R per trade):\n"
            + "\n".join(rows)
            + "\n\nBy setup:\n"
            + "\n".join(setup_rows)
        )

    # ---- Verdict ----------------------------------------------------
    notes = []

    if passing:
        profile, trades, p, u = passing[0]
        per_day = len(trades) / days

        notes.append(
            f"✅ A setting held up on unseen data: "
            f"{bt.esc(profile_name(profile))}"
        )
        notes.append(
            f"📡 It produces about {per_day:.1f} scalps per trading day."
        )

        if per_day < 10:
            notes.append(
                "ℹ️ That is below your 10 to 15 target. We do not pad it "
                "with weak signals. More volume needs a third setup that "
                "also passes this test."
            )

        notes.append(
            "🧪 Next step: run in shadow mode for 1 to 2 weeks and compare "
            "live results with this backtest before going live."
        )
    else:
        notes.append(
            "🔴 No setting was positive on BOTH practice and unseen data. "
            "Do not post these scalps. We refine the setups or drop them."
        )

    notes.append(
        "⚠️ 12 settings were tested, so one can look good by luck. "
        "Shadow trading is the final check."
    )

    sections.append("✅ <b>3. VERDICT</b>\n" + f"{bt.RULE}\n" + "\n\n".join(notes))

    return sections


def run(bars5, bars1h):
    setups, evaluated = find_setups(bars5, bars1h)
    return build_sections(bars5, setups, evaluated)


def main():
    print("Downloading history from Twelve Data...")

    bars5 = history.load("5min", PAGES_5M)
    bars1h = history.load("1h", PAGES_1H)

    print(f"Got {len(bars5)} 5M and {len(bars1h)} 1H candles.")

    if len(bars5) < 3000 or len(bars1h) < 200:
        raise RuntimeError("Not enough history returned to backtest.")

    sections = run(bars5, bars1h)

    plain = "\n\n".join(bt.strip_tags(section) for section in sections)

    print(plain)

    with open("scalp_backtest_report.txt", "w") as handle:
        handle.write(plain)

    bt.send_admin(sections)


if __name__ == "__main__":
    main()
