from datetime import datetime, timezone, timedelta
from common import load_json, save_json, send_text, send_photo, HISTORY_FILE, STATE_FILE, rollover_hour, next_reopen
from cards import make_update_card

EDU_POSTS = [
    ("🎓 TRADING TIP: FVG", "A Fair Value Gap (FVG) happens when price moves so fast it 'skips a step' — like running upstairs and missing one. Price often comes back to 'step on' that missed zone before continuing. We flag these on signals as extra confluence."),
    ("🎓 TRADING TIP: Killzones", "Not every hour has equal 'traffic.' Killzones are the busiest, most liquid hours (like London/NY overlap) where moves tend to be more reliable. Thin hours (like deep Asian session) can produce less trustworthy moves."),
    ("🎓 TRADING TIP: Fibonacci", "After a big move, price often 'rests' at a few predictable percentage levels (38.2%, 50%, 61.8%) before continuing — like always stopping at the same benches walking up a hill. We check if entries land near these levels."),
    ("🎓 TRADING TIP: Risk Management", "Never risk more than 1-2% of your account on a single trade. On small accounts, even the smallest lot size can exceed this on a single gold stop-loss — size your position to your account, not the other way around."),
    ("🎓 TRADING TIP: Breakeven Protection", "Once TP1 hits, we move your tracked stop loss to entry — meaning from that point, the trade can only end in a win or a scratch, never a full loss on OUR tracking. Remember to move your own broker SL manually too!"),
]

NEWS_LINKS = (
    "📰 *GOLD MARKET NEWS*\n\n"
    "Catch up on what's moving gold this week from trusted, live sources:\n\n"
    "🔗 Kitco News (Gold-focused): https://www.kitco.com/news/\n"
    "🔗 Investing.com Commodities: https://www.investing.com/commodities/gold-news\n\n"
    "Stay informed before markets reopen! 📈"
)

def weekly_recap_deep_dive():
    history = load_json(HISTORY_FILE, [])
    since = datetime.now(timezone.utc) - timedelta(days=7)
    trades = []
    for t in history:
        try:
            if datetime.fromisoformat(t["closed_at"]) > since:
                trades.append(t)
        except Exception:
            continue
    wins = sum(1 for t in trades if t.get("tp_hit", [False] * 4)[0])
    losses = sum(1 for t in trades if t.get("outcome") == "loss")
    total_pips = sum(t.get("result_pips", 0) for t in trades)
    n = len(trades)
    rate = (wins / n * 100) if n else 0

    make_update_card("WEEKLY DEEP-DIVE", "Past 7 Days Performance", "/tmp/weekend.png")
    if n == 0:
        cap = "📊 *WEEKLY DEEP-DIVE*\nNo trades closed in the past 7 days."
    else:
        cap = (f"📊 *WEEKLY DEEP-DIVE*\nLast 7 days: {n} trades\n"
               f"✅ Wins: {wins} | 🛑 Losses: {losses}\n"
               f"📈 Win Rate: {rate:.0f}%\n💰 Total pips: {total_pips:+.0f}\n\n"
               f"Rest up this weekend — markets reopen Sunday! 🔔")
    send_photo("/tmp/weekend.png", cap)

def educational_post():
    state = load_json(STATE_FILE, {})
    idx = state.get("weekend_edu_index", 0) % len(EDU_POSTS)
    title, body = EDU_POSTS[idx]
    make_update_card(title, "Weekend Learning", "/tmp/weekend.png")
    send_photo("/tmp/weekend.png", f"{title}\n\n{body}")
    state["weekend_edu_index"] = (idx + 1) % len(EDU_POSTS)
    save_json(STATE_FILE, state)

def news_links_post():
    make_update_card("GOLD NEWS", "Weekend Reading", "/tmp/weekend.png")
    send_photo("/tmp/weekend.png", NEWS_LINKS)

def reopening_reminder():
    now = datetime.now(timezone.utc)
    reopen_str = next_reopen(now).strftime("%d %b %Y, %H:%M UTC")
    make_update_card("REOPENING SOON", f"Gold reopens: {reopen_str}", "/tmp/weekend.png")
    send_photo("/tmp/weekend.png", f"🔔 *GET READY!*\nGold market reopens: {reopen_str}\nSignals resume shortly after. See you there! 🚀")

def main():
    now = datetime.now(timezone.utc)
    wd = now.weekday()  # 5=Saturday, 6=Sunday
    hour = now.hour

    if wd == 5 and hour == 10:
        weekly_recap_deep_dive()
    elif wd == 5 and hour == 16:
        educational_post()
    elif wd == 5 and hour == 20:
        news_links_post()
    elif wd == 6 and hour == 10:
        educational_post()
    elif wd == 6 and hour == 16:
        news_links_post()
    elif wd == 6 and hour == 20:
        reopening_reminder()
    else:
        print("No scheduled weekend content this hour.")

if __name__ == "__main__":
    main()
