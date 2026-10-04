from datetime import datetime, timezone, timedelta
from common import load_json, save_json, send_photo, fetch_json_url, STATE_FILE, DATA_DIR
from cards import make_update_card, ALERT_COLOR

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_CACHE = DATA_DIR / "news_calendar_cache.json"
CACHE_MAX_AGE_MIN = 60
WATCH_COUNTRY = "USD"
WATCH_IMPACT = "High"

EVENT_INFO = {
    "cpi": ("Consumer Price Index — measures the change in prices consumers pay for goods and services, the main gauge of inflation.", "Higher-than-forecast inflation often pressures gold short-term on rate-hike expectations, but gold can also gain as an inflation hedge — reaction depends on the Fed-rate-path implications."),
    "pce": ("Personal Consumption Expenditures Price Index — the Fed's preferred inflation gauge.", "Similar to CPI: a hot reading can pressure gold on rate expectations, a cool reading often supports it."),
    "non-farm": ("Non-Farm Payrolls — the number of jobs added in the US economy last month, excluding farm work.", "A strong jobs number often pressures gold (stronger economy, less rate-cut urgency); a weak number often supports it."),
    "employment change": ("Measures the net change in employed people — a key economic health signal.", "Stronger-than-expected jobs data generally weighs on gold; weaker data often lifts it."),
    "unemployment rate": ("The percentage of the labor force that is jobless and actively seeking work.", "A rising unemployment rate often supports gold (recession/rate-cut expectations); a falling rate can pressure it."),
    "unemployment claims": ("Weekly count of people filing for unemployment benefits for the first time.", "Rising claims can support gold on growth concerns; falling claims can pressure it."),
    "fed": ("Federal Reserve policy event — can move all markets sharply as it signals the future path of interest rates.", "Rate cuts or dovish tone typically support gold; rate hikes or hawkish tone typically pressure it."),
    "fomc": ("Federal Open Market Committee event — the Fed's rate-setting body statement or minutes.", "Same as above: dovish signals tend to lift gold, hawkish signals tend to weigh on it."),
    "gdp": ("Gross Domestic Product — the broadest measure of economic growth.", "Strong growth can pressure gold short-term; weak growth often supports it on rate-cut hopes."),
    "retail sales": ("Measures the total value of sales at the retail level — a key consumer-spending gauge.", "Strong retail sales can pressure gold; weak sales often support it."),
    "ppi": ("Producer Price Index — measures inflation at the wholesale/producer level, often a leading signal for consumer inflation.", "Similar reaction pattern to CPI."),
    "ism manufacturing": ("Institute for Supply Management's manufacturing activity survey — above 50 signals expansion.", "Weak manufacturing data can support gold on growth concerns; strong data can pressure it."),
    "ism services": ("Institute for Supply Management's services-sector activity survey.", "Similar to manufacturing PMI — weak data tends to support gold, strong data tends to pressure it."),
    "interest rate": ("A central bank's official interest rate decision.", "Rate cuts typically support gold; rate hikes typically pressure it."),
}

def get_event_info(title):
    t = title.lower()
    for key, (desc, note) in EVENT_INFO.items():
        if key in t:
            return desc, note
    return ("A high-impact economic release.", "May cause above-average volatility in gold — trade cautiously around this time.")

def load_calendar():
    cache = load_json(CALENDAR_CACHE, {})
    fetched_at = cache.get("fetched_at")
    stale = True
    if fetched_at:
        age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds() / 60
        stale = age_min > CACHE_MAX_AGE_MIN
    if stale:
        try:
            events = fetch_json_url(CALENDAR_URL)
            cache = {"fetched_at": datetime.now(timezone.utc).isoformat(), "events": events}
            save_json(CALENDAR_CACHE, cache)
        except Exception as e:
            print(f"Calendar fetch failed, using stale cache if available: {e}")
    return cache.get("events", [])

def send_alert_card(minutes_label, event):
    title = event["title"]
    desc, note = get_event_info(title)
    forecast = event.get("forecast") or "N/A"
    previous = event.get("previous") or "N/A"

    make_update_card("NEWS ALERT", f"{minutes_label} MINUTES", "/tmp/news_alert.png",
                      subtitle=title.upper()[:28], accent=ALERT_COLOR)
    caption = (
        f"⚠️ *NEWS ALERT — {minutes_label} MIN*\n\n"
        f"📰 Event: {title} ({event['country']})\n"
        f"📝 {desc}\n\n"
        f"📊 Previous: `{previous}` | Forecast: `{forecast}`\n\n"
        f"🥇 Gold Impact: {note}\n\n"
        f"⚠️ Stay alert — volatility expected around this release."
    )
    send_photo("/tmp/news_alert.png", caption)

def main():
    now = datetime.now(timezone.utc)
    events = load_calendar()
    state = load_json(STATE_FILE, {})
    alerted = state.get("news_alerted", {})

    for event in events:
        if event.get("country") != WATCH_COUNTRY or event.get("impact") != WATCH_IMPACT:
            continue
        try:
            event_time = datetime.fromisoformat(event["date"])
        except Exception:
            continue
        event_time_utc = event_time.astimezone(timezone.utc)
        minutes_until = (event_time_utc - now).total_seconds() / 60
        event_id = f"{event['date']}_{event['title']}"

        if 12.5 <= minutes_until < 17.5 and alerted.get(event_id + "_15") != True:
            send_alert_card("15", event)
            alerted[event_id + "_15"] = True
        elif 2.5 <= minutes_until < 7.5 and alerted.get(event_id + "_5") != True:
            send_alert_card("5", event)
            alerted[event_id + "_5"] = True

    state["news_alerted"] = alerted
    save_json(STATE_FILE, state)

if __name__ == "__main__":
    main()
