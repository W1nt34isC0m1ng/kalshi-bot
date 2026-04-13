from __future__ import annotations

import json
import time
from typing import Callable
from urllib.parse import urlparse
from websocket import WebSocketApp

from .auth import KalshiSigner


class KalshiWebSocket:
    def __init__(self, ws_url: str, signer: KalshiSigner, on_message: Callable[[dict], None]):
        self.ws_url = ws_url
        self.signer = signer
        self.on_message_cb = on_message
        self.app: WebSocketApp | None = None
        # Parse the path from ws_url (e.g. "/trade-api/ws/v2") for signing,
        # consistent with how KalshiHttpClient handles _base_path.
        self._ws_path = urlparse(ws_url).path.rstrip("/")

    def _headers(self) -> list[str]:
        ts = str(int(time.time() * 1000))
        signature = self.signer.sign(ts, "GET", self._ws_path)
        return [
            f"KALSHI-ACCESS-KEY: {self.signer.api_key_id}",
            f"KALSHI-ACCESS-TIMESTAMP: {ts}",
            f"KALSHI-ACCESS-SIGNATURE: {signature}",
        ]

    def subscribe(self, tickers: list[str]) -> None:
        if not self.app or not tickers:
            return
        msg = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker", "trade", "orderbook_delta"],
                "market_tickers": tickers,
            },
        }
        self.app.send(json.dumps(msg))

    def run(self, tickers: list[str]) -> None:
        def on_open(ws):
            self.subscribe(tickers)

        def on_message(ws, message: str):
            try:
                self.on_message_cb(json.loads(message))
            except Exception:
                pass

        self.app = WebSocketApp(
            self.ws_url,
            header=self._headers(),
            on_open=on_open,
            on_message=on_message,
        )
        self.app.run_forever(ping_interval=20, ping_timeout=10)
