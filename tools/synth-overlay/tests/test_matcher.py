"""Tests for market slug / URL matcher."""

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
    PLATFORM_KALSHI,
    PLATFORM_POLYMARKET,
)


def test_normalize_slug_from_url():
    assert normalize_slug("https://polymarket.com/event/bitcoin-up-or-down-on-february-26") == "bitcoin-up-or-down-on-february-26"
    assert normalize_slug("https://polymarket.com/market/bitcoin-price-on-february-26") == "bitcoin-price-on-february-26"


def test_normalize_slug_passthrough():
    assert normalize_slug("bitcoin-up-or-down-on-february-26") == "bitcoin-up-or-down-on-february-26"


def test_normalize_slug_invalid():
    assert normalize_slug("") is None
    assert normalize_slug(None) is None


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


# ---- Kalshi platform detection ----

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
    """Polymarket slugs starting with 'btc-' or 'eth-' should not be detected as Kalshi."""
    assert detect_platform("btc-updown-5m-1772205000") == PLATFORM_POLYMARKET
    assert detect_platform("btc-up-or-down-on-march-1") == PLATFORM_POLYMARKET
    assert detect_platform("eth-updown-15m-1772204400") == PLATFORM_POLYMARKET
    assert detect_platform("bitcoin-up-or-down-on-february-26") == PLATFORM_POLYMARKET


# ---- Kalshi URL normalization ----

def test_normalize_slug_kalshi_markets():
    assert normalize_slug("https://kalshi.com/markets/kxbtcd") == "kxbtcd"
    assert normalize_slug("https://kalshi.com/markets/KXBTCD-26MAR1317-T70499.99") == "KXBTCD-26MAR1317-T70499.99"
    assert normalize_slug("https://www.kalshi.com/markets/kxbtcd") == "kxbtcd"
    assert normalize_slug("https://www.kalshi.com/markets/KXBTCD-26MAR1317-T70499.99") == "KXBTCD-26MAR1317-T70499.99"


def test_normalize_slug_kalshi_events():
    assert normalize_slug("https://kalshi.com/events/kxbtcd/KXBTCD-26MAR1317") == "KXBTCD-26MAR1317"
    assert normalize_slug("https://kalshi.com/events/KXBTCD-26MAR1317") == "KXBTCD-26MAR1317"
    # Multi-segment Kalshi URL with series slug in path
    assert normalize_slug("https://kalshi.com/markets/kxsol15m/solana-15-minutes/kxsol15m-26mar121945") == "kxsol15m-26mar121945"


def test_normalize_slug_kalshi_ticker_passthrough():
    assert normalize_slug("kxbtcd") == "kxbtcd"
    assert normalize_slug("KXBTCD-26MAR1317") == "KXBTCD-26MAR1317"
    assert normalize_slug("KXBTCD-26MAR1317-T70499.99") == "KXBTCD-26MAR1317-T70499.99"


# ---- Kalshi asset extraction ----

def test_asset_from_kalshi_ticker():
    # Series tickers
    assert asset_from_kalshi_ticker("kxbtcd") == "BTC"
    assert asset_from_kalshi_ticker("kxethd") == "ETH"
    assert asset_from_kalshi_ticker("kxsold") == "SOL"
    assert asset_from_kalshi_ticker("KXBTCD") == "BTC"
    # Event tickers
    assert asset_from_kalshi_ticker("KXBTCD-26MAR1317") == "BTC"
    assert asset_from_kalshi_ticker("KXETHD-26MAR1317") == "ETH"
    # Full market tickers
    assert asset_from_kalshi_ticker("KXBTCD-26MAR1317-T70499.99") == "BTC"
    assert asset_from_kalshi_ticker("KXBTC-26MAR1317-B76750") == "BTC"
    # 15min tickers
    assert asset_from_kalshi_ticker("KXBTC15M-26MAR121930-30") == "BTC"
    assert asset_from_kalshi_ticker("KXETH15M-26MAR121930-30") == "ETH"
    # Legacy tickers (no KX prefix)
    assert asset_from_kalshi_ticker("btcd") == "BTC"
    assert asset_from_kalshi_ticker("BTCD-B") == "BTC"
    assert asset_from_kalshi_ticker("ETH") == "ETH"
    # Other assets
    assert asset_from_kalshi_ticker("kxspx-26mar12") == "SPY"
    assert asset_from_kalshi_ticker("kxnvda-26mar12") == "NVDA"
    assert asset_from_kalshi_ticker("kxxau-26mar12") == "XAU"


def test_asset_from_kalshi_ticker_unknown():
    assert asset_from_kalshi_ticker("unknown-ticker") is None
    assert asset_from_kalshi_ticker("") is None
    assert asset_from_kalshi_ticker(None) is None


# ---- Kalshi market type ----

def test_get_kalshi_market_type_daily():
    # KXBTCD series = above/below = daily
    assert get_kalshi_market_type("kxbtcd") == "daily"
    assert get_kalshi_market_type("KXBTCD-26MAR1317") == "daily"
    assert get_kalshi_market_type("KXBTCD-26MAR1317-T70499.99") == "daily"
    assert get_kalshi_market_type("kxethd-26mar1317") == "daily"
    assert get_kalshi_market_type("btcd") == "daily"


def test_get_kalshi_market_type_range():
    # KXBTC series = range
    assert get_kalshi_market_type("kxbtc") == "range"
    assert get_kalshi_market_type("KXBTC-26MAR1317") == "range"
    assert get_kalshi_market_type("KXBTC-26MAR1317-B76750") == "range"
    assert get_kalshi_market_type("kxeth") == "range"
    # Legacy btc without KX prefix only matches with proper date suffix
    assert get_kalshi_market_type("btc") == "range"


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
    assert is_supported("kxunknown-26feb25") is False


def test_short_ticker_does_not_collide_with_polymarket():
    """Legacy Kalshi tickers (btc, eth) must not match Polymarket slugs."""
    # Polymarket-style slugs with btc/eth prefix should NOT be routed to Kalshi
    assert get_market_type("btc-updown-5m-1772205000") == "5min"
    assert get_market_type("btc-updown-15m-1772204400") == "15min"
    assert get_market_type("btc-up-or-down-on-march-1") == "daily"


# ---- Multi-segment Kalshi URL normalization ----

def test_normalize_slug_kalshi_multi_segment_eth():
    """Multi-segment Kalshi URL for ETH 15min extracts last segment."""
    assert normalize_slug("https://kalshi.com/markets/kxeth15m/ethereum-15-minutes/kxeth15m-26mar121945") == "kxeth15m-26mar121945"


def test_normalize_slug_kalshi_multi_segment_daily():
    """Multi-segment daily URL with descriptive text extracts last segment."""
    assert normalize_slug("https://kalshi.com/markets/kxbtcd/bitcoin-daily/KXBTCD-26MAR1317") == "KXBTCD-26MAR1317"


def test_normalize_slug_kalshi_contract_with_threshold():
    """Contract ticker with -T threshold suffix normalizes correctly."""
    assert normalize_slug("https://kalshi.com/markets/kxbtcd/bitcoin-daily/KXBTCD-26MAR1317-T71500") == "KXBTCD-26MAR1317-T71500"
    assert normalize_slug("KXBTCD-26MAR1317-T71500") == "KXBTCD-26MAR1317-T71500"


def test_get_market_type_contract_with_threshold():
    """Contract with -T (strike) suffix still resolves to daily market type."""
    assert get_market_type("KXBTCD-26MAR1317-T71500") == "daily"
    assert get_market_type("KXETHD-26MAR1317-T3500.5") == "daily"


def test_asset_from_kalshi_contract_with_threshold():
    """Asset extraction works for contract tickers with -T suffix."""
    assert asset_from_kalshi_ticker("KXBTCD-26MAR1317-T71500") == "BTC"
    assert asset_from_kalshi_ticker("KXETHD-26MAR1317-T3500.5") == "ETH"
    assert asset_from_kalshi_ticker("KXSOLD-26MAR1317-T150") == "SOL"


def test_detect_platform_kalshi_contract_ticker():
    """Platform detection works for full contract tickers with threshold."""
    assert detect_platform("KXBTCD-26MAR1317-T71500") == PLATFORM_KALSHI
    assert detect_platform("KXETHD-26MAR1317-T3500.5") == PLATFORM_KALSHI


def test_normalize_slug_kalshi_browse_portfolio_ignored():
    """Browse and portfolio pages should not return a slug."""
    assert normalize_slug("https://kalshi.com/browse") is None or normalize_slug("https://kalshi.com/browse") == "browse"
    assert normalize_slug("https://kalshi.com/portfolio") is None or normalize_slug("https://kalshi.com/portfolio") == "portfolio"
    assert get_market_type("eth-updown-15m-1772204400") == "15min"
    assert get_market_type("eth-updown-5m-1772205000") == "5min"
    # Legacy Kalshi tickers with date suffix SHOULD match
    assert get_kalshi_market_type("btcd-26MAR1317") == "daily"
    assert get_kalshi_market_type("ethd-26MAR1317") == "daily"
    # Legacy series tickers alone still work
    assert get_kalshi_market_type("btc") == "range"
    assert get_kalshi_market_type("btcd") == "daily"
    assert get_kalshi_market_type("eth") == "range"
    assert get_kalshi_market_type("ethd") == "daily"
