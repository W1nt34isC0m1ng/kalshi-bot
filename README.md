Kalshi Bot
A real Kalshi trading system scaffold for market scanning, signal generation, order placement, and order management.

What it does
Pulls open markets from Kalshi REST API
Scores them for spread/liquidity/momentum dislocations
Watches live data over WebSockets
Can place, amend, and cancel limit orders
Enforces basic risk caps before sending any order
Setup
Create a Python 3.11+ virtual environment
pip install -r requirements.txt
Copy .env.example to .env and fill in your API credentials
Start in demo mode with DRY_RUN=true
Run
python -m src.kalshi_bot.main
Notes
REST market data can be fetched without authentication.
Trading and WebSocket sessions require signed auth headers.
Sign the path without query parameters.
Start in demo. Then switch KALSHI_ENV=prod and production URLs only when you are satisfied.
