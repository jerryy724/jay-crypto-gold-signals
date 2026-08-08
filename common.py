import os, json, requests
from pathlib import Path
from datetime import timedelta, date

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
raw_chat_id = os.environ["TELEGRAM_CHAT_ID"].strip().strip('"').strip("'")
for bad_dash in ["\u2013", "\u2014", "\u2212"]:
    raw_chat_id = raw_chat_id.replace(bad_dash, "-")
CHAT_ID = raw_chat_id
TD_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
TD_KEY_2 = os.environ.get("TWELVEDATA_API_KEY_2", "")

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

def td_get(endpoint, symbol, **params):
    keys = [k for k in [TD_KEY, TD_KEY_2] if k]
    last_error = None
    for key in keys:
        url = f"https://api.twelvedata.com/{endpoint}"
        p = dict(params)
        p.update({"symbol": symbol, "apikey": key})
        try:
            r = requests.get(url, params=p, timeout=20)
            data = r.json()
            if r.status_code == 429 or (isinstance(data, dict) and data.get("code") == 429):
                last_error = f"Key ending ...{key[-4:]} hit its limit"
                continue
            r.raise_for_status()
            return data
        except Exception as e:
            last_error = str(e)
            continue
    raise RuntimeError(f"All Twelve Data API keys failed. Last error: {last_error}")

def get_price(symbol):
    return float(td_get("price", symbol)["price"])

def send_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=20)
    if not r.ok:
        print("TELEGRAM SAYS:", r.status_code, r.text)
    r.raise_for_status()

def send_photo(path, caption_text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption_text, "parse_mode": "Markdown"},
                           files={"photo": f}, timeout=30)
    if not r.ok:
        print("TELEGRAM SAYS:", r.status_code, r.text)
    r.raise_for_status()

def nth_sunday(year, month, n):
    d = date(year, month, 1)
    first_sunday = d + timedelta(days=(6 - d.weekday()) % 7)
    return first_sunday + timedelta(weeks=n - 1)

def is_exness_summer(now):
    dst_start = nth_sunday(now.year, 3, 2)
    dst_end = nth_sunday(now.year, 11, 1)
    return dst_start <= now.date() < dst_end

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
    from datetime import datetime, timezone
    rh = rollover_hour(now)
    wd = now.weekday()
    if wd == 4:
        days_ahead = 2
    elif wd == 5:
        days_ahead = 1
    else:
        days_ahead = 0
    reopen_date = now.date() + timedelta(days=days_ahead)
    return datetime(reopen_date.year, reopen_date.month, reopen_date.day, rh + 1, 0, tzinfo=timezone.utc)

def last_trading_day_of_month(now):
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)
    last_day = next_month - timedelta(days=1)
    while last_day.weekday() >= 5:
        last_day -= timedelta(days=1)
    return last_day.date()
