from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont
from common import get_price, td_get, send_photo, load_json, save_json, OPEN_TRADES_FILE

ASSETS = [
    {"symbol": "XAU/USD", "label": "GOLD (XAU/USD)"},
    {"symbol": "BTC/USD", "label": "BITCOIN (BTC/USD)"},
    {"symbol": "ETH/USD", "label": "ETHEREUM (ETH/USD)"},
]

W, H = 1080, 1350
BLACK, GOLD, WHITE = (10,10,10), (212,175,55), (240,240,240)
GREEN, RED = (46,204,113), (231,76,60)
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def font(size):
    return ImageFont.truetype(BOLD, size)

def center_text(d, y, text, f, fill, w=W):
    bbox = d.textbbox((0,0), text, font=f)
    d.text(((w-(bbox[2]-bbox[0]))/2, y), text, font=f, fill=fill)

def make_card(direction, label, filename):
    accent = GREEN if direction == "BUY" else RED
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16,16,W-16,H-16], radius=34, outline=accent, width=8)
    d.rounded_rectangle([38,38,W-38,H-38], radius=24, outline=(55,55,55), width=2)
    center_text(d, 110, f"★  {label}  ★", font(42), WHITE)
    arrow = "▲" if direction == "BUY" else "▼"
    center_text(d, 460, arrow, font(130), accent)
    center_text(d, 640, direction, font(150), accent)
    center_text(d, H-160, "⚡ JAY CRYPTGOLD SIGNALS ⚡", font(34), GOLD)
    img.save(filename)

def get_signal(symbol):
    price = get_price(symbol)
    atr = float(td_get("atr", symbol, interval="1h", time_period=14)["values"][0]["atr"])
    rsi = float(td_get("rsi", symbol, interval="1h", time_period=14)["values"][0]["rsi"])
    direction = "BUY" if rsi >= 50 else "SELL"
    sign = 1 if direction == "BUY" else -1
    sl = price - sign * 1.5 * atr
    tps = [price + sign * m * atr for m in (1,2,3,4)]
    return direction, price, sl, tps

def caption(direction, label, entry, sl, tps):
    emoji = "🟢" if direction == "BUY" else "🔴"
    lines = [f"{emoji} {direction} — {label}", "", f"Entry: `{entry:.2f}`"]
    for i, tp in enumerate(tps, 1):
        lines.append(f"🎯 TP{i}: `{tp:.2f}`")
    lines.append(f"🛑 SL: `{sl:.2f}`")
    lines += ["", "⚠️ Trade responsibly. Not financial advice."]
    return "\n".join(lines)

def main():
    hour = datetime.now(timezone.utc).hour
    asset = ASSETS[hour % 3]
    direction, entry, sl, tps = get_signal(asset["symbol"])
    make_card(direction, asset["label"], "/tmp/card.png")
    send_photo("/tmp/card.png", caption(direction, asset["label"], entry, sl, tps))

    trades = load_json(OPEN_TRADES_FILE, [])
    trades.append({
        "id": f"{asset['symbol']}-{int(datetime.now(timezone.utc).timestamp())}",
        "symbol": asset["symbol"], "label": asset["label"], "direction": direction,
        "entry": entry, "sl": sl, "tps": tps, "tp_hit": [False]*4,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    })
    save_json(OPEN_TRADES_FILE, trades)

if __name__ == "__main__":
    main()
