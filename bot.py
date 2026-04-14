#!/usr/bin/env python3
"""
Telegram Stock & Crypto Alert Bot
Tracks stocks, indices, and crypto. Alerts subscribers when prices drop
10/20/30/40/50% from all-time highs. Includes relevant news context
and market-wide sentiment analysis.
Designed to run hourly via GitHub Actions.
"""

import os
import json
import logging
import requests
import yfinance as yf
from datetime import datetime, timezone

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

STOCKS = {
    "AAPL": "Apple",
    "AMZN": "Amazon",
    "GOOGL": "Google",
    "TSLA": "Tesla",
    "META": "Meta",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "NFLX": "Netflix",
    "WMT": "Walmart",
}

INDICES = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones",
}

CRYPTO = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
}

MARKET_INDICES = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow Jones"}

ALL_TICKERS = {**STOCKS, **INDICES, **CRYPTO}
ALERT_THRESHOLDS = [10, 20, 30, 40, 50]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(filename, default=None):
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default if default is not None else {}


def save_json(filename, data):
    ensure_data_dir()
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def tg_api(method, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    resp = requests.post(url, json=params, timeout=30)
    return resp.json()


def send_message(chat_id, text, parse_mode="HTML"):
    if len(text) <= 4096:
        return tg_api("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode)
    chunks = [text[i : i + 4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        tg_api("sendMessage", chat_id=chat_id, text=chunk, parse_mode=parse_mode)


def get_updates(offset=None):
    params = {"timeout": 5, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    return tg_api("getUpdates", **params)


def load_subscribers():
    return load_json("subscribers.json", default=[])


def save_subscribers(subs):
    save_json("subscribers.json", subs)


def process_updates():
    subs = load_subscribers()
    state = load_json("update_offset.json", default={"offset": None})
    offset = state.get("offset")
    result = get_updates(offset=offset)
    if not result.get("ok"):
        log.warning("Failed to fetch updates: %s", result)
        return subs
    updates = result.get("result", [])
    new_offset = offset
    for update in updates:
        new_offset = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text", "").strip().lower()
        chat_id = msg.get("chat", {}).get("id")
        first_name = msg.get("from", {}).get("first_name", "there")
        if not chat_id:
            continue
        if text == "/start":
            if chat_id not in subs:
                subs.append(chat_id)
                save_subscribers(subs)
                log.info("New subscriber: %s (chat_id=%s)", first_name, chat_id)
            send_message(
                chat_id,
                f"\ud83d\udc4b Welcome, <b>{first_name}</b>!\n\n"
                "You'll receive alerts when these assets drop "
                "<b>10%, 20%, 30%, 40%, or 50%</b> from their all-time highs.\n\n"
                "<b>\ud83c\udfe2 Stocks:</b> " + ", ".join(STOCKS.values()) + "\n"
                "<b>\ud83d\udcc8 Indices:</b> " + ", ".join(INDICES.values()) + "\n"
                "<b>\ud83e\ude99 Crypto:</b> " + ", ".join(CRYPTO.values()) + "\n\n"
                "<b>Commands:</b>\n"
                "/status \u2014 current prices vs ATH\n"
                "/market \u2014 market health check\n"
                "/stop \u2014 unsubscribe from alerts",
            )
        elif text == "/stop":
            if chat_id in subs:
                subs.remove(chat_id)
                save_subscribers(subs)
                log.info("Unsubscribed: chat_id=%s", chat_id)
            send_message(chat_id, "\u2705 You've been unsubscribed. Send /start to rejoin.")
        elif text == "/status":
            send_message(chat_id, "\u23f3 Fetching data...")
            status_text = build_status_message()
            send_message(chat_id, status_text)
        elif text == "/market":
            send_message(chat_id, "\u23f3 Analyzing market...")
            market_text = build_market_message()
            send_message(chat_id, market_text)
    save_json("update_offset.json", {"offset": new_offset})
    return subs


def fetch_ath_and_current(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="max")
        if hist.empty:
            log.warning("No data for %s", ticker_symbol)
            return None, None
        ath = float(hist["High"].max())
        current = float(hist["Close"].iloc[-1])
        return ath, current
    except Exception as e:
        log.error("Error fetching %s: %s", ticker_symbol, e)
        return None, None


def fetch_daily_change(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="5d")
        if len(hist) < 2:
            return None
        prev_close = float(hist["Close"].iloc[-2])
        current = float(hist["Close"].iloc[-1])
        return ((current - prev_close) / prev_close) * 100
    except Exception:
        return None


def fetch_news(ticker_symbol, max_items=3):
    try:
        ticker = yf.Ticker(ticker_symbol)
        news = ticker.news
        if not news:
            return []
        headlines = []
        for item in news[:max_items]:
            content = item.get("content", {})
            title = content.get("title", item.get("title", ""))
            link = ""
            canonical = content.get("canonicalUrl", {})
            if isinstance(canonical, dict):
                link = canonical.get("url", "")
            if not link:
                ctr = content.get("clickThroughUrl", {})
                if isinstance(ctr, dict):
                    link = ctr.get("url", "")
            if not link:
                link = item.get("link", "")
            if title:
                headlines.append({"title": title, "link": link})
        return headlines
    except Exception as e:
        log.warning("News fetch failed for %s: %s", ticker_symbol, e)
        return []


def format_news(headlines):
    if not headlines:
        return "   \ud83d\udcf0 No recent news available"
    lines = []
    for h in headlines:
        if h["link"]:
            lines.append(f'   \ud83d\udcf0 <a href="{h["link"]}">{h["title"]}</a>')
        else:
            lines.append(f"   \ud83d\udcf0 {h['title']}")
    return "\n".join(lines)


def assess_market_health():
    index_data = []
    for symbol, name in MARKET_INDICES.items():
        ath, current = fetch_ath_and_current(symbol)
        daily_change = fetch_daily_change(symbol)
        if ath and current:
            drop_from_ath = ((ath - current) / ath) * 100
            index_data.append({
                "symbol": symbol, "name": name, "current": current,
                "ath": ath, "drop_from_ath": drop_from_ath, "daily_change": daily_change,
            })
    if not index_data:
        return {"status": "unknown", "label": "\u26aa Unknown", "message": "Could not fetch market data.", "indices": [], "recommendation": ""}
    avg_drop = sum(d["drop_from_ath"] for d in index_data) / len(index_data)
    daily_changes = [d["daily_change"] for d in index_data if d["daily_change"] is not None]
    avg_daily = sum(daily_changes) / len(daily_changes) if daily_changes else 0
    if avg_drop >= 30:
        status, label = "crash", "\ud83d\udd34 CRASH"
        recommendation = "\u26d4 <b>Market in severe decline.</b> All major indices are 30%+ below ATH. Extremely risky to buy."
    elif avg_drop >= 20:
        status, label = "bear", "\ud83d\udd34 BEAR MARKET"
        recommendation = "\u26a0\ufe0f <b>Bear market territory.</b> Indices are 20%+ below ATH. Buying carries high risk."
    elif avg_drop >= 10:
        status, label = "correction", "\ud83d\udfe0 CORRECTION"
        recommendation = "\u26a0\ufe0f <b>Market correction.</b> Indices are 10%+ below ATH. Be cautious."
    elif avg_daily < -2:
        status, label = "selloff", "\ud83d\udfe1 SELL-OFF"
        recommendation = "\u26a0\ufe0f <b>Sharp daily sell-off.</b> Wait for stabilization before buying."
    elif avg_drop >= 5:
        status, label = "pullback", "\ud83d\udfe1 PULLBACK"
        recommendation = "\u2139\ufe0f <b>Mild pullback.</b> Normal volatility \u2014 proceed with caution."
    else:
        status, label = "healthy", "\ud83d\udfe2 HEALTHY"
        recommendation = "\u2705 <b>Market near highs.</b> Conditions are favorable."
    return {"status": status, "label": label, "avg_drop": avg_drop, "avg_daily": avg_daily, "indices": index_data, "recommendation": recommendation}


def build_market_message():
    health = assess_market_health()
    lines = [f"\ud83c\udfdb <b>Market Health: {health['label']}</b>\n"]
    for idx in health.get("indices", []):
        daily_str = ""
        if idx["daily_change"] is not None:
            arrow = "\ud83d\udcc8" if idx["daily_change"] >= 0 else "\ud83d\udcc9"
            daily_str = f" | Today: {arrow} {idx['daily_change']:+.1f}%"
        lines.append(f"  <b>{idx['name']}</b>: ${idx['current']:,.2f} (ATH: ${idx['ath']:,.2f}, -{idx['drop_from_ath']:.1f}%){daily_str}")
    lines.append(f"\n{health['recommendation']}")
    lines.append(f"\n\ud83d\udd50 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def build_status_message():
    lines = ["\ud83d\udcca <b>Portfolio Status</b>\n"]
    sections = [("\ud83c\udfe2 Stocks", STOCKS), ("\ud83d\udcc8 Indices", INDICES), ("\ud83e\ude99 Crypto", CRYPTO)]
    for section_name, tickers in sections:
        lines.append(f"\n<b>{section_name}</b>")
        for symbol, name in tickers.items():
            ath, current = fetch_ath_and_current(symbol)
            daily_change = fetch_daily_change(symbol)
            if ath and current:
                drop_pct = ((ath - current) / ath) * 100
                emoji = "\ud83d\udfe2" if drop_pct < 10 else "\ud83d\udfe1" if drop_pct < 20 else "\ud83d\udfe0" if drop_pct < 30 else "\ud83d\udd34"
                daily_str = f" | Today: {daily_change:+.1f}%" if daily_change is not None else ""
                lines.append(f"{emoji} <b>{name}</b> ({symbol})\n   ${current:,.2f} | ATH: ${ath:,.2f} | Down: {drop_pct:.1f}%{daily_str}")
            else:
                lines.append(f"\u26aa <b>{name}</b> ({symbol}) \u2014 data unavailable")
    lines.append(f"\n\ud83d\udd50 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def check_and_alert():
    subs = load_subscribers()
    if not subs:
        log.info("No subscribers \u2014 skipping alerts.")
        return
    market = assess_market_health()
    sent_alerts = load_json("sent_alerts.json", default={})
    new_alerts = []
    for symbol, name in ALL_TICKERS.items():
        ath, current = fetch_ath_and_current(symbol)
        if not ath or not current:
            continue
        drop_pct = ((ath - current) / ath) * 100
        symbol_alerts = sent_alerts.get(symbol, [])
        for threshold in ALERT_THRESHOLDS:
            if drop_pct >= threshold and threshold not in symbol_alerts:
                news = fetch_news(symbol)
                new_alerts.append({"symbol": symbol, "name": name, "threshold": threshold, "drop_pct": drop_pct, "current": current, "ath": ath, "news": news})
                symbol_alerts.append(threshold)
        for threshold in list(symbol_alerts):
            if drop_pct < threshold:
                symbol_alerts.remove(threshold)
        sent_alerts[symbol] = symbol_alerts
    save_json("sent_alerts.json", sent_alerts)
    if new_alerts:
        for alert in new_alerts:
            emoji = "\u26a0\ufe0f" if alert["threshold"] <= 20 else "\ud83d\udea8"
            text = (f"{emoji} <b>ATH Drop Alert</b>\n\n<b>{alert['name']}</b> ({alert['symbol']})\n"
                    f"\ud83d\udcc9 Down <b>{alert['drop_pct']:.1f}%</b> from ATH\n"
                    f"\ud83d\udcb0 Current: <b>${alert['current']:,.2f}</b>\n"
                    f"\ud83c\udfd4 ATH: <b>${alert['ath']:,.2f}</b>\n"
                    f"\ud83c\udfaf Threshold crossed: <b>{alert['threshold']}%</b>\n")
            text += "\n<b>\ud83d\udcf0 Why it might be down:</b>\n" + format_news(alert["news"])
            text += f"\n\n<b>\ud83c\udfdb Market: {market['label']}</b>\n{market['recommendation']}"
            text += f"\n\n\ud83d\udd50 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            for chat_id in subs:
                try:
                    send_message(chat_id, text)
                    log.info("Alert sent: %s -%d%% to chat_id=%s", alert["symbol"], alert["threshold"], chat_id)
                except Exception as e:
                    log.error("Failed to send to %s: %s", chat_id, e)
        log.info("Sent %d alert(s) to %d subscriber(s).", len(new_alerts), len(subs))
    else:
        log.info("No new threshold crossings detected.")
    if market["status"] in ("correction", "bear", "crash", "selloff"):
        market_msg = f"\ud83c\udfdb <b>Market Warning</b>\n\nStatus: {market['label']}\n\n{market['recommendation']}\n\n<b>Index Details:</b>\n"
        for idx in market.get("indices", []):
            if idx["daily_change"] is not None:
                daily_str = f" (today: {idx['daily_change']:+.1f}%)"
                market_msg += f"  \u2022 {idx['name']}: ${idx['current']:,.2f} (-{idx['drop_from_ath']:.1f}% from ATH){daily_str}\n"
        market_msg += f"\n\ud83d\udd50 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        for chat_id in subs:
            try:
                send_message(chat_id, market_msg)
            except Exception as e:
                log.error("Failed to send market warning to %s: %s", chat_id, e)


def main():
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return
    ensure_data_dir()
    log.info("=== Stock Alert Bot Run ===")
    log.info("Processing Telegram updates...")
    process_updates()
    log.info("Checking prices and sending alerts...")
    check_and_alert()
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
