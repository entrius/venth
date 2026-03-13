"""Map Polymarket / Kalshi URL/slug to Synth market type and supported asset."""

import re
from typing import Literal

MARKET_DAILY = "daily"
MARKET_HOURLY = "hourly"
MARKET_15MIN = "15min"
MARKET_5MIN = "5min"
MARKET_RANGE = "range"

PLATFORM_POLYMARKET = "polymarket"
PLATFORM_KALSHI = "kalshi"

_HOURLY_TIME_PATTERN = re.compile(r"\d{1,2}(am|pm)")
_15MIN_PATTERN = re.compile(r"(updown|up-down)-15m-|(?<!\d)15-?min")
_5MIN_PATTERN = re.compile(r"(updown|up-down)-5m-|(?<!1)5-?min")

_ASSET_PREFIXES = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "xrp": "XRP",
    "btc": "BTC",
    "eth": "ETH",
    "sol": "SOL",
}

# Kalshi series/ticker prefixes → asset mapping.
# Real series tickers discovered from Kalshi API:
#   Above/below (hourly): KXBTCD, KXETHD, KXSOLD, KXXRPD, KXDOGED, ...
#   Range (hourly):        KXBTC, KXETH, KXSOL, KXXRP, ...
#   15min:                 KXBTC15M, KXETH15M, KXSOL15M, ...
#   Above/below (daily):   BTCD, ETHD  (legacy, no KX prefix)
#   Range (daily):         BTC, ETH    (legacy, no KX prefix)
_KALSHI_ASSET_MAP = {
    # BTC
    "kxbtcd": "BTC",
    "kxbtc15m": "BTC",
    "kxbtc": "BTC",
    "btcd": "BTC",
    "btcd-b": "BTC",
    "btc": "BTC",
    # ETH
    "kxethd": "ETH",
    "kxeth15m": "ETH",
    "kxeth": "ETH",
    "ethd": "ETH",
    "eth": "ETH",
    # SOL
    "kxsold": "SOL",
    "kxsol15m": "SOL",
    "kxsol": "SOL",
    # XRP
    "kxxrpd": "XRP",
    "kxxrp15m": "XRP",
    "kxxrp": "XRP",
    # Others
    "kxspx": "SPY",
    "kxspy": "SPY",
    "kxtsla": "TSLA",
    "kxnvda": "NVDA",
    "kxaapl": "AAPL",
    "kxgoogl": "GOOGL",
    "kxxau": "XAU",
    "kxdoged": "DOGE",
    "kxdoge": "DOGE",
}

# Kalshi market type detection from series ticker.
# -B<digits> suffix = range bracket; -T<digits> suffix = above/below strike.
_KALSHI_RANGE_BRACKET_PATTERN = re.compile(r"-b\d+", re.IGNORECASE)
_KALSHI_STRIKE_PATTERN = re.compile(r"-t\d+", re.IGNORECASE)

# Series tickers that are explicitly range markets
_KALSHI_RANGE_SERIES = {"kxbtc", "kxeth", "kxsol", "kxxrp", "btc", "eth"}
# Series tickers that are explicitly above/below (directional) markets
_KALSHI_DIRECTIONAL_SERIES = {"kxbtcd", "kxethd", "kxsold", "kxxrpd", "kxdoged", "btcd", "btcd-b", "ethd"}
# Series tickers that are 15min markets
_KALSHI_15MIN_SERIES = {"kxbtc15m", "kxeth15m", "kxsol15m", "kxxrp15m", "kxada15m", "kxbnb15m", "kxbch15m", "kxdoge15m"}


# Kalshi date suffix pattern: digit(s) followed by letters/digits (e.g. 26MAR1317, 26MAR121930)
_KALSHI_DATE_SUFFIX = re.compile(r"^\d+[A-Za-z]")
# Short legacy tickers that could collide with Polymarket slug prefixes
_KALSHI_SHORT_TICKERS = {"btc", "eth", "btcd", "ethd", "btcd-b"}


def _kalshi_series_from_ticker(ticker: str) -> str | None:
    """Extract the series ticker from a full Kalshi market/event ticker.
    
    Examples:
        KXBTCD-26MAR1317-T70499.99 → kxbtcd
        KXBTC-26MAR1317-B76750     → kxbtc
        KXBTC15M-26MAR121930-30    → kxbtc15m
        KXBTCD-26MAR1317           → kxbtcd  (event ticker)
        kxbtcd                     → kxbtcd  (series ticker as-is)
    """
    if not ticker:
        return None
    t = ticker.lower().strip()
    # Try matching known series directly (longest first)
    for series in sorted(_KALSHI_ASSET_MAP.keys(), key=len, reverse=True):
        if t == series:
            return series
        if t.startswith(series + "-"):
            remainder = t[len(series) + 1:]  # part after "series-"
            # Short tickers (btc, eth, etc.) require Kalshi-style date suffix
            # to avoid matching Polymarket slugs like "btc-updown-5m-..."
            if series in _KALSHI_SHORT_TICKERS:
                if not _KALSHI_DATE_SUFFIX.match(remainder):
                    continue
            return series
    return None


def detect_platform(url_or_slug: str) -> str | None:
    """Detect which platform a URL or slug belongs to."""
    if not url_or_slug or not isinstance(url_or_slug, str):
        return None
    s = url_or_slug.strip().lower()
    if "polymarket.com" in s:
        return PLATFORM_POLYMARKET
    if "kalshi.com" in s:
        return PLATFORM_KALSHI
    # Kalshi ticker format: starts with kx or matches known series
    if s.startswith("kx"):
        return PLATFORM_KALSHI
    # Check if it matches a known Kalshi series (legacy tickers like BTC, BTCD)
    series = _kalshi_series_from_ticker(s)
    if series is not None:
        return PLATFORM_KALSHI
    # Default: assume Polymarket slug format (backward compat)
    if re.match(r"^[a-z0-9-]+$", s) and not s.startswith("kx"):
        return PLATFORM_POLYMARKET
    return None


def asset_from_slug(slug: str) -> str | None:
    """Extract the asset ticker (BTC, ETH, …) from a Polymarket slug prefix."""
    if not slug:
        return None
    slug_lower = slug.lower()
    for prefix, ticker in _ASSET_PREFIXES.items():
        if slug_lower.startswith(prefix + "-"):
            return ticker
    return None


def asset_from_kalshi_ticker(ticker: str) -> str | None:
    """Extract the asset ticker from a Kalshi market/event/series ticker.
    
    Examples:
        KXBTCD-26MAR1317-T70499.99 → BTC
        KXBTC-26MAR1317-B76750     → BTC
        KXBTC15M-26MAR121930-30    → BTC
        KXETHD                     → ETH
        BTCD-B                     → BTC
    """
    if not ticker:
        return None
    series = _kalshi_series_from_ticker(ticker)
    if series and series in _KALSHI_ASSET_MAP:
        return _KALSHI_ASSET_MAP[series]
    return None


def normalize_slug(url_or_slug: str) -> str | None:
    """Extract market slug from Polymarket or Kalshi URL, or return slug as-is."""
    if not url_or_slug or not isinstance(url_or_slug, str):
        return None
    s = url_or_slug.strip()
    # Polymarket URL
    m = re.search(r"polymarket\.com/(?:event/|market/)?([a-zA-Z0-9-]+)", s)
    if m:
        return m.group(1)
    # Kalshi URL: kalshi.com/markets/<series>/<desc>/<ticker> or kalshi.com/events/<series>/<ticker>
    # Extract the last path segment that looks like a ticker
    m = re.search(r"kalshi\.com/(?:markets|events)/(.+?)(?:\?|#|$)", s)
    if m:
        segments = [seg for seg in m.group(1).split("/") if seg]
        # Last segment is the most specific (contract/event ticker)
        ticker_seg = segments[-1] if segments else None
        if ticker_seg and re.match(r"^[a-zA-Z0-9_.-]+$", ticker_seg):
            return ticker_seg
    if re.match(r"^[a-zA-Z0-9_.-]+$", s):
        return s
    return None


def get_kalshi_market_type(ticker: str) -> Literal["daily", "hourly", "15min", "range"] | None:
    """Infer Synth market type from a Kalshi ticker.
    
    Kalshi market type detection:
    - Series in _KALSHI_15MIN_SERIES → 15min
    - Series in _KALSHI_RANGE_SERIES or -B<digits> suffix → range
    - Series in _KALSHI_DIRECTIONAL_SERIES or -T<digits> suffix → daily (above/below)
    - Daily-frequency series → daily
    - Hourly-frequency series → hourly
    - Fallback: daily for known series
    """
    if not ticker:
        return None
    series = _kalshi_series_from_ticker(ticker)
    if not series:
        return None
    # 15min series
    if series in _KALSHI_15MIN_SERIES:
        return MARKET_15MIN
    # Range: series is known range OR the specific contract has -B suffix
    if series in _KALSHI_RANGE_SERIES:
        return MARKET_RANGE
    ticker_lower = ticker.lower()
    if _KALSHI_RANGE_BRACKET_PATTERN.search(ticker_lower):
        return MARKET_RANGE
    # Directional (above/below): series is known directional OR has -T suffix
    if series in _KALSHI_DIRECTIONAL_SERIES:
        return MARKET_DAILY
    if _KALSHI_STRIKE_PATTERN.search(ticker_lower):
        return MARKET_DAILY
    # Fallback for known assets
    if series in _KALSHI_ASSET_MAP:
        return MARKET_DAILY
    return None


def get_market_type(slug: str) -> Literal["daily", "hourly", "15min", "5min", "range"] | None:
    """Infer Synth market type from slug. Returns None if not recognizable."""
    if not slug:
        return None
    slug_lower = slug.lower()
    # Check if this is a Kalshi ticker (starts with kx or matches a known Kalshi series)
    if _kalshi_series_from_ticker(slug_lower) is not None:
        return get_kalshi_market_type(slug)
    if _5MIN_PATTERN.search(slug_lower):
        return MARKET_5MIN
    if _15MIN_PATTERN.search(slug_lower):
        return MARKET_15MIN
    if "up-or-down" in slug_lower and _HOURLY_TIME_PATTERN.search(slug_lower):
        return MARKET_HOURLY
    if "up-or-down" in slug_lower and "on-" in slug_lower:
        return MARKET_DAILY
    if "price-on" in slug_lower:
        return MARKET_RANGE
    return None


def is_supported(slug: str) -> bool:
    """True if slug maps to a Synth-supported market (daily, hourly, or range)."""
    return get_market_type(slug) is not None
