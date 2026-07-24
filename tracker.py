from datetime import datetime, timezone
from common import get_price, send_text, load_json, save_json, OPEN_TRADES_FILE, HISTORY_FILE

def hit(direction, level, price, is_tp):
    if direction == "BUY":
        return price >= level if is_tp else price <= level
    return price <= level if is_tp else price >= level

def main():
    trades = load_json(OPEN_TRADES_FILE, [])
    history = load_json(HISTORY_FILE, [])
    still_open = []
    for t in trades:
        price = get_price(t["symbol"])
        closed = False
        if hit(t["direction"], t["sl"], price, is_tp=False):
            move = price - t["entry"] if t["direction"] == "BUY" else t["entry"] - price
            send_text(f"🛑 SL HIT — {t['label']} {t['direction']}\n{move:+.2f}")
            t["outcome"] = "loss"; t["closed_at"] = datetime.now(timezone.utc).isoformat(); t["result"] = move
            history.append(t); closed = True
        else:
            for i, tp in enumerate(t["tps"]):
                if not t["tp_hit"][i] and hit(t["direction"], tp, price, is_tp=True):
                    move = tp - t["entry"] if t["direction"] == "BUY" else t["entry"] - tp
                    send_text(f"✅ TP{i+1} HIT — {t['label']} {t['direction']}\n{move:+.2f} secured")
                    t["tp_hit"][i] = True
            if all(t["tp_hit"]):
                tp4 = t["tps"][3]
                move = tp4 - t["entry"] if t["direction"] == "BUY" else t["entry"] - tp4
                t["outcome"] = "win"; t["closed_at"] = datetime.now(timezone.utc).isoformat(); t["result"] = move
                history.append(t); closed = True
        if not closed:
            still_open.append(t)
    save_json(OPEN_TRADES_FILE, still_open)
    save_json(HISTORY_FILE, history)

if __name__ == "__main__":
    main()
