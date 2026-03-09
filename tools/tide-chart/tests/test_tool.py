import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
# Add tool directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

"""
Tests for the Tide Chart tool.

All tests run against mock data (no API key needed).
They verify data fetching, normalization, metric calculation,
ranking, dashboard generation, horizon toggling, probability
calculation, and Flask API endpoints.
"""

import json
import warnings

from synth_client import SynthClient
from chart import (
    CRYPTO_ASSETS,
    ALL_ASSETS,
    PERCENTILE_KEYS,
    PERCENTILE_LEVELS,
    fetch_all_data,
    normalize_percentiles,
    calculate_metrics,
    add_relative_to_spy,
    add_relative_to_benchmark,
    rank_equities,
    get_normalized_series,
    get_assets_for_horizon,
    calculate_target_probability,
)
from main import (
    generate_dashboard_html, create_app, build_insights, make_time_points,
    validate_trade_params, GTRADE_CONFIG, TRADEABLE_ASSETS, ASSET_GROUPS,
    get_asset_leverage_limits,
)


def _make_client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return SynthClient()


def test_client_loads_in_mock_mode():
    """Verify the client initializes in mock mode without an API key."""
    client = _make_client()
    assert client.mock_mode is True


def test_fetch_all_equities_data():
    """Verify fetch_all_data returns data for all 9 assets (24h default)."""
    client = _make_client()
    data = fetch_all_data(client)

    assert len(data) == 9
    for asset in ALL_ASSETS:
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

    for asset in ALL_ASSETS:
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

    for asset in ALL_ASSETS:
        m = metrics[asset]
        assert abs(m["skew"] - (m["upside"] - m["downside"])) < 1e-10


def test_calculate_metrics_range():
    """Verify range = upside + downside."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)

    for asset in ALL_ASSETS:
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

    for asset in ALL_ASSETS:
        if asset == "SPY":
            continue
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

    assert len(ranked) == 9
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

    assert len(series) == 9
    for asset in ALL_ASSETS:
        assert asset in series
        assert len(series[asset]) == 289
        # First step should be near 0 (current price normalized)
        first = series[asset][0]
        assert "0.5" in first


def test_generate_dashboard_html():
    """Verify dashboard HTML generation produces valid output."""
    client = _make_client()
    html = generate_dashboard_html(client)

    assert isinstance(html, str)
    assert "<!DOCTYPE html>" in html
    assert "Tide Chart" in html
    assert "plotly" in html.lower()
    # Check all asset tickers appear (default 24h = all assets)
    for asset in ALL_ASSETS:
        assert asset in html
    # Check table has rows
    assert "<tr>" in html
    assert "cone-chart" in html
    # Check sortable table headers
    assert "sortable" in html
    assert "data-sort" in html
    # Check nominal values are displayed
    assert "nominal" in html
    assert "$" in html
    # Check legendgroup is set for trace grouping
    assert "legendgroup" in html
    # Check Bounds column
    assert "data-sort=\"bounds\"" in html
    # Check column header tooltips
    assert "data-tip=" in html
    assert "50th percentile" in html
    # Check time-based x-axis
    assert "Time (ET)" in html
    assert "%I:%M %p" in html
    # Check legend toggle hint and rescale handler
    assert "click legend to toggle assets" in html
    assert "plotly_legendclick" in html
    assert "yaxis.autorange" in html
    # Check tooltip focus support
    assert "data-tip]:focus-visible::after" in html
    # Check new interactive elements
    assert "horizon-toggle" in html
    assert "Intraday (1H)" in html
    assert "Next Day (24H)" in html
    assert "Probability Calculator" in html
    assert "calc-asset" in html
    assert "calc-price" in html
    assert "auto-refresh" in html.lower()
    assert "/api/data" in html
    assert "/api/probability" in html


def test_calculate_metrics_nominal_values():
    """Verify nominal dollar values are computed correctly."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)

    for asset in ALL_ASSETS:
        m = metrics[asset]
        final = data[asset]["percentiles"][-1]
        cp = data[asset]["current_price"]

        assert "median_move_nominal" in m
        assert "upside_nominal" in m
        assert "downside_nominal" in m
        assert "skew_nominal" in m
        assert "range_nominal" in m

        assert abs(m["median_move_nominal"] - (final["0.5"] - cp)) < 1e-10
        assert abs(m["upside_nominal"] - (final["0.95"] - cp)) < 1e-10
        assert abs(m["downside_nominal"] - (cp - final["0.05"])) < 1e-10
        assert abs(m["skew_nominal"] - (m["upside_nominal"] - m["downside_nominal"])) < 1e-10
        assert abs(m["range_nominal"] - (m["upside_nominal"] + m["downside_nominal"])) < 1e-10


def test_calculate_metrics_projection_bounds():
    """Verify price_high and price_low are computed correctly."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)

    for asset in ALL_ASSETS:
        m = metrics[asset]
        final = data[asset]["percentiles"][-1]

        assert "price_high" in m
        assert "price_low" in m
        assert abs(m["price_high"] - final["0.95"]) < 1e-10
        assert abs(m["price_low"] - final["0.05"]) < 1e-10
        assert m["price_high"] >= m["price_low"]


def test_volatility_values():
    """Verify volatility values are positive floats."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)

    for asset in ALL_ASSETS:
        assert metrics[asset]["volatility"] > 0
        assert isinstance(metrics[asset]["volatility"], float)


# --- New tests for issue #12 interactive features ---


def test_get_assets_for_horizon_24h():
    """Verify 24h horizon returns all assets (equities + crypto)."""
    assets = get_assets_for_horizon("24h")
    assert assets == ALL_ASSETS


def test_get_assets_for_horizon_1h():
    """Verify 1h horizon returns crypto assets."""
    assets = get_assets_for_horizon("1h")
    assert assets == CRYPTO_ASSETS


def test_fetch_all_data_1h_horizon():
    """Verify fetch_all_data returns crypto data for 1h horizon."""
    client = _make_client()
    data = fetch_all_data(client, horizon="1h")

    assert len(data) == len(CRYPTO_ASSETS)
    for asset in CRYPTO_ASSETS:
        assert asset in data
        assert "current_price" in data[asset]
        assert "percentiles" in data[asset]
        assert "average_volatility" in data[asset]
        assert isinstance(data[asset]["percentiles"], list)
        assert len(data[asset]["percentiles"]) > 0


def test_add_relative_to_benchmark_equities():
    """Verify benchmark is SPY for 24h (all assets)."""
    client = _make_client()
    data = fetch_all_data(client, horizon="24h")
    metrics = calculate_metrics(data)
    metrics, benchmark = add_relative_to_benchmark(metrics)

    assert benchmark == "SPY"
    assert metrics["SPY"]["relative_median"] == 0.0
    assert metrics["SPY"]["relative_skew"] == 0.0


def test_add_relative_to_benchmark_crypto():
    """Verify benchmark is BTC for crypto assets."""
    client = _make_client()
    data = fetch_all_data(client, horizon="1h")
    metrics = calculate_metrics(data)
    metrics, benchmark = add_relative_to_benchmark(metrics)

    assert benchmark == "BTC"
    assert metrics["BTC"]["relative_median"] == 0.0
    assert metrics["BTC"]["relative_skew"] == 0.0


def test_calculate_target_probability_within_range():
    """Verify probability calculation returns value between bounds."""
    client = _make_client()
    data = fetch_all_data(client, horizon="24h")
    percentiles = data["SPY"]["percentiles"]
    current_price = data["SPY"]["current_price"]

    prob = calculate_target_probability(percentiles, current_price)
    assert 0 < prob < 100


def test_calculate_target_probability_extreme_low():
    """Verify probability for very low target clamps to lowest level."""
    client = _make_client()
    data = fetch_all_data(client, horizon="24h")
    percentiles = data["SPY"]["percentiles"]

    prob = calculate_target_probability(percentiles, 0.01)
    assert prob == PERCENTILE_LEVELS[0] * 100


def test_calculate_target_probability_extreme_high():
    """Verify probability for very high target clamps to highest level."""
    client = _make_client()
    data = fetch_all_data(client, horizon="24h")
    percentiles = data["SPY"]["percentiles"]

    prob = calculate_target_probability(percentiles, 999999.0)
    assert prob == PERCENTILE_LEVELS[-1] * 100


def test_calculate_target_probability_interpolation():
    """Verify linear interpolation with synthetic data."""
    # Construct a minimal percentile step
    step = {k: float(i + 1) * 10 for i, k in enumerate(PERCENTILE_KEYS)}
    # step: {"0.005": 10, "0.05": 20, "0.2": 30, ...}
    percentiles = [step]

    # Target exactly at a percentile boundary
    prob = calculate_target_probability(percentiles, 20.0)
    assert abs(prob - PERCENTILE_LEVELS[1] * 100) < 1e-6  # 5.0

    # Target midway between 2nd and 3rd percentile (20.0 and 30.0)
    midpoint = 25.0
    prob = calculate_target_probability(percentiles, midpoint)
    expected = (PERCENTILE_LEVELS[1] + 0.5 * (PERCENTILE_LEVELS[2] - PERCENTILE_LEVELS[1])) * 100
    assert abs(prob - expected) < 1e-6


def test_make_time_points_24h():
    """Verify 24h generates 289 time points."""
    points = make_time_points("24h")
    assert len(points) == 289


def test_make_time_points_1h():
    """Verify 1h generates 61 time points."""
    points = make_time_points("1h")
    assert len(points) == 61


def test_build_insights():
    """Verify insight card data structure."""
    client = _make_client()
    data = fetch_all_data(client)
    metrics = calculate_metrics(data)
    ins = build_insights(metrics)

    assert "alignment_text" in ins
    assert "alignment_class" in ins
    assert "widest_name" in ins
    assert "skew_name" in ins
    assert ins["alignment_class"] in ("bullish", "bearish", "mixed")


def test_flask_index_route():
    """Verify Flask index route returns HTML."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.get("/")
        assert resp.status_code == 200
        assert b"Tide Chart" in resp.data
        assert b"<!DOCTYPE html>" in resp.data


def test_flask_api_data_24h():
    """Verify /api/data returns valid JSON for 24h."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.get("/api/data?horizon=24h")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "traces" in data
        assert "table_rows" in data
        assert "insights" in data
        assert "assets" in data
        assert data["horizon"] == "24h"
        assert data["benchmark"] == "SPY"


def test_flask_api_data_1h():
    """Verify /api/data returns valid JSON for 1h."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.get("/api/data?horizon=1h")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["horizon"] == "1h"
        assert data["benchmark"] == "BTC"
        assert "BTC" in data["assets"]


def test_flask_api_data_invalid_horizon():
    """Verify /api/data rejects invalid horizon."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.get("/api/data?horizon=7d")
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data


def test_flask_api_probability_valid():
    """Verify /api/probability returns correct structure."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.post("/api/probability",
                       data=json.dumps({"asset": "SPY", "target_price": 600.0, "horizon": "24h"}),
                       content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "probability_below" in data
        assert "probability_above" in data
        assert "current_price" in data
        assert abs(data["probability_below"] + data["probability_above"] - 100.0) < 0.01


def test_flask_api_probability_invalid_asset():
    """Verify /api/probability rejects asset not in horizon."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.post("/api/probability",
                       data=json.dumps({"asset": "SPY", "target_price": 600.0, "horizon": "1h"}),
                       content_type="application/json")
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "not available" in data["error"]


def test_flask_api_probability_invalid_price():
    """Verify /api/probability rejects non-positive price."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.post("/api/probability",
                       data=json.dumps({"asset": "SPY", "target_price": -10, "horizon": "24h"}),
                       content_type="application/json")
        assert resp.status_code == 400


def test_flask_api_probability_missing_body():
    """Verify /api/probability handles missing JSON body."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.post("/api/probability", content_type="application/json")
        assert resp.status_code == 400


# --- Tests for issue #14 wallet and trading integration ---


def test_dashboard_has_wallet_button():
    """Verify wallet connect button is present in dashboard HTML."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert 'id="wallet-btn"' in html
    assert "Connect Wallet" in html


def test_dashboard_has_trade_panel():
    """Verify trade panel element exists in dashboard HTML."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert 'id="trade-panel"' in html
    assert "trade-form-grid" in html
    assert "trade-connect-overlay" in html


def test_dashboard_has_ethers_cdn():
    """Verify ethers.js CDN script tag is included."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert "cdn.jsdelivr.net" in html
    assert "ethers" in html


def test_dashboard_has_trade_buttons_for_all_assets():
    """Verify trade buttons appear for all supported assets."""
    client = _make_client()
    html = generate_dashboard_html(client)
    for asset in ["BTC", "ETH", "SOL", "XAU", "SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]:
        assert f'data-asset="{asset}"' in html


def test_dashboard_has_toast_container():
    """Verify toast notification container exists."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert 'id="toast-container"' in html


def test_dashboard_has_gtrade_config():
    """Verify gTrade configuration is embedded in dashboard JS."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert "GTRADE_CONFIG" in html
    assert "0xFF162c694eAA571f685030649814282eA457f169" in html
    assert "0xaf88d065e77c8cC2239327C5EDb3A432268e5831" in html
    assert "42161" in html


def test_dashboard_has_wallet_manager():
    """Verify wallet management functions exist in dashboard JS."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert "handleWalletConnect" in html
    assert "switchToArbitrum" in html
    assert "walletState" in html
    assert "eth_requestAccounts" in html
    assert "wallet_switchEthereumChain" in html
    assert "wallet_addEthereumChain" in html


def test_dashboard_has_trade_execution():
    """Verify trade execution functions and ABIs exist in dashboard JS."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert "submitTrade" in html
    assert "openTradePanel" in html
    assert "closeTradePanel" in html
    assert "DIAMOND_ABI" in html
    assert "ERC20_ABI" in html


def test_table_rows_have_trade_column():
    """Verify table rows include trade button cells for all assets."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.get("/api/data?horizon=24h")
        data = json.loads(resp.data)
        assert 'trade-cell-btn' in data["table_rows"]
        assert 'data-asset="SPY"' in data["table_rows"]
        assert 'data-asset="BTC"' in data["table_rows"]


# --- Tests for server-side trade validation and config ---


def test_validate_trade_params_valid():
    """Verify valid trade params produce no errors."""
    errors = validate_trade_params({"asset": "SPY", "collateral": 100, "leverage": 15})
    assert errors == []


def test_validate_trade_params_invalid_asset():
    """Verify unsupported asset is rejected."""
    errors = validate_trade_params({"asset": "DOGE", "collateral": 100, "leverage": 15})
    assert any("not supported" in e for e in errors)


def test_validate_trade_params_below_min_position():
    """Verify position below minimum size is rejected."""
    errors = validate_trade_params({"asset": "SPY", "collateral": 10, "leverage": 2})
    assert any("below minimum" in e for e in errors)


def test_validate_trade_params_leverage_out_of_range():
    """Verify leverage outside allowed range is rejected per asset group."""
    # SPY is an index — max 50x
    errors_high = validate_trade_params({"asset": "SPY", "collateral": 1000, "leverage": 51})
    assert any("Leverage" in e for e in errors_high)

    errors_low = validate_trade_params({"asset": "SPY", "collateral": 1000, "leverage": 1})
    assert any("Leverage" in e for e in errors_low)

    # BTC is crypto — max 500x, so 200x should be valid
    errors_btc = validate_trade_params({"asset": "BTC", "collateral": 100, "leverage": 200})
    assert errors_btc == []

    # NVDA is stocks — max 50x
    errors_nvda = validate_trade_params({"asset": "NVDA", "collateral": 1000, "leverage": 51})
    assert any("Leverage" in e for e in errors_nvda)

    # XAU is commodities — min 2x, max 250x
    errors_xau_low = validate_trade_params({"asset": "XAU", "collateral": 1000, "leverage": 1.5})
    assert any("Leverage" in e for e in errors_xau_low)

    errors_xau_ok = validate_trade_params({"asset": "XAU", "collateral": 100, "leverage": 100})
    assert errors_xau_ok == []


def test_validate_trade_params_bad_collateral():
    """Verify negative collateral is rejected."""
    errors = validate_trade_params({"asset": "SPY", "collateral": -5, "leverage": 15})
    assert any("positive" in e for e in errors)


def test_validate_trade_params_bad_slippage():
    """Verify slippage outside allowed range is rejected."""
    errors = validate_trade_params({"asset": "SPY", "collateral": 100, "leverage": 15, "slippage": 10})
    assert any("Slippage" in e for e in errors)


def test_flask_api_gtrade_config():
    """Verify /api/gtrade/config returns correct configuration with asset groups."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.get("/api/gtrade/config")
        cfg = json.loads(resp.data)
        assert cfg["chain_id"] == 42161
        assert cfg["pair_indices"]["SPY"] == 86
        assert cfg["pair_indices"]["BTC"] == 0
        assert cfg["pair_indices"]["XAU"] == 90
        assert "diamond_address" in cfg
        assert "gtrade_app_url" in cfg
        assert cfg["asset_groups"]["crypto"]["max_leverage"] == 500
        assert cfg["asset_groups"]["stocks"]["max_leverage"] == 50
        assert cfg["asset_groups"]["commodities"]["max_leverage"] == 250


def test_flask_api_gtrade_validate_valid():
    """Verify /api/gtrade/validate-trade accepts valid trade."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        resp = tc.post(
            "/api/gtrade/validate-trade",
            data=json.dumps({"asset": "SPY", "collateral": 100, "leverage": 15}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["valid"] is True
        assert data["pair_index"] == 86


def test_flask_api_gtrade_validate_invalid():
    """Verify /api/gtrade/validate-trade rejects invalid trade."""
    client = _make_client()
    app = create_app(client)
    with app.test_client() as tc:
        # DOGE is unsupported, collateral 5 with leverage 200 is below min position
        resp = tc.post(
            "/api/gtrade/validate-trade",
            data=json.dumps({"asset": "DOGE", "collateral": 5, "leverage": 200}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["valid"] is False
        assert len(data["errors"]) >= 1


def test_gtrade_config_matches_tradeable_assets():
    """Verify GTRADE_CONFIG pair indices and asset groups both cover TRADEABLE_ASSETS."""
    assert set(GTRADE_CONFIG["pair_indices"].keys()) == TRADEABLE_ASSETS
    grouped = set()
    for info in ASSET_GROUPS.values():
        grouped.update(info["assets"])
    assert grouped == TRADEABLE_ASSETS


def test_get_asset_leverage_limits():
    """Verify per-group leverage limits are returned correctly."""
    assert get_asset_leverage_limits("BTC") == (1.1, 500)
    assert get_asset_leverage_limits("ETH") == (1.1, 500)
    assert get_asset_leverage_limits("SOL") == (1.1, 500)
    assert get_asset_leverage_limits("SPY") == (1.1, 50)
    assert get_asset_leverage_limits("NVDA") == (1.1, 50)
    assert get_asset_leverage_limits("XAU") == (2, 250)
    # Unknown asset gets safe defaults
    assert get_asset_leverage_limits("DOGE") == (1.1, 50)


def test_dashboard_has_asset_groups_js():
    """Verify dashboard JS includes per-group leverage constraints."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert "assetGroups" in html
    assert "getAssetLimits" in html
    assert "maxLeverage: 500" in html
    assert "maxLeverage: 50" in html
    assert "maxLeverage: 250" in html


def test_dashboard_has_gtrade_fallback_link():
    """Verify gTrade fallback link appears in error handler."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert "gains.trade/trading" in html
    assert "Try on gTrade" in html


def test_dashboard_calls_server_validation():
    """Verify submitTrade calls /api/gtrade/validate-trade before on-chain tx."""
    client = _make_client()
    html = generate_dashboard_html(client)
    assert "/api/gtrade/validate-trade" in html
    assert "valData.valid" in html
    assert "valData.pair_index" in html


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
    test_calculate_metrics_nominal_values()
    test_calculate_metrics_projection_bounds()
    test_generate_dashboard_html()
    test_volatility_values()
    test_get_assets_for_horizon_24h()
    test_get_assets_for_horizon_1h()
    test_fetch_all_data_1h_horizon()
    test_add_relative_to_benchmark_equities()
    test_add_relative_to_benchmark_crypto()
    test_calculate_target_probability_within_range()
    test_calculate_target_probability_extreme_low()
    test_calculate_target_probability_extreme_high()
    test_calculate_target_probability_interpolation()
    test_make_time_points_24h()
    test_make_time_points_1h()
    test_build_insights()
    test_flask_index_route()
    test_flask_api_data_24h()
    test_flask_api_data_1h()
    test_flask_api_data_invalid_horizon()
    test_flask_api_probability_valid()
    test_flask_api_probability_invalid_asset()
    test_flask_api_probability_invalid_price()
    test_flask_api_probability_missing_body()
    test_dashboard_has_wallet_button()
    test_dashboard_has_trade_panel()
    test_dashboard_has_ethers_cdn()
    test_dashboard_has_trade_buttons_for_all_assets()
    test_dashboard_has_toast_container()
    test_dashboard_has_gtrade_config()
    test_dashboard_has_wallet_manager()
    test_dashboard_has_trade_execution()
    test_table_rows_have_trade_column()
    test_validate_trade_params_valid()
    test_validate_trade_params_invalid_asset()
    test_validate_trade_params_below_min_position()
    test_validate_trade_params_leverage_out_of_range()
    test_validate_trade_params_bad_collateral()
    test_validate_trade_params_bad_slippage()
    test_flask_api_gtrade_config()
    test_flask_api_gtrade_validate_valid()
    test_flask_api_gtrade_validate_invalid()
    test_gtrade_config_matches_tradeable_assets()
    test_get_asset_leverage_limits()
    test_dashboard_has_asset_groups_js()
    test_dashboard_has_gtrade_fallback_link()
    test_dashboard_calls_server_validation()
    print("All tests passed!")
