from datetime import datetime, timezone
from common import (
    send_photo, load_json, save_json, OPEN_TRADES_FILE, STATE_FILE,
    is_market_open, fetch_gold_bars_1h, fetch_gold_bars_4h,
    score_htf_confirmation, score_market_structure, score_liquidity,
    score_momentum, score_news, atr, ema, rsi
)
from cards import make_signal_card

SYMBOL = "XAU/USD"
LABEL = "GOLD (XAU/USD)"
DIVIDER = "━━━━━━━━━━━━━━━"
MIN_QUALITY_SCORE = 60

def session_tag(now):
    h = now.hour
    if h >= 21 or h < 6:
        return "Sydney/Asian Session"
    if h < 12:
        return "London Session"
    if h < 16:
        return "London/NY Overlap"
    return "New York Session"

def next_signal_number():
    state = load_json(STATE_FILE, {})
    n = state.get("signal_count", 0) + 1
    state["signal_count"] = n
    save_json(STATE_FILE, state)
    return n

def generate_signal():
    bars_1h = fetch_gold_bars_1h(days=10)
    bars_4h = fetch_gold_bars_4h(days=30)
    
    price = bars_1h[-1]["close"]
    closes_1h = [b["close"] for b in bars_1h]
    atr_1h = atr(bars_1h, 14)
    rsi_1h = rsi(closes_1h, 14)
    
    closes_4h = [b["close"] for b in bars_4h]
    ema_50_4h = ema(closes_4h, 50)
    
    trend = "BUY" if price > ema_50_4h else "SELL"
    direction = trend
    
    sign = 1 if direction == "BUY" else -1
    sl = price - sign * 1.5 * atr_1h
    tps = [price + sign * m * atr_1h for m in (0.7, 1.5, 2.5, 3.5)]
    zone_buffer = 0.15 * atr_1h
    zone_low = price - zone_buffer
    zone_high = price + zone_buffer
    
    # Risk:Reward based on SL vs TP4
    sl_distance = abs(price - sl)
    tp4_distance = abs(tps[3] - price)
    rr = tp4_distance / sl_distance if sl_distance > 0 else 0
    
    scores = {}
    scores["htf"] = score_htf_confirmation(price, bars_4h)
    scores["structure"] = score_market_structure(bars_1h, price, direction, atr_1h)
    scores["liquidity"] = score_liquidity(bars_1h, direction, atr_1h)
    scores["momentum"] = score_momentum(rsi_1h, direction)
    scores["news"] = score_news()
    
    total_score = sum(s[0] for s in scores.values())
    
    if total_score >= 85:
        conviction = "🔥🔥 ELITE SETUP"
    elif total_score >= 70:
        conviction = "🔥 High Conviction"
    elif total_score >= 55:
        conviction = "⚡ Standard Setup"
    else:
        conviction = "❌ Low Quality"
    
    return direction, price, sl, tps, zone_low, zone_high, rr, conviction, total_score, scores

def caption(direction, entry_low, entry_high, sl, tps, rr, now, signal_no, conviction, score, scores):
    emoji = "🟢" if direction == "BUY" else "🔴"
    issued_str = now.strftime("%d %b %Y, %H:%M UTC")
    
    score_lines = []
    for key, (pts, reason) in scores.items():
        icon = "✅" if pts >= 15 else "⚠️" if pts >= 5 else "❌"
        score_lines.append(f"{icon} {key.upper()}: {pts}pts — {reason}")
    
    lines = [
        "JAY GOLD MASTER",
        DIVIDER,
        f"{emoji} {direction} — {LABEL}",
        f"Signal #{signal_no:03d} | {session_tag(now)}",
        conviction,
        f"Quality Score: {score}/100",
        f"Issued: {issued_str}",
        DIVIDER,
        "",
        f"Entry Zone: `{entry_high:.2f}` - `{entry_low:.2f}`",
        "",
    ]
    for i, tp in enumerate(tps, 1):
        lines.append(f"🎯 TP{i}: `{tp:.2f}`")
    lines.append("")
    lines.append(f"🛑 SL: `{sl:.2f}`")
    lines.append(f"⚖️ Risk:Reward — 1:{rr:.1f}")
    lines.append("")
    lines.append("📊 Analysis:")
    lines.extend(score_lines)
    lines += ["", "⚠️ Trade responsibly. Use lower position sizes to avoid high risk."]
    return "\n".join(lines)

def main():
    now = datetime.now(timezone.utc)
    if not is_market_open(now):
        print("Market closed — skipping.")
        return
    
    try:
        direction, entry, sl, tps, zone_low, zone_high, rr, conviction, score, scores = generate_signal()
    except Exception as e:
        print(f"Signal generation failed: {e}")
        return
    
    if score < MIN_QUALITY_SCORE:
        print(f"Quality too low ({score}/100, min {MIN_QUALITY_SCORE}). Skipping.")
        for k, (pts, reason) in scores.items():
            print(f"  {k}: {pts}pts — {reason}")
        return
    
    signal_no = next_signal_number()
    make_signal_card(direction, LABEL, "/tmp/card.png")
    send_photo("/tmp/card.png", caption(
        direction, zone_low, zone_high, sl, tps, rr, now, signal_no,
        conviction, score, scores
    ))
    
    trades = load_json(OPEN_TRADES_FILE, [])
    trades.append({
        "id": f"gold-{int(now.timestamp())}",
        "symbol": SYMBOL, "label": LABEL,
        "direction": direction, "entry": entry,
        "sl": sl, "tps": tps, "tp_hit": [False] * 4,
        "opened_at": now.isoformat(),
        "quality_score": score,
        "breakeven_moved": False,
    })
    save_json(OPEN_TRADES_FILE, trades)
    print(f"Signal #{signal_no:03d} posted. Quality: {score}/100")

if __name__ == "__main__":
    main()
