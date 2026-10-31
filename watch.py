"""
Gold trade monitor and public performance recaps.

This file preserves the existing JSON trade format and Telegram card style.
New performance fields are additive, so older trade-history records remain readable.
"""

from datetime import datetime, timezone, timedelta
import math

from common import (
    get_price,
    send_text,
    send_photo,
    load_json,
    save_json,
    OPEN_TRADES_FILE,
    HISTORY_FILE,
    STATE_FILE,
    is_market_open,
    last_trading_day_of_month,
    rollover_hour,
    next_reopen,
)

from cards import make_update_card


DEFAULT_TS = "1970-01-01T00:00:00+00:00"
PIP_SIZE = 0.01
GOLD_SYMBOL = "XAU/USD"

RECAP_IMAGE = "/tmp/recap.png"
UPDATE_IMAGE = "/tmp/update.png"

# Performance estimates assume an equal 25% scale-out at each of the four TPs.
# This is a reporting convention, not proof of a subscriber's actual execution.
TP_WEIGHTS = (0.25, 0.25, 0.25, 0.25)


def utc_now():
    return datetime.now(timezone.utc)


def parse_timestamp(value, default=None):
    """Parse ISO timestamps, including older values without a UTC offset."""

    if not value:
        return default

    try:
        parsed = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except (TypeError, ValueError):
        return default


def pips(diff):
    return abs(float(diff)) / PIP_SIZE


def signed_move(direction, entry, exit_price):

    if direction == "BUY":
        return float(exit_price) - float(entry)

    return float(entry) - float(exit_price)


def hit(direction, level, price, is_tp):
    """Check whether the current quote crossed a target or stop level."""

    level = float(level)
    price = float(price)

    if direction == "BUY":
        return price >= level if is_tp else price <= level

    return price <= level if is_tp else price >= level


def ensure_trade_fields(trade):
    """
    Normalize trade fields while preserving compatibility
    with the existing trade JSON structure.
    """

    tps = trade.get("tps")

    if not isinstance(tps, list) or len(tps) != 4:
        raise ValueError(
            "Trade must contain exactly four take-profit levels"
        )

    direction = str(
        trade.get("direction", "")
    ).upper()

    if direction not in ("BUY", "SELL"):
        raise ValueError(
            "Trade direction must be BUY or SELL"
        )

    trade["direction"] = direction
    trade["entry"] = float(trade["entry"])
    trade["sl"] = float(trade["sl"])

    trade["tps"] = [
        float(level) for level in tps
    ]

    flags = trade.get(
        "tp_hit",
        [False, False, False, False]
    )

    if not isinstance(flags, list):
        flags = [False, False, False, False]

    trade["tp_hit"] = (
        flags + [False] * 4
    )[:4]

    trade["tp_hit"] = [
        bool(value) for value in trade["tp_hit"]
    ]

    # Preserve the original risk level before the stop
    # is moved to breakeven.
    trade.setdefault(
        "initial_sl",
        trade["sl"]
    )

    trade["initial_sl"] = float(
        trade["initial_sl"]
    )

    trade.setdefault(
        "breakeven_moved",
        bool(
            trade["tp_hit"][0]
            and trade["sl"] == trade["entry"]
        )
    )

    return trade


def estimate_realized_pips(trade, exit_price):
    """
    Estimate net pips using 25% scale-outs at each TP
    and the final exit for any remaining position.
    """

    entry = float(trade["entry"])
    direction = trade["direction"]

    realized = 0.0
    allocated = 0.0

    for index, was_hit in enumerate(trade["tp_hit"]):

        if was_hit:

            weight = TP_WEIGHTS[index]

            move = signed_move(
                direction,
                entry,
                trade["tps"][index]
            )

            realized += weight * (
                move / PIP_SIZE
            )

            allocated += weight

    remaining = max(
        0.0,
        1.0 - allocated
    )

    if remaining:

        move = signed_move(
            direction,
            entry,
            exit_price
        )

        realized += remaining * (
            move / PIP_SIZE
        )

    return realized


def estimate_result_r(trade, realized_pips):

    risk_pips = (
        abs(
            float(trade["entry"])
            - float(trade["initial_sl"])
        ) / PIP_SIZE
    )

    if risk_pips <= 0:
        return None

    return realized_pips / risk_pips


def publish_trade_update(message):
    """
    Send a public Telegram update.

    Telegram failures are allowed to reach the workflow
    so the run can be retried rather than silently losing updates.
    """

    send_text(message)


def run_tracker():

    trades = load_json(
        OPEN_TRADES_FILE,
        []
    )

    history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(trades, list):
        raise ValueError(
            "data/open_trades.json must contain a JSON list"
        )

    if not isinstance(history, list):
        raise ValueError(
            "data/trade_history.json must contain a JSON list"
        )

    gold_trades = [
        trade
        for trade in trades
        if isinstance(trade, dict)
        and trade.get("symbol") == GOLD_SYMBOL
    ]

    if not gold_trades:
        return

    # Fetch one quote for all open gold trades.
    try:

        price = float(
            get_price(GOLD_SYMBOL)
        )

        if not math.isfinite(price) or price <= 0:
            raise ValueError(
                f"Invalid gold quote: {price}"
            )

    except Exception as exc:

        print(
            "Gold price fetch failed; "
            f"open trades left unchanged: {exc}"
        )

        return

    now = utc_now()

    still_open = []
    changed = False

    for original in trades:

        if (
            not isinstance(original, dict)
            or original.get("symbol") != GOLD_SYMBOL
        ):

            still_open.append(original)
            continue

        trade = dict(original)

        try:

            ensure_trade_fields(trade)

        except (
            KeyError,
            TypeError,
            ValueError
        ) as exc:

            print(
                f"Skipping malformed trade "
                f"{trade.get('id', 'unknown')}: {exc}"
            )

            still_open.append(original)
            continue

        opened_at = parse_timestamp(
            trade.get("opened_at"),
            now
        )

        issued_str = opened_at.strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        was_changed = False

        # Complete a pending breakeven move if TP1
        # was already recorded by an earlier run.
        if (
            trade["tp_hit"][0]
            and not trade.get("breakeven_moved", False)
        ):

            trade["sl"] = trade["entry"]
            trade["breakeven_moved"] = True

            was_changed = True

            publish_trade_update(
                f"🛡️ *BREAKEVEN MOVED*\n\n"
                f"📌 Pair: {trade.get('label', GOLD_SYMBOL)}\n"
                f"💰 SL moved to entry: `{trade['entry']:.2f}`\n"
                f"📅 Signal: {issued_str}\n"
                f"✅ TP1 secured — stop moved to entry."
            )

        # Check the active stop before newly reached targets.
        # If a quote has crossed both since the previous poll,
        # this conservative ordering avoids assuming a favourable
        # intraperiod price path.
        if hit(
            trade["direction"],
            trade["sl"],
            price,
            is_tp=False
        ):

            realized_pips = estimate_realized_pips(
                trade,
                price
            )

            result_r = estimate_result_r(
                trade,
                realized_pips
            )

            is_be_stop = bool(
                trade.get("breakeven_moved", False)
            )

            if is_be_stop:

                title = "BREAKEVEN STOP HIT"
                emoji = "🟡"

            elif realized_pips < 0:

                title = "STOP LOSS HIT"
                emoji = "🔴"

            else:

                title = "TRADE CLOSED"
                emoji = "✅"

            result_line = (
                f"💰 Estimated net pips: "
                f"{realized_pips:+.0f}"
            )

            if result_r is not None:

                result_line += (
                    f" ({result_r:+.2f}R)"
                )

            publish_trade_update(
                f"{emoji} *{title}*\n\n"
                f"📌 Pair: {trade.get('label', GOLD_SYMBOL)}\n"
                f"📉 Exit: `{price:.2f}`\n"
                f"📅 Issued: {issued_str}\n"
                f"{result_line}"
            )

            if realized_pips > 0.000001:
                trade["outcome"] = "win"

            elif realized_pips < -0.000001:
                trade["outcome"] = "loss"

            else:
                trade["outcome"] = "breakeven"

            trade["exit_price"] = price
            trade["closed_at"] = now.isoformat()

            trade["realized_pips"] = realized_pips

            # Keep the old field for compatibility with existing recaps.
            trade["result_pips"] = realized_pips

            if result_r is not None:
                trade["result_r"] = result_r

            trade["result_method"] = (
                "estimated_equal_25pct_tp_scale_out"
            )

            history.append(trade)

            changed = True
            continue

        # Record every newly reached target.
        for index, target in enumerate(trade["tps"]):

            if (
                not trade["tp_hit"][index]
                and hit(
                    trade["direction"],
                    target,
                    price,
                    is_tp=True
                )
            ):

                move = signed_move(
                    trade["direction"],
                    trade["entry"],
                    target
                )

                publish_trade_update(
                    f"✅ *TAKE PROFIT {index + 1} HIT!*\n\n"
                    f"📌 Pair: {trade.get('label', GOLD_SYMBOL)}\n"
                    f"📈 Exit: `{price:.2f}`\n"
                    f"📅 Issued: {issued_str}\n"
                    f"💰 Pips: +{pips(move):.0f}"
                )

                trade["tp_hit"][index] = True
                was_changed = True

        # TP1 secures a partial gain and moves the remaining
        # position's stop to entry.
        #
        # This tracker does not place broker-side orders.
        if (
            trade["tp_hit"][0]
            and not trade.get("breakeven_moved", False)
        ):

            trade["sl"] = trade["entry"]
            trade["breakeven_moved"] = True

            was_changed = True

            publish_trade_update(
                f"🛡️ *BREAKEVEN MOVED*\n\n"
                f"📌 Pair: {trade.get('label', GOLD_SYMBOL)}\n"
                f"💰 SL moved to entry: `{trade['entry']:.2f}`\n"
                f"📅 Signal: {issued_str}\n"
                f"✅ TP1 secured — stop moved to entry."
            )

        # Close the trade after all four targets have been reached.
        if all(trade["tp_hit"]):

            realized_pips = estimate_realized_pips(
                trade,
                price
            )

            result_r = estimate_result_r(
                trade,
                realized_pips
            )

            trade["outcome"] = (
                "win" if realized_pips > 0
                else "breakeven"
            )

            trade["exit_price"] = price
            trade["closed_at"] = now.isoformat()

            trade["realized_pips"] = realized_pips
            trade["result_pips"] = realized_pips

            if result_r is not None:
                trade["result_r"] = result_r

            trade["result_method"] = (
                "estimated_equal_25pct_tp_scale_out"
            )

            history.append(trade)

            changed = True
            continue

        if was_changed:
            changed = True

        still_open.append(trade)

    if changed:

        save_json(
            OPEN_TRADES_FILE,
            still_open
        )

        save_json(
            HISTORY_FILE,
            history
        )


def period_stats(history, since_ts):

    period_trades = []

    for trade in history:

        closed_at = parse_timestamp(
            trade.get("closed_at"),
            None
        )

        if (
            closed_at is not None
            and closed_at > since_ts
        ):

            period_trades.append(trade)

    wins = 0
    losses = 0
    breakeven = 0

    total_pips = 0.0

    for trade in period_trades:

        outcome = str(
            trade.get("outcome", "")
        ).lower()

        if outcome == "win":

            wins += 1

        elif outcome == "loss":

            losses += 1

        elif outcome == "breakeven":

            breakeven += 1

        else:

            # Compatibility with older records.
            flags = trade.get(
                "tp_hit",
                [False, False, False, False]
            )

            if isinstance(flags, list) and flags and flags[0]:
                wins += 1
            else:
                losses += 1

        value = trade.get(
            "realized_pips",
            trade.get("result_pips", 0)
        )

        try:
            total_pips += float(value)

        except (TypeError, ValueError):
            pass

    decided = wins + losses

    win_rate = (
        wins / decided * 100.0
        if decided
        else 0.0
    )

    return (
        len(period_trades),
        wins,
        losses,
        breakeven,
        win_rate,
        total_pips
    )


def date_range_label(label, now):

    if label == "DAILY":

        return now.strftime("%d %b %Y")

    if label == "WEEKLY":

        monday = (
            now.date()
            - timedelta(days=now.weekday())
        )

        return (
            f"{monday.strftime('%d %b')} - "
            f"{now.strftime('%d %b %Y')}"
        )

    if label == "MONTHLY":

        first = now.date().replace(day=1)

        return (
            f"{first.strftime('%d %b')} - "
            f"{now.strftime('%d %b %Y')}"
        )

    return ""


def post_recap(label, state):

    history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(history, list):
        raise ValueError(
            "data/trade_history.json must contain a JSON list"
        )

    state_key = {
        "DAILY": "last_daily_ts",
        "WEEKLY": "last_weekly_ts",
        "MONTHLY": "last_monthly_ts",
    }[label]

    since = parse_timestamp(
        state.get(state_key),
        parse_timestamp(DEFAULT_TS)
    )

    now = utc_now()

    date_label = date_range_label(
        label,
        now
    )

    (
        n,
        wins,
        losses,
        breakeven,
        win_rate,
        total_pips
    ) = period_stats(
        history,
        since
    )

    make_update_card(
        f"{label} RECAP",
        date_label,
        RECAP_IMAGE
    )

    if n == 0:

        caption = (
            f"📊 {label} RECAP\n"
            f"🗓️ {date_label}\n"
            f"No trades closed."
        )

    else:

        caption = (
            f"📊 {label} RECAP\n"
            f"🗓️ {date_label}\n"
            f"Closed: {n}\n"
            f"✅ Wins: {wins}\n"
            f"🛑 Losses: {losses}\n"
            f"🟡 Breakeven: {breakeven}\n"
            f"📈 Win Rate: {win_rate:.0f}%\n"
            f"💰 Estimated net pips: {total_pips:+.0f}"
        )

    # Advance the recap timestamp only after Telegram confirms the post.
    send_photo(
        RECAP_IMAGE,
        caption
    )

    state[state_key] = now.isoformat()


def publish_market_status(title, message, caption):

    make_update_card(
        title,
        message,
        UPDATE_IMAGE
    )

    send_photo(
        UPDATE_IMAGE,
        caption
    )


def main():

    now = utc_now()

    state = load_json(
        STATE_FILE,
        {}
    )

    if not isinstance(state, dict):
        state = {}

    was_open = state.get("was_open")

    currently_open = is_market_open(now)

    # Market closing announcement.
    if was_open is True and not currently_open:

        reopen_str = next_reopen(
            now
        ).strftime("%d %b %Y, %H:%M UTC")

        closed_date = now.strftime(
            "%d %b %Y"
        )

        publish_market_status(
            "MARKET CLOSED",
            (
                f"Closed: {closed_date}\n"
                f"All trades on hold.\n"
                f"Reopens: {reopen_str}"
            ),
            (
                f"🔒 *GOLD MARKET CLOSED*\n"
                f"🗓️ {closed_date}\n"
                f"All trades on hold.\n"
                f"🕒 Reopens: {reopen_str}"
            )
        )

    # Market reopening announcement.
    if was_open is False and currently_open:

        open_str = now.strftime(
            "%d %b %Y, %H:%M UTC"
        )

        publish_market_status(
            "MARKET OPEN",
            (
                f"Opened: {open_str}\n"
                f"Signals resume."
            ),
            (
                f"🟢 *GOLD MARKET OPEN*\n"
                f"🗓️ {open_str}\n"
                f"Signals resume."
            )
        )

    rollover = rollover_hour(now)

    if (
        now.weekday() in (0, 1, 2, 3, 4)
        and now.hour == rollover
    ):

        today = now.date().isoformat()

        if state.get("last_daily_date") != today:

            try:

                post_recap(
                    "DAILY",
                    state
                )

                state["last_daily_date"] = today

            except Exception as exc:

                print(
                    f"Daily recap failed: {exc}"
                )

            if now.weekday() == 4:

                try:

                    post_recap(
                        "WEEKLY",
                        state
                    )

                except Exception as exc:

                    print(
                        f"Weekly recap failed: {exc}"
                    )

                if (
                    now.date()
                    == last_trading_day_of_month(now)
                ):

                    try:

                        post_recap(
                            "MONTHLY",
                            state
                        )

                    except Exception as exc:

                        print(
                            f"Monthly recap failed: {exc}"
                        )

    state["was_open"] = currently_open

    save_json(
        STATE_FILE,
        state
    )

    if currently_open:
        run_tracker()


if __name__ == "__main__":
    main()
