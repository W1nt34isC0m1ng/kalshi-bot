from kalshi_python_sync import Configuration, KalshiClient

config = Configuration(
    host="https://demo-api.kalshi.co/trade-api/v2"
)

# public data works without auth for some endpoints
client = KalshiClient(config)

# then call market/series endpoints from the SDK
markets = client.get_markets()
print("Markets:", markets)