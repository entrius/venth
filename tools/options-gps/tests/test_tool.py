"""
Basic tests for the tool.

These tests run against mock data (no API key needed).
They verify that the tool can import the client, fetch data,
and produce expected output shapes.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from synth_client import SynthClient


def test_client_loads_in_mock_mode():
    """Verify the client initializes in mock mode without an API key."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = SynthClient()
    assert client.mock_mode is True


def test_prediction_percentiles():
    """Verify prediction percentiles returns expected structure."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = SynthClient()

    data = client.get_prediction_percentiles("BTC", horizon="24h")
    assert "current_price" in data
    assert "forecast_future" in data
    assert "percentiles" in data["forecast_future"]
    assert isinstance(data["forecast_future"]["percentiles"], list)
    assert len(data["forecast_future"]["percentiles"]) > 0


def test_volatility():
    """Verify volatility returns expected structure."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = SynthClient()

    data = client.get_volatility("BTC", horizon="24h")
    assert "forecast_future" in data
    assert "average_volatility" in data["forecast_future"]


if __name__ == "__main__":
    test_client_loads_in_mock_mode()
    test_prediction_percentiles()
    test_volatility()
    print("All tests passed!")
