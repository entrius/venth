"""Tests for market slug / URL matcher — Polymarket + Kalshi platform registry."""

import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from matcher import (
    asset_from_kalshi_ticker,
    asset_from_slug,
    detect_platform,
    get_kalshi_market_type,
    get_market_type,
    is_supported,
    normalize_slug,
    resolve,
    registry,
    ERR_INVALID_INPUT,
    ERR_NORMALIZE_FAILED,
    ERR_UNKNOWN_PLATFORM,
    ERR_UNSUPPORTED_MARKET,
    PLATFORM_KALSHI,
    PLATFORM_POLYMARKET,
    Platform,
    PlatformRegistry,
    ResolveResult,
)


# ═══════════════════════════════════════════════════════════════════════
# Polymarket — slug normalization
# ═══════════════════════════════════════════════════════════════════════

def test_normalize_slug_from_url():
    assert normalize_slug("https://polymarket.com/event/bitcoin-up-or-down-on-february-26") == "bitcoin-up-or-down-on-february-26"
    assert normalize_slug("https://polymarket.com/market/bitcoin-price-on-february-26") == "bitcoin-price-on-february-26"


def test_normalize_slug_passthrough():
    assert normalize_slug("bitcoin-up-or-down-on-february-26") == "bitcoin-up-or-down-on-february-26"


def test_normalize_slug_invalid():
    assert normalize_slug("") is None
    assert normalize_slug(None) is None


# ═══════════════════════════════════════════════════════════════════════
# Polymarket — market type
# ═══════════════════════════════════════════════════════════════════════

def test_get_market_type_daily():
    assert get_market_type("bitcoin-up-or-down-on-february-26") == "daily"
    assert get_market_type("btc-up-or-down-on-march-1") == "daily"


def test_get_market_type_hourly():
    assert get_market_type("bitcoin-up-or-down-february-25-6pm-et") == "hourly"
    assert get_market_type("bitcoin-up-or-down-february-26-10am-et") == "hourly"
    assert get_market_type("btc-up-or-down-march-1-3pm-et") == "hourly"


def test_get_market_type_15min():
    assert get_market_type("btc-updown-15m-1772204400") == "15min"
    assert get_market_type("eth-updown-15m-1772204400") == "15min"
    assert get_market_type("sol-up-down-15m-1772204400") == "15min"
    assert get_market_type("bitcoin-15min-market") == "15min"


def test_get_market_type_5min():
    assert get_market_type("btc-updown-5m-1772205000") == "5min"
    assert get_market_type("eth-updown-5m-1772205000") == "5min"
    assert get_market_type("sol-up-down-5m-1772205000") == "5min"
    assert get_market_type("bitcoin-5min-market") == "5min"


def test_get_market_type_range():
    assert get_market_type("bitcoin-price-on-february-26") == "range"


def test_get_market_type_unsupported():
    assert get_market_type("random-slug") is None


def test_is_supported():
    assert is_supported("bitcoin-up-or-down-on-february-26") is True
    assert is_supported("bitcoin-price-on-february-26") is True
    assert is_supported("btc-updown-15m-1772204400") is True
    assert is_supported("eth-updown-5m-1772205000") is True
    assert is_supported("unknown-market") is False


# ═══════════════════════════════════════════════════════════════════════
# Polymarket — asset extraction
# ═══════════════════════════════════════════════════════════════════════

def test_asset_from_slug():
    assert asset_from_slug("bitcoin-up-or-down-on-february-26") == "BTC"
    assert asset_from_slug("ethereum-up-or-down-on-february-28") == "ETH"
    assert asset_from_slug("solana-up-or-down-on-march-1") == "SOL"
    assert asset_from_slug("xrp-up-or-down-on-march-1") == "XRP"


def test_asset_from_slug_short_prefixes():
    assert asset_from_slug("btc-up-or-down-on-march-1") == "BTC"
    assert asset_from_slug("eth-updown-15m-1772204400") == "ETH"
    assert asset_from_slug("sol-updown-5m-1772205000") == "SOL"


def test_asset_from_slug_unknown():
    assert asset_from_slug("random-slug") is None
    assert asset_from_slug("") is None
    assert asset_from_slug(None) is None


# ═══════════════════════════════════════════════════════════════════════
# Platform detection
# ═══════════════════════════════════════════════════════════════════════

def test_detect_platform_polymarket():
    assert detect_platform("https://polymarket.com/event/bitcoin-up-or-down-on-february-26") == PLATFORM_POLYMARKET
    assert detect_platform("bitcoin-up-or-down-on-february-26") == PLATFORM_POLYMARKET


def test_detect_platform_kalshi():
    assert detect_platform("https://kalshi.com/markets/kxbtcd") == PLATFORM_KALSHI
    assert detect_platform("https://kalshi.com/events/KXBTCD-26MAR1317") == PLATFORM_KALSHI
    assert detect_platform("https://www.kalshi.com/markets/kxbtcd") == PLATFORM_KALSHI
    assert detect_platform("kxbtcd-26mar1317") == PLATFORM_KALSHI
    assert detect_platform("KXBTCD-26MAR1317-T70499.99") == PLATFORM_KALSHI


def test_detect_platform_none():
    assert detect_platform("") is None
    assert detect_platform(None) is None


def test_detect_platform_polymarket_not_kalshi():
    """Polymarket slugs with btc/eth prefix should NOT be detected as Kalshi."""
    assert detect_platform("btc-updown-5m-1772205000") == PLATFORM_POLYMARKET
    assert detect_platform("btc-up-or-down-on-march-1") == PLATFORM_POLYMARKET
    assert detect_platform("eth-updown-15m-1772204400") == PLATFORM_POLYMARKET
    assert detect_platform("bitcoin-up-or-down-on-february-26") == PLATFORM_POLYMARKET


# ═══════════════════════════════════════════════════════════════════════
# Kalshi — slug normalization
# ═══════════════════════════════════════════════════════════════════════

def test_normalize_slug_kalshi_markets():
    assert normalize_slug("https://kalshi.com/markets/kxbtcd") == "kxbtcd"
    assert normalize_slug("https://kalshi.com/markets/KXBTCD-26MAR1317-T70499.99") == "KXBTCD-26MAR1317-T70499.99"
    assert normalize_slug("https://www.kalshi.com/markets/kxbtcd") == "kxbtcd"


def test_normalize_slug_kalshi_events():
    assert normalize_slug("https://kalshi.com/events/kxbtcd/KXBTCD-26MAR1317") == "KXBTCD-26MAR1317"
    assert normalize_slug("https://kalshi.com/events/KXBTCD-26MAR1317") == "KXBTCD-26MAR1317"


def test_normalize_slug_kalshi_multi_segment():
    assert normalize_slug("https://kalshi.com/markets/kxsol15m/solana-15-minutes/kxsol15m-26mar121945") == "kxsol15m-26mar121945"
    assert normalize_slug("https://kalshi.com/markets/kxbtcd/bitcoin-daily/KXBTCD-26MAR1317") == "KXBTCD-26MAR1317"


def test_normalize_slug_kalshi_ticker_passthrough():
    assert normalize_slug("kxbtcd") == "kxbtcd"
    assert normalize_slug("KXBTCD-26MAR1317") == "KXBTCD-26MAR1317"
    assert normalize_slug("KXBTCD-26MAR1317-T70499.99") == "KXBTCD-26MAR1317-T70499.99"


# ═══════════════════════════════════════════════════════════════════════
# Kalshi — asset extraction
# ═══════════════════════════════════════════════════════════════════════

def test_asset_from_kalshi_ticker():
    assert asset_from_kalshi_ticker("kxbtcd") == "BTC"
    assert asset_from_kalshi_ticker("kxethd") == "ETH"
    assert asset_from_kalshi_ticker("kxsold") == "SOL"
    assert asset_from_kalshi_ticker("KXBTCD") == "BTC"


def test_asset_from_kalshi_event_ticker():
    assert asset_from_kalshi_ticker("KXBTCD-26MAR1317") == "BTC"
    assert asset_from_kalshi_ticker("KXETHD-26MAR1317") == "ETH"


def test_asset_from_kalshi_contract_ticker():
    assert asset_from_kalshi_ticker("KXBTCD-26MAR1317-T70499.99") == "BTC"
    assert asset_from_kalshi_ticker("KXBTC-26MAR1317-B76750") == "BTC"


def test_asset_from_kalshi_15min():
    assert asset_from_kalshi_ticker("KXBTC15M-26MAR121930-30") == "BTC"
    assert asset_from_kalshi_ticker("KXETH15M-26MAR121930-30") == "ETH"


def test_asset_from_kalshi_legacy():
    assert asset_from_kalshi_ticker("btcd") == "BTC"
    assert asset_from_kalshi_ticker("ETH") == "ETH"


def test_asset_from_kalshi_other():
    assert asset_from_kalshi_ticker("kxspx-26mar12") == "SPY"
    assert asset_from_kalshi_ticker("kxnvda-26mar12") == "NVDA"
    assert asset_from_kalshi_ticker("kxxau-26mar12") == "XAU"


def test_asset_from_kalshi_unknown():
    assert asset_from_kalshi_ticker("unknown-ticker") is None
    assert asset_from_kalshi_ticker("") is None
    assert asset_from_kalshi_ticker(None) is None


# ═══════════════════════════════════════════════════════════════════════
# Kalshi — market type
# ═══════════════════════════════════════════════════════════════════════

def test_get_kalshi_market_type_daily():
    assert get_kalshi_market_type("kxbtcd") == "daily"
    assert get_kalshi_market_type("KXBTCD-26MAR1317") == "daily"
    assert get_kalshi_market_type("KXBTCD-26MAR1317-T70499.99") == "daily"
    assert get_kalshi_market_type("kxethd-26mar1317") == "daily"
    assert get_kalshi_market_type("btcd") == "daily"


def test_get_kalshi_market_type_range():
    assert get_kalshi_market_type("kxbtc") == "range"
    assert get_kalshi_market_type("KXBTC-26MAR1317") == "range"
    assert get_kalshi_market_type("KXBTC-26MAR1317-B76750") == "range"
    assert get_kalshi_market_type("kxeth") == "range"


def test_get_kalshi_market_type_15min():
    assert get_kalshi_market_type("kxbtc15m") == "15min"
    assert get_kalshi_market_type("KXBTC15M-26MAR121930-30") == "15min"
    assert get_kalshi_market_type("kxeth15m") == "15min"
    assert get_kalshi_market_type("kxsol15m") == "15min"


def test_get_market_type_kalshi_via_unified():
    """get_market_type should route Kalshi tickers correctly."""
    assert get_market_type("kxbtcd") == "daily"
    assert get_market_type("KXBTCD-26MAR1317-T70499.99") == "daily"
    assert get_market_type("kxbtc") == "range"
    assert get_market_type("KXBTC-26MAR1317-B76750") == "range"
    assert get_market_type("kxbtc15m") == "15min"
    assert get_market_type("kxethd") == "daily"


def test_is_supported_kalshi():
    assert is_supported("kxbtcd") is True
    assert is_supported("kxethd") is True
    assert is_supported("kxbtc") is True
    assert is_supported("kxbtc15m") is True
    assert is_supported("KXBTCD-26MAR1317-T70499.99") is True


# ═══════════════════════════════════════════════════════════════════════
# Short ticker disambiguation (Polymarket vs Kalshi)
# ═══════════════════════════════════════════════════════════════════════

def test_short_ticker_does_not_collide_with_polymarket():
    """Polymarket slugs with btc/eth prefix must not be routed to Kalshi."""
    assert get_market_type("btc-updown-5m-1772205000") == "5min"
    assert get_market_type("btc-updown-15m-1772204400") == "15min"
    assert get_market_type("btc-up-or-down-on-march-1") == "daily"
    assert get_market_type("eth-updown-15m-1772204400") == "15min"
    assert get_market_type("eth-updown-5m-1772205000") == "5min"


def test_legacy_kalshi_tickers_still_work():
    assert get_kalshi_market_type("btc") == "range"
    assert get_kalshi_market_type("btcd") == "daily"
    assert get_kalshi_market_type("eth") == "range"
    assert get_kalshi_market_type("ethd") == "daily"


# ═══════════════════════════════════════════════════════════════════════
# resolve() — one-shot resolver
# ═══════════════════════════════════════════════════════════════════════

def test_resolve_polymarket():
    r = resolve("bitcoin-up-or-down-on-february-26")
    assert r is not None
    assert r["platform"] == "polymarket"
    assert r["asset"] == "BTC"
    assert r["market_type"] == "daily"
    assert r["slug"] == "bitcoin-up-or-down-on-february-26"


def test_resolve_polymarket_url():
    r = resolve("https://polymarket.com/event/bitcoin-up-or-down-on-february-26")
    assert r is not None
    assert r["platform"] == "polymarket"
    assert r["asset"] == "BTC"


def test_resolve_kalshi_ticker():
    r = resolve("KXBTCD-26MAR1317", "kalshi")
    assert r is not None
    assert r["platform"] == "kalshi"
    assert r["asset"] == "BTC"
    assert r["market_type"] == "daily"


def test_resolve_kalshi_url():
    r = resolve("https://kalshi.com/markets/KXBTCD-26MAR1317")
    assert r is not None
    assert r["platform"] == "kalshi"
    assert r["asset"] == "BTC"


def test_resolve_kalshi_15min():
    r = resolve("kxbtc15m", "kalshi")
    assert r is not None
    assert r["market_type"] == "15min"
    assert r["asset"] == "BTC"


def test_resolve_kalshi_range():
    r = resolve("kxbtc", "kalshi")
    assert r is not None
    assert r["market_type"] == "range"


def test_resolve_unsupported():
    assert resolve("random-unknown-slug") is None
    assert resolve("") is None
    assert resolve(None) is None


def test_resolve_kalshi_auto_detect():
    """Platform auto-detected from kx prefix."""
    r = resolve("kxethd")
    assert r is not None
    assert r["platform"] == "kalshi"
    assert r["asset"] == "ETH"


# ═══════════════════════════════════════════════════════════════════════
# Extended Kalshi ticker coverage (XRP, DOGE, kxspy, btcd-b)
# ═══════════════════════════════════════════════════════════════════════

def test_asset_from_kalshi_xrp():
    assert asset_from_kalshi_ticker("kxxrpd") == "XRP"
    assert asset_from_kalshi_ticker("kxxrp") == "XRP"
    assert asset_from_kalshi_ticker("kxxrp15m") == "XRP"
    assert asset_from_kalshi_ticker("KXXRPD-26MAR1317") == "XRP"


def test_asset_from_kalshi_doge():
    assert asset_from_kalshi_ticker("kxdoged") == "DOGE"
    assert asset_from_kalshi_ticker("kxdoge") == "DOGE"
    assert asset_from_kalshi_ticker("kxdoge15m") == "DOGE"


def test_asset_from_kalshi_spy_variant():
    assert asset_from_kalshi_ticker("kxspy") == "SPY"
    assert asset_from_kalshi_ticker("kxspx") == "SPY"


def test_asset_from_kalshi_btcd_b():
    assert asset_from_kalshi_ticker("btcd-b") == "BTC"
    assert asset_from_kalshi_ticker("BTCD-B") == "BTC"


def test_get_kalshi_market_type_btcd_b():
    assert get_kalshi_market_type("btcd-b") == "daily"


def test_get_kalshi_market_type_xrp():
    assert get_kalshi_market_type("kxxrpd") == "daily"
    assert get_kalshi_market_type("kxxrp") == "range"
    assert get_kalshi_market_type("kxxrp15m") == "15min"


def test_get_kalshi_market_type_doge():
    assert get_kalshi_market_type("kxdoged") == "daily"
    assert get_kalshi_market_type("kxdoge") == "range"
    assert get_kalshi_market_type("kxdoge15m") == "15min"


def test_detect_platform_kalshi_btcd_b():
    assert detect_platform("BTCD-B") == PLATFORM_KALSHI


def test_is_supported_extended_kalshi():
    assert is_supported("kxxrpd") is True
    assert is_supported("kxdoged") is True
    assert is_supported("kxspy") is True
    assert is_supported("btcd-b") is True
    assert is_supported("kxunknown-26feb25") is False


# ═══════════════════════════════════════════════════════════════════════
# Contract ticker with -T (strike) and -B (bracket) suffixes
# ═══════════════════════════════════════════════════════════════════════

def test_get_market_type_contract_with_threshold():
    assert get_market_type("KXBTCD-26MAR1317-T71500") == "daily"
    assert get_market_type("KXETHD-26MAR1317-T3500.5") == "daily"


def test_asset_from_kalshi_contract_with_threshold():
    assert asset_from_kalshi_ticker("KXBTCD-26MAR1317-T71500") == "BTC"
    assert asset_from_kalshi_ticker("KXETHD-26MAR1317-T3500.5") == "ETH"
    assert asset_from_kalshi_ticker("KXSOLD-26MAR1317-T150") == "SOL"


def test_detect_platform_kalshi_contract_ticker():
    assert detect_platform("KXBTCD-26MAR1317-T71500") == PLATFORM_KALSHI
    assert detect_platform("KXETHD-26MAR1317-T3500.5") == PLATFORM_KALSHI


def test_legacy_kalshi_with_date_suffix():
    assert get_kalshi_market_type("btcd-26MAR1317") == "daily"
    assert get_kalshi_market_type("ethd-26MAR1317") == "daily"


# ═══════════════════════════════════════════════════════════════════════
# Multi-segment Kalshi URL normalization — additional cases
# ═══════════════════════════════════════════════════════════════════════

def test_normalize_slug_kalshi_multi_segment_eth():
    assert normalize_slug("https://kalshi.com/markets/kxeth15m/ethereum-15-minutes/kxeth15m-26mar121945") == "kxeth15m-26mar121945"


def test_normalize_slug_kalshi_contract_with_threshold():
    assert normalize_slug("https://kalshi.com/markets/kxbtcd/bitcoin-daily/KXBTCD-26MAR1317-T71500") == "KXBTCD-26MAR1317-T71500"
    assert normalize_slug("KXBTCD-26MAR1317-T71500") == "KXBTCD-26MAR1317-T71500"


# ═══════════════════════════════════════════════════════════════════════
# ResolveResult — structured diagnostics
# ═══════════════════════════════════════════════════════════════════════

def test_resolve_result_success():
    r = registry.resolve("bitcoin-up-or-down-on-february-26")
    assert r.ok is True
    assert r.slug == "bitcoin-up-or-down-on-february-26"
    assert r.asset == "BTC"
    assert r.market_type == "daily"
    assert r.platform == "polymarket"
    assert r.error_code is None
    assert r.error is None


def test_resolve_result_invalid_input():
    r = registry.resolve("")
    assert r.ok is False
    assert r.error_code == ERR_INVALID_INPUT
    assert "Missing" in r.error

    r2 = registry.resolve(None)
    assert r2.ok is False
    assert r2.error_code == ERR_INVALID_INPUT


def test_resolve_result_unsupported_market():
    r = registry.resolve("random-unknown-slug")
    assert r.ok is False
    assert r.error_code == ERR_UNSUPPORTED_MARKET
    assert r.slug == "random-unknown-slug"
    assert r.platform is not None


def test_resolve_result_unknown_platform():
    r = registry.resolve("some-slug", platform_hint="robinhood")
    assert r.ok is False
    assert r.error_code == ERR_UNKNOWN_PLATFORM
    assert "robinhood" in r.error


def test_resolve_result_to_dict_omits_none():
    r = registry.resolve("kxbtcd", "kalshi")
    d = r.to_dict()
    assert d["ok"] is True
    assert "error_code" not in d
    assert "error" not in d
    assert d["platform"] == "kalshi"
    assert d["asset"] == "BTC"


def test_resolve_result_error_to_dict():
    r = registry.resolve("")
    d = r.to_dict()
    assert d["ok"] is False
    assert "error_code" in d
    assert "error" in d
    assert "slug" not in d  # None values omitted


def test_resolve_result_kalshi_structured():
    r = registry.resolve("KXBTCD-26MAR1317", "kalshi")
    assert r.ok is True
    assert r.platform == "kalshi"
    assert r.asset == "BTC"
    assert r.market_type == "daily"


# ═══════════════════════════════════════════════════════════════════════
# PlatformRegistry — introspection & capabilities
# ═══════════════════════════════════════════════════════════════════════

def test_registry_has_two_platforms():
    assert len(registry.platform_names) == 2
    assert "polymarket" in registry.platform_names
    assert "kalshi" in registry.platform_names


def test_registry_priority_order():
    """Polymarket (priority=0) comes before Kalshi (priority=10)."""
    assert registry.platform_names[0] == "polymarket"
    assert registry.platform_names[1] == "kalshi"


def test_registry_get_platform():
    poly = registry.get("polymarket")
    assert poly is not None
    assert poly["domain"] == "polymarket.com"

    kalshi = registry.get("kalshi")
    assert kalshi is not None
    assert kalshi["domain"] == "kalshi.com"

    assert registry.get("nonexistent") is None


def test_registry_capabilities():
    caps = registry.capabilities()
    assert len(caps) == 2
    poly_cap = caps[0]
    assert poly_cap["name"] == "polymarket"
    assert "BTC" in poly_cap["supported_assets"]
    assert "daily" in poly_cap["supported_market_types"]
    assert poly_cap["label"] == "Poly"

    kalshi_cap = caps[1]
    assert kalshi_cap["name"] == "kalshi"
    assert "NVDA" in kalshi_cap["supported_assets"]
    assert "15min" in kalshi_cap["supported_market_types"]
    assert kalshi_cap["label"] == "Kalshi"


def test_registry_all_supported_assets():
    assets = registry.all_supported_assets()
    assert "BTC" in assets
    assert "ETH" in assets
    assert "NVDA" in assets  # Kalshi-only
    assert "XRP" in assets


def test_platform_capabilities_includes_domain():
    caps = registry.capabilities()
    kalshi_cap = [c for c in caps if c["name"] == "kalshi"][0]
    assert kalshi_cap["domain"] == "kalshi.com"
    assert isinstance(kalshi_cap["supported_assets"], list)
    assert isinstance(kalshi_cap["supported_market_types"], list)


# ═══════════════════════════════════════════════════════════════════════
# Custom platform registration (extensibility proof)
# ═══════════════════════════════════════════════════════════════════════

_MOCK_CONFIG = {
    "mockex": {
        "domain": "mockex.com",
        "label": "Mock",
        "priority": 20,
        "supported_assets": frozenset({"BTC"}),
        "supported_market_types": frozenset({"daily"}),
        "url_re": re.compile(r"mockex\.com/markets/([a-zA-Z0-9._-]+)", re.I),
        "slug_re": re.compile(r"^MX-", re.I),
        "detect": lambda s: "mockex.com" in s or s.startswith("mx-"),
        "resolve_asset": lambda slug: "BTC" if slug.lower().startswith("mx-") else None,
        "resolve_market_type": lambda slug: "daily" if slug.lower().startswith("mx-") else None,
    },
}


def test_custom_platform_registration():
    """New platforms can be registered as config dicts without touching existing code."""
    custom_reg = PlatformRegistry(_MOCK_CONFIG)

    r = custom_reg.resolve("https://mockex.com/markets/MX-BTC-DAILY")
    assert r.ok is True
    assert r.platform == "mockex"
    assert r.asset == "BTC"
    assert r.market_type == "daily"


def test_custom_platform_does_not_affect_default_registry():
    """Config-based registry instances are isolated."""
    assert registry.get("mockex") is None


def test_custom_platform_capabilities():
    custom_reg = PlatformRegistry(_MOCK_CONFIG)
    caps = custom_reg.capabilities()
    assert len(caps) == 1
    assert caps[0]["name"] == "mockex"
    assert caps[0]["supported_assets"] == ["BTC"]
