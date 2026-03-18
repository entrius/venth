"""
Data processing module for the Tide Chart dashboard.

Fetches prediction percentiles and volatility for supported assets,
normalizes to percentage change, calculates comparison metrics,
ranks assets by forecast outlook, and computes target price probabilities.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

EQUITIES = ["SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]
CRYPTO_ASSETS = ["BTC", "ETH", "SOL", "XAU"]
PERCENTILE_KEYS = ["0.005", "0.05", "0.2", "0.35", "0.5", "0.65", "0.8", "0.95", "0.995"]
PERCENTILE_LEVELS = [0.005, 0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95, 0.995]


ALL_ASSETS = EQUITIES + CRYPTO_ASSETS


def get_assets_for_horizon(horizon: str) -> list[str]:
    """Return the list of supported assets for a given time horizon.

    Equities (SPY, NVDA, TSLA, AAPL, GOOGL) only support 24h.
    Crypto + XAU (BTC, ETH, SOL, XAU) support both 1h and 24h.
    The 24h horizon includes all assets.
    """
    if horizon == "1h":
        return list(CRYPTO_ASSETS)
    return list(ALL_ASSETS)


def fetch_all_data(client, horizon: str = "24h") -> dict:
    """Fetch prediction percentiles and volatility for all assets in a horizon.

    Args:
        client: SynthClient instance.
        horizon: "1h" or "24h".

    Returns:
        dict: {asset: {"percentiles": ..., "volatility": ..., "current_price": float}}
    """
    import time
    assets = get_assets_for_horizon(horizon)
    data = {}
    for i, asset in enumerate(assets):
        print(f"Fetching percentiles for {asset}...", flush=True)
        forecast = client.get_prediction_percentiles(asset, horizon=horizon)
        time.sleep(1.0)
        print(f"Fetching volatility for {asset}...", flush=True)
        vol = client.get_volatility(asset, horizon=horizon)
        time.sleep(1.0)
        print(f"Done fetching {asset}.", flush=True)
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
    """Calculate comparison metrics for each asset.

    Uses the final time step (end of forecast window) for metric computation.

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

        # Nominal (dollar) values
        median_move_nominal = final["0.5"] - current_price
        upside_nominal = final["0.95"] - current_price
        downside_nominal = current_price - final["0.05"]

        metrics[asset] = {
            "median_move": median_move,
            "upside": upside,
            "downside": downside,
            "skew": skew,
            "range_pct": range_pct,
            "volatility": info["average_volatility"],
            "current_price": current_price,
            "median_move_nominal": median_move_nominal,
            "upside_nominal": upside_nominal,
            "downside_nominal": downside_nominal,
            "skew_nominal": upside_nominal - downside_nominal,
            "range_nominal": upside_nominal + downside_nominal,
            "price_high": current_price + upside_nominal,
            "price_low": current_price - downside_nominal,
        }
    return metrics


def add_relative_to_benchmark(metrics) -> dict:
    """Add relative-to-benchmark fields for each asset.

    Uses SPY as benchmark for equities, BTC for crypto assets.

    Args:
        metrics: Dict from calculate_metrics().

    Returns:
        Same dict with added relative_median, relative_skew, and benchmark fields.
    """
    assets = list(metrics.keys())
    benchmark = "SPY" if "SPY" in metrics else assets[0]
    bench_m = metrics[benchmark]
    for asset, m in metrics.items():
        m["relative_median"] = m["median_move"] - bench_m["median_move"]
        m["relative_skew"] = m["skew"] - bench_m["skew"]
    return metrics, benchmark


def add_relative_to_spy(metrics):
    """Add relative-to-SPY fields for each equity (legacy wrapper)."""
    result, _ = add_relative_to_benchmark(metrics)
    return result


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
    """Get full normalized time series for all assets (for charting).

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


def calculate_target_probability(percentiles: list[dict], target_price: float) -> float:
    """Calculate the probability of an asset reaching a target price.

    Uses the final time step's percentile distribution and linear interpolation
    to estimate P(price <= target). Returns the probability as a percentage (0-100).

    Args:
        percentiles: List of percentile dicts (time steps). Uses the final step.
        target_price: The target price to evaluate.

    Returns:
        float: Probability (0-100) that the price will be at or below the target.
    """
    final_step = percentiles[-1]
    prices = [final_step[k] for k in PERCENTILE_KEYS]
    levels = PERCENTILE_LEVELS

    # Target below the lowest percentile
    if target_price <= prices[0]:
        return levels[0] * 100

    # Target above the highest percentile
    if target_price >= prices[-1]:
        return levels[-1] * 100

    # Linear interpolation between bracketing percentiles
    for i in range(len(prices) - 1):
        if prices[i] <= target_price <= prices[i + 1]:
            price_range = prices[i + 1] - prices[i]
            if price_range == 0:
                return levels[i] * 100
            fraction = (target_price - prices[i]) / price_range
            prob = levels[i] + fraction * (levels[i + 1] - levels[i])
            return prob * 100

    return 50.0
