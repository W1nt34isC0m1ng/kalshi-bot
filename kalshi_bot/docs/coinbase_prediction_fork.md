# Coinbase Prediction Market Fork

This branch is the Coinbase prediction-market-focused fork of the bot.

## Current Reality

Coinbase prediction markets are exposed inside Coinbase, but Coinbase has said
the initial market flow comes from Kalshi. In other words, the contracts,
tickers, and market structure are still Kalshi-like even when the user accesses
them through Coinbase.

As of this fork, there is no public Coinbase-specific prediction-market trading
API wired into the bot. The safe architecture is therefore:

- keep the proven crypto probability strategy unchanged
- keep Kalshi-style market discovery and ticker parsing
- isolate the venue layer behind a Coinbase prediction adapter
- treat `coinbase_kalshi` as the default mode for this fork
- add a native Coinbase API implementation only after official API docs/keys are
  available

## Fork Goals

- Focus on Coinbase-distributed prediction markets.
- Keep strategy logic exchange-agnostic where possible.
- Avoid mixing Coinbase prediction-market experiments into the main Kalshi bot.
- Preserve auditability via EV, requested count, premium, and notional journal
  fields.

## Environment Knobs

```env
PREDICTION_MARKET_VENUE=coinbase_kalshi
COINBASE_PREDICTION_MODE=kalshi_powered
COINBASE_PREDICTION_BASE_URL=
```

`coinbase_kalshi` means the bot models Coinbase prediction markets as
Kalshi-powered markets and uses the existing Kalshi API/client machinery.

`COINBASE_PREDICTION_BASE_URL` is intentionally blank until Coinbase publishes
or confirms a stable prediction-market API endpoint for programmatic trading.

## Next Implementation Step

If Coinbase exposes native prediction-market REST/WebSocket endpoints, add a
real implementation in `src/kalshi_bot/coinbase_prediction.py` and route
`build_clients()` through that client when:

```env
PREDICTION_MARKET_VENUE=coinbase_native
```

Until then, this fork should stay paper-first or Kalshi-powered only.
