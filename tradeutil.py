"""
tradeutil.py - small helpers shared by the signal job and the trade monitor.
"""

from datetime import timedelta

from common import is_market_open

# A trade is closed at market after this many MARKET hours (weekends and
# the daily break do not count). Matches the tested strategy.
MAX_HOLD_MARKET_HOURS = 72


def market_hours_between(start, end):
    """Whole hours between two times during which gold was tradeable."""

    if end <= start:
        return 0

    cursor = start.replace(minute=0, second=0, microsecond=0)
    count = 0
    steps = 0

    while cursor < end and steps < 3000:
        if is_market_open(cursor):
            count += 1

        cursor += timedelta(hours=1)
        steps += 1

    return count
