import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import exchanges  # type: ignore[import]


def _mock_option_data():
    return {
        "current_price": 100.0,
        "call_options": {
            "90": 12.0,
            "100": 8.0,
            "110": 5.0,
        },
        "put_options": {
            "90": 3.0,
            "100": 6.0,
            "110": 11.0,
        },
    }


def test_compute_divergence_summary_basic():
    data = _mock_option_data()
    summary = exchanges.compute_divergence_summary(data)
    # We expect all three mock exchanges to appear
    assert "Aevo" in summary
    assert "Deribit" in summary
    assert "GenericMarket" in summary

    for name, stats in summary.items():
        assert stats["n_quotes"] > 0
        assert stats["avg_abs_div"] >= 0
        assert stats["max_abs_div"] >= stats["avg_abs_div"]


def test_compute_divergence_summary_zero_price_safeguards():
    # If current price or fair prices are invalid, we should get an empty dict
    empty = exchanges.compute_divergence_summary({"current_price": 0})
    assert empty == {}

    bad = exchanges.compute_divergence_summary(
        {"current_price": 100, "call_options": {"100": 0}, "put_options": {}}
    )
    assert bad == {}


def test_compute_divergence_alpha():
    data = _mock_option_data()
    by_ex = exchanges.get_exchange_quotes(data)
    alpha = exchanges.compute_divergence_alpha(data, by_ex)
    assert alpha >= 0
    # Mock exchanges diverge from Synth, so alpha should be positive
    assert alpha > 0


def test_compute_best_venue():
    data = _mock_option_data()
    by_ex = exchanges.get_exchange_quotes(data)
    # Minimal strategy: BUY 1 Call @ 100
    class Leg:
        action = "BUY"
        quantity = 1
        option_type = "Call"
        strike = 100.0
        premium = 8.0

    class Strat:
        legs = [Leg()]

    venue, net = exchanges.compute_best_venue(Strat(), by_ex, data)
    assert venue is not None
    assert net is not None
    assert venue in by_ex


def test_get_exchange_quotes_returns_dict():
    data = _mock_option_data()
    quotes = exchanges.get_exchange_quotes(data)
    assert isinstance(quotes, dict)
    assert len(quotes) >= 1

