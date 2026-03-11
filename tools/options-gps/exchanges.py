"""
Exchange data provider for Options GPS line shopping.

When API keys are present, performs real network calls to Aevo and Deribit
for live option quotes. Falls back to mock data when keys are unset or
API calls fail.

Environment variables:
- AEVO_API_KEY: optional, for Aevo REST API
- DERIBIT_CLIENT_ID / DERIBIT_CLIENT_SECRET: optional; Deribit public
  market data does not require auth, but we attempt live fetch when configured.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


@dataclass
class ExchangeQuote:
    """Normalized quote for a single option leg on one exchange."""

    exchange: str
    option_type: str  # "Call" or "Put"
    strike: float
    price: float


AEVO_API_KEY = os.getenv("AEVO_API_KEY")
DERIBIT_CLIENT_ID = os.getenv("DERIBIT_CLIENT_ID")
DERIBIT_CLIENT_SECRET = os.getenv("DERIBIT_CLIENT_SECRET")

HAS_REAL_EXCHANGE_CONFIG = any([AEVO_API_KEY, DERIBIT_CLIENT_ID, DERIBIT_CLIENT_SECRET])

# Deribit public API (no auth for market data)
DERIBIT_API = "https://www.deribit.com/api/v2"
AEVO_API = "https://api.aevo.xyz"


def _fetch_deribit_quotes(asset: str, target_strikes: List[float]) -> Dict[str, List[ExchangeQuote]]:
    """
    Fetch live option book summaries from Deribit public API.
    Returns quotes mapped to nearest target strikes. Falls back to empty dict on failure.
    """
    if not HAS_REQUESTS:
        return {}
    currency = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}.get(asset.upper(), "BTC")
    url = f"{DERIBIT_API}/public/get_book_summary_by_currency"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "public/get_book_summary_by_currency", "params": {"currency": currency, "kind": "option"}}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        result = data.get("result") or []
    except Exception:
        return {}
    quotes: List[ExchangeQuote] = []
    for item in result:
        name = str(item.get("instrument_name", ""))
        parts = name.split("-")
        if len(parts) < 4:
            continue
        try:
            strike = float(parts[-2])
        except (ValueError, IndexError):
            continue
        opt_type = "Call" if parts[-1].upper() == "C" else "Put"
        price = float(item.get("mark_price") or item.get("last_price", 0))
        if price <= 0:
            bid = float(item.get("bid_price") or 0)
            ask = float(item.get("ask_price") or 0)
            price = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid or ask)
        if price <= 0:
            continue
        nearest = min(target_strikes, key=lambda s: abs(s - strike))
        quotes.append(ExchangeQuote("Deribit", opt_type, nearest, price))
    if not quotes:
        return {}
    return {"Deribit": quotes}


def _fetch_aevo_quotes(asset: str, target_strikes: List[float]) -> Dict[str, List[ExchangeQuote]]:
    """
    Fetch live option data from Aevo when AEVO_API_KEY is set.
    Tries REST endpoints; falls back to empty dict on failure.
    """
    if not AEVO_API_KEY or not HAS_REQUESTS:
        return {}
    try:
        # Aevo: GET /options returns option instruments for index
        idx = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(asset.upper())
        if not idx:
            return {}
        r = requests.get(
            f"{AEVO_API}/options",
            headers={"accept": "application/json", "aevo-key": AEVO_API_KEY},
            params={"asset": asset},
            timeout=8
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        opts = data if isinstance(data, list) else (data.get("options") or data.get("data") or [])
        if not isinstance(opts, list):
            return {}
        quotes: List[ExchangeQuote] = []
        for o in opts:
            if not isinstance(o, dict):
                continue
            stk = o.get("strike")
            side = str(o.get("option_type", o.get("side", ""))).lower()
            if stk is None or not side:
                continue
            strike = float(stk)
            opt_type = "Call" if "call" in side or side == "c" else "Put"
            mid = o.get("mark_price") or o.get("mid")
            if mid is None:
                b, a = o.get("bid"), o.get("ask")
                mid = (float(b) + float(a)) / 2 if (b is not None and a is not None) else None
            if mid is None or float(mid) <= 0:
                continue
            nearest = min(target_strikes, key=lambda s: abs(s - strike))
            quotes.append(ExchangeQuote("Aevo", opt_type, nearest, float(mid)))
        return {"Aevo": quotes} if quotes else {}
    except Exception:
        return {}


def _build_mock_exchange_quotes(option_data: dict) -> Dict[str, List[ExchangeQuote]]:
    """
    Build synthetic multi-exchange quotes by perturbing Synth fair prices.

    Heuristic (per strike / side):
    - "Aevo": slightly richer calls, cheaper puts.
    - "Deribit": slightly cheaper calls, richer puts.
    - "GenericMarket": small random-like wobble around fair price.
    """
    current = float(option_data.get("current_price", 0) or 0)
    if current <= 0:
        return {}

    calls = option_data.get("call_options") or {}
    puts = option_data.get("put_options") or {}

    quotes: Dict[str, List[ExchangeQuote]] = {
        "Aevo": [],
        "Deribit": [],
        "GenericMarket": [],
    }

    for k, v in calls.items():
        strike = float(k)
        fair = float(v)
        if fair <= 0:
            continue
        # Aevo: calls 8% richer
        quotes["Aevo"].append(
            ExchangeQuote("Aevo", "Call", strike, fair * 1.08)
        )
        # Deribit: calls 5% cheaper
        quotes["Deribit"].append(
            ExchangeQuote("Deribit", "Call", strike, fair * 0.95)
        )
        # GenericMarket: small ±3% wobble depending on moneyness
        bump = 1.03 if strike >= current else 0.97
        quotes["GenericMarket"].append(
            ExchangeQuote("GenericMarket", "Call", strike, fair * bump)
        )

    for k, v in puts.items():
        strike = float(k)
        fair = float(v)
        if fair <= 0:
            continue
        # Aevo: puts 5% cheaper
        quotes["Aevo"].append(
            ExchangeQuote("Aevo", "Put", strike, fair * 0.95)
        )
        # Deribit: puts 8% richer
        quotes["Deribit"].append(
            ExchangeQuote("Deribit", "Put", strike, fair * 1.08)
        )
        # GenericMarket: small ±3% wobble depending on moneyness
        bump = 0.97 if strike >= current else 1.03
        quotes["GenericMarket"].append(
            ExchangeQuote("GenericMarket", "Put", strike, fair * bump)
        )

    return {name: qs for name, qs in quotes.items() if qs}


def get_exchange_quotes(option_data: dict, asset: str = "BTC") -> Dict[str, List[ExchangeQuote]]:
    """
    Get multi-exchange quotes: live from Deribit (and Aevo when key set), mock fallback.
    When API keys are present, performs real network calls for live execution data.
    """
    current = float(option_data.get("current_price", 0) or 0)
    calls = option_data.get("call_options") or {}
    puts = option_data.get("put_options") or {}
    target_strikes = sorted({float(k) for k in list(calls.keys()) + list(puts.keys())})
    if current <= 0 or not target_strikes:
        return _build_mock_exchange_quotes(option_data)
    asset = str(option_data.get("asset", asset)).upper() or "BTC"
    by_exchange: Dict[str, List[ExchangeQuote]] = {}
    # Deribit: public API, always try
    if HAS_REQUESTS:
        deribit = _fetch_deribit_quotes(asset, target_strikes)
        by_exchange.update(deribit)
        aevo = _fetch_aevo_quotes(asset, target_strikes)
        by_exchange.update(aevo)
    mock = _build_mock_exchange_quotes(option_data)
    for name, qs in mock.items():
        if name not in by_exchange or not by_exchange[name]:
            by_exchange[name] = qs
    return by_exchange


def compute_divergence_summary(option_data: dict, by_exchange: Dict[str, List[ExchangeQuote]] | None = None) -> Dict[str, dict]:
    """
    For each mock exchange, compute how far its quotes diverge from Synth fair prices.

    Returns:
        {
          "Aevo": {
             "avg_abs_div": 0.07,          # 7% average |market - fair| / fair
             "max_abs_div": 0.15,
             "rich_calls": 0.08,           # mean signed divergence for calls
             "rich_puts": -0.04,           # negative = cheaper than fair
             "n_quotes": 42,
          },
          ...
        }
    """
    current = float(option_data.get("current_price", 0) or 0)
    if current <= 0:
        return {}

    calls = {float(k): float(v) for k, v in (option_data.get("call_options") or {}).items() if float(v) > 0}
    puts = {float(k): float(v) for k, v in (option_data.get("put_options") or {}).items() if float(v) > 0}
    if not calls and not puts:
        return {}

    if by_exchange is None:
        by_exchange = get_exchange_quotes(option_data)
    summaries: Dict[str, dict] = {}

    for name, quotes in by_exchange.items():
        if not quotes:
            continue
        abs_divs: List[float] = []
        call_divs: List[float] = []
        put_divs: List[float] = []
        for q in quotes:
            if q.option_type == "Call":
                fair = calls.get(q.strike)
            else:
                fair = puts.get(q.strike)
            if fair is None or fair <= 0:
                continue
            div = (q.price - fair) / fair
            abs_divs.append(abs(div))
            if q.option_type == "Call":
                call_divs.append(div)
            else:
                put_divs.append(div)
        if not abs_divs:
            continue
        summaries[name] = {
            "avg_abs_div": sum(abs_divs) / len(abs_divs),
            "max_abs_div": max(abs_divs),
            "rich_calls": sum(call_divs) / len(call_divs) if call_divs else 0.0,
            "rich_puts": sum(put_divs) / len(put_divs) if put_divs else 0.0,
            "n_quotes": len(abs_divs),
            "has_real_config": HAS_REAL_EXCHANGE_CONFIG,
        }

    return summaries


def compute_divergence_alpha(option_data: dict, by_exchange: Dict[str, List[ExchangeQuote]] | None = None) -> float:
    """
    Edge detection: alpha over agreement.
    Higher divergence of Synth from market mean = higher alpha = more conviction.
    Uses z-score: z = (synth - market_mean) / market_std.
    Returns mean |z| across strikes/sides as alpha factor (0 = no edge, >1 = meaningful edge).
    """
    calls = {float(k): float(v) for k, v in (option_data.get("call_options") or {}).items() if float(v) > 0}
    puts = {float(k): float(v) for k, v in (option_data.get("put_options") or {}).items() if float(v) > 0}
    if not calls and not puts:
        return 0.0
    if by_exchange is None:
        by_exchange = get_exchange_quotes(option_data)
    z_scores: List[float] = []

    def _add_z(strike: float, fair: float, opt_type: str) -> None:
        prices = []
        for qs in by_exchange.values():
            for q in qs:
                if q.option_type == opt_type and abs(q.strike - strike) < 1:
                    prices.append(q.price)
                    break
        if len(prices) < 2:
            return
        mean_p = sum(prices) / len(prices)
        var = sum((p - mean_p) ** 2 for p in prices) / len(prices)
        std_p = math.sqrt(var) if var > 0 else 1e-9
        z = (fair - mean_p) / std_p
        z_scores.append(abs(z))

    for strike, fair in calls.items():
        _add_z(strike, fair, "Call")
    for strike, fair in puts.items():
        _add_z(strike, fair, "Put")
    return sum(z_scores) / len(z_scores) if z_scores else 0.0


def compute_best_venue(
    strategy: Any,
    by_exchange: Dict[str, List[ExchangeQuote]],
    option_data: dict,
) -> Tuple[str | None, float | None]:
    """
    Point to the winner: which exchange offers best execution for this strategy.
    Returns (venue_name, best_net_cost) or (None, None) if no data.
    For debit: lower cost is better. For credit: higher (less negative) is better.
    """
    if not strategy or not hasattr(strategy, "legs") or not strategy.legs:
        return None, None
    calls = {float(k): float(v) for k, v in (option_data.get("call_options") or {}).items()}
    puts = {float(k): float(v) for k, v in (option_data.get("put_options") or {}).items()}
    strikes = sorted(set(calls.keys()) | set(puts.keys()))
    if not strikes:
        return None, None
    best_venue: str | None = None
    best_net: float | None = None
    for ex_name, qs in by_exchange.items():
        price_map: Dict[Tuple[float, str], float] = {}
        for q in qs:
            price_map[(q.strike, q.option_type)] = q.price
        net = 0.0
        for leg in strategy.legs:
            strike = leg.strike
            opt_type = leg.option_type
            price = price_map.get((strike, opt_type))
            if price is None:
                nearest_s = min(strikes, key=lambda s: abs(s - strike))
                price = calls.get(nearest_s) if opt_type == "Call" else puts.get(nearest_s)
            if price is None:
                break
            mult = leg.quantity if leg.action.upper() == "BUY" else -leg.quantity
            net += price * mult
        else:
            if best_net is None or net > best_net:
                best_net = net
                best_venue = ex_name
    return best_venue, best_net

