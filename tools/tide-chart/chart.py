"""
Data processing module for the Tide Chart dashboard.

Fetches prediction percentiles and volatility for 5 equities,
normalizes to percentage change, calculates comparison metrics,
and ranks equities by forecast outlook.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from synth_client import SynthClient

EQUITIES = ["SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]
PERCENTILE_KEYS = ["0.005", "0.05", "0.2", "0.35", "0.5", "0.65", "0.8", "0.95", "0.995"]


def fetch_all_data(client):
    """Fetch prediction percentiles and volatility for all 5 equities.

    Returns:
        dict: {asset: {"percentiles": ..., "volatility": ..., "current_price": float}}
    """
    data = {}
    for asset in EQUITIES:
        forecast = client.get_prediction_percentiles(asset, horizon="24h")
        vol = client.get_volatility(asset, horizon="24h")
        data[asset] = {
            "current_price": forecast["current_price"],
            "percentiles": forecast["forecast_future"]["percentiles"],
            "average_volatility": vol["forecast_future"]["average_volatility"],
        }
    return data


def normalize_percentiles(percentiles, current_price):
    """Convert raw price percentiles to percentage change from current price.

    Args:
        percentiles: List of dicts (289 time steps), each with percentile keys.
        current_price: Current asset price.

    Returns:
        List of dicts with same keys but values as % change.
    """
    normalized = []
    for step in percentiles:
        norm_step = {}
        for key in PERCENTILE_KEYS:
            if key in step:
                norm_step[key] = (step[key] - current_price) / current_price * 100
        normalized.append(norm_step)
    return normalized


def calculate_metrics(data):
    """Calculate comparison metrics for each equity.

    Uses the final time step (end of 24h window) for metric computation.

    Args:
        data: Dict from fetch_all_data().

    Returns:
        dict: {asset: {median_move, upside, downside, skew, range_pct,
                        volatility, current_price}}
    """
    metrics = {}
    for asset, info in data.items():
        current_price = info["current_price"]
        final = info["percentiles"][-1]

        median_move = (final["0.5"] - current_price) / current_price * 100
        upside = (final["0.95"] - current_price) / current_price * 100
        downside = (current_price - final["0.05"]) / current_price * 100
        skew = upside - downside
        range_pct = upside + downside

        metrics[asset] = {
            "median_move": median_move,
            "upside": upside,
            "downside": downside,
            "skew": skew,
            "range_pct": range_pct,
            "volatility": info["average_volatility"],
            "current_price": current_price,
        }
    return metrics


def add_relative_to_spy(metrics):
    """Add relative-to-SPY fields for each equity.

    Args:
        metrics: Dict from calculate_metrics().

    Returns:
        Same dict with added relative_median and relative_skew fields.
    """
    spy = metrics["SPY"]
    for asset, m in metrics.items():
        m["relative_median"] = m["median_move"] - spy["median_move"]
        m["relative_skew"] = m["skew"] - spy["skew"]
    return metrics


def rank_equities(metrics, sort_by="median_move", ascending=False):
    """Rank equities by a given metric.

    Args:
        metrics: Dict from calculate_metrics() with relative fields.
        sort_by: Metric key to sort by.
        ascending: Sort direction.

    Returns:
        List of (asset, metrics_dict) tuples, sorted by sort_by.
    """
    items = list(metrics.items())
    items.sort(key=lambda x: x[1][sort_by], reverse=not ascending)
    return items


def get_normalized_series(data):
    """Get full normalized time series for all equities (for charting).

    Args:
        data: Dict from fetch_all_data().

    Returns:
        dict: {asset: list of normalized percentile dicts}
    """
    series = {}
    for asset, info in data.items():
        series[asset] = normalize_percentiles(
            info["percentiles"], info["current_price"]
        )
    return series
