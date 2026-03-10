"""
gTrade (Gains Network) integration for the Tide Chart dashboard.

Handles pair mapping, chain configuration, trade parameter validation,
and API proxying for the gTrade decentralized trading protocol on Arbitrum.
"""

import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from typing import Optional

ARBITRUM_CHAIN_ID = 42161
ARBITRUM_CHAIN_ID_HEX = "0xa4b1"
ARBITRUM_RPC = "https://arb1.arbitrum.io/rpc"

GTRADE_BACKEND_URL = "https://backend-arbitrum.gains.trade"

# gTrade Diamond proxy on Arbitrum One
GTRADE_TRADING_CONTRACT = "0xFF162c694eAA571f685030649814282eA457f169"

# USDC on Arbitrum One (native USDC, not bridged)
USDC_CONTRACT = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDC_DECIMALS = 6
USDC_COLLATERAL_INDEX = 3

# Per-group protocol limits enforced by gTrade smart contracts
GROUP_LIMITS = {
    "crypto": {"min_leverage": 2, "max_leverage": 150, "min_position_usd": 1500, "max_collateral_usd": 100_000},
    "stocks": {"min_leverage": 2, "max_leverage": 150, "min_position_usd": 1500, "max_collateral_usd": 100_000},
    "commodities": {"min_leverage": 2, "max_leverage": 150, "min_position_usd": 1500, "max_collateral_usd": 100_000},
}

# Tide Chart ticker -> gTrade pair mapping (all Synth API assets)
GTRADE_PAIRS = {
    "BTC": {"name": "BTC/USD", "group_index": 0, "group": "crypto"},
    "ETH": {"name": "ETH/USD", "group_index": 0, "group": "crypto"},
    "SOL": {"name": "SOL/USD", "group_index": 0, "group": "crypto"},
    "XAU": {"name": "XAU/USD", "group_index": 4, "group": "commodities"},
    "SPY": {"name": "SPY/USD", "group_index": 3, "group": "stocks"},
    "NVDA": {"name": "NVDA/USD", "group_index": 3, "group": "stocks"},
    "TSLA": {"name": "TSLA/USD", "group_index": 3, "group": "stocks"},
    "AAPL": {"name": "AAPL/USD", "group_index": 3, "group": "stocks"},
    "GOOGL": {"name": "GOOGL/USD", "group_index": 3, "group": "stocks"},
}

TRADEABLE_ASSETS = list(GTRADE_PAIRS.keys())

MIN_COLLATERAL_USD = 5

# Liquidation threshold: gTrade liquidates when collateral loss reaches this %
LIQ_THRESHOLD_PCT = 90

# Approximate trading fees by group (% of position size)
GROUP_FEES = {
    "crypto": {"open_fee_pct": 0.06, "close_fee_pct": 0.06},
    "stocks": {"open_fee_pct": 0.01, "close_fee_pct": 0.01},
    "commodities": {"open_fee_pct": 0.01, "close_fee_pct": 0.01},
}

# US stock market hours (Eastern Time)
_ET = ZoneInfo("America/New_York")
_MARKET_OPEN_H, _MARKET_OPEN_M = 9, 30
_MARKET_CLOSE_H, _MARKET_CLOSE_M = 16, 0

# US market holidays for 2025-2026 (federal holidays when NYSE is closed)
_MARKET_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
}

_trading_vars_cache: Optional[dict] = None
_trading_vars_ts: float = 0


def get_tradeable_assets() -> list[str]:
    return list(TRADEABLE_ASSETS)


def is_tradeable(asset: str) -> bool:
    return asset in GTRADE_PAIRS


def get_asset_limits(asset: str) -> dict:
    """Return the protocol limits for a given asset based on its group."""
    pair = GTRADE_PAIRS.get(asset)
    if not pair:
        return {}
    return GROUP_LIMITS.get(pair["group"], {})


def validate_trade_params(
    asset: str,
    direction: str,
    leverage: float,
    collateral_usd: float,
) -> tuple[bool, str]:
    """Validate trade parameters against gTrade protocol limits.

    Returns (is_valid, error_message).  Every check here mirrors a
    condition that would cause the gTrade contract to revert.
    """
    if not is_tradeable(asset):
        return False, f"{asset} is not available for trading on gTrade"

    if direction not in ("long", "short"):
        return False, "Direction must be 'long' or 'short'"

    limits = get_asset_limits(asset)
    min_lev = limits.get("min_leverage", 2)
    max_lev = limits.get("max_leverage", 150)
    min_pos = limits.get("min_position_usd", 1500)
    max_col = limits.get("max_collateral_usd", 100_000)

    if not isinstance(leverage, (int, float)) or leverage < min_lev:
        return False, f"Leverage must be at least {min_lev}x"

    if leverage > max_lev:
        return False, f"Leverage cannot exceed {max_lev}x"

    if not isinstance(collateral_usd, (int, float)) or collateral_usd < MIN_COLLATERAL_USD:
        return False, f"Minimum collateral is ${MIN_COLLATERAL_USD}"

    if collateral_usd > max_col:
        return False, f"Maximum collateral is ${max_col:,}"

    position_usd = collateral_usd * leverage
    if position_usd < min_pos:
        return False, f"Position size (${position_usd:,.0f}) below minimum ${min_pos:,}"

    return True, ""


def build_trade_summary(
    asset: str,
    current_price: float,
    direction: str,
    leverage: float,
    collateral_usd: float,
) -> dict:
    """Build a human-readable trade summary with computed values."""
    pair_info = GTRADE_PAIRS[asset]
    return {
        "asset": asset,
        "pair_name": pair_info["name"],
        "direction": direction,
        "leverage": leverage,
        "collateral_usd": collateral_usd,
        "position_size_usd": collateral_usd * leverage,
        "current_price": current_price,
        "chain": "Arbitrum One",
        "collateral_token": "USDC",
        "protocol": "gTrade (Gains Network)",
    }


def get_chain_config() -> dict:
    return {
        "chain_id": ARBITRUM_CHAIN_ID,
        "chain_id_hex": ARBITRUM_CHAIN_ID_HEX,
        "chain_name": "Arbitrum One",
        "rpc_url": ARBITRUM_RPC,
        "block_explorer": "https://arbiscan.io",
        "native_currency": {"name": "ETH", "symbol": "ETH", "decimals": 18},
    }


def get_contract_config() -> dict:
    """Return contract addresses and configuration for frontend."""
    return {
        "trading_contract": GTRADE_TRADING_CONTRACT,
        "usdc_contract": USDC_CONTRACT,
        "usdc_decimals": USDC_DECIMALS,
        "collateral_index": USDC_COLLATERAL_INDEX,
        "pairs": {
            asset: {**info, "asset": asset}
            for asset, info in GTRADE_PAIRS.items()
        },
        "group_limits": GROUP_LIMITS,
        "collateral_limits": {"min_usd": MIN_COLLATERAL_USD},
    }


def fetch_trading_variables() -> Optional[dict]:
    """Fetch live trading variables from gTrade backend.

    Returns pair indices, fees, and other trading parameters.
    Returns None if the request fails.
    """
    try:
        resp = requests.get(f"{GTRADE_BACKEND_URL}/trading-variables", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def get_cached_trading_variables(max_age_seconds: int = 300) -> Optional[dict]:
    """Fetch trading variables with simple time-based caching."""
    global _trading_vars_cache, _trading_vars_ts
    now = time.time()
    if _trading_vars_cache and (now - _trading_vars_ts) < max_age_seconds:
        return _trading_vars_cache
    result = fetch_trading_variables()
    if result:
        _trading_vars_cache = result
        _trading_vars_ts = now
    return _trading_vars_cache


def get_pair_name_map() -> dict:
    """Build a pairIndex -> name mapping from cached trading variables."""
    tv = get_cached_trading_variables()
    if not tv or "pairs" not in tv:
        return {}
    result = {}
    for i, pair in enumerate(tv["pairs"]):
        pair_from = pair.get("from", "")
        pair_to = pair.get("to", "")
        if pair_from:
            result[i] = f"{pair_from}/{pair_to}"
    return result


def fetch_open_trades(address: str) -> list[dict]:
    """Fetch open trades for a wallet address from the gTrade backend.

    Returns a list of open trade dicts, or empty list on failure.
    """
    if not address:
        return []
    try:
        resp = requests.get(
            f"{GTRADE_BACKEND_URL}/open-trades/{address.lower()}",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except (requests.RequestException, ValueError):
        return []


def fetch_trade_history(address: str) -> list[dict]:
    """Fetch historical trades (open & closed) for a wallet address.

    Returns a list of trade history dicts, or empty list on failure.
    """
    if not address:
        return []
    try:
        resp = requests.get(
            f"{GTRADE_BACKEND_URL}/personal-trading-history-table/{address.lower()}",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except (requests.RequestException, ValueError):
        return []


def is_market_open() -> tuple[bool, str]:
    """Check if the US stock market is currently open.

    Returns (is_open, reason). Stocks on gTrade can only be traded
    during NYSE regular hours: Mon-Fri 9:30 AM - 4:00 PM ET,
    excluding federal holidays.
    """
    now = datetime.now(_ET)
    if now.date() in _MARKET_HOLIDAYS:
        return False, "Market closed (holiday)"
    wd = now.weekday()
    if wd >= 5:
        day_name = "Saturday" if wd == 5 else "Sunday"
        return False, f"Market closed ({day_name})"
    market_open = now.replace(hour=_MARKET_OPEN_H, minute=_MARKET_OPEN_M, second=0, microsecond=0)
    market_close = now.replace(hour=_MARKET_CLOSE_H, minute=_MARKET_CLOSE_M, second=0, microsecond=0)
    if now < market_open:
        return False, f"Market opens at 9:30 AM ET (currently {now.strftime('%I:%M %p')} ET)"
    if now >= market_close:
        return False, "Market closed (after 4:00 PM ET)"
    return True, "Market open"


def estimate_trade_fees(asset: str, collateral_usd: float, leverage: float) -> dict:
    """Estimate opening and closing fees for a trade.

    Returns a dict with open_fee, close_fee, total_fee (all in USD),
    and the fee_pct used. Fees are a percentage of position size.
    """
    pair = GTRADE_PAIRS.get(asset)
    if not pair:
        return {"open_fee": 0, "close_fee": 0, "total_fee": 0, "fee_pct": 0}
    group = pair["group"]
    fees = GROUP_FEES.get(group, GROUP_FEES["crypto"])
    position_usd = collateral_usd * leverage
    open_fee = position_usd * fees["open_fee_pct"] / 100
    close_fee = position_usd * fees["close_fee_pct"] / 100
    return {
        "open_fee": round(open_fee, 4),
        "close_fee": round(close_fee, 4),
        "total_fee": round(open_fee + close_fee, 4),
        "fee_pct": fees["open_fee_pct"],
        "position_usd": round(position_usd, 2),
    }


def calculate_liquidation_price(
    entry_price: float, is_long: bool, leverage: float,
) -> float:
    """Calculate the liquidation price for a leveraged position.

    gTrade liquidates when collateral loss reaches ~90%. The remaining
    ~10% covers the liquidator incentive.
    """
    if leverage <= 0 or entry_price <= 0:
        return 0.0
    threshold = LIQ_THRESHOLD_PCT / 100
    if is_long:
        return entry_price * (1 - threshold / leverage)
    return entry_price * (1 + threshold / leverage)


def resolve_pair_index(asset: str, trading_vars: Optional[dict] = None, skip_fetch: bool = False) -> Optional[int]:
    """Resolve a Tide Chart ticker to its gTrade pair index.

    Uses trading variables from the gTrade API when available.
    Set skip_fetch=True to avoid network calls (returns None if no cached data).
    Returns None if the pair can't be resolved.
    """
    if asset not in GTRADE_PAIRS:
        return None

    if trading_vars is None and not skip_fetch:
        trading_vars = get_cached_trading_variables()

    target_name = GTRADE_PAIRS[asset]["name"]

    if trading_vars and "pairs" in trading_vars:
        for i, pair in enumerate(trading_vars["pairs"]):
            pair_from = pair.get("from", "")
            pair_to = pair.get("to", "")
            if f"{pair_from}/{pair_to}" == target_name:
                return i

    return None
