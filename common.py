import os, json, requests
from pathlib import Path

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TD_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
OPEN_TRADES_FILE = DATA_DIR / "open_trades.json"
HISTORY_FILE = DATA_DIR / "trade_history.json"

def load_json(path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def td_get(endpoint, symbol, **params):
    url = f"https://api.twelvedata.com/{endpoint}"
    params.update({"symbol": symbol, "apikey": TD_KEY})
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def get_price(symbol):
    return float(td_get("price", symbol)["price"])

def send_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=20)
    r.raise_for_status()

def send_photo(path, caption_text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption_text, "parse_mode": "Markdown"},
                           files={"photo": f}, timeout=30)
    r.raise_for_status()
