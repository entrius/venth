"""
Tests for the Tide Chart tool.

All tests run against mock data (no API key needed).
They verify data fetching, normalization, metric calculation,
ranking, and dashboard generation.
"""

import sys
import os
import warnings

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
# Add tool directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from synth_client import SynthClient
from chart import (
    EQUITIES,
    PERCENTILE_KEYS,
    fetch_all_data,
    normalize_percentiles,
    calculate_metrics,
    add_relative_to_spy,
    rank_equities,
    get_normalized_series,
)
from main import generate_dashboard_html


def _make_client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return SynthClient()


def test_client_loads_in_mock_mode():
    """Verify the client initializes in mock mode without an API key."""
    client = _make_client()
    assert client.mock_mode is True


def test_fetch_all_equities_data():
    """Verify fetch_all_data returns data for all 5 equities."""
    client = _make_client()
    data = fetch_all_data(client)

    assert len(data) == 5
    for asset in EQUITIES:
        assert asset in data
        assert "current_price" in data[asset]
        assert "percentiles" in data[asset]
        assert "average_volatility" in data[asset]
        assert isinstance(data[asset]["current_price"], (int, float))
        assert isinstance(data[asset]["percentiles"], list)
        assert len(data[asset]["percentiles"]) == 289


def test_normalize_percentiles():
    """Verify normalization converts prices to % change correctly."""
    current_price = 100.0
    percentiles = [
        {"0.05": 95.0, "0.5": 100.0, "0.95": 110.0},
        {"0.05": 90.0, "0.5": 102.0, "0.95": 115.0},
    ]
    result = normalize_percentiles(percentiles, current_price)

    assert len(result) == 2
    # First step
    assert result[0]["0.05"] == -5.0    # (95-100)/100*100
    assert result[0]["0.5"] == 0.0      # (100-100)/100*100
    assert result[0]["0.95"] == 10.0    # (110-100)/100*100
    # Second step
    assert result[1]["0.05"] == -10.0
    assert result[1]["0.5"] == 2.0
    assert result[1]["0.95"] == 15.0


def test_calculate_metrics_median_move():
    """Verify median move calculation from final percentile."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)

    for asset in EQUITIES:
        m = metrics[asset]
        final = data[asset]["percentiles"][-1]
        cp = data[asset]["current_price"]
        expected_median = (final["0.5"] - cp) / cp * 100
        assert abs(m["median_move"] - expected_median) < 1e-10


def test_calculate_metrics_skew():
    """Verify skew = upside - downside."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)

    for asset in EQUITIES:
        m = metrics[asset]
        assert abs(m["skew"] - (m["upside"] - m["downside"])) < 1e-10


def test_calculate_metrics_range():
    """Verify range = upside + downside."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)

    for asset in EQUITIES:
        m = metrics[asset]
        assert abs(m["range_pct"] - (m["upside"] + m["downside"])) < 1e-10


def test_relative_to_spy():
    """Verify relative-to-SPY calculations."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)
    metrics = add_relative_to_spy(metrics)

    spy_median = metrics["SPY"]["median_move"]
    spy_skew = metrics["SPY"]["skew"]

    # SPY relative to itself should be 0
    assert metrics["SPY"]["relative_median"] == 0.0
    assert metrics["SPY"]["relative_skew"] == 0.0

    for asset in ["NVDA", "TSLA", "AAPL", "GOOGL"]:
        m = metrics[asset]
        expected_rel_median = m["median_move"] - spy_median
        expected_rel_skew = m["skew"] - spy_skew
        assert abs(m["relative_median"] - expected_rel_median) < 1e-10
        assert abs(m["relative_skew"] - expected_rel_skew) < 1e-10


def test_rank_equities_sorting():
    """Verify equities are ranked by median_move descending."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)
    metrics = add_relative_to_spy(metrics)
    ranked = rank_equities(metrics, sort_by="median_move")

    assert len(ranked) == 5
    for i in range(len(ranked) - 1):
        assert ranked[i][1]["median_move"] >= ranked[i + 1][1]["median_move"]


def test_rank_equities_ascending():
    """Verify ascending sort works."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)
    metrics = add_relative_to_spy(metrics)
    ranked = rank_equities(metrics, sort_by="volatility", ascending=True)

    for i in range(len(ranked) - 1):
        assert ranked[i][1]["volatility"] <= ranked[i + 1][1]["volatility"]


def test_get_normalized_series():
    """Verify normalized series has correct structure."""
    client = _make_client()
    data = fetch_all_data(client)
    series = get_normalized_series(data)

    assert len(series) == 5
    for asset in EQUITIES:
        assert asset in series
        assert len(series[asset]) == 289
        # First step should be near 0 (current price normalized)
        first = series[asset][0]
        assert "0.5" in first


def test_generate_dashboard_html():
    """Verify dashboard HTML generation produces valid output."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)
    metrics = add_relative_to_spy(metrics)
    ranked = rank_equities(metrics, sort_by="median_move")
    normalized = get_normalized_series(data)

    html = generate_dashboard_html(normalized, metrics, ranked)

    assert isinstance(html, str)
    assert "<!DOCTYPE html>" in html
    assert "Tide Chart" in html
    assert "plotly" in html.lower()
    # Check all equity tickers appear
    for asset in EQUITIES:
        assert asset in html
    # Check table has rows
    assert "<tr>" in html
    assert "cone-chart" in html
    # Check relative_skew column exists (Skew vs SPY header)
    assert "Skew vs SPY" in html
    # Check sortable table headers
    assert "sortable" in html
    assert "data-sort" in html


def test_volatility_values():
    """Verify volatility values are positive floats."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)

    for asset in EQUITIES:
        assert metrics[asset]["volatility"] > 0
        assert isinstance(metrics[asset]["volatility"], float)


if __name__ == "__main__":
    test_client_loads_in_mock_mode()
    test_fetch_all_equities_data()
    test_normalize_percentiles()
    test_calculate_metrics_median_move()
    test_calculate_metrics_skew()
    test_calculate_metrics_range()
    test_relative_to_spy()
    test_rank_equities_sorting()
    test_rank_equities_ascending()
    test_get_normalized_series()
    test_generate_dashboard_html()
    test_volatility_values()
    print("All tests passed!")
