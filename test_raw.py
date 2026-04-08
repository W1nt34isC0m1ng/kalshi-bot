import requests

url = "https://api.elections.kalshi.com/trade-api/v2/markets"
response = requests.get(url)
print("Status:", response.status_code)
print("Response:", response.json())