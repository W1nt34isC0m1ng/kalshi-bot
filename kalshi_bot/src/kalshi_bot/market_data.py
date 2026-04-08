from __future__ import annotations

from typing import Iterable

from .client import KalshiHttpClient
from .models import Market


class MarketDataService:
    def __init__(self, client: KalshiHttpClient):
        self.client = client

    def iter_open_markets(self, limit_per_page: int = 100) -> Iterable[Market]:
        cursor = None
        while True:
            page = self.client.get_markets(limit=limit_per_page, cursor=cursor, status="open")
            for row in page.get("markets", []):
                yield Market.from_api(row)
            cursor = page.get("cursor")
            if not cursor:
                break

    def get_top_of_book(self, ticker: str) -> dict:
        return self.client.get_orderbook(ticker)
