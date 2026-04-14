# Stock & Crypto Alert Bot for Telegram

A Telegram bot that tracks stocks, indices, and crypto — alerting you when prices drop **10%, 20%, 30%, 40%, or 50%** from their all-time highs. Includes news context and market health analysis.

## Setup

1. Fork this repo
2. Add secret: `TELEGRAM_BOT_TOKEN` in Settings > Secrets > Actions
3. Send `/start` to your bot on Telegram

## Features

- ATH Drop Alerts at 10/20/30/40/50% thresholds
- News context explaining why stocks are down
- Market health analysis (correction/bear/crash warnings)
- Tracks: AAPL, AMZN, GOOGL, TSLA, META, MSFT, NVDA, NFLX, WMT, BTC, ETH
- Indices: S&P 500, Nasdaq, Dow Jones
- Commands: /start, /stop, /status, /market

## Run Locally

```bash
export TELEGRAM_BOT_TOKEN="your-token-here"
pip install -r requirements.txt
python bot.py
```
