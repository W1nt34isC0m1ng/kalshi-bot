"""Shared Kalshi ticker parsing utilities.

Format: ``KX{ASSET}15M-{YY}{MMM}{DD}{HH}{MM}-{STRIKE_CODE}``
where the date/time portion is in Eastern (Kalshi) time.

Used by both `settle.py` (for resolution) and `risk.py` (for expiry-based
pruning of the dry-run risk state).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Kalshi market hours run on Eastern time.  Using the IANA zone rather than a
# fixed UTC-4 offset means November's DST fallback (EDT→EST) is handled
# correctly without any manual intervention.
KALSHI_TZ = ZoneInfo("America/New_York")

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Matches the series prefix and date/time components.  Series prefix is
# captured broadly so ETH, BTC, and any future asset are supported.
_TICKER_RE = re.compile(r"^(KX[A-Z0-9]+15M)-(\d{2})(\w{3})(\d{2})(\d{2})(\d{2})-(\w+)$")


def parse_expiry_utc(ticker: str) -> datetime | None:
    """Parse the option expiry from a Kalshi 15-min ticker.

    Returns a timezone-aware UTC datetime, or None if the ticker doesn't match.
    """
    if not ticker:
        return None
    m = _TICKER_RE.match(ticker.upper())
    if not m:
        return None
    _series, yy, mmm, dd, hh, mn, _suffix = m.groups()
    if mmm not in MONTHS:
        return None
    try:
        et = datetime(
            year=2000 + int(yy),
            month=MONTHS[mmm],
            day=int(dd),
            hour=int(hh),
            minute=int(mn),
            tzinfo=KALSHI_TZ,
        )
    except ValueError:
        return None
    return et.astimezone(timezone.utc)


def parse_market_ticker(ticker: str) -> tuple[str, datetime, str] | None:
    """Parse a Kalshi 15-min ticker into its component parts.

    Returns ``(series_prefix, expiry_utc, suffix)`` or ``None`` if the ticker
    does not match the expected format.

    Example::

        series, expiry, suffix = parse_market_ticker("KXBTC15M-26APR111700-00")
        # series = "KXBTC15M", expiry = datetime(2026, 4, 11, 21, 0, tzinfo=UTC), suffix = "00"
    """
    if not ticker:
        return None
    m = _TICKER_RE.match(ticker.upper())
    if not m:
        return None
    series, yy, mmm, dd, hh, mn, suffix = m.groups()
    if mmm not in MONTHS:
        return None
    try:
        et = datetime(
            year=2000 + int(yy),
            month=MONTHS[mmm],
            day=int(dd),
            hour=int(hh),
            minute=int(mn),
            tzinfo=KALSHI_TZ,
        )
    except ValueError:
        return None
    return series, et.astimezone(timezone.utc), suffix
