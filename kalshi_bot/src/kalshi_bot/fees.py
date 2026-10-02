from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

KALSHI_BINARY_FEE_RATE_DOLLARS = Decimal("0.07")
POLYMARKET_CRYPTO_TAKER_FEE_RATE = Decimal("0.07")


def premium_cents_for_side(side: str, yes_price_cents: int) -> int:
    """Return the bought contract premium in cents for a YES-equivalent price."""
    normalized = (side or "").lower()
    if normalized == "yes":
        return yes_price_cents
    if normalized == "no":
        return 100 - yes_price_cents
    raise ValueError(f"Unknown binary side: {side!r}")


def estimate_kalshi_fee_cents(side: str, yes_price_cents: int, contract_count: int = 1) -> int:
    """Estimate Kalshi binary-contract fees in cents for a dry-run order."""
    if contract_count <= 0:
        return 0

    premium_cents = premium_cents_for_side(side, yes_price_cents)
    if premium_cents <= 0 or premium_cents >= 100:
        return 0

    premium_dollars = Decimal(premium_cents) / Decimal(100)
    fee_dollars = (
        KALSHI_BINARY_FEE_RATE_DOLLARS
        * Decimal(contract_count)
        * premium_dollars
        * (Decimal(1) - premium_dollars)
    )
    return int((fee_dollars * Decimal(100)).to_integral_value(rounding=ROUND_CEILING))


def estimate_polymarket_taker_fee_cents(
    side: str,
    yes_price_cents: int,
    contract_count: int = 1,
    fee_rate: float | Decimal | None = None,
) -> float:
    """Estimate Polymarket taker fees in cents.

    Polymarket documents fees as:
        fee = contracts * feeRate * p * (1 - p)
    rounded to 5 decimal places in USDC. Makers are not charged; this bot uses
    the taker formula as a conservative after-fee estimate for dry-run fills.
    """
    if contract_count <= 0:
        return 0.0

    premium_cents = premium_cents_for_side(side, yes_price_cents)
    if premium_cents <= 0 or premium_cents >= 100:
        return 0.0

    rate = Decimal(str(fee_rate)) if fee_rate is not None else POLYMARKET_CRYPTO_TAKER_FEE_RATE
    premium = Decimal(premium_cents) / Decimal(100)
    fee_dollars = Decimal(contract_count) * rate * premium * (Decimal(1) - premium)
    fee_dollars = fee_dollars.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    return float(fee_dollars * Decimal(100))


def estimate_polymarket_fee_cents(
    side: str,
    yes_price_cents: int,
    contract_count: int = 1,
    fee_rate: float | Decimal | None = None,
    liquidity_role: str = "maker",
) -> float:
    """Estimate Polymarket trading fees in cents for the expected liquidity role."""
    if (liquidity_role or "maker").lower() == "maker":
        return 0.0
    return estimate_polymarket_taker_fee_cents(
        side,
        yes_price_cents,
        contract_count=contract_count,
        fee_rate=fee_rate,
    )


def estimate_exchange_fee_cents(
    exchange: str,
    side: str,
    yes_price_cents: int,
    contract_count: int = 1,
    fee_rate: float | Decimal | None = None,
    liquidity_role: str = "maker",
) -> int | float:
    normalized_exchange = (exchange or "kalshi").lower()
    if normalized_exchange == "polymarket":
        return estimate_polymarket_fee_cents(
            side,
            yes_price_cents,
            contract_count=contract_count,
            fee_rate=fee_rate,
            liquidity_role=liquidity_role,
        )
    return estimate_kalshi_fee_cents(side, yes_price_cents, contract_count=contract_count)
