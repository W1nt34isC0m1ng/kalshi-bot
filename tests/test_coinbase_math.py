"""Unit tests for pure math functions in coinbase.py."""
from __future__ import annotations

import math
import sys
import os

# Make the package importable without a full install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kalshi_bot.coinbase import (
    compute_d2,
    compute_implied_vol,
    norm_cdf,
    prob_above_strike,
)


# ------------------------------------------------------------------ #
# norm_cdf                                                             #
# ------------------------------------------------------------------ #

class TestNormCdf:
    def test_zero_is_half(self):
        assert abs(norm_cdf(0.0) - 0.5) < 1e-9

    def test_large_positive_approaches_one(self):
        assert norm_cdf(10.0) > 0.9999

    def test_large_negative_approaches_zero(self):
        assert norm_cdf(-10.0) < 1e-4

    def test_symmetry(self):
        for x in (0.5, 1.0, 1.96, 2.576):
            assert abs(norm_cdf(x) + norm_cdf(-x) - 1.0) < 1e-9

    def test_known_values(self):
        # N(1.96) ≈ 0.975
        assert abs(norm_cdf(1.96) - 0.975) < 0.001
        # N(-1.645) ≈ 0.05
        assert abs(norm_cdf(-1.645) - 0.05) < 0.001


# ------------------------------------------------------------------ #
# prob_above_strike                                                    #
# ------------------------------------------------------------------ #

class TestProbAboveStrike:
    def test_atm_near_half(self):
        """ATM with zero drift → roughly 50%."""
        p = prob_above_strike(100.0, 100.0, 900.0, 0.80)
        assert abs(p - 0.5) < 0.02

    def test_spot_far_above_strike_is_high(self):
        """Spot >> strike → high probability."""
        p = prob_above_strike(105.0, 100.0, 900.0, 0.80)
        assert p > 0.60

    def test_spot_far_below_strike_is_low(self):
        """Spot << strike → low probability."""
        p = prob_above_strike(95.0, 100.0, 900.0, 0.80)
        assert p < 0.40

    def test_expired_above(self):
        """When secs_left ≤ 1 and spot > strike, should return 1."""
        p = prob_above_strike(101.0, 100.0, 0.0, 0.80)
        assert p == 1.0

    def test_expired_below(self):
        """When secs_left ≤ 1 and spot < strike, should return 0."""
        p = prob_above_strike(99.0, 100.0, 0.0, 0.80)
        assert p == 0.0

    def test_invalid_spot(self):
        """Zero / negative spot returns 0.5 (undefined)."""
        assert prob_above_strike(0.0, 100.0, 900.0, 0.80) == 0.50

    def test_clamped_to_sane_range(self):
        """Result is always within [0.02, 0.98]."""
        for spot, strike in [(1.0, 10000.0), (10000.0, 1.0)]:
            p = prob_above_strike(spot, strike, 900.0, 0.80)
            assert 0.02 <= p <= 0.98

    def test_positive_drift_raises_probability(self):
        """Positive drift should move the probability upward."""
        p_zero = prob_above_strike(100.0, 100.0, 900.0, 0.80, mu_per_minute=0.0)
        p_drift = prob_above_strike(100.0, 100.0, 900.0, 0.80, mu_per_minute=0.001)
        assert p_drift > p_zero

    def test_negative_drift_lowers_probability(self):
        """Negative drift should move the probability downward."""
        p_zero = prob_above_strike(100.0, 100.0, 900.0, 0.80, mu_per_minute=0.0)
        p_drift = prob_above_strike(100.0, 100.0, 900.0, 0.80, mu_per_minute=-0.001)
        assert p_drift < p_zero


# ------------------------------------------------------------------ #
# compute_d2                                                           #
# ------------------------------------------------------------------ #

class TestComputeD2:
    def test_atm_zero(self):
        """Spot == strike with no drift → d2 == 0."""
        d2 = compute_d2(100.0, 100.0, 900.0, 0.80)
        assert abs(d2) < 1e-6

    def test_positive_when_above_strike(self):
        d2 = compute_d2(105.0, 100.0, 900.0, 0.80)
        assert d2 > 0

    def test_positive_when_below_strike(self):
        """compute_d2 returns abs(log-moneyness / sigma_t) — always non-negative."""
        d2 = compute_d2(95.0, 100.0, 900.0, 0.80)
        assert d2 > 0

    def test_zero_inputs_return_zero(self):
        assert compute_d2(0.0, 100.0, 900.0, 0.80) == 0.0
        assert compute_d2(100.0, 0.0, 900.0, 0.80) == 0.0
        assert compute_d2(100.0, 100.0, 0.0, 0.80) == 0.0
        assert compute_d2(100.0, 100.0, 900.0, 0.0) == 0.0

    def test_more_time_reduces_d2(self):
        """Longer time-to-expiry means more vol, so d2 shrinks."""
        d2_short = compute_d2(105.0, 100.0, 60.0, 0.80)
        d2_long = compute_d2(105.0, 100.0, 900.0, 0.80)
        assert d2_short > d2_long

    def test_higher_vol_reduces_d2(self):
        """Higher sigma means strike is less decisive."""
        d2_low = compute_d2(105.0, 100.0, 900.0, 0.50)
        d2_high = compute_d2(105.0, 100.0, 900.0, 2.00)
        assert d2_low > d2_high


# ------------------------------------------------------------------ #
# compute_implied_vol                                                  #
# ------------------------------------------------------------------ #

class TestComputeImpliedVol:
    def test_degenerate_price_returns_none(self):
        """Prices near 0 or 1 are outside the supported range."""
        assert compute_implied_vol(0.01, 100.0, 100.0, 900.0) is None
        assert compute_implied_vol(0.99, 100.0, 100.0, 900.0) is None

    def test_atm_returns_none(self):
        """Exactly ATM (spot == strike) → log-moneyness 0 → undefined."""
        assert compute_implied_vol(0.5, 100.0, 100.0, 900.0) is None

    def test_roundtrip(self):
        """Back out vol from a price generated by prob_above_strike and compare.

        Uses spot=100.5 / strike=100.0 which keeps d2 around 1.4 and the
        market price around 0.92 — comfortably within compute_implied_vol's
        valid range of (0.03, 0.97).
        """
        spot, strike, secs, sigma = 100.5, 100.0, 600.0, 0.80
        price = prob_above_strike(spot, strike, secs, sigma)
        assert 0.03 < price < 0.97, f"test precondition failed: price={price}"
        recovered = compute_implied_vol(price, spot, strike, secs)
        assert recovered is not None
        assert abs(recovered - sigma) / sigma < 0.10  # within 10%

    def test_returns_positive_value(self):
        """Any successful result must be strictly positive."""
        vol = compute_implied_vol(0.70, 105.0, 100.0, 900.0)
        if vol is not None:
            assert vol > 0

    def test_negative_moneyness_returns_none(self):
        """If implied vol would be negative (inconsistent sign), return None."""
        # market_price=0.70 implies we expect spot > strike, but spot < strike
        # → implied sigma negative → should return None
        result = compute_implied_vol(0.70, 95.0, 100.0, 900.0)
        assert result is None
