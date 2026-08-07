from datetime import datetime, timezone, timedelta
from common import (get_gold_price, send_text, send_photo, load_json, save_json,
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
            still_open.append(t)
            continue
        
        try:
            price = get_gold_price()
        except Exception as e:
            print(f"Price fetch failed: {e}")
            still_open.append(t)
            continue
        
        now = datetime.now(timezone.utc)
        issued_str = datetime.fromisoformat(t["opened_at"]).strftime("%Y-%m-%d %H:%M:%S UTC")
        closed = False
        
        # Breakeven: move SL to entry after TP1
        if t.get("tp_hit", [False]*4)[0] and not t.get("breakeven_moved", False):
            t["sl"] = t["entry"]
            t["breakeven_moved"] = True
            send_text(
                f"🛡️ *BREAKEVEN MOVED*\n\n"
                f"📌 Pair: {t['label']}\n"
                f"💰 SL moved to entry: `{t['entry']:.2f}`\n"
                f"📅 Signal: {issued_str}\n"
                f"✅ TP1 secured — risk-free trade now!"
            )
        
        # Check SL
        if hit(t["direction"], t["sl"], price, is_tp=False):
            move = signed_move(t["direction"], t["entry"], t["sl"])
            outcome_text = "BREAKEVEN" if t.get("breakeven_moved") else "STOP LOSS"
            emoji = "🟡" if t.get("breakeven_moved") else "🔴"
            pips_val = pips(move)
            
            send_text(
                f"{emoji} *{outcome_text} HIT*\n\n"
                f"📌 Pair: {t['label']}\n"
                f"📉 Exit: `{price:.2f}`\n"
                f"📅 Issued: {issued_str}\n"
                f"💰 Pips: {pips_val:+.0f}"
            )
            t["outcome"] = "breakeven" if t.get("breakeven_moved") else "loss"
            t["exit_price"] = price
            t["closed_at"] = now.isoformat()
            t["result_pips"] = pips_val if t.get("breakeven_moved") else -pips_val
            history.append(t)
            closed = True
        else:
            # Check TPs
            for i, tp in enumerate(t["tps"]):
                if not t["tp_hit"][i] and hit(t["direction"], tp, price, is_tp=True):
                    move = signed_move(t["direction"], t["entry"], tp)
                    send_text(
                        f"✅ *TAKE PROFIT {i+1} HIT!*\n\n"
                        f"📌 Pair: {t['label']}\n"
                        f"📈 Exit: `{price:.2f}`\n"
                        f"📅 Issued: {issued_str}\n"
                        f"💰 Pips: +{pips(move):.0f}"
                    )
                    t["tp_hit"][i] = True
            
            # All TPs hit
            if all(t["tp_hit"]):
                move = signed_move(t["direction"], t["entry"], t["tps"][3])
                t["outcome"] = "win"
                t["exit_price"] = price
                t["closed_at"] = now.isoformat()
                t["result_pips"] = pips(move)
                history.append(t)
                closed = True
        
        if not closed:
            still_open.append(t)
    
    save_json(OPEN_TRADES_FILE, still_open)
    save_json(HISTORY_FILE, history)

def period_stats(history, since_ts):
    trades = []
    for t in history:
        try:
            if datetime.fromisoformat(t["closed_at"]) > since_ts:
                trades.append(t)
        except Exception:
            continue
    wins = sum(1 for t in trades if t.get("outcome") == "win")
    be = sum(1 for t in trades if t.get("outcome") == "breakeven")
    losses = sum(1 for t in trades if t.get("outcome") == "loss")
    total_pips = sum(t.get("result_pips", 0) for t in trades)
    total = wins + be + losses
    rate = (wins / total * 100) if total else 0
    return len(trades), wins, be, losses, rate, total_pips

def date_range_label(label, now):
    if label == "DAILY":
        return now.strftime("%d %b %Y")
    if label == "WEEKLY":
        mon = now.date() - timedelta(days=now.weekday())
        return f"{mon.strftime('%d %b')} - {now.strftime('%d %b %Y')}"
    if label == "MONTHLY":
        first = now.date().replace(day=1)
        return f"{first.strftime('%d %b')} - {now.strftime('%d %b %Y')}"
    return ""

def post_recap(label, state):
    history = load_json(HISTORY_FILE, [])
    key = {"DAILY": "last_daily_ts", "WEEKLY": "last_weekly_ts", "MONTHLY": "last_monthly_ts"}[label]
    since = datetime.fromisoformat(state.get(key, DEFAULT_TS))
    n, wins, be, losses, rate, total_pips = period_stats(history, since)
    now = datetime.now(timezone.utc)
    dl = date_range_label(label, now)
    
    make_update_card(f"{label} RECAP", dl, "/tmp/recap.png")
    if n == 0:
        cap = f"📊 {label} RECAP\n🗓️ {dl}\nNo trades closed."
    else:
        cap = (f"📊 {label} RECAP\n🗓️ {dl}\nClosed: {n}\n"
               f"✅ Wins: {wins} | 🟡 BE: {be} | 🛑 Losses: {losses}\n"
               f"📈 Win rate: {rate:.0f}%\n💰 Total pips: {total_pips:+.0f}")
    send_photo("/tmp/recap.png", cap)
    state[key] = now.isoformat()

def main():
    now = datetime.now(timezone.utc)
    state = load_json(STATE_FILE, {})
    was_open = state.get("was_open")
    currently_open = is_market_open(now)
    
    if was_open is True and not currently_open:
        rs = next_reopen(now).strftime("%d %b %Y, %H:%M UTC")
        cd = now.strftime("%d %b %Y")
        make_update_card("MARKET CLOSED", f"Closed: {cd}\nAll trades on hold.\nReopens: {rs}", "/tmp/update.png")
        send_photo("/tmp/update.png", f"🔒 *GOLD MARKET CLOSED*\n🗓️ {cd}\nAll trades on hold.\n🕒 Reopens: {rs}")
    if was_open is False and currently_open:
        os = now.strftime("%d %b %Y, %H:%M UTC")
        make_update_card("MARKET OPEN", f"Opened: {os}\nSignals resume.", "/tmp/update.png")
        send_photo("/tmp/update.png", f"🟢 *GOLD MARKET OPEN*\n🗓️ {os}\nSignals resume.")
    
    rh = rollover_hour(now)
    if now.weekday() in (0,1,2,3,4) and now.hour == rh:
        td = now.date().isoformat()
        if state.get("last_daily_date") != td:
            try:
                post_recap("DAILY", state)
            except Exception as e:
                print(f"Daily recap failed: {e}")
            if now.weekday() == 4:
                try:
                    post_recap("WEEKLY", state)
                except Exception as e:
                    print(f"Weekly recap failed: {e}")
                if now.date() == last_trading_day_of_month(now):
                    try:
                        post_recap("MONTHLY", state)
                    except Exception as e:
                        print(f"Monthly recap failed: {e}")
            state["last_daily_date"] = td
    
    state["was_open"] = currently_open
    save_json(STATE_FILE, state)
    
    if currently_open:
        run_tracker()

if __name__ == "__main__":
    main()
