# Kalshi Bot

A real Kalshi trading system scaffold for market scanning, signal generation, order placement, and order management.

## What it does
- Pulls open markets from Kalshi REST API
- Scores them for spread/liquidity/momentum dislocations
- Watches live data over WebSockets
- Can place, amend, and cancel limit orders
- Enforces basic risk caps before sending any order

## Setup
1. Create a Python 3.11+ virtual environment
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your API credentials
4. Start in demo mode with `DRY_RUN=true`

## Run
```bash
python -m src.kalshi_bot.main
```

## Live 15m scanner assets
The live crypto scanner remains BTC-only by default:

```env
CRYPTO_15M_SERIES=KXBTC15M
DRY_RUN=true
```

To dry-run additional verified Kalshi 15m binaries, opt in with a CSV such as:

```env
CRYPTO_15M_SERIES=KXBTC15M,KXETH15M,KXSOL15M,KXDOGE15M,KXXRP15M,KXGOLD15M
```

Series/product mappings are centralized in `src/kalshi_bot/assets.py`. ETH is supported but still opt-in because these markets have been illiquid. Gold is opt-in via Coinbase Exchange `PAXG-USD`; Kalshi settles against Pyth GOLD, so monitor basis risk before relying on it. Silver (`KXSILVER15M`) and WTI (`KXWTI15M`) are intentionally blocked because Coinbase does not provide the Exchange candle products the live model needs.

## Notes
- REST market data can be fetched without authentication.
- Trading and WebSocket sessions require signed auth headers.
- Sign the path without query parameters.
- Start in demo. Then switch `KALSHI_ENV=prod` and production URLs only when you are satisfied.
