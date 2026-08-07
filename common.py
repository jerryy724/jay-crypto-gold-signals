import os, json, requests
import yfinance as yf
from pathlib import Path
from datetime import datetime, timezone, timedelta, date

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
raw_chat_id = os.environ["TELEGRAM_CHAT_ID"].strip().strip('"').strip("'")
for bad_dash in ["\u2013", "\u2014", "\u2212"]:
    raw_chat_id = raw_chat_id.replace(bad_dash, "-")
CHAT_ID = raw_chat_id

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
OPEN_TRADES_FILE = DATA_DIR / "open_trades.json"
HISTORY_FILE = DATA_DIR / "trade_history.json"
STATE_FILE = DATA_DIR / "market_state.json"
PRE_ALERT_FILE = DATA_DIR / "pre_alert_state.json"

def load_json(path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ============================================================
# FREE GOLD DATA (Yahoo Finance)
# ============================================================

def get_gold_price():
    for symbol in ["GC=F", "XAUUSD=X"]:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            continue
    raise RuntimeError("Failed to fetch gold price")

def fetch_gold_bars_1h(days=10):
    for symbol in ["GC=F", "XAUUSD=X"]:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=f"{days}d", interval="1h")
            if not df.empty and len(df) >= 50:
                return [{"open": float(r["Open"]), "high": float(r["High"]),
                         "low": float(r["Low"]), "close": float(r["Close"])} for _, r in df.iterrows()]
        except Exception:
            continue
    raise RuntimeError("Failed to fetch 1h bars")

def fetch_gold_bars_4h(days=30):
    for symbol in ["GC=F", "XAUUSD=X"]:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=f"{days}d", interval="4h")
            if not df.empty and len(df) >= 20:
                return [{"open": float(r["Open"]), "high": float(r["High"]),
                         "low": float(r["Low"]), "close": float(r["Close"])} for _, r in df.iterrows()]
        except Exception:
            continue
    raise RuntimeError("Failed to fetch 4h bars")

# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e

def rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(bars, period=14):
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

# ============================================================
# MARKET STRUCTURE
# ============================================================

def find_swing_highs(bars, lookback=5):
    swings = []
    for i in range(lookback, len(bars) - lookback):
        ch = bars[i]["high"]
        left = all(bars[i - j]["high"] <= ch for j in range(1, lookback + 1))
        right = all(bars[i + j]["high"] <= ch for j in range(1, lookback + 1))
        if left and right:
            swings.append({"index": i, "price": ch, "type": "high"})
    return swings

def find_swing_lows(bars, lookback=5):
    swings = []
    for i in range(lookback, len(bars) - lookback):
        cl = bars[i]["low"]
        left = all(bars[i - j]["low"] >= cl for j in range(1, lookback + 1))
        right = all(bars[i + j]["low"] >= cl for j in range(1, lookback + 1))
        if left and right:
            swings.append({"index": i, "price": cl, "type": "low"})
    return swings

def get_nearest_structure(bars, price, direction, atr_val):
    if direction == "BUY":
        lows = find_swing_lows(bars, lookback=3)
        valid = [s for s in lows if s["price"] < price]
        if not valid:
            return None, 999
        nearest = max(valid, key=lambda s: s["price"])
        dist = (price - nearest["price"]) / atr_val if atr_val > 0 else 999
        return nearest["price"], dist
    else:
        highs = find_swing_highs(bars, lookback=3)
        valid = [s for s in highs if s["price"] > price]
        if not valid:
            return None, 999
        nearest = min(valid, key=lambda s: s["price"])
        dist = (nearest["price"] - price) / atr_val if atr_val > 0 else 999
        return nearest["price"], dist

# ============================================================
# LIQUIDITY FILTER
# ============================================================

def check_liquidity(bars, direction, atr_val):
    recent = bars[-20:]
    tol = 0.3 * atr_val
    if direction == "BUY":
        lows = [b["low"] for b in recent]
        for i, l1 in enumerate(lows):
            for l2 in lows[i+1:]:
                if abs(l1 - l2) < tol:
                    return False
    else:
        highs = [b["high"] for b in recent]
        for i, h1 in enumerate(highs):
            for h2 in highs[i+1:]:
                if abs(h1 - h2) < tol:
                    return False
    return True

# ============================================================
# NEWS FILTER (Advisory only — never blocks signals)
# ============================================================

def check_news_filter():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        events = r.json()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=30)
        
        for event in events:
            try:
                et = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            except:
                continue
            if now <= et <= cutoff:
                impact = event.get("impact", "").upper()
                currency = event.get("country", "").upper()
                if impact in ["HIGH", "HOLIDAY"] and currency in ["USD", "US", "ALL"]:
                    return False, f"High-impact {currency} news at {et.strftime('%H:%M UTC')}"
        return True, "No high-impact news"
    except Exception as e:
        return True, f"News check failed: {e}"

# ============================================================
# QUALITY SCORING (News advisory, max 15 pts)
# ============================================================

def score_htf_confirmation(price, bars_4h):
    if len(bars_4h) < 50:
        return 15, "HTF data limited"
    c4 = [b["close"] for b in bars_4h]
    e50 = ema(c4, 50)
    r = rsi(c4, 14)
    if price > e50 and r > 50:
        return 25, "Strong bullish HTF"
    elif price < e50 and r < 50:
        return 25, "Strong bearish HTF"
    elif price > e50:
        return 15, "Bullish HTF, mixed RSI"
    elif price < e50:
        return 15, "Bearish HTF, mixed RSI"
    return 10, "HTF neutral"

def score_market_structure(bars_1h, price, direction, atr_val):
    sp, dist = get_nearest_structure(bars_1h, price, direction, atr_val)
    if sp is None:
        return 10, "No clear structure"
    if dist <= 1.0:
        return 25, f"At strong {direction} structure"
    elif dist <= 2.0:
        return 20, f"Near {direction} structure"
    elif dist <= 3.0:
        return 15, f"Approaching {direction} structure"
    return 5, f"Far from structure ({dist:.1f} ATR)"

def score_liquidity(bars_1h, direction, atr_val):
    safe = check_liquidity(bars_1h, direction, atr_val)
    return (15, "Clean liquidity") if safe else (0, "Liquidity pool — avoid")

def score_momentum(rsi_1h, direction):
    if direction == "BUY":
        if 40 <= rsi_1h <= 65:
            return 15, "Healthy bullish momentum"
        elif 35 <= rsi_1h < 40:
            return 10, "Bullish building"
        elif rsi_1h > 70:
            return 5, "Overbought caution"
        return 5, "Weak bullish momentum"
    else:
        if 35 <= rsi_1h <= 60:
            return 15, "Healthy bearish momentum"
        elif 60 < rsi_1h <= 65:
            return 10, "Bearish building"
        elif rsi_1h < 30:
            return 5, "Oversold caution"
        return 5, "Weak bearish momentum"

def score_news():
    safe, reason = check_news_filter()
    if safe:
        return 15, reason
    return 5, reason

# ============================================================
# TELEGRAM
# ============================================================

def send_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=20)
    if not r.ok:
        print("TELEGRAM:", r.status_code, r.text)
    r.raise_for_status()

def send_photo(path, caption_text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption_text, "parse_mode": "Markdown"},
                           files={"photo": f}, timeout=30)
    if not r.ok:
        print("TELEGRAM:", r.status_code, r.text)
    r.raise_for_status()

# ============================================================
# MARKET HOURS
# ============================================================

def nth_sunday(year, month, n):
    d = date(year, month, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7) + timedelta(weeks=n - 1)

def is_exness_summer(now):
    return nth_sunday(now.year, 3, 2) <= now.date() < nth_sunday(now.year, 11, 1)

def rollover_hour(now):
    return 21 if is_exness_summer(now) else 22

def is_market_open(now):
    rh = rollover_hour(now)
    wd = now.weekday()
    if wd == 5:
        return False
    if wd == 4:
        return now.hour < rh
    if wd == 6:
        return now.hour >= rh + 1
    return now.hour != rh

def next_reopen(now):
    rh = rollover_hour(now)
    wd = now.weekday()
    days_ahead = 2 if wd == 4 else 1 if wd == 5 else 0
    reopen = now.date() + timedelta(days=days_ahead)
    return datetime(reopen.year, reopen.month, reopen.day, rh + 1, 0, tzinfo=timezone.utc)

def last_trading_day_of_month(now):
    nm = now.replace(month=now.month + 1, day=1) if now.month < 12 else now.replace(year=now.year + 1, month=1, day=1)
    ld = nm - timedelta(days=1)
    while ld.weekday() >= 5:
        ld -= timedelta(days=1)
    return ld.date()
