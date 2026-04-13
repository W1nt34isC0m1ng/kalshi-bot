from __future__ import annotations

import time
import uuid
from typing import Any
from urllib.parse import urlparse

import requests

from .auth import KalshiSigner


class KalshiHttpClient:
    def __init__(self, base_url: str, signer: KalshiSigner | None = None, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.signer = signer
        self.timeout = timeout
        self.session = requests.Session()
        # The path prefix embedded in base_url (e.g. "/trade-api/v2").
        # Kalshi requires the *full* URL path in the signed message, not just
        # the per-endpoint fragment.
        self._base_path = urlparse(self.base_url).path.rstrip("/")

    def _headers(self, method: str, path: str) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.signer:
            ts = str(int(time.time() * 1000))
            # Sign the full path: e.g. "/trade-api/v2/events" not "/events"
            full_path = self._base_path + path
            headers |= {
                "KALSHI-ACCESS-KEY": self.signer.api_key_id,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": self.signer.sign(ts, method, full_path),
            }
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = self._headers(method, path)

        if json is not None:
            headers["Content-Type"] = "application/json"

        response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=json,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()

        if response.content:
            return response.json()
        return {}

    def list_series(self) -> dict[str, Any]:
        return self.request("GET", "/series")

    def get_series(self, series_ticker: str) -> dict[str, Any]:
        return self.request("GET", f"/series/{series_ticker}")

    def get_events(
        self,
        *,
        series_ticker: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        with_nested_markets: bool | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        if with_nested_markets is not None:
            params["with_nested_markets"] = str(with_nested_markets).lower()
        if status:
            params["status"] = status
        return self.request("GET", "/events", params=params)

    def get_event(self, event_ticker: str) -> dict[str, Any]:
        return self.request("GET", f"/events/{event_ticker}")

    def get_markets(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        status: str = "open",
        event_ticker: str | None = None,
        series_ticker: str | None = None,
        tickers: str | None = None,
        mve_filter: str | None = "exclude",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": limit,
            "status": status,
        }
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if tickers:
            params["tickers"] = tickers
        if mve_filter:
            params["mve_filter"] = mve_filter

        return self.request("GET", "/markets", params=params)

    def get_market(self, ticker: str) -> dict[str, Any]:
        return self.request("GET", f"/markets/{ticker}")

    def get_market_candlesticks(
        self,
        ticker: str,
        *,
        start_ts: int | None = None,
        end_ts: int | None = None,
        period_interval: int = 60,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"period_interval": period_interval}
        if start_ts is not None:
            params["start_ts"] = start_ts
        if end_ts is not None:
            params["end_ts"] = end_ts
        return self.request("GET", f"/markets/{ticker}/candlesticks", params=params)

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict[str, Any]:
        return self.request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_balance(self) -> dict[str, Any]:
        return self.request("GET", "/portfolio/balance")

    def get_positions(self) -> dict[str, Any]:
        return self.request("GET", "/portfolio/positions")

    def get_orders(self, *, status: str | None = None) -> dict[str, Any]:
        params = {"status": status} if status else None
        return self.request("GET", "/portfolio/orders", params=params)

    def create_order(
        self,
        *,
        ticker: str,
        side: str,
        action: str,
        count: int,
        price: int,
        expiration_ts: int,
        post_only: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            # price is always the YES price (1-99 cents).
            # For a NO order, the NO price = 100 - yes_price.
            "yes_price": price if side == "yes" else None,
            "no_price": (100 - price) if side == "no" else None,
            "expiration_ts": expiration_ts,
            "post_only": post_only,
            "client_order_id": str(uuid.uuid4()),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self.request("POST", "/portfolio/orders", json=payload)

    def amend_order(
        self,
        order_id: str,
        *,
        ticker: str,
        side: str,
        action: str,
        count: int,
        price: int,
    ) -> dict[str, Any]:
        payload = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "yes_price": price if side == "yes" else None,
            "no_price": (100 - price) if side == "no" else None,
            "updated_client_order_id": str(uuid.uuid4()),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self.request("POST", f"/portfolio/orders/{order_id}/amend", json=payload)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/portfolio/orders/{order_id}")