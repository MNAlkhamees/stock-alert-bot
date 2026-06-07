#!/usr/bin/env python3
"""
Telegram Stock & Crypto Alert Bot
──────────────────────────────────
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

# ─── Configuration ───────────────────────────────────────────────────────────

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

# Market health is determined by these indices
MARKET_INDICES = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow Jones"}

ALL_TICKERS = {**STOCKS, **INDICES, **CRYPTO}
ALERT_THRESHOLDS = [10, 20, 30, 40, 50]

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ─── Helpers ─────────────────────────────────────────────────────────────────


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


# ─── Telegram API ────────────────────────────────────────────────────────────


def tg_api(method, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    resp = requests.post(url, json=params, timeout=30)
    return resp.json()


def send_message(chat_id, text, parse_mode="HTML"):
    # Telegram messages max 4096 chars — split if needed
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


# ─── Subscriber Management ───────────────────────────────────────────────────


def load_subscribers():
    return load_json("subscribers.json", default=[])


def save_subscribers(subs):
    save_json("subscribers.json", subs)


def process_updates():
    """Process pending Telegram messages for /start, /stop, /status, /market commands."""
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
                f"👋 Welcome, <b>{first_name}</b>!\n\n"
                "You'll receive alerts when these assets drop "
                "<b>10%, 20%, 30%, 40%, or 50%</b> from their all-time highs.\n\n"
                "<b>🏢 Stocks:</b> " + ", ".join(STOCKS.values()) + "\n"
                "<b>📈 Indices:</b> " + ", ".join(INDICES.values()) + "\n"
                "<b>🪙 Crypto:</b> " + ", ".join(CRYPTO.values()) + "\n\n"
                "<b>Commands:</b>\n"
                "/status — current prices vs ATH\n"
                "/market — market health check\n"
                "/stop — unsubscribe from alerts",
            )

        elif text == "/stop":
            if chat_id in subs:
                subs.remove(chat_id)
                save_subscribers(subs)
                log.info("Unsubscribed: chat_id=%s", chat_id)
            send_message(chat_id, "✅ You've been unsubscribed. Send /start to rejoin.")

        elif text == "/status":
            send_message(chat_id, "⏳ Fetching data...")
            status_text = build_status_message()
            send_message(chat_id, status_text)

        elif text == "/market":
            send_message(chat_id, "⏳ Analyzing market...")
            market_text = build_market_message()
            send_message(chat_id, market_text)

    save_json("update_offset.json", {"offset": new_offset})
    return subs


# ─── Stock / Crypto Data ────────────────────────────────────────────────────


def fetch_ath_and_current(ticker_symbol):
    """Fetch the all-time high and current price for a ticker."""
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
    """Fetch the 1-day percentage change."""
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


# ─── News ────────────────────────────────────────────────────────────────────


def fetch_news(ticker_symbol, max_items=3):
    """Fetch recent news headlines for a ticker via yfinance."""
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
            # Try to get URL from canonical or clickThroughUrl
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
    """Format news headlines for Telegram."""
    if not headlines:
        return "   📰 No recent news available"
    lines = []
    for h in headlines:
        if h["link"]:
            lines.append(f'   📰 <a href="{h["link"]}">{h["title"]}</a>')
        else:
            lines.append(f"   📰 {h['title']}")
    return "\n".join(lines)


# ─── Market Health ───────────────────────────────────────────────────────────


def assess_market_health():
    """
    Analyze overall market conditions.
    Returns a dict with market status, individual index data, and a recommendation.
    """
    index_data = []
    for symbol, name in MARKET_INDICES.items():
        ath, current = fetch_ath_and_current(symbol)
        daily_change = fetch_daily_change(symbol)
        if ath and current:
            drop_from_ath = ((ath - current) / ath) * 100
            index_data.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "current": current,
                    "ath": ath,
                    "drop_from_ath": drop_from_ath,
                    "daily_change": daily_change,
                }
            )

    if not index_data:
        return {
            "status": "unknown",
            "label": "⚪ Unknown",
            "message": "Could not fetch market data.",
            "indices": [],
            "recommendation": "",
        }

    avg_drop = sum(d["drop_from_ath"] for d in index_data) / len(index_data)
    daily_changes = [d["daily_change"] for d in index_data if d["daily_change"] is not None]
    avg_daily = sum(daily_changes) / len(daily_changes) if daily_changes else 0

    # Determine market status
    if avg_drop >= 30:
        status = "crash"
        label = "🔴 CRASH"
        recommendation = (
            "⛔ <b>Market in severe decline.</b> All major indices are 30%+ below ATH. "
            "Extremely risky to buy — consider staying on the sidelines."
        )
    elif avg_drop >= 20:
        status = "bear"
        label = "🔴 BEAR MARKET"
        recommendation = (
            "⚠️ <b>Bear market territory.</b> Indices are 20%+ below ATH. "
            "Buying individual stocks against this trend carries high risk."
        )
    elif avg_drop >= 10:
        status = "correction"
        label = "🟠 CORRECTION"
        recommendation = (
            "⚠️ <b>Market correction.</b> Indices are 10%+ below ATH. "
            "Be cautious — some stocks may have further to fall."
        )
    elif avg_daily < -2:
        status = "selloff"
        label = "🟡 SELL-OFF"
        recommendation = (
            "⚠️ <b>Sharp daily sell-off.</b> Market is dropping fast today. "
            "Wait for stabilization before buying."
        )
    elif avg_drop >= 5:
        status = "pullback"
        label = "🟡 PULLBACK"
        recommendation = (
            "ℹ️ <b>Mild pullback.</b> Market is slightly below recent highs. "
            "Normal volatility — proceed with caution."
        )
    else:
        status = "healthy"
        label = "🟢 HEALTHY"
        recommendation = (
            "✅ <b>Market near highs.</b> Conditions are favorable. "
            "Standard risk management applies."
        )

    return {
        "status": status,
        "label": label,
        "avg_drop": avg_drop,
        "avg_daily": avg_daily,
        "indices": index_data,
        "recommendation": recommendation,
    }


def build_market_message():
    """Build the /market command response."""
    health = assess_market_health()
    lines = [f"🏛 <b>Market Health: {health['label']}</b>\n"]

    for idx in health.get("indices", []):
        daily_str = ""
        if idx["daily_change"] is not None:
            arrow = "📈" if idx["daily_change"] >= 0 else "📉"
            daily_str = f" | Today: {arrow} {idx['daily_change']:+.1f}%"
        lines.append(
            f"  <b>{idx['name']}</b>: ${idx['current']:,.2f}"
            f" (ATH: ${idx['ath']:,.2f}, -{idx['drop_from_ath']:.1f}%){daily_str}"
        )

    lines.append(f"\n{health['recommendation']}")
    lines.append(f"\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


# ─── Status Message ──────────────────────────────────────────────────────────


def build_status_message():
    """Build the /status command response with prices and news."""
    lines = ["📊 <b>Portfolio Status</b>\n"]

    sections = [
        ("🏢 Stocks", STOCKS),
        ("📈 Indices", INDICES),
        ("🪙 Crypto", CRYPTO),
    ]

    for section_name, tickers in sections:
        lines.append(f"\n<b>{section_name}</b>")
        for symbol, name in tickers.items():
            ath, current = fetch_ath_and_current(symbol)
            daily_change = fetch_daily_change(symbol)
            if ath and current:
                drop_pct = ((ath - current) / ath) * 100
                if drop_pct < 10:
                    emoji = "🟢"
                elif drop_pct < 20:
                    emoji = "🟡"
                elif drop_pct < 30:
                    emoji = "🟠"
                else:
                    emoji = "🔴"
                daily_str = ""
                if daily_change is not None:
                    daily_str = f" | Today: {daily_change:+.1f}%"
                lines.append(
                    f"{emoji} <b>{name}</b> ({symbol})\n"
                    f"   ${current:,.2f} | ATH: ${ath:,.2f} | "
                    f"Down: {drop_pct:.1f}%{daily_str}"
                )
            else:
                lines.append(f"⚪ <b>{name}</b> ({symbol}) — data unavailable")

    lines.append(f"\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


# ─── Alert Logic ─────────────────────────────────────────────────────────────


def check_and_alert():
    """Check all tickers and send alerts with news context and market health."""
    subs = load_subscribers()
    if not subs:
        log.info("No subscribers — skipping alerts.")
        return

    # Get market health first
    market = assess_market_health()

    # Load previously sent alerts
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
                # Fetch news to explain WHY it's down
                news = fetch_news(symbol)
                new_alerts.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "threshold": threshold,
                        "drop_pct": drop_pct,
                        "current": current,
                        "ath": ath,
                        "news": news,
                    }
                )
                symbol_alerts.append(threshold)

        # Remove thresholds if price recovered (so it can re-alert)
        for threshold in list(symbol_alerts):
            if drop_pct < threshold:
                symbol_alerts.remove(threshold)

        sent_alerts[symbol] = symbol_alerts

    save_json("sent_alerts.json", sent_alerts)

    # Send alerts
    if new_alerts:
        for alert in new_alerts:
            emoji = "⚠️" if alert["threshold"] <= 20 else "🚨"
            text = (
                f"{emoji} <b>ATH Drop Alert</b>\n\n"
                f"<b>{alert['name']}</b> ({alert['symbol']})\n"
                f"📉 Down <b>{alert['drop_pct']:.1f}%</b> from ATH\n"
                f"💰 Current: <b>${alert['current']:,.2f}</b>\n"
                f"🏔 ATH: <b>${alert['ath']:,.2f}</b>\n"
                f"🎯 Threshold crossed: <b>{alert['threshold']}%</b>\n"
            )

            # Add news context
            text += "\n<b>📰 Why it might be down:</b>\n"
            text += format_news(alert["news"])

            # Add market context
            text += (
                f"\n\n<b>🏛 Market: {market['label']}</b>\n"
                f"{market['recommendation']}"
            )

            text += f"\n\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

            for chat_id in subs:
                try:
                    send_message(chat_id, text)
                    log.info(
                        "Alert sent: %s -%d%% to chat_id=%s",
                        alert["symbol"],
                        alert["threshold"],
                        chat_id,
                    )
                except Exception as e:
                    log.error("Failed to send to %s: %s", chat_id, e)

        log.info("Sent %d alert(s) to %d subscriber(s).", len(new_alerts), len(subs))
    else:
        log.info("No new threshold crossings detected.")

    # Send market warning only when status CHANGES (same logic as stock alerts)
    last_market = sent_alerts.get("_market_status", "healthy")
    current_market = market["status"]

    if current_market != last_market:
        if current_market in ("correction", "bear", "crash", "selloff"):
            market_msg = (
                f"🏛 <b>Market Warning</b>\n\n"
                f"Status: {market['label']}\n\n"
                f"{market['recommendation']}\n\n"
                f"<b>Index Details:</b>\n"
            )
            for idx in market.get("indices", []):
                daily_str = ""
                if idx["daily_change"] is not None:
                    daily_str = f" (today: {idx['daily_change']:+.1f}%)"
                    market_msg += (
                        f"  • {idx['name']}: ${idx['current']:,.2f} "
                        f"(-{idx['drop_from_ath']:.1f}% from ATH){daily_str}\n"
                    )

            market_msg += f"\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

            for chat_id in subs:
                try:
                    send_message(chat_id, market_msg)
                except Exception as e:
                    log.error("Failed to send market warning to %s: %s", chat_id, e)

            log.info("Market status changed: %s → %s — warning sent.", last_market, current_market)
        elif current_market in ("healthy", "pullback"):
            # Market recovered — notify subscribers
            recovery_msg = (
                f"🏛 <b>Market Recovery</b>\n\n"
                f"Status: {market['label']}\n\n"
                f"{market['recommendation']}\n\n"
                f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            )
            for chat_id in subs:
                try:
                    send_message(chat_id, recovery_msg)
                except Exception as e:
                    log.error("Failed to send recovery msg to %s: %s", chat_id, e)

            log.info("Market status changed: %s → %s — recovery sent.", last_market, current_market)

        sent_alerts["_market_status"] = current_market
        save_json("sent_alerts.json", sent_alerts)
    else:
        log.info("Market status unchanged (%s) — no warning sent.", current_market)


# ─── Main ────────────────────────────────────────────────────────────────────


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
