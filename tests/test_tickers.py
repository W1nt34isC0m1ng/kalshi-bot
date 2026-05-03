"""Unit tests for tickers.py parsing utilities."""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kalshi_bot.tickers import parse_expiry_utc, parse_market_ticker


class TestParseExpiryUtc:
    def test_btc_ticker(self):
        """Known BTC ticker should parse to the correct UTC expiry."""
        # KXBTC15M-26APR111700-00 → 2026-04-11 17:00 EDT = 21:00 UTC
        result = parse_expiry_utc("KXBTC15M-26APR111700-00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 11
        assert result.hour == 21
        assert result.minute == 0

    def test_eth_ticker(self):
        """ETH ticker should parse the same way as BTC."""
        result = parse_expiry_utc("KXETH15M-26JAN151530-00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 15

    def test_case_insensitive(self):
        """Lowercase tickers should parse fine."""
        upper = parse_expiry_utc("KXBTC15M-26APR111700-00")
        lower = parse_expiry_utc("kxbtc15m-26apr111700-00")
        assert upper is not None and lower is not None
        assert upper == lower

    def test_empty_string_returns_none(self):
        assert parse_expiry_utc("") is None

    def test_none_returns_none(self):
        assert parse_expiry_utc(None) is None  # type: ignore[arg-type]

    def test_garbage_returns_none(self):
        assert parse_expiry_utc("NOT-A-TICKER") is None
        assert parse_expiry_utc("KXBTC15M-BADDATE-00") is None

    def test_invalid_month_returns_none(self):
        """Unknown month abbreviation → None."""
        assert parse_expiry_utc("KXBTC15M-26ZZZ111700-00") is None

    def test_result_is_utc(self):
        result = parse_expiry_utc("KXBTC15M-26APR111700-00")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_november_dst_fallback(self):
        """In November, New York is UTC-5 (EST), not UTC-4 (EDT).

        This test validates the ZoneInfo fix: the old fixed -4 offset would
        return 22:00 UTC instead of the correct 23:00 UTC.
        """
        # KXBTC15M-26NOV011800-00 → 2026-11-01 18:00 EST = 23:00 UTC
        # (On 2026-11-01 DST has ended — clocks fell back at 2am)
        result = parse_expiry_utc("KXBTC15M-26NOV011800-00")
        assert result is not None
        assert result.hour == 23  # would be 22 with the old fixed -4 offset


class TestParseMarketTicker:
    def test_returns_tuple(self):
        result = parse_market_ticker("KXBTC15M-26APR111700-00")
        assert result is not None
        series, expiry, suffix = result
        assert series == "KXBTC15M"
        assert isinstance(expiry, datetime)
        assert suffix == "00"

    def test_expiry_matches_parse_expiry_utc(self):
        """Both parsers must return identical expiry datetimes."""
        ticker = "KXBTC15M-26APR111700-00"
        via_market = parse_market_ticker(ticker)
        via_expiry = parse_expiry_utc(ticker)
        assert via_market is not None and via_expiry is not None
        _, expiry, _ = via_market
        assert expiry == via_expiry

    def test_garbage_returns_none(self):
        assert parse_market_ticker("NOT-A-TICKER") is None
        assert parse_market_ticker("") is None
        assert parse_market_ticker(None) is None  # type: ignore[arg-type]

    def test_suffix_captured(self):
        result = parse_market_ticker("KXBTC15M-26APR111715-15")
        assert result is not None
        _, _, suffix = result
        assert suffix == "15"
