"""
history.py - loads a LONG history of gold candles from Twelve Data by paging
backwards in time, so tests cover more months, more trades and more market
conditions (rising and falling gold).
"""

import time
from datetime import timedelta

import main as engine
from common import td_get

PAGE_SIZE = 5000
PAUSE_SECONDS = 10


def clean(row):
    try:
        bar = {
            "datetime": engine.parse_bar_time(row["datetime"]).isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
    except (KeyError, TypeError, ValueError):
        return None

    if bar["high"] < bar["low"]:
        return None
    if not (bar["low"] <= bar["open"] <= bar["high"]):
        return None
    if not (bar["low"] <= bar["close"] <= bar["high"]):
        return None

    return bar


def fetch_page(interval, end_date=None):
    params = {
        "interval": interval,
        "outputsize": PAGE_SIZE,
        "order": "ASC",
        "timezone": "UTC",
        "format": "JSON",
    }

    if end_date:
        params["end_date"] = end_date

    data = td_get("time_series", engine.SYMBOL, **params)

    if not isinstance(data, dict) or "values" not in data:
        message = data.get("message") if isinstance(data, dict) else data
        print(f"No more {interval} history available: {message}")
        return []

    bars = [clean(row) for row in data["values"]]

    return [bar for bar in bars if bar]


def load(interval, pages):
    """Newest page first, then older pages. Returns bars oldest to newest."""

    merged = {}
    end_date = None

    for page in range(pages):
        bars = fetch_page(interval, end_date)

        if not bars:
            break

        before = len(merged)

        for bar in bars:
            merged[bar["datetime"]] = bar

        print(
            f"{interval} page {page + 1}: {len(bars)} candles, "
            f"{bars[0]['datetime'][:10]} to {bars[-1]['datetime'][:10]}"
        )

        if len(merged) == before:
            break

        oldest = engine.parse_bar_time(bars[0]["datetime"])
        end_date = (oldest - timedelta(seconds=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        if page < pages - 1:
            time.sleep(PAUSE_SECONDS)

    return [merged[key] for key in sorted(merged)]
