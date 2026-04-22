from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import KalshiHttpClient


@dataclass(frozen=True)
class CoinbasePredictionConfig:
    """Configuration for Coinbase prediction-market execution.

    Coinbase prediction markets are currently modeled as Kalshi-powered event
    markets in this fork. A native Coinbase implementation can replace the
    delegated methods below once official endpoints are available.
    """

    mode: str = "kalshi_powered"
    native_base_url: str = ""


class CoinbasePredictionClient:
    """Coinbase prediction-market adapter.

    In `kalshi_powered` mode, Coinbase prediction markets share Kalshi-style
    tickers/contracts, so this adapter delegates to the existing Kalshi client.
    The wrapper gives this fork a clean seam for adding a native Coinbase API
    without touching strategy code.
    """

    def __init__(
        self,
        *,
        config: CoinbasePredictionConfig,
        kalshi_client: KalshiHttpClient | None = None,
    ) -> None:
        self.config = config
        self.kalshi_client = kalshi_client

        if self.config.mode == "kalshi_powered" and self.kalshi_client is None:
            raise ValueError("kalshi_powered mode requires a KalshiHttpClient")
        if self.config.mode not in {"kalshi_powered", "native"}:
            raise ValueError(f"Unsupported Coinbase prediction mode: {self.config.mode}")

    def _delegate(self) -> KalshiHttpClient:
        if self.config.mode != "kalshi_powered" or self.kalshi_client is None:
            raise NotImplementedError(
                "Native Coinbase prediction-market API is not implemented yet. "
                "Use COINBASE_PREDICTION_MODE=kalshi_powered until official "
                "Coinbase prediction-market endpoints are available."
            )
        return self.kalshi_client

    def get_events(self, **kwargs: Any) -> dict[str, Any]:
        return self._delegate().get_events(**kwargs)

    def get_event(self, event_ticker: str) -> dict[str, Any]:
        return self._delegate().get_event(event_ticker)

    def get_markets(self, **kwargs: Any) -> dict[str, Any]:
        return self._delegate().get_markets(**kwargs)

    def get_market(self, ticker: str) -> dict[str, Any]:
        return self._delegate().get_market(ticker)

    def get_orderbook(self, ticker: str) -> dict[str, Any]:
        return self._delegate().get_orderbook(ticker)

    def get_balance(self) -> dict[str, Any]:
        return self._delegate().get_balance()

    def get_positions(self) -> dict[str, Any]:
        return self._delegate().get_positions()

    def get_orders(self, **kwargs: Any) -> dict[str, Any]:
        return self._delegate().get_orders(**kwargs)

    def create_order(self, **kwargs: Any) -> dict[str, Any]:
        return self._delegate().create_order(**kwargs)
