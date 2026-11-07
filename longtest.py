"""
longtest.py - runs the backtest AND the Strategy v2 test on a much longer
history (about 2+ years of hourly gold candles). More trades and more market
conditions make the results far more trustworthy than a 7 month window.
Reuses backtest.py, backtest_v2.py and history.py.
"""

import backtest as bt
import backtest_v2 as v2
import history

PAGES_1H = 3
PAGES_4H = 1

# The v2 report has a generic line that only makes sense for short tests.
# On a long history we do not claim where gains are, so we soften it.
GENERIC_NOTE = (
    ", and the gains are in exit rules and trade selection, "
    "not more indicators."
)


def context_section(bars_1h):
    first = bars_1h[0]
    last = bars_1h[-1]

    change = 100.0 * (last["close"] - first["close"]) / first["close"]

    return (
        "🌍 <b>LONG HISTORY TEST</b>  🔒 private\n"
        f"{bt.RULE}\n"
        f"🗓 {first['datetime'][:10]} → {last['datetime'][:10]}\n"
        f"🕯 {len(bars_1h):,} hourly candles\n"
        f"🥇 Gold moved {first['close']:.0f} → {last['close']:.0f} "
        f"({change:+.0f}%) over this window\n\n"
        "More months means more trades and both rising and falling gold. "
        "Treat results here as the main evidence."
    )


def main():
    print("Downloading long history from Twelve Data...")

    bars_1h = history.load("1h", PAGES_1H)
    bars_4h = history.load("4h", PAGES_4H)

    print(f"Got {len(bars_1h)} 1H and {len(bars_4h)} 4H candles.")

    if len(bars_1h) < 600 or len(bars_4h) < 300:
        raise RuntimeError("Not enough history returned to backtest.")

    sections = [context_section(bars_1h)]
    sections += bt.run(bars_1h, bars_4h)
    sections += v2.run(bars_1h, bars_4h)
    sections = [
        section.replace(GENERIC_NOTE, ", not real edge.")
        for section in sections
    ]

    plain = "\n\n".join(bt.strip_tags(section) for section in sections)

    print(plain)

    with open("longtest_report.txt", "w") as handle:
        handle.write(plain)

    bt.send_admin(sections)


if __name__ == "__main__":
    main()
