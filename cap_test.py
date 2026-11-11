"""
JAY GOLD - Cap test

The frequency test showed that more signals means more trades open at the
same time. This test measures that trade-off properly: for each candidate
entry rule set and each open-trade cap, how many signals per day do we get,
and what happens to the edge and the worst dip?

Exit used everywhere: the live ladder (1R / 2R / 3R, 40/30/30, stop to
entry after TP1, 72 market-hour time stop), cost $0.30 per trade.

A combination passes only when:
- practice average R > 0 AND unseen average R >= +0.05R,
- at least 150 unseen trades,
- daily z-score >= 1.5 over the whole period.

Needs history.py, backtest.py, freq_test.py, main.py.
"""

import backtest as bt
import freq_test as ft
import history
import main as engine

PAGES_1H = 3
PAGES_4H = 1

SPLIT_FRACTION = 0.60
MIN_UNSEEN_TRADES = 150
MIN_UNSEEN_R = 0.05
MIN_Z = 1.5

CAPS = (4, 8, 12, 100)

CANDIDATES = [
    ("A. Current rules", {}),
    ("B. Pullback resumes", {"trend_mode": "STRUCTURE"}),
    ("E. ADX floor 10", {"adx_min": 10.0}),
    (
        "B+E. Pullbacks and ADX floor 10",
        {"trend_mode": "STRUCTURE", "adx_min": 10.0},
    ),
    (
        "H. Loosest (reference only)",
        {
            "trend_mode": "STRUCTURE",
            "momentum_min": 2,
            "rsi_buy": 50,
            "rsi_sell": 50,
            "adx_min": 10.0,
        },
    ),
]


def replay_trades(params, bars_1h, bars_4h):
    saved = dict(engine.PARAMS)
    engine.PARAMS.update(params)

    try:
        signals, _ = bt.replay_signals(bars_1h, bars_4h)
    finally:
        engine.PARAMS.clear()
        engine.PARAMS.update(saved)

    trades = []

    for signal in signals:
        trade = bt.simulate(ft.EXIT, signal, bars_1h)

        if trade:
            trades.append(trade)

    return trades


def evaluate(trades, split_index, days):
    practice = [t for t in trades if t["entry_index"] < split_index]
    unseen = [t for t in trades if t["entry_index"] >= split_index]

    p = bt.stats(practice)
    u = bt.stats(unseen)
    a = bt.stats(trades)

    if not p or not u or not a:
        return None

    return {
        "p": p,
        "u": u,
        "a": a,
        "z": ft.daily_z(trades),
        "per_day": len(trades) / days,
        "active": len({t["time"].date() for t in trades}) / days,
        "overlap": ft.max_overlap(trades),
    }


def passes(result):
    if not result:
        return False

    return (
        result["p"]["avg_r"] > 0
        and result["u"]["avg_r"] >= MIN_UNSEEN_R
        and result["u"]["n"] >= MIN_UNSEEN_TRADES
        and result["z"] >= MIN_Z
    )


def build_sections(bars_1h, table):
    split_index = int(len(bars_1h) * SPLIT_FRACTION)
    days = max(1, len(ft.trading_day_set(bars_1h)))

    first = bars_1h[0]["datetime"][:10]
    last = bars_1h[-1]["datetime"][:10]
    split_date = bars_1h[split_index]["datetime"][:10]

    sections = [
        "🧮 <b>JAY GOLD CAP TEST</b>  🔒 private\n"
        f"{bt.RULE}\n"
        f"🗓 {first} → {last}\n"
        f"🔵 Practice → {split_date} · 🟣 Unseen after\n"
        f"🎯 Live ladder 1R/2R/3R · cost ${bt.COST_USD:.2f} per trade\n"
        f"🔒 Caps tested: {', '.join(str(c) for c in CAPS)} trades open at once\n"
        "✅ needs unseen edge of at least +0.05R, 150+ unseen trades, "
        "daily z of 1.5+"
    ]

    winners = []

    for name, params, rows in table:
        lines = [f"<b>{bt.esc(name)}</b>"]

        for cap, result in rows:
            if not result:
                lines.append(f"⚪ Cap {cap}: too few trades")
                continue

            ok = passes(result)
            icon = "✅" if ok else "⚠️"

            if ok:
                winners.append((name, params, cap, result))

            lines.append(
                f"{icon} Cap {cap}: <b>{result['per_day']:.1f}/day</b> · "
                f"days with a signal {100 * result['active']:.0f}% · "
                f"🔵{result['p']['avg_r']:+.2f}R 🟣{result['u']['avg_r']:+.2f}R · "
                f"z {result['z']:.1f} · dip {result['a']['max_dd']:.0f}R · "
                f"max open {result['overlap']}"
            )

        sections.append("\n".join(lines))

    notes = []

    if winners:
        winners.sort(key=lambda item: item[3]["per_day"], reverse=True)

        notes.append("🏆 Passing combinations, most signals first:")

        for name, params, cap, result in winners[:5]:
            notes.append(
                f"• {bt.esc(name)} with cap {cap}: "
                f"{result['per_day']:.1f} signals/day, unseen "
                f"{result['u']['avg_r']:+.2f}R, dip {result['a']['max_dd']:.0f}R"
            )

        name, params, cap, result = winners[0]

        shown = ", ".join(
            f'"{key}": {value!r}' for key, value in params.items()
        ) or "no change from today"

        notes.append(
            f"🔧 For the top one, set MAX_OPEN_TRADES = {cap} and these "
            f"values inside PARAMS in main.py:\n{bt.esc(shown)}"
        )
        notes.append(
            "📐 Pick the cap by what exposure you are comfortable asking "
            "members to carry. A bigger cap gives more signals and a deeper "
            "worst dip."
        )
    else:
        notes.append(
            "🔴 Nothing passed the stricter bar. Keep current rules and "
            "choose a cap by risk comfort."
        )

    notes.append(
        f"⚠️ {len(CANDIDATES) * len(CAPS)} combinations were tested. Treat "
        "any winner as a candidate and compare it with live results over "
        "2 weeks."
    )

    sections.append("✅ <b>VERDICT</b>\n" + f"{bt.RULE}\n" + "\n\n".join(notes))

    return sections


def run(bars_1h, bars_4h):
    split_index = int(len(bars_1h) * SPLIT_FRACTION)
    days = max(1, len(ft.trading_day_set(bars_1h)))

    table = []

    for name, params in CANDIDATES:
        trades = replay_trades(params, bars_1h, bars_4h)

        rows = []

        for cap in CAPS:
            capped = ft.cap_open(trades, cap)
            rows.append((cap, evaluate(capped, split_index, days)))

        table.append((name, params, rows))

    return build_sections(bars_1h, table)


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

    with open("cap_test_report.txt", "w") as handle:
        handle.write(plain)

    bt.send_admin(sections)


if __name__ == "__main__":
    main()
