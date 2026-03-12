"""Market Line Shopping: fetch and compare option prices across exchanges.
Supports Deribit and Aevo for crypto assets (BTC, ETH, SOL).
Live mode fetches real-time quotes via public APIs (no auth needed).
Mock mode loads from JSON when mock_dir is provided."""

import json
import math
import os
from dataclasses import dataclass

CRYPTO_ASSETS = {"BTC", "ETH", "SOL"}
EXCHANGES = ["deribit", "aevo"]

DERIBIT_API = "https://www.deribit.com/api/v2/public"
AEVO_API = "https://api.aevo.xyz"
HTTP_TIMEOUT = 10


@dataclass
class ExchangeQuote:
    """Normalized option quote from any exchange."""
    exchange: str       # "deribit" or "aevo"
    asset: str
    strike: float
    option_type: str    # "call" or "put"
    bid: float
    ask: float
    mid: float          # (bid + ask) / 2
    implied_vol: float | None  # from exchange, if available


@dataclass
class EdgeMetrics:
    """Statistical edge for a strike/type: Synth vs market consensus."""
    synth_fair: float
    market_mean: float      # mean of exchange mids
    std_dev: float          # population std across all pricing sources
    z_score: float          # (synth - market_mean) / std
    divergence_pct: float   # simple % divergence for display
    n_sources: int          # pricing sources count (synth + exchanges)
    best_venue: str         # exchange with best execution price
    best_price: float       # best execution price (lowest ask)
    edge_label: str         # "STRONG" |z|>=2, "MODERATE" |z|>=1, "WEAK" |z|>=0.5, "NONE"


# --- HTTP helper ---

def _http_get_json(url: str, timeout: int = HTTP_TIMEOUT) -> dict | list:
    """GET JSON from URL. Raises on failure."""
    import requests as _req
    resp = _req.get(url, timeout=timeout, headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


# --- Fetch functions ---

def fetch_deribit(asset: str, mock_dir: str | None = None) -> list[ExchangeQuote]:
    """Fetch option quotes from Deribit. Mock JSON when mock_dir is provided,
    otherwise live from Deribit public API (no auth needed)."""
    if asset not in CRYPTO_ASSETS:
        return []
    if mock_dir is not None:
        return _load_mock(asset, mock_dir, "deribit")
    return _fetch_deribit_live(asset)


def fetch_aevo(asset: str, mock_dir: str | None = None) -> list[ExchangeQuote]:
    """Fetch option quotes from Aevo. Mock JSON when mock_dir is provided,
    otherwise live from Aevo public API (no auth needed)."""
    if asset not in CRYPTO_ASSETS:
        return []
    if mock_dir is not None:
        return _load_mock(asset, mock_dir, "aevo")
    return _fetch_aevo_live(asset)


def fetch_all_exchanges(asset: str, mock_dir: str | None = None) -> list[ExchangeQuote]:
    """Fetch quotes from all supported exchanges and combine."""
    if asset not in CRYPTO_ASSETS:
        return []
    return fetch_deribit(asset, mock_dir) + fetch_aevo(asset, mock_dir)


def _fetch_deribit_live(asset: str) -> list[ExchangeQuote]:
    """Fetch all option book summaries from Deribit public API (single request).
    Deribit prices are in base currency fraction — multiply by underlying_price for USD."""
    url = f"{DERIBIT_API}/get_book_summary_by_currency?currency={asset}&kind=option"
    try:
        data = _http_get_json(url)
    except Exception:
        return []
    quotes = []
    for item in data.get("result", []):
        name = item.get("instrument_name", "")
        parsed = _parse_instrument_key(name)
        if parsed is None:
            continue
        strike, opt_type = parsed
        underlying = float(item.get("underlying_price") or 0)
        if underlying <= 0:
            continue
        bid_raw = item.get("bid_price")
        ask_raw = item.get("ask_price")
        if not bid_raw or not ask_raw:
            continue
        bid_f, ask_f = float(bid_raw), float(ask_raw)
        if bid_f <= 0 or ask_f <= 0:
            continue
        bid = bid_f * underlying
        ask = ask_f * underlying
        mid = (bid + ask) / 2
        iv = float(item["mark_iv"]) if item.get("mark_iv") else None
        quotes.append(ExchangeQuote("deribit", asset, strike, opt_type, bid, ask, mid, iv))
    return quotes


def _fetch_aevo_live(asset: str) -> list[ExchangeQuote]:
    """Fetch option quotes from Aevo public API.
    Discovers instruments via /markets, fetches orderbooks in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        markets = _http_get_json(f"{AEVO_API}/markets?asset={asset}&instrument_type=OPTION")
    except Exception:
        try:
            all_mkts = _http_get_json(f"{AEVO_API}/markets")
            markets = [m for m in all_mkts if isinstance(m, dict)
                       and m.get("underlying_asset", "").upper() == asset
                       and m.get("instrument_type", "").upper() == "OPTION"]
        except Exception:
            return []
    if not isinstance(markets, list):
        return []
    active = [m for m in markets if m.get("is_active", True)][:40]
    if not active:
        return []

    def _fetch_one(mkt):
        name = mkt.get("instrument_name", "")
        if not name:
            return None
        parsed = _parse_instrument_key(name)
        if parsed is None:
            return None
        strike, opt_type = parsed
        try:
            book = _http_get_json(f"{AEVO_API}/orderbook?instrument_name={name}", timeout=5)
        except Exception:
            return None
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return None
        try:
            bid = float(bids[0][0])
            ask = float(asks[0][0])
        except (IndexError, ValueError, TypeError):
            return None
        if bid <= 0 or ask <= 0:
            return None
        mid = (bid + ask) / 2
        bid_iv = float(bids[0][2]) if len(bids[0]) > 2 else None
        ask_iv = float(asks[0][2]) if len(asks[0]) > 2 else None
        return ExchangeQuote("aevo", asset, strike, opt_type, bid, ask, mid, _avg_iv(bid_iv, ask_iv))

    quotes = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_fetch_one, m) for m in active]
        for f in as_completed(futures):
            try:
                q = f.result()
                if q:
                    quotes.append(q)
            except Exception:
                pass
    return quotes


# --- Price comparison ---

def best_market_price(quotes: list[ExchangeQuote], strike: float, option_type: str) -> ExchangeQuote | None:
    """Find best (lowest ask) quote across exchanges for a given strike/type."""
    matches = [q for q in quotes if q.strike == strike and q.option_type == option_type]
    if not matches:
        return None
    return min(matches, key=lambda q: q.ask)


def best_execution_price(quotes: list[ExchangeQuote], strike: float,
                         option_type: str, action: str = "BUY") -> ExchangeQuote | None:
    """Action-aware best execution: lowest ask for BUY, highest bid for SELL."""
    matches = [q for q in quotes if q.strike == strike and q.option_type == option_type]
    if not matches:
        return None
    if action == "SELL":
        return max(matches, key=lambda q: q.bid)
    return min(matches, key=lambda q: q.ask)


# --- Edge detection ---

def compute_divergence(synth_fair: float, market_mid: float) -> float:
    """Compute divergence percentage: (synth_fair - market_mid) / synth_fair * 100.
    Positive = market cheaper than fair (favorable entry).
    Returns 0.0 if synth_fair is zero."""
    if synth_fair == 0:
        return 0.0
    return (synth_fair - market_mid) / synth_fair * 100


def compute_edge(synth_fair: float, quotes: list[ExchangeQuote],
                 strike: float, option_type: str) -> EdgeMetrics | None:
    """Statistical edge via z-score: how far Synth deviates from market consensus.
    Uses population std dev across all pricing sources (Synth + exchange mids).
    Positive z = Synth values higher than market (market underpriced).
    Higher |z| = stronger conviction — alpha is in disagreement."""
    matches = [q for q in quotes if q.strike == strike and q.option_type == option_type]
    if not matches:
        return None

    exchange_mids = [q.mid for q in matches]
    market_mean = sum(exchange_mids) / len(exchange_mids)

    all_prices = [synth_fair] + exchange_mids
    n = len(all_prices)
    mean_all = sum(all_prices) / n
    variance = sum((p - mean_all) ** 2 for p in all_prices) / n
    std = math.sqrt(variance) if variance > 0 else 0

    # Floor: 0.1% of market mean to avoid near-zero division
    noise = max(std, market_mean * 0.001)

    z_score = (synth_fair - market_mean) / noise
    div_pct = compute_divergence(synth_fair, market_mean)

    best = min(matches, key=lambda q: q.ask)

    if abs(z_score) >= 2.0:
        edge_label = "STRONG"
    elif abs(z_score) >= 1.0:
        edge_label = "MODERATE"
    elif abs(z_score) >= 0.5:
        edge_label = "WEAK"
    else:
        edge_label = "NONE"

    return EdgeMetrics(
        synth_fair=synth_fair,
        market_mean=market_mean,
        std_dev=noise,
        z_score=z_score,
        divergence_pct=div_pct,
        n_sources=n,
        best_venue=best.exchange,
        best_price=best.ask,
        edge_label=edge_label,
    )


def leg_divergences(strategy, quotes: list[ExchangeQuote], synth_options: dict) -> dict:
    """For each leg, compute edge metrics with action-aware execution routing.
    Returns {leg_index: {"divergence_pct", "z_score", "edge_label",
                          "best_exchange", "best_price", "synth_fair",
                          "market_mean", "std_dev"}}"""
    result = {}
    call_opts = synth_options.get("call_options", {})
    put_opts = synth_options.get("put_options", {})
    for i, leg in enumerate(strategy.legs):
        strike_key = str(int(leg.strike)) if leg.strike == int(leg.strike) else str(leg.strike)
        synth_fair = (call_opts if leg.option_type.lower() == "call" else put_opts).get(strike_key)
        if synth_fair is None:
            continue
        synth_fair = float(synth_fair)
        edge = compute_edge(synth_fair, quotes, leg.strike, leg.option_type.lower())
        if edge is None:
            continue
        # Route to best execution: lowest ask for BUY, highest bid for SELL
        exec_q = best_execution_price(quotes, leg.strike, leg.option_type.lower(), leg.action)
        if exec_q is None:
            continue
        exec_price = exec_q.ask if leg.action == "BUY" else exec_q.bid
        result[i] = {
            "divergence_pct": edge.divergence_pct,
            "z_score": edge.z_score,
            "edge_label": edge.edge_label,
            "best_exchange": exec_q.exchange,
            "best_price": exec_price,
            "synth_fair": synth_fair,
            "market_mean": edge.market_mean,
            "std_dev": edge.std_dev,
        }
    return result


def strategy_divergence(strategy, quotes: list[ExchangeQuote], synth_options: dict) -> float | None:
    """Average z-score across all legs. None if no exchange data for any leg.
    Positive = Synth values strategy higher than market (potential edge)."""
    divs = leg_divergences(strategy, quotes, synth_options)
    if not divs:
        return None
    return sum(d["z_score"] for d in divs.values()) / len(divs)


# --- Mock loaders ---

def _parse_instrument_key(key: str) -> tuple[float, str] | None:
    """Parse strike and option type from instrument key like 'BTC-26FEB26-67500-C' or 'BTC-67500-C'."""
    parts = key.split("-")
    try:
        strike = float(parts[-2])
        opt_type = "call" if parts[-1] == "C" else "put"
        return strike, opt_type
    except (IndexError, ValueError):
        return None


def _avg_iv(bid_iv: float | None, ask_iv: float | None) -> float | None:
    """Average bid/ask IV, falling back to whichever is available."""
    if bid_iv is not None and ask_iv is not None:
        return (bid_iv + ask_iv) / 2
    return bid_iv if bid_iv is not None else ask_iv


def _load_mock(asset: str, mock_dir: str, exchange: str) -> list[ExchangeQuote]:
    """Load mock exchange data. Handles both Deribit format (bid/ask fields)
    and Aevo format (bids/asks arrays)."""
    path = os.path.join(mock_dir, f"{exchange}_{asset}.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    quotes = []
    order_books = data.get("order_books", {})
    is_aevo = exchange == "aevo"
    for key, book in order_books.items():
        parsed = _parse_instrument_key(key)
        if parsed is None:
            continue
        strike, opt_type = parsed
        if is_aevo:
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if not bids or not asks:
                continue
            bid = float(bids[0][0])
            ask = float(asks[0][0])
            bid_iv = float(bids[0][2]) if len(bids[0]) > 2 else None
            ask_iv = float(asks[0][2]) if len(asks[0]) > 2 else None
        else:
            bid = float(book.get("bid", 0))
            ask = float(book.get("ask", 0))
            bid_iv = float(book["bid_iv"]) if "bid_iv" in book else None
            ask_iv = float(book["ask_iv"]) if "ask_iv" in book else None
        quotes.append(ExchangeQuote(
            exchange=exchange, asset=asset, strike=strike,
            option_type=opt_type, bid=bid, ask=ask, mid=(bid + ask) / 2,
            implied_vol=_avg_iv(bid_iv, ask_iv),
        ))
    return quotes
