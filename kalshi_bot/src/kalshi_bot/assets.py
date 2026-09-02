from __future__ import annotations

import logging
import os

DEFAULT_CRYPTO_15M_SERIES = ("KXBTC15M",)

# Verified against Kalshi public markets API and Coinbase Exchange products on
# 2026-09-02. Only products with Coinbase Exchange candle support are enabled
# because the live model needs 1-minute candles for open-price and vol inputs.
ASSET_CONFIG = {
    "KXBTC15M": {
        "product": "BTC-USD",
        "vol_mult": 1.00,
        "notes": "Kalshi settles against CF Benchmarks BRTI.",
    },
    "KXETH15M": {
        "product": "ETH-USD",
        "vol_mult": 1.10,
        "notes": "Opt-in only; historically illiquid on Kalshi.",
    },
    "KXSOL15M": {
        "product": "SOL-USD",
        "vol_mult": 1.00,
        "notes": "Kalshi settles against CF Benchmarks SOLUSDRTI.",
    },
    "KXDOGE15M": {
        "product": "DOGE-USD",
        "vol_mult": 1.00,
        "notes": "Kalshi settles against CF Benchmarks DOGEUSDRTI.",
    },
    "KXXRP15M": {
        "product": "XRP-USD",
        "vol_mult": 1.00,
        "notes": "Kalshi settles against CF Benchmarks XRPUSDRTI.",
    },
    "KXGOLD15M": {
        "product": "PAXG-USD",
        "vol_mult": 1.00,
        "notes": (
            "Opt-in only; Kalshi settles against Pyth GOLD, while Coinbase "
            "Exchange candle support is available for tokenized gold PAXG-USD."
        ),
    },
}

BLOCKED_CRYPTO_15M_SERIES = {
    "KXSILVER15M": (
        "Kalshi series exists, but Coinbase Exchange has no XAG-USD/SILVER-USD "
        "candle product; skipping rather than using an unrelated proxy."
    ),
    "KXWTI15M": (
        "Kalshi series exists, but Coinbase has no WTI-USD spot or candle "
        "product; skipping rather than using an unrelated proxy."
    ),
}


def asset_prefix_from_ticker(ticker: str) -> str | None:
    t = (ticker or "").upper()
    for prefix in ASSET_CONFIG:
        if t.startswith(prefix):
            return prefix
    return None


def parse_crypto_15m_series_csv(raw: str | None) -> list[str]:
    if raw is None:
        requested = list(DEFAULT_CRYPTO_15M_SERIES)
    else:
        requested = [item.strip().upper() for item in raw.split(",") if item.strip()]

    series: list[str] = []
    seen: set[str] = set()
    for ticker in requested:
        if ticker in seen:
            continue
        seen.add(ticker)

        if ticker in ASSET_CONFIG:
            series.append(ticker)
            continue

        if ticker in BLOCKED_CRYPTO_15M_SERIES:
            logging.warning(
                "CRYPTO_15M_SERIES skipping %s: %s",
                ticker,
                BLOCKED_CRYPTO_15M_SERIES[ticker],
            )
        else:
            logging.warning(
                "CRYPTO_15M_SERIES ignoring unsupported series %s; "
                "add a verified Kalshi series and public spot/candle product mapping first",
                ticker,
            )

    return series


def crypto_15m_series_from_env() -> list[str]:
    return parse_crypto_15m_series_csv(os.getenv("CRYPTO_15M_SERIES"))
