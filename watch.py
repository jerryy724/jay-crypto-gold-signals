from datetime import datetime, timezone
from common import (get_price, send_text, send_photo, load_json, save_json,
                     OPEN_TRADES_FILE, HISTORY_FILE, STATE_FILE,
                     is_market_open, last_trading_day_of_month, rollover_hour, next_reopen)
from cards import make_update_card

DEFAULT_TS = "1970-01-01T00:00:00+00:00"
PIP_SIZE = 0.01
GOLD_SYMBOL = "XAU/USD"

def pips(diff):
    return abs(diff) / PIP_SIZE

def hit(direction, level, price, is_tp):
    if direction == "BUY":
        return price >= level if is_tp else price <= level
    return price <= level if is_tp else price >= level

def signed_move(direction, entry, exit_price):
    return (exit_price - entry) if direction == "BUY" else (entry - exit_price)

def run_tracker():
    trades = load_json(OPEN_TRADES_FILE, [])
    history = load_json(HISTORY_FILE, [])
    still_open = []
    for t in trades:
        if t.get("symbol") != GOLD_SYMBOL:
            continue
        try:
            price = get_price(t["symbol"])
        except Exception as e:
            print(f"Price fetch failed this check, will retry next time: {e}")
            still_open.append(t)
            continue
        now = datetime.now(timezone.utc)
        hit_time_str = now.strftime("%d %b %Y | %H:%M UTC")
        issued_str = datetime.fromisoformat(t["opened_at"]).strftime("%Y-%m-%d %H:%M:%S UTC")
        closed = False
        if hit(t["direction"], t["sl"], price, is_tp=False):
            move = signed_move(t["direction"], t["entry"], t["sl"])
            send_text(
                f"🔴 *STOP LOSS HIT*\n\n"
                f"📌 Pair: {t['label']}\n"
                f"📉 Current Price: `{price:.2f}`\n"
                f"🕒 Hit Time: {hit_time_str}\n"
                f"📅 Signal Issued: {issued_str}\n"
                f"💰 Pips: -{pips(move):.0f}"
            )
            t["outcome"] = "loss"; t["closed_at"] = now.isoformat()
            t["result_pips"] = -pips(move)
            history.append(t); closed = True
        else:
            for i, tp in enumerate(t["tps"]):
                if not t["tp_hit"][i] and hit(t["direction"], tp, price, is_tp=True):
                    move = signed_move(t["direction"], t["entry"], tp)
                    send_text(
                        f"✅ *TAKE PROFIT {i+1} HIT!*\n\n"
                        f"📌 Pair: {t['label']}\n"
                        f"📈 Current Price: `{price:.2f}`\n"
                        f"🕒 Hit Time: {hit_time_str}\n"
                        f"📅 Signal Issued: {issued_str}\n"
                        f"💰 Pips: +{pips(move):.0f}"
                    )
                    t["tp_hit"][i] = True
            if all(t["tp_hit"]):
                move = signed_move(t["direction"], t["entry"], t["tps"][3])
                t["outcome"] = "win"; t["closed_at"] = now.isoformat()
                t["result_pips"] = pips(move)
                history.append(t); closed = True
        if not closed:
            still_open.append(t)
    save_json(OPEN_TRADES_FILE, still_open)
    save_json(HISTORY_FILE, history)

def period_stats(history, since_ts):
    trades = [t for t in history if datetime.fromisoformat(t["closed_at"]) > since_ts]
    wins = sum(1 for t in trades if t["outcome"] == "win")
    losses = sum(1 for t in trades if t["outcome"] == "loss")
    total_pips = sum(t.get("result_pips", 0) for t in trades)
    total = wins + losses
    rate = (wins / total * 100) if total else 0
    return len(trades), wins, losses, rate, total_pips

def post_recap(label, state):
    history = load_json(HISTORY_FILE, [])
    key = {"DAILY": "last_daily_ts", "WEEKLY": "last_weekly_ts", "MONTHLY": "last_monthly_ts"}[label]
    since = datetime.fromisoformat(state.get(key, DEFAULT_TS))
    n, wins, losses, rate, total_pips = period_stats(history, since)
    make_update_card(f"{label} RECAP", "Performance Update", "/tmp/recap.png")
    if n == 0:
        caption = f"📊 {label} RECAP\nNo trades closed this period."
    else:
        caption = f"📊 {label} RECAP\nTrades closed: {n}\n✅ TP hits: {wins}\n🛑 SL hits: {losses}\n📈 Win rate: {rate:.0f}%\n💰 Total pips: {total_pips:+.0f}"
    send_photo("/tmp/recap.png", caption)
    state[key] = datetime.now(timezone.utc).isoformat()

def main():
    now = datetime.now(timezone.utc)
    state = load_json(STATE_FILE, {})
    was_open = state.get("was_open")
    currently_open = is_market_open(now)

    if was_open is True and not currently_open:
        reopen_str = next_reopen(now).strftime("%d %b %Y, %H:%M UTC")
        make_update_card("MARKET CLOSED", f"Gold market is closed.\nAll trades on hold.\nReopens: {reopen_str}", "/tmp/update.png")
        send_photo("/tmp/update.png", f"🔒 *GOLD MARKET CLOSED*\nAll trades on hold.\n🕒 Reopens: {reopen_str}")
    if was_open is False and currently_open:
        make_update_card("MARKET OPEN", "Gold market is now open.\nSignals resume.", "/tmp/update.png")
        send_photo("/tmp/update.png", "🟢 *GOLD MARKET OPEN*\nSignals resume.")

    rh = rollover_hour(now)
    if now.weekday() in (0, 1, 2, 3, 4) and now.hour == rh:
        today_str = now.date().isoformat()
        if state.get("last_daily_date") != today_str:
            post_recap("DAILY", state)
            if now.weekday() == 4:
                post_recap("WEEKLY", state)
                if now.date() == last_trading_day_of_month(now):
                    post_recap("MONTHLY", state)
            state["last_daily_date"] = today_str

    state["was_open"] = currently_open
    save_json(STATE_FILE, state)

    if currently_open:
        run_tracker()

if __name__ == "__main__":
    main()
