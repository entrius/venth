"""Tests for alert threshold logic and edge-based notification decisions."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from edge import compute_edge_pct, signal_from_edge, strength_from_edge


def _exceeds_threshold(edge_pct, threshold):
    """Mirror the threshold check used by the extension's alert system."""
    return abs(edge_pct) >= threshold


class TestAlertThreshold:
    """Verify edge values correctly trigger or skip alert thresholds."""

    def test_strong_edge_exceeds_default_threshold(self):
        edge = compute_edge_pct(0.55, 0.50)  # 5pp
        assert _exceeds_threshold(edge, 3.0) is True

    def test_moderate_edge_below_default_threshold(self):
        edge = compute_edge_pct(0.52, 0.50)  # 2pp
        assert _exceeds_threshold(edge, 3.0) is False

    def test_negative_edge_exceeds_threshold(self):
        edge = compute_edge_pct(0.40, 0.45)  # -5pp
        assert _exceeds_threshold(edge, 3.0) is True

    def test_exact_threshold_triggers(self):
        edge = compute_edge_pct(0.53, 0.50)  # 3pp
        assert _exceeds_threshold(edge, 3.0) is True

    def test_custom_low_threshold(self):
        edge = compute_edge_pct(0.51, 0.50)  # 1pp
        assert _exceeds_threshold(edge, 0.5) is True

    def test_custom_high_threshold(self):
        edge = compute_edge_pct(0.55, 0.50)  # 5pp
        assert _exceeds_threshold(edge, 10.0) is False

    def test_zero_edge_never_triggers(self):
        edge = compute_edge_pct(0.50, 0.50)  # 0pp
        assert _exceeds_threshold(edge, 0.5) is False


class TestAlertSignalContext:
    """Ensure alert notifications carry correct signal and strength context."""

    def test_underpriced_signal_on_positive_edge(self):
        edge = compute_edge_pct(0.55, 0.50)
        assert signal_from_edge(edge) == "underpriced"

    def test_overpriced_signal_on_negative_edge(self):
        edge = compute_edge_pct(0.45, 0.50)
        assert signal_from_edge(edge) == "overpriced"

    def test_fair_signal_within_threshold(self):
        edge = compute_edge_pct(0.502, 0.500)
        assert signal_from_edge(edge) == "fair"

    def test_strong_strength_for_large_edge(self):
        edge = compute_edge_pct(0.55, 0.50)
        assert strength_from_edge(edge) == "strong"

    def test_moderate_strength_for_medium_edge(self):
        edge = compute_edge_pct(0.52, 0.50)
        assert strength_from_edge(edge) == "moderate"

    def test_no_strength_for_small_edge(self):
        edge = compute_edge_pct(0.505, 0.500)
        assert strength_from_edge(edge) == "none"


class TestAlertEdgeCases:
    """Edge cases for alert threshold evaluation."""

    def test_threshold_with_fractional_edge(self):
        edge = compute_edge_pct(0.525, 0.500)  # 2.5pp
        assert _exceeds_threshold(edge, 2.5) is True
        assert _exceeds_threshold(edge, 3.0) is False

    def test_minimum_valid_threshold(self):
        edge = compute_edge_pct(0.505, 0.500)  # 0.5pp
        assert _exceeds_threshold(edge, 0.5) is True

    def test_symmetric_positive_negative(self):
        """Positive and negative edges of same magnitude should both trigger."""
        pos = compute_edge_pct(0.55, 0.50)
        neg = compute_edge_pct(0.45, 0.50)
        assert _exceeds_threshold(pos, 3.0) is True
        assert _exceeds_threshold(neg, 3.0) is True
