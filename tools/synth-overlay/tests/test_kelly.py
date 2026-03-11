"""Tests for Kelly Criterion position sizing logic."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import pytest
from kelly import compute_kelly_sizing, KELLY_MAX_FRACTION


class TestKellyBasicBehavior:
    def test_yes_side_when_synth_higher(self):
        """Synth says 60% up, market prices 50% → YES is +EV."""
        r = compute_kelly_sizing(0.60, 0.50, confidence=0.8, balance=1000)
        assert r["side"] == "YES"
        assert r["fraction"] > 0
        assert r["size"] > 0
        assert r["ev_per_dollar"] > 0

    def test_no_side_when_synth_lower(self):
        """Synth says 40% up, market prices 50% → NO is +EV."""
        r = compute_kelly_sizing(0.40, 0.50, confidence=0.8, balance=1000)
        assert r["side"] == "NO"
        assert r["fraction"] > 0
        assert r["size"] > 0
        assert r["ev_per_dollar"] > 0

    def test_no_ev_when_fair(self):
        """Synth agrees with market → no positive EV side."""
        r = compute_kelly_sizing(0.50, 0.50, confidence=0.8, balance=1000)
        assert r["side"] is None
        assert r["fraction"] == 0
        assert r["size"] == 0

    def test_size_scales_with_balance(self):
        r1 = compute_kelly_sizing(0.60, 0.50, confidence=0.8, balance=1000)
        r2 = compute_kelly_sizing(0.60, 0.50, confidence=0.8, balance=2000)
        assert r2["size"] == pytest.approx(r1["size"] * 2, abs=0.01)

    def test_fraction_scales_with_confidence(self):
        r_high = compute_kelly_sizing(0.60, 0.50, confidence=1.0, balance=1000)
        r_low = compute_kelly_sizing(0.60, 0.50, confidence=0.3, balance=1000)
        assert r_high["fraction"] > r_low["fraction"]


class TestKellyEdgeCases:
    def test_null_synth_returns_empty(self):
        r = compute_kelly_sizing(None, 0.50, confidence=0.8, balance=1000)
        assert r["side"] is None

    def test_null_market_returns_empty(self):
        r = compute_kelly_sizing(0.60, None, confidence=0.8, balance=1000)
        assert r["side"] is None

    def test_extreme_market_prob_low(self):
        """Market prob near 0 should be rejected (division safety)."""
        r = compute_kelly_sizing(0.50, 0.005, confidence=0.8, balance=1000)
        assert r["side"] is None

    def test_extreme_market_prob_high(self):
        """Market prob near 1 should be rejected."""
        r = compute_kelly_sizing(0.50, 0.995, confidence=0.8, balance=1000)
        assert r["side"] is None

    def test_synth_zero_returns_empty(self):
        r = compute_kelly_sizing(0.0, 0.50, confidence=0.8, balance=1000)
        assert r["side"] is None

    def test_synth_one_returns_empty(self):
        r = compute_kelly_sizing(1.0, 0.50, confidence=0.8, balance=1000)
        assert r["side"] is None

    def test_no_balance_gives_zero_size(self):
        r = compute_kelly_sizing(0.60, 0.50, confidence=0.8, balance=None)
        assert r["side"] == "YES"
        assert r["fraction"] > 0
        assert r["size"] == 0

    def test_zero_balance_gives_zero_size(self):
        r = compute_kelly_sizing(0.60, 0.50, confidence=0.8, balance=0)
        assert r["side"] == "YES"
        assert r["size"] == 0

    def test_null_confidence_uses_half(self):
        r_null = compute_kelly_sizing(0.60, 0.50, confidence=None, balance=1000)
        r_half = compute_kelly_sizing(0.60, 0.50, confidence=0.5, balance=1000)
        assert r_null["fraction"] == r_half["fraction"]

    def test_nan_synth_returns_empty(self):
        r = compute_kelly_sizing(float("nan"), 0.50, confidence=0.8, balance=1000)
        assert r["side"] is None

    def test_inf_market_returns_empty(self):
        r = compute_kelly_sizing(0.60, float("inf"), confidence=0.8, balance=1000)
        assert r["side"] is None

    def test_nan_confidence_uses_half(self):
        r_nan = compute_kelly_sizing(0.60, 0.50, confidence=float("nan"), balance=1000)
        r_half = compute_kelly_sizing(0.60, 0.50, confidence=0.5, balance=1000)
        assert r_nan["fraction"] == r_half["fraction"]

    def test_neg_inf_synth_returns_empty(self):
        r = compute_kelly_sizing(float("-inf"), 0.50, confidence=0.8, balance=1000)
        assert r["side"] is None


class TestKellyCap:
    def test_fraction_never_exceeds_cap(self):
        """Even with massive edge + full confidence, fraction is capped."""
        r = compute_kelly_sizing(0.95, 0.50, confidence=1.0, balance=10000)
        assert r["fraction"] <= KELLY_MAX_FRACTION

    def test_cap_applied_with_large_edge(self):
        r = compute_kelly_sizing(0.90, 0.30, confidence=1.0, balance=5000)
        assert r["fraction"] == KELLY_MAX_FRACTION
        assert r["size"] == pytest.approx(KELLY_MAX_FRACTION * 5000, abs=0.01)


class TestKellyFormula:
    def test_yes_ev_calculation(self):
        """Verify EV = p_synth * b - (1 - p_synth) for YES side."""
        synth, market = 0.60, 0.45
        b = (1 - market) / market
        expected_ev = synth * b - (1 - synth)
        r = compute_kelly_sizing(synth, market, confidence=1.0, balance=1000)
        assert r["side"] == "YES"
        assert r["ev_per_dollar"] == pytest.approx(expected_ev, abs=0.001)

    def test_no_ev_calculation(self):
        """Verify EV = (1 - p_synth) * b_no - p_synth for NO side."""
        synth, market = 0.35, 0.50
        market_no = 1 - market
        b_no = (1 - market_no) / market_no
        expected_ev = (1 - synth) * b_no - synth
        r = compute_kelly_sizing(synth, market, confidence=1.0, balance=1000)
        assert r["side"] == "NO"
        assert r["ev_per_dollar"] == pytest.approx(expected_ev, abs=0.001)

    def test_symmetry(self):
        """Synth=0.6/market=0.5 YES should mirror synth=0.4/market=0.5 NO."""
        r_yes = compute_kelly_sizing(0.60, 0.50, confidence=0.8, balance=1000)
        r_no = compute_kelly_sizing(0.40, 0.50, confidence=0.8, balance=1000)
        assert r_yes["side"] == "YES"
        assert r_no["side"] == "NO"
        assert r_yes["fraction"] == pytest.approx(r_no["fraction"], abs=0.001)
        assert r_yes["size"] == pytest.approx(r_no["size"], abs=0.01)
        assert r_yes["ev_per_dollar"] == pytest.approx(r_no["ev_per_dollar"], abs=0.001)

    def test_small_edge_small_fraction(self):
        """A tiny edge (1pp) should produce a very small Kelly fraction."""
        r = compute_kelly_sizing(0.51, 0.50, confidence=0.8, balance=10000)
        if r["side"] is not None:
            assert r["fraction"] < 0.05
            assert r["size"] < 500
