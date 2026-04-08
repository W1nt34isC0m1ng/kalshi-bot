from __future__ import annotations

import time
import uuid
from typing import Any
from urllib.parse import urlencode

import requests

from .auth import KalshiSigner


class KalshiHttpClient:
    def __init__(self, base_url: str, signer: KalshiSigner | None = None, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.signer = signer
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self, method: str, path: str) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.signer:
            ts = str(int(time.time() * 1000))
            headers |= {
                "KALSHI-ACCESS-KEY": self.signer.api_key_id,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": self.signer.sign(ts, method, path),
            }
        return headers

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = self._headers(method, path)
        if json is not None:
            headers["Content-Type"] = "application/json"
        response = self.session.request(method=method, url=url, params=params, json=json, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    def get_markets(self, *, limit: int = 100, cursor: str | None = None, status: str = "open") -> dict[str, Any]:
        params = {"limit": limit, "status": status}
        if cursor:
            params["cursor"] = cursor
        return self.request("GET", "/markets", params=params)

    def get_market(self, ticker: str) -> dict[str, Any]:
        return self.request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict[str, Any]:
        return self.request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_balance(self) -> dict[str, Any]:
        return self.request("GET", "/portfolio/balance")

    def get_positions(self) -> dict[str, Any]:
        return self.request("GET", "/portfolio/positions")

    def get_orders(self, *, status: str | None = None) -> dict[str, Any]:
        params = {"status": status} if status else None
        return self.request("GET", "/portfolio/orders", params=params)

    def create_order(self, *, ticker: str, side: str, action: str, count: int, price: int, expiration_ts: int, post_only: bool = True) -> dict[str, Any]:
        payload = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "yes_price": price if side == "yes" else None,
            "no_price": price if side == "no" else None,
            "expiration_ts": expiration_ts,
            "post_only": post_only,
            "client_order_id": str(uuid.uuid4()),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self.request("POST", "/portfolio/orders", json=payload)

    def amend_order(self, order_id: str, *, ticker: str, side: str, action: str, count: int, price: int) -> dict[str, Any]:
        payload = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "yes_price": price if side == "yes" else None,
            "no_price": price if side == "no" else None,
            "updated_client_order_id": str(uuid.uuid4()),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self.request("POST", f"/portfolio/orders/{order_id}/amend", json=payload)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/portfolio/orders/{order_id}")
