# kalshi-bot

Autopilot for the Kalshi **15-minute BTC binary** market.  
Every 15 minutes the bot:

1. Fetches the latest 1-minute BTC/USDT candles from Binance (no API key required).
2. Computes a directional signal using an **EMA crossover + RSI** strategy.
3. Looks up the soonest-expiring open market in the configured Kalshi series.
4. Places a **market buy** order on the YES (up) or NO (down) side.

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# edit .env with your Kalshi email, password, and preferred settings

# 3a. Run continuously (trades every 15 minutes)
python main.py

# 3b. Dry-run (signals only, no real orders)
python main.py --dry-run

# 3c. Single cycle and exit
python main.py --once
```

---

## Configuration (`.env`)

| Variable        | Default   | Description                                   |
|-----------------|-----------|-----------------------------------------------|
| `KALSHI_EMAIL`  | —         | Kalshi account email (**required**)           |
| `KALSHI_PASSWORD` | —       | Kalshi account password (**required**)        |
| `NUM_CONTRACTS` | `1`       | Contracts to buy per cycle                    |
| `BTC_SERIES`    | `KXBTCD`  | Kalshi series ticker for the BTC 15-min market |

---

## Project layout

```
kalshi-bot/
├── main.py              # Entry point (scheduling, CLI flags)
├── src/
│   ├── kalshi_client.py # Kalshi REST API v2 wrapper
│   ├── btc_price.py     # Binance public API for BTC price/klines
│   ├── strategy.py      # EMA crossover + RSI signal logic
│   └── trader.py        # Trading cycle orchestrator
├── tests/               # pytest unit tests
├── requirements.txt
└── .env.example
```

---

## Strategy

| Condition                   | Signal |
|-----------------------------|--------|
| RSI ≥ 70 (overbought)       | NO     |
| RSI ≤ 30 (oversold)         | YES    |
| Fast EMA (5) > Slow EMA (10)| YES    |
| Fast EMA (5) < Slow EMA (10)| NO     |

---

## Running tests

```bash
pytest tests/ -v
```

---

## Disclaimer

This bot is provided for educational purposes only.  
Trading on prediction markets involves risk of loss.  
Use at your own risk.
