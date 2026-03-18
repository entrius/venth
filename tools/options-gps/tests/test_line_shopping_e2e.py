"""End-to-end scripted test for Market Line Shopping (issue #32).
Runs the full pipeline with Synth mock + exchange mock data and verifies
exchange data flows through divergence scoring to ranking output."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import (
    generate_strategies,
    rank_strategies,
    select_three_cards,
    forecast_confidence,
    run_forecast_fusion,
)
from exchange import (
    fetch_all_exchanges,
    strategy_divergence,
    leg_divergences,
)

MOCK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "mock_data", "exchange_options")

OPTION_DATA = {
    "current_price": 67723,
    "call_options": {
        "65000": 2847.68, "66000": 1864.60, "67000": 987.04,
        "67500": 638.43, "68000": 373.27, "68500": 197.11,
        "69000": 93.43, "70000": 15.13,
    },
    "put_options": {
        "65000": 0.99, "66000": 17.91, "67000": 140.36,
        "67500": 291.75, "68000": 526.59, "68500": 850.42,
        "69000": 1246.74, "70000": 2168.44,
    },
}

P24H = {
    "0.05": 66000, "0.2": 67000, "0.35": 67400,
    "0.5": 67800, "0.65": 68200, "0.8": 68800, "0.95": 70000,
}

CURRENT_PRICE = 67723.0


def test_full_line_shopping_pipeline():
    """Load Synth mock + exchange mock -> compute divergence -> rank -> verify exchange data flows through."""
    # Step 1: Load exchange quotes from mock
    quotes = fetch_all_exchanges("BTC", mock_dir=MOCK_DIR)
    assert len(quotes) > 0, "Should load exchange quotes from mock"
    exchanges = {q.exchange for q in quotes}
    assert "deribit" in exchanges
    assert "aevo" in exchanges

    # Step 2: Generate strategies from Synth mock options
    candidates = generate_strategies(OPTION_DATA, "bullish", "medium", asset="BTC")
    assert len(candidates) > 0

    # Step 3: Compute divergence_by_strategy
    divergence_by_strategy = {}
    for c in candidates:
        div = strategy_divergence(c, quotes, OPTION_DATA)
        if div is not None:
            divergence_by_strategy[id(c)] = div

    # At least some strategies should have exchange divergence data
    assert len(divergence_by_strategy) > 0, "Some strategies should have exchange data"

    # Step 4: Rank with divergence
    fusion = run_forecast_fusion(None, P24H, CURRENT_PRICE)
    confidence = forecast_confidence(P24H, CURRENT_PRICE)
    outcome_prices = [float(P24H[k]) for k in sorted(P24H.keys())]

    scored = rank_strategies(
        candidates, fusion, "bullish", outcome_prices, "medium", CURRENT_PRICE,
        confidence=confidence, divergence_by_strategy=divergence_by_strategy,
    )
    assert len(scored) >= 2

    # Step 5: Best strategy should exist and have valid score
    best_card, safer, upside = select_three_cards(scored)
    assert best_card is not None
    assert best_card.score > 0

    # Step 6: Verify ranking changes vs no-divergence baseline
    scored_base = rank_strategies(
        candidates, fusion, "bullish", outcome_prices, "medium", CURRENT_PRICE,
        confidence=confidence,
    )
    # Scores should differ when divergence is applied
    has_difference = False
    for s_div, s_base in zip(scored, scored_base):
        if abs(s_div.score - s_base.score) > 0.001:
            has_difference = True
            break
    assert has_difference, "Divergence should affect at least some scores"

    # Step 7: Verify leg divergences work for the best strategy
    leg_divs = leg_divergences(best_card.strategy, quotes, OPTION_DATA)
    # Should have at least one leg with exchange data
    if best_card.strategy.legs:
        # Only expect data if exchange has that strike
        strikes_in_exchange = {q.strike for q in quotes}
        strategy_strikes = {leg.strike for leg in best_card.strategy.legs}
        if strikes_in_exchange & strategy_strikes:
            assert len(leg_divs) > 0


def test_non_crypto_skips_exchange():
    """XAU asset -> no exchange data -> ranking unchanged."""
    quotes = fetch_all_exchanges("XAU", mock_dir=MOCK_DIR)
    assert quotes == []

    xau_options = {
        "current_price": 2000,
        "call_options": {"1950": 60, "2000": 30, "2050": 10},
        "put_options": {"1950": 10, "2000": 30, "2050": 60},
    }
    p24h = {"0.05": 1950, "0.2": 1970, "0.35": 1985,
            "0.5": 2000, "0.65": 2015, "0.8": 2030, "0.95": 2050}
    candidates = generate_strategies(xau_options, "bullish", "medium")
    outcome_prices = [float(p24h[k]) for k in sorted(p24h.keys())]
    fusion = run_forecast_fusion(None, p24h, 2000)
    confidence = forecast_confidence(p24h, 2000)

    scored_a = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 2000, confidence)
    scored_b = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 2000, confidence,
                               divergence_by_strategy=None)
    assert len(scored_a) == len(scored_b)
    for a, b in zip(scored_a, scored_b):
        assert abs(a.score - b.score) < 0.001


def test_exchange_failure_graceful():
    """Invalid mock dir -> empty quotes -> ranking proceeds normally."""
    import unittest.mock as mock
    with mock.patch("exchange.fetch_derive", return_value=[]):
        quotes = fetch_all_exchanges("BTC", mock_dir="/nonexistent/path")
        assert quotes == []

    candidates = generate_strategies(OPTION_DATA, "bullish", "medium", asset="BTC")
    outcome_prices = [float(P24H[k]) for k in sorted(P24H.keys())]
    fusion = run_forecast_fusion(None, P24H, CURRENT_PRICE)
    confidence = forecast_confidence(P24H, CURRENT_PRICE)

    # Should rank fine without exchange data
    scored = rank_strategies(
        candidates, fusion, "bullish", outcome_prices, "medium", CURRENT_PRICE,
        confidence=confidence, divergence_by_strategy=None,
    )
    assert len(scored) > 0
    best, safer, upside = select_three_cards(scored)
    assert best is not None


if __name__ == "__main__":
    test_full_line_shopping_pipeline()
    print("PASS: test_full_line_shopping_pipeline")
    test_non_crypto_skips_exchange()
    print("PASS: test_non_crypto_skips_exchange")
    test_exchange_failure_graceful()
    print("PASS: test_exchange_failure_graceful")
    print("\nAll line shopping E2E tests passed.")
