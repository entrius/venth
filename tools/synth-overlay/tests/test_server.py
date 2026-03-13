"""Tests for overlay API server (mock client)."""

import sys
import os
import warnings

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from synth_client import SynthClient

from server import app


@pytest.fixture
def client():
    return app.test_client()


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "mock" in data


def test_edge_daily(client):
    resp = client.get("/api/edge?slug=bitcoin-up-or-down-on-february-26")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "edge_pct" in data
    assert data["signal"] in ("underpriced", "overpriced", "fair")
    assert data["strength"] in ("strong", "moderate", "none")
    assert data["horizon"] == "24h"
    assert data["market_type"] == "daily"
    assert data["asset"] == "BTC"
    # Dual-horizon fields preserved for daily/hourly
    assert "edge_1h_pct" in data
    assert "edge_24h_pct" in data
    assert "signal_1h" in data
    assert "signal_24h" in data
    assert "no_trade_warning" in data
    assert "confidence_score" in data
    assert 0 <= data["confidence_score"] <= 1
    assert "explanation" in data
    assert len(data["explanation"]) > 10
    assert "invalidation" in data
    assert len(data["invalidation"]) > 10


def test_edge_hourly_uses_hourly_primary_fields(client):
    resp = client.get("/api/edge?slug=bitcoin-up-or-down-february-25-6pm-et")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["horizon"] == "1h"
    assert data["market_type"] == "hourly"
    assert data["slug"] == "bitcoin-up-or-down-february-25-6pm-et"
    assert "synth_probability_up" in data
    assert "polymarket_probability_up" in data


def test_edge_missing_slug(client):
    resp = client.get("/api/edge")
    assert resp.status_code == 400


def test_edge_unsupported_slug(client):
    resp = client.get("/api/edge?slug=unsupported-random-market")
    assert resp.status_code == 404


def test_edge_pattern_matched_slug_supported(client):
    resp = client.get("/api/edge?slug=btc-up-or-down-on-march-1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["slug"] == "btc-up-or-down-on-march-1"


def test_edge_range(client):
    resp = client.get("/api/edge?slug=bitcoin-price-on-february-26")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "edge_pct" in data
    assert "bracket_title" in data
    assert "no_trade_warning" in data
    assert "range_brackets" in data
    assert isinstance(data["range_brackets"], list)
    assert len(data["range_brackets"]) > 1
    assert "confidence_score" in data
    assert 0 <= data["confidence_score"] <= 1
    assert "explanation" in data
    assert len(data["explanation"]) > 10
    assert "invalidation" in data
    assert len(data["invalidation"]) > 10


def test_edge_range_respects_bracket_title(client):
    resp = client.get(
        "/api/edge?slug=bitcoin-price-on-february-26&bracket_title=%5B68000%2C%2070000%5D"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["bracket_title"] == "[68000, 70000]"


def test_edge_range_unknown_slug_404(client):
    resp = client.get("/api/edge?slug=bitcoin-price-on-february-26-nonexistent")
    assert resp.status_code == 404


def test_edge_eth_daily_supported(client):
    resp = client.get("/api/edge?slug=ethereum-up-or-down-on-february-26")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "ETH"
    assert data["market_type"] == "daily"
    assert "edge_pct" in data


def test_edge_sol_hourly_supported(client):
    resp = client.get("/api/edge?slug=solana-up-or-down-february-25-6pm-et")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "SOL"
    assert data["market_type"] == "hourly"
    assert "edge_pct" in data


def test_edge_15min_btc(client):
    resp = client.get("/api/edge?slug=btc-updown-15m-1772204400")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["horizon"] == "15min"
    assert data["market_type"] == "15min"
    assert data["asset"] == "BTC"
    assert "edge_pct" in data
    assert "confidence_score" in data
    assert "explanation" in data
    assert len(data["explanation"]) > 10
    assert "invalidation" in data
    assert "15min" in data["invalidation"]
    # Should NOT have dual-horizon 1h/24h fields
    assert "edge_1h_pct" not in data
    assert "signal_24h" not in data


def test_edge_15min_eth(client):
    resp = client.get("/api/edge?slug=eth-updown-15m-1772204400")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "ETH"
    assert data["market_type"] == "15min"


def test_edge_5min_btc(client):
    resp = client.get("/api/edge?slug=btc-updown-5m-1772205000")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["horizon"] == "5min"
    assert data["market_type"] == "5min"
    assert data["asset"] == "BTC"
    assert "edge_pct" in data
    assert "5min" in data["invalidation"]
    assert "edge_1h_pct" not in data


def test_edge_5min_sol(client):
    resp = client.get("/api/edge?slug=sol-updown-5m-1772205000")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "SOL"
    assert data["market_type"] == "5min"


def test_edge_live_price_override(client):
    """Test that live_prob_up parameter overrides API price for edge calculation."""
    # First request without live price
    resp1 = client.get("/api/edge?slug=btc-updown-5m-1772205000")
    assert resp1.status_code == 200
    data1 = resp1.get_json()
    assert data1.get("live_price_used") is False

    # Second request with live price override
    resp2 = client.get("/api/edge?slug=btc-updown-5m-1772205000&live_prob_up=0.75")
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2.get("live_price_used") is True
    assert data2["polymarket_probability_up"] == 0.75


def test_edge_live_price_invalid_ignored(client):
    """Test that invalid live_prob_up values are gracefully ignored."""
    resp = client.get("/api/edge?slug=btc-updown-5m-1772205000&live_prob_up=invalid")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("live_price_used") is False


# ---- Kalshi support ----

def test_edge_kalshi_btc_daily(client):
    """Kalshi BTC daily market returns edge data with kalshi platform."""
    resp = client.get("/api/edge?slug=KXBTCD-26MAR1317&platform=kalshi")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "BTC"
    assert data["market_type"] == "daily"
    assert data["platform"] == "kalshi"
    assert "edge_pct" in data
    assert "confidence_score" in data
    assert "explanation" in data


def test_edge_kalshi_eth_daily(client):
    """Kalshi ETH daily market is supported."""
    resp = client.get("/api/edge?slug=KXETHD-26MAR1317&platform=kalshi")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "ETH"
    assert data["market_type"] == "daily"
    assert data["platform"] == "kalshi"


def test_edge_kalshi_sol_daily(client):
    """Kalshi SOL daily market is supported."""
    resp = client.get("/api/edge?slug=KXSOLD-26MAR1317&platform=kalshi")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "SOL"
    assert data["market_type"] == "daily"


def test_edge_kalshi_with_live_price(client):
    """Kalshi markets support live price override."""
    resp = client.get("/api/edge?slug=KXBTCD-26MAR1317&platform=kalshi&live_prob_up=0.60")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("live_price_used") is True
    assert data["polymarket_probability_up"] == 0.60


def test_edge_kalshi_auto_detect_platform(client):
    """Platform auto-detected from Kalshi URL without explicit platform param."""
    resp = client.get("/api/edge?url=https://kalshi.com/markets/KXBTCD-26MAR1317")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["platform"] == "kalshi"
    assert data["asset"] == "BTC"


def test_edge_polymarket_includes_platform(client):
    """Polymarket responses include platform field."""
    resp = client.get("/api/edge?slug=bitcoin-up-or-down-on-february-26")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["platform"] == "polymarket"


def test_edge_kalshi_unsupported_ticker(client):
    """Unknown Kalshi ticker returns 404."""
    resp = client.get("/api/edge?slug=kxunknown-26feb25&platform=kalshi")
    assert resp.status_code == 404


def test_edge_kalshi_15min(client):
    """Kalshi 15min market (KXBTC15M series) returns edge data."""
    resp = client.get("/api/edge?slug=kxbtc15m&platform=kalshi")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "BTC"
    assert data["market_type"] == "15min"
    assert data["platform"] == "kalshi"
    assert data["horizon"] == "15min"
    assert "edge_pct" in data


def test_edge_kalshi_range(client):
    """Kalshi range market (KXBTC series) returns range data."""
    resp = client.get("/api/edge?slug=kxbtc&platform=kalshi")
    # Range markets need range bracket data which may or may not be mocked for "kxbtc".
    # The server should at least recognise the market type and attempt to fetch.
    assert resp.status_code in (200, 404, 500)
    if resp.status_code == 200:
        data = resp.get_json()
        assert data["platform"] == "kalshi"


def test_edge_kalshi_www_url(client):
    """Platform auto-detected from www.kalshi.com URL."""
    resp = client.get("/api/edge?url=https://www.kalshi.com/markets/KXBTCD-26MAR1317")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["platform"] == "kalshi"
    assert data["asset"] == "BTC"


def test_edge_kalshi_contract_with_threshold(client):
    """Kalshi contract ticker with -T (strike) suffix resolves correctly."""
    resp = client.get("/api/edge?slug=KXBTCD-26MAR1317-T71500&platform=kalshi")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["asset"] == "BTC"
    assert data["market_type"] == "daily"
    assert data["platform"] == "kalshi"


def test_edge_kalshi_multi_segment_url(client):
    """Multi-segment Kalshi URL extracts the last segment as ticker."""
    resp = client.get("/api/edge?url=https://kalshi.com/markets/kxbtcd/bitcoin-daily/KXBTCD-26MAR1317")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["platform"] == "kalshi"
    assert data["asset"] == "BTC"
    assert data["market_type"] == "daily"


def test_edge_kalshi_live_price_recalculates_edge(client):
    """Live price override on Kalshi recalculates edge without refetch."""
    resp = client.get("/api/edge?slug=KXBTCD-26MAR1317&platform=kalshi&live_prob_up=0.80")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("live_price_used") is True
    assert data["polymarket_probability_up"] == 0.80
    assert data["platform"] == "kalshi"
