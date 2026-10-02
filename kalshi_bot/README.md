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

## Exchange selection
Kalshi remains the default:

```env
EXCHANGE=kalshi
DRY_RUN=true
```

To scan Polymarket's short-cadence crypto "Up or Down" markets in dry-run mode:

```env
EXCHANGE=polymarket
DRY_RUN=true
POLYMARKET_ASSETS=btc
POLYMARKET_TIMEFRAME=15m
POLYMARKET_FEE_LIQUIDITY_ROLE=maker
```

Then run from this directory:

```bash
PYTHONPATH=. python -m src.kalshi_bot.main
```

The Polymarket adapter uses the public Gamma API to discover the active
`{asset}-updown-15m-{unix_window_start}` event, then uses the public CLOB
`/book` endpoint for the Up/Down outcome token order books. Gamma currently
returns the Chainlink stream resolution source and crypto market config (for
example 60-second TWAP), but not the already-fixed start reference price as a
numeric field. Until that is exposed, the shared crypto strategy continues to
use its Coinbase open-spot proxy for the strike estimate and logs the
Polymarket resolution source in the journal so results can be reviewed with
that basis risk in mind.

Polymarket live orders remain behind `DRY_RUN=false` and require wallet/API
credentials for `py-clob-client-v2`:

```env
POLYMARKET_PRIVATE_KEY=
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_API_PASSPHRASE=
POLYMARKET_SIGNATURE_TYPE=
POLYMARKET_FUNDER=
POLYMARKET_ALLOW_LIVE_WITHOUT_POSITION_RECONCILIATION=false
```

Keep these out of git. Polymarket availability and trading access are subject
to Polymarket account, wallet, and geographic restrictions; the public read
endpoints may still show `restricted=true` for events even when order books are
readable.

The bot submits Polymarket orders as post-only maker quotes and therefore uses
`POLYMARKET_FEE_LIQUIDITY_ROLE=maker` by default. Polymarket's current trading
fees documentation says makers are not charged fees; taker fees for Crypto use
`fee = contracts * feeRate * p * (1 - p)`, with `feeRate=0.07`, rounded to 5
decimal places in USDC. The adapter reads `feeSchedule.rate` from Gamma when
available and falls back to `0.07` for these crypto markets if taker modeling is
enabled with `POLYMARKET_FEE_LIQUIDITY_ROLE=taker`.

Startup position reconciliation for live Polymarket accounts is not implemented
yet. `DRY_RUN=false` fails closed unless
`POLYMARKET_ALLOW_LIVE_WITHOUT_POSITION_RECONCILIATION=true` is set, which means
you explicitly accept relying on local risk state until account reconciliation is
added.

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
