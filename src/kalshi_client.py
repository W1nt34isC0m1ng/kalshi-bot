"""Kalshi REST API v2 client.

Handles authentication (email/password), market lookup,
order placement, and portfolio queries.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"


class KalshiError(Exception):
    """Raised when the Kalshi API returns an error response."""


class KalshiClient:
    """Thin wrapper around the Kalshi trading API v2."""

    def __init__(self, email: str, password: str, base_url: str = _BASE_URL) -> None:
        self.email = email
        self.password = password
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = None
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self) -> str:
        """Authenticate and store the session token.

        Returns the token string.
        """
        resp = self._session.post(
            f"{self.base_url}/login",
            json={"email": self.email, "password": self.password},
            timeout=15,
        )
        self._raise_for_status(resp)
        data = resp.json()
        self._token = data["token"]
        self._session.headers.update({"Authorization": self._token})
        logger.info("Kalshi login successful (member_id=%s)", data.get("member_id"))
        return self._token

    @property
    def is_authenticated(self) -> bool:
        return self._token is not None

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    def get_markets(
        self,
        series_ticker: Optional[str] = None,
        status: str = "open",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return a list of markets, optionally filtered by series and status."""
        params: dict[str, Any] = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        resp = self._session.get(
            f"{self.base_url}/markets", params=params, timeout=15
        )
        self._raise_for_status(resp)
        return resp.json().get("markets", [])

    def get_market(self, ticker: str) -> dict[str, Any]:
        """Return details for a single market by ticker."""
        resp = self._session.get(
            f"{self.base_url}/markets/{ticker}", timeout=15
        )
        self._raise_for_status(resp)
        return resp.json().get("market", resp.json())

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_order(
        self,
        ticker: str,
        side: str,
        count: int,
        order_type: str = "market",
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
    ) -> dict[str, Any]:
        """Place a buy order on the given market.

        Args:
            ticker:     Market ticker (e.g. ``"KXBTCD-25Mar2110:45-T71074.50"``).
            side:       ``"yes"`` or ``"no"``.
            count:      Number of contracts.
            order_type: ``"market"`` or ``"limit"``.
            yes_price:  Limit price in cents for YES side (limit orders only).
            no_price:   Limit price in cents for NO side (limit orders only).

        Returns:
            The raw order response dict from Kalshi.
        """
        if side not in ("yes", "no"):
            raise ValueError(f"side must be 'yes' or 'no', got {side!r}")
        if count < 1:
            raise ValueError(f"count must be >= 1, got {count}")

        payload: dict[str, Any] = {
            "ticker": ticker,
            "action": "buy",
            "type": order_type,
            "side": side,
            "count": count,
        }
        if yes_price is not None:
            payload["yes_price"] = yes_price
        if no_price is not None:
            payload["no_price"] = no_price

        resp = self._session.post(
            f"{self.base_url}/orders", json=payload, timeout=15
        )
        self._raise_for_status(resp)
        return resp.json().get("order", resp.json())

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order by ID."""
        resp = self._session.delete(
            f"{self.base_url}/orders/{order_id}", timeout=15
        )
        self._raise_for_status(resp)
        return resp.json()

    def get_orders(self, ticker: Optional[str] = None, status: str = "resting") -> list[dict[str, Any]]:
        """Return open/resting orders, optionally filtered by market ticker."""
        params: dict[str, Any] = {"status": status}
        if ticker:
            params["ticker"] = ticker
        resp = self._session.get(
            f"{self.base_url}/portfolio/orders", params=params, timeout=15
        )
        self._raise_for_status(resp)
        return resp.json().get("orders", [])

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    def get_balance(self) -> int:
        """Return the available balance in cents."""
        resp = self._session.get(
            f"{self.base_url}/portfolio/balance", timeout=15
        )
        self._raise_for_status(resp)
        return resp.json().get("balance", 0)

    def get_positions(self) -> list[dict[str, Any]]:
        """Return current market positions."""
        resp = self._session.get(
            f"{self.base_url}/portfolio/positions", timeout=15
        )
        self._raise_for_status(resp)
        return resp.json().get("market_positions", [])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise KalshiError(
                f"Kalshi API error {resp.status_code}: {detail}"
            )
