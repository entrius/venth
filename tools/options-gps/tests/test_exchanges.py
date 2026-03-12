"""Tests for multi-exchange market line shopping (issue #32)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from exchanges import (
    compute_divergence,
    get_market_lines,
    MockExchangeProvider,
    MarketLineResult,
    _classify_consensus,
    _default_providers,
)
from pipeline import (
    adjust_confidence_for_divergence,
    forecast_confidence,
)

SYNTH_OPTIONS = {
    "current_price": 67723,
    "call_options": {
        "66500": 1400, "67000": 987, "67500": 640,
        "68000": 373, "68500": 197, "69000": 90,
    },
    "put_options": {
        "66500": 57, "67000": 140, "67500": 291,
        "68000": 526, "68500": 850, "69000": 1200,
    },
}

P24H = {
    "0.05": 66000, "0.2": 67000, "0.35": 67400,
    "0.5": 67800, "0.65": 68200, "0.8": 68800, "0.95": 70000,
}


# ── compute_divergence ──────────────────────────────────────────

def test_divergence_identical_prices():
    """Zero divergence when exchange prices match Synth exactly."""
    ex_prices = {
        "call_options": dict(SYNTH_OPTIONS["call_options"]),
        "put_options": dict(SYNTH_OPTIONS["put_options"]),
    }
    result = compute_divergence(SYNTH_OPTIONS, ex_prices, "TestExchange")
    assert result is not None
    assert result.avg_abs_div == 0.0
    assert result.max_abs_div == 0.0
    assert result.rich_calls == 0.0
    assert result.rich_puts == 0.0
    assert result.n_strikes == 12  # 6 calls + 6 puts


def test_divergence_uniformly_rich():
    """Exchange prices 10% above Synth -> positive divergence."""
    ex_prices = {
        "call_options": {k: float(v) * 1.10 for k, v in SYNTH_OPTIONS["call_options"].items()},
        "put_options": {k: float(v) * 1.10 for k, v in SYNTH_OPTIONS["put_options"].items()},
    }
    result = compute_divergence(SYNTH_OPTIONS, ex_prices, "RichExchange")
    assert result is not None
    assert abs(result.avg_abs_div - 10.0) < 0.5
    assert result.rich_calls > 0
    assert result.rich_puts > 0


def test_divergence_uniformly_cheap():
    """Exchange prices 5% below Synth -> negative signed divergence."""
    ex_prices = {
        "call_options": {k: float(v) * 0.95 for k, v in SYNTH_OPTIONS["call_options"].items()},
        "put_options": {k: float(v) * 0.95 for k, v in SYNTH_OPTIONS["put_options"].items()},
    }
    result = compute_divergence(SYNTH_OPTIONS, ex_prices, "CheapExchange")
    assert result is not None
    assert abs(result.avg_abs_div - 5.0) < 0.5
    assert result.rich_calls < 0
    assert result.rich_puts < 0


def test_divergence_empty_exchange_prices():
    """No overlapping strikes -> None."""
    result = compute_divergence(SYNTH_OPTIONS, {"call_options": {}, "put_options": {}}, "Empty")
    assert result is None


def test_divergence_zero_synth_prices():
    """Synth prices at zero are skipped (no division by zero)."""
    opts = {
        "current_price": 100,
        "call_options": {"100": 0, "110": 10},
        "put_options": {"90": 0, "100": 10},
    }
    ex = {"call_options": {"100": 5, "110": 12}, "put_options": {"90": 3, "100": 11}}
    result = compute_divergence(opts, ex, "ZeroTest")
    assert result is not None
    assert result.n_strikes == 2  # only the non-zero synth prices


def test_divergence_partial_overlap():
    """Exchange has only some strikes."""
    ex_prices = {
        "call_options": {"67000": 1000},  # only 1 of 6 call strikes
        "put_options": {"68000": 500},     # only 1 of 6 put strikes
    }
    result = compute_divergence(SYNTH_OPTIONS, ex_prices, "Partial")
    assert result is not None
    assert result.n_strikes == 2


# ── MockExchangeProvider ────────────────────────────────────────

def test_mock_provider_returns_all_strikes():
    """Mock provider should return prices for every strike in Synth data."""
    provider = MockExchangeProvider("Test", call_bias=0.0, put_bias=0.0, noise_scale=0.0, seed=0)
    prices = provider.get_option_prices("BTC", SYNTH_OPTIONS)
    assert set(prices["call_options"].keys()) == set(SYNTH_OPTIONS["call_options"].keys())
    assert set(prices["put_options"].keys()) == set(SYNTH_OPTIONS["put_options"].keys())


def test_mock_provider_zero_noise_matches_bias():
    """With zero noise, divergence should equal the bias exactly."""
    provider = MockExchangeProvider("Exact", call_bias=0.05, put_bias=-0.03, noise_scale=0.0, seed=0)
    prices = provider.get_option_prices("BTC", SYNTH_OPTIONS)
    for strike, synth_price in SYNTH_OPTIONS["call_options"].items():
        synth_f = float(synth_price)
        ex_f = float(prices["call_options"][strike])
        assert abs(ex_f - synth_f * 1.05) < 0.02, f"Call {strike}: expected {synth_f * 1.05}, got {ex_f}"


def test_mock_provider_deterministic():
    """Same seed should produce identical prices."""
    p1 = MockExchangeProvider("A", 0.01, -0.01, 0.03, seed=99)
    p2 = MockExchangeProvider("B", 0.01, -0.01, 0.03, seed=99)
    prices1 = p1.get_option_prices("BTC", SYNTH_OPTIONS)
    prices2 = p2.get_option_prices("BTC", SYNTH_OPTIONS)
    assert prices1 == prices2


def test_mock_provider_positive_prices():
    """All mock prices should be > 0."""
    provider = MockExchangeProvider("Floor", call_bias=-0.5, put_bias=-0.5, noise_scale=0.1, seed=42)
    prices = provider.get_option_prices("BTC", SYNTH_OPTIONS)
    for k, v in prices["call_options"].items():
        assert v > 0, f"Call {k} has non-positive price {v}"
    for k, v in prices["put_options"].items():
        assert v > 0, f"Put {k} has non-positive price {v}"


# ── _classify_consensus ────────────────────────────────────────

def test_consensus_classification():
    assert _classify_consensus(1.0) == "strong_agreement"
    assert _classify_consensus(2.9) == "strong_agreement"
    assert _classify_consensus(3.0) == "moderate_agreement"
    assert _classify_consensus(6.9) == "moderate_agreement"
    assert _classify_consensus(7.0) == "weak_agreement"
    assert _classify_consensus(14.9) == "weak_agreement"
    assert _classify_consensus(15.0) == "disagreement"
    assert _classify_consensus(50.0) == "disagreement"


# ── get_market_lines ────────────────────────────────────────────

def test_market_lines_default_providers():
    """Full pipeline with default mock providers returns valid result."""
    result = get_market_lines(SYNTH_OPTIONS, asset="BTC")
    assert isinstance(result, MarketLineResult)
    assert len(result.summaries) == 3  # Aevo, Deribit, OKX
    assert result.avg_divergence > 0
    assert result.max_divergence >= result.avg_divergence
    assert result.consensus in ("strong_agreement", "moderate_agreement", "weak_agreement", "disagreement")
    for s in result.summaries:
        assert s.n_strikes == 12
        assert s.avg_abs_div >= 0
        assert s.max_abs_div >= s.avg_abs_div


def test_market_lines_empty_options():
    """Empty option data returns safe empty result."""
    result = get_market_lines({"current_price": 0, "call_options": {}, "put_options": {}})
    assert result.summaries == []
    assert result.avg_divergence == 0.0
    assert result.consensus == "disagreement"


def test_market_lines_custom_providers():
    """Custom provider list is respected."""
    tight = MockExchangeProvider("Tight", call_bias=0.0, put_bias=0.0, noise_scale=0.001, seed=1)
    result = get_market_lines(SYNTH_OPTIONS, providers=[tight])
    assert len(result.summaries) == 1
    assert result.summaries[0].exchange == "Tight"
    assert result.summaries[0].avg_abs_div < 1.0  # very tight


def test_market_lines_failing_provider():
    """A provider that raises should be skipped, not crash the pipeline."""
    class BrokenProvider:
        name = "Broken"
        def get_option_prices(self, asset, synth_options):
            raise ConnectionError("simulated failure")
    good = MockExchangeProvider("Good", 0.0, 0.0, 0.01, seed=1)
    result = get_market_lines(SYNTH_OPTIONS, providers=[BrokenProvider(), good])
    assert len(result.summaries) == 1
    assert result.summaries[0].exchange == "Good"


# ── adjust_confidence_for_divergence ────────────────────────────

def test_confidence_nudge_strong_agreement():
    base = 0.7
    adjusted = adjust_confidence_for_divergence(base, 2.0, "strong_agreement")
    assert adjusted == 0.75


def test_confidence_nudge_moderate_agreement():
    base = 0.7
    adjusted = adjust_confidence_for_divergence(base, 5.0, "moderate_agreement")
    assert adjusted == 0.7  # no change


def test_confidence_nudge_weak_agreement():
    base = 0.7
    adjusted = adjust_confidence_for_divergence(base, 10.0, "weak_agreement")
    assert abs(adjusted - 0.67) < 1e-9


def test_confidence_nudge_disagreement():
    base = 0.7
    adjusted = adjust_confidence_for_divergence(base, 20.0, "disagreement")
    assert abs(adjusted - 0.63) < 1e-9


def test_confidence_nudge_capped_at_one():
    adjusted = adjust_confidence_for_divergence(0.98, 1.0, "strong_agreement")
    assert adjusted == 1.0


def test_confidence_nudge_floored_at_point_one():
    adjusted = adjust_confidence_for_divergence(0.12, 25.0, "disagreement")
    assert adjusted == 0.1


def test_confidence_nudge_zero_divergence():
    """Zero divergence means no adjustment."""
    base = 0.6
    adjusted = adjust_confidence_for_divergence(base, 0.0, "strong_agreement")
    assert adjusted == 0.6


# ── End-to-end: line shopping + confidence integration ──────────

def test_end_to_end_line_shopping_adjusts_confidence():
    """Full flow: Synth data -> market lines -> confidence adjustment."""
    base_confidence = forecast_confidence(P24H, 67723.0)
    market = get_market_lines(SYNTH_OPTIONS, asset="BTC")
    adjusted = adjust_confidence_for_divergence(base_confidence, market.avg_divergence, market.consensus)
    # Mock providers produce small divergence -> confidence should shift slightly
    assert abs(adjusted - base_confidence) <= 0.07
    assert 0.1 <= adjusted <= 1.0


def test_default_providers_count():
    providers = _default_providers()
    assert len(providers) == 3
    names = {p.name for p in providers}
    assert names == {"Aevo", "Deribit", "OKX"}
