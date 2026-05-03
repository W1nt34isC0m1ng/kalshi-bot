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

## Run tests
```bash
python -m pytest tests/
```

## Notes
- REST market data can be fetched without authentication.
- Trading and WebSocket sessions require signed auth headers.
- Sign the path without query parameters.
- Start in demo. Then switch `KALSHI_ENV=prod` and production URLs only when you are satisfied.

---

## ⚠️ Before switching to production

Switching from demo to prod is **a single env-var change** that controls where real money goes.
Complete this checklist before flipping the switch:

- [ ] Run the bot in `DRY_RUN=true` (demo) for at least a full trading session and review `logs/trade_journal.csv` for unexpected signals or errors.
- [ ] Verify `KALSHI_PRIVATE_KEY_PATH` points to the PEM key tied to a **funded production account**.
- [ ] Review `MAX_TOTAL_NOTIONAL_CENTS` — the default is **$100**. Set it to a value you are comfortable losing entirely.
- [ ] Review `MAX_NOTIONAL_CENTS_PER_MARKET` and `MAX_POSITION_PER_MARKET` to confirm per-market exposure caps suit your bankroll.
- [ ] Review `ORDER_COUNT` / `BANKROLL_CENTS` / `RISK_FRACTION_PER_TRADE` if `AUTO_SIZING=true`.
- [ ] Set `KALSHI_BASE_URL=https://trading-api.kalshi.com/trade-api/v2` and `KALSHI_WS_URL=wss://trading-api.kalshi.com/trade-api/ws/v2`.
- [ ] Set `KALSHI_ENV=prod` and `DRY_RUN=false`.
- [ ] Monitor `logs/heartbeat.txt` to confirm the bot is running (timestamp is updated each loop).
- [ ] Periodically run `python -m kalshi_bot.settle` or rely on the auto-settle in the main loop to populate `logs/outcomes.csv` with resolved P&L.
