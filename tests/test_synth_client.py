"""
Root-level tests for the synth_client package.

Verifies that all endpoints work correctly in mock mode
against the generated mock data files.
"""

import warnings
import pytest

# Suppress the mock mode warning for all tests
@pytest.fixture(autouse=True)
def suppress_mock_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


@pytest.fixture
def client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from synth_client import SynthClient
        return SynthClient()


# ─── Assets available for each endpoint ──────────────────────────────

ALL_ASSETS = ["BTC", "ETH", "SOL", "XAU", "SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]
CRYPTO_ASSETS = ["BTC", "ETH", "SOL", "XAU"]  # Support 1h horizon
OPTION_ASSETS = ["BTC", "ETH", "SOL", "SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]  # Excludes XAU


# ─── Prediction Percentiles ─────────────────────────────────────────

@pytest.mark.parametrize("asset", ALL_ASSETS)
def test_prediction_percentiles_24h(client, asset):
    data = client.get_prediction_percentiles(asset, horizon="24h")
    assert "current_price" in data
    assert "forecast_future" in data
    assert isinstance(data["forecast_future"]["percentiles"], list)


@pytest.mark.parametrize("asset", CRYPTO_ASSETS)
def test_prediction_percentiles_1h(client, asset):
    data = client.get_prediction_percentiles(asset, horizon="1h")
    assert "current_price" in data
    assert "forecast_future" in data


# ─── Volatility ─────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", ALL_ASSETS)
def test_volatility_24h(client, asset):
    data = client.get_volatility(asset, horizon="24h")
    assert "forecast_future" in data
    assert "average_volatility" in data["forecast_future"]


@pytest.mark.parametrize("asset", CRYPTO_ASSETS)
def test_volatility_1h(client, asset):
    data = client.get_volatility(asset, horizon="1h")
    assert "forecast_future" in data or "realized" in data


# ─── Option Pricing ─────────────────────────────────────────────────

@pytest.mark.parametrize("asset", OPTION_ASSETS)
def test_option_pricing(client, asset):
    data = client.get_option_pricing(asset)
    assert "expiry_time" in data
    assert "call_options" in data
    assert "put_options" in data


# ─── Liquidation ────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", ALL_ASSETS)
def test_liquidation(client, asset):
    data = client.get_liquidation(asset)
    assert "data" in data
    assert isinstance(data["data"], list)
    if len(data["data"]) > 0:
        assert "price_change" in data["data"][0]


# ─── LP Bounds ──────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", ALL_ASSETS)
def test_lp_bounds(client, asset):
    data = client.get_lp_bounds(asset)
    assert "data" in data
    assert isinstance(data["data"], list)


# ─── LP Probabilities ───────────────────────────────────────────────

@pytest.mark.parametrize("asset", ALL_ASSETS)
def test_lp_probabilities(client, asset):
    data = client.get_lp_probabilities(asset)
    assert "data" in data


# ─── Polymarket ─────────────────────────────────────────────────────

def test_polymarket_daily(client):
    data = client.get_polymarket_daily()
    assert "current_price" in data
    assert "synth_probability_up" in data or "synth_probability" in data


def test_polymarket_hourly(client):
    data = client.get_polymarket_hourly()
    assert "current_price" in data


def test_polymarket_range(client):
    data = client.get_polymarket_range()
    assert isinstance(data, list)


# ─── Leaderboard ────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", ALL_ASSETS)
def test_leaderboard(client, asset):
    data = client.get_leaderboard(asset)
    assert isinstance(data, list)
    if len(data) > 0:
        assert "neuron_uid" in data[0]
        assert "rewards" in data[0]


def test_get_asset_summary_returns_current_price(client):
    summary = client.get_asset_summary("BTC", horizon="24h")
    assert summary["asset"] == "BTC"
    assert summary["horizon"] == "24h"
    assert "current_price" in summary
    assert isinstance(summary["current_price"], (int, float))


def test_get_asset_summary_rejects_unsupported_asset(client):
    import pytest

    with pytest.raises(ValueError):
        client.get_asset_summary("DOGE", horizon="24h")


def test_get_asset_summary_rejects_unsupported_horizon(client):
    import pytest

    with pytest.raises(ValueError):
        client.get_asset_summary("BTC", horizon="7d")
