"""Shared Kalshi ticker parsing utilities.

Format: ``KX{ASSET}15M-{YY}{MMM}{DD}{HH}{MM}-{STRIKE_CODE}``
where the date/time portion is in Eastern (Kalshi) time.

Used by both `settle.py` (for resolution) and `risk.py` (for expiry-based
pruning of the dry-run risk state).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Kalshi market hours run on Eastern time. We use a fixed -4 offset (EDT);
# when DST ends in November this becomes a 1-hour bug — revisit then.
KALSHI_TZ = timezone(timedelta(hours=-4))

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_TICKER_RE = re.compile(r"^KX(BTC|ETH)15M-(\d{2})(\w{3})(\d{2})(\d{2})(\d{2})-")


def parse_expiry_utc(ticker: str) -> datetime | None:
    """Parse the option expiry from a Kalshi 15-min ticker.

    Returns a timezone-aware UTC datetime, or None if the ticker doesn't match.
    """
    if not ticker:
        return None
    m = _TICKER_RE.match(ticker.upper())
    if not m:
        return None
    _asset, yy, mmm, dd, hh, mn = m.groups()
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
