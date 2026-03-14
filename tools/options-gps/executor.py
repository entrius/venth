"""Autonomous execution engine for Options GPS.
Consumes pipeline.py data classes and exchange.py pricing functions.
Supports Deribit (JSON-RPC 2.0), Aevo (REST + EIP-712 L2 signing), and dry-run simulation.
Auto-routing uses leg_divergences to pick the best venue per leg.
Includes order monitoring, auto-cancel on partial failure, slippage protection,
max-loss budget, position sizing, and execution audit logging."""

import hashlib
import hmac
import json
import os
import random
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

try:
    from eth_account import Account as _EthAccount
    _HAS_ETH_ACCOUNT = True
except ImportError:
    _HAS_ETH_ACCOUNT = False

from exchange import best_execution_price, leg_divergences


# --- Helpers ---

def _now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# --- Data classes ---

@dataclass
class OrderRequest:
    instrument: str       # "BTC-26FEB26-67500-C" (Deribit) or "BTC-67500-C" (Aevo)
    action: str           # "BUY" | "SELL"
    quantity: int
    order_type: str       # "limit" | "market"
    price: float          # limit price from best_execution_price
    exchange: str         # "deribit" | "aevo" | "dry_run"
    leg_index: int        # index into strategy.legs
    strike: float = 0.0   # strike price for quote lookup
    option_type: str = ""  # "call" | "put" for quote lookup


@dataclass
class OrderResult:
    order_id: str
    status: str           # "filled" | "open" | "rejected" | "error" | "simulated" | "timeout"
    fill_price: float
    fill_quantity: int
    instrument: str
    action: str
    exchange: str
    error: str | None = None
    slippage_pct: float = 0.0   # actual slippage vs limit price
    timestamp: str = ""         # ISO 8601 when order was placed
    latency_ms: float = 0.0    # round-trip latency for the order


@dataclass
class ExecutionPlan:
    strategy_description: str
    strategy_type: str
    exchange: str
    asset: str
    expiry: str
    orders: list[OrderRequest] = field(default_factory=list)
    estimated_cost: float = 0.0
    estimated_max_loss: float = 0.0
    dry_run: bool = False
    timeout_seconds: float = 30.0  # order monitoring timeout


@dataclass
class ExecutionReport:
    plan: ExecutionPlan
    results: list[OrderResult] = field(default_factory=list)
    all_filled: bool = False
    net_cost: float = 0.0
    summary: str = ""
    slippage_total: float = 0.0     # total slippage across all fills
    started_at: str = ""            # ISO 8601 execution start
    finished_at: str = ""           # ISO 8601 execution end
    cancelled_orders: list[str] = field(default_factory=list)  # order IDs cancelled on failure


# --- Instrument name builders ---

def deribit_instrument_name(asset: str, expiry: str, strike: float, option_type: str) -> str:
    """Build Deribit instrument name like BTC-26FEB26-67500-C.
    Parses ISO 8601 expiry string to DDMonYY format."""
    strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    ot = "C" if option_type.lower() == "call" else "P"
    date_part = _format_deribit_date(expiry)
    return f"{asset}-{date_part}-{strike_str}-{ot}"


def aevo_instrument_name(asset: str, strike: float, option_type: str) -> str:
    """Build Aevo instrument name like BTC-67500-C. No date in Aevo names."""
    strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    ot = "C" if option_type.lower() == "call" else "P"
    return f"{asset}-{strike_str}-{ot}"


def _format_deribit_date(expiry: str) -> str:
    """Parse ISO 8601 expiry to Deribit DDMonYY format (e.g. 26FEB26).
    Falls back to UNKNOWN if parsing fails."""
    if not expiry:
        return "UNKNOWN"
    try:
        expiry = expiry.replace("Z", "+00:00")
        dt = datetime.fromisoformat(expiry)
        return dt.strftime("%d%b%y").upper()
    except (ValueError, TypeError):
        return "UNKNOWN"


# --- Retry helpers ---

def _is_retryable(err: Exception) -> bool:
    """True for transient HTTP errors worth retrying (429, 502, 503, timeouts)."""
    if isinstance(err, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(err, requests.HTTPError) and err.response is not None:
        return err.response.status_code in (429, 502, 503)
    return False


def _retry(fn, max_attempts: int = 3):
    """Call fn() with exponential backoff on retryable errors."""
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            if _is_retryable(e) and attempt < max_attempts - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise


# --- Executors ---

class BaseExecutor(ABC):
    @abstractmethod
    def authenticate(self) -> bool:
        ...

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...


class DryRunExecutor(BaseExecutor):
    """Simulates order execution using exchange quote data. No network calls.
    Stateful: tracks placed orders for realistic status queries and cancellation."""

    def __init__(self, exchange_quotes: list):
        self.exchange_quotes = exchange_quotes
        self._orders: dict[str, OrderResult] = {}

    def authenticate(self) -> bool:
        return True

    def place_order(self, order: OrderRequest) -> OrderResult:
        t0 = time.monotonic()
        ts = _now_iso()
        if not order.strike or not order.option_type:
            result = OrderResult(
                order_id=f"dry-{uuid.uuid4().hex[:8]}",
                status="error", fill_price=0.0, fill_quantity=0,
                instrument=order.instrument, action=order.action,
                exchange="dry_run", error="Missing strike or option_type on order",
                timestamp=ts,
            )
            self._orders[result.order_id] = result
            return result
        quote = best_execution_price(
            self.exchange_quotes, order.strike, order.option_type, order.action,
        )
        if quote is None:
            fill_price = order.price
        else:
            fill_price = quote.ask if order.action == "BUY" else quote.bid
        slippage = _compute_slippage(order.price, fill_price, order.action)
        latency = (time.monotonic() - t0) * 1000
        result = OrderResult(
            order_id=f"dry-{uuid.uuid4().hex[:8]}",
            status="simulated",
            fill_price=fill_price,
            fill_quantity=order.quantity,
            instrument=order.instrument,
            action=order.action,
            exchange="dry_run",
            slippage_pct=slippage,
            timestamp=ts,
            latency_ms=round(latency, 2),
        )
        self._orders[result.order_id] = result
        return result

    def get_order_status(self, order_id: str) -> OrderResult:
        if order_id in self._orders:
            return self._orders[order_id]
        return OrderResult(
            order_id=order_id, status="not_found", fill_price=0.0,
            fill_quantity=0, instrument="", action="", exchange="dry_run",
        )

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = "cancelled"
            return True
        return False


class DeribitExecutor(BaseExecutor):
    """Executes orders on Deribit via JSON-RPC 2.0 over POST.
    Uses `contracts` parameter for unambiguous option sizing.
    Converts USD prices to BTC using index price, snaps to order book,
    and aligns to tick size (0.0005 BTC).
    Retries on transient errors (429, 502, 503, timeout)."""

    TICK_SIZE = 0.0005  # Deribit option price tick size in BTC

    def __init__(self, client_id: str, client_secret: str, testnet: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = (
            "https://test.deribit.com/api/v2"
            if testnet
            else "https://www.deribit.com/api/v2"
        )
        self.token: str | None = None
        self._rpc_id = 0
        self._index_cache: dict[str, float] = {}  # asset -> USD index price

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _rpc(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC 2.0 POST request with retry on transient errors."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        def _call():
            resp = requests.post(self.base_url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"].get("message", str(data["error"])))
            return data.get("result", {})

        return _retry(_call)

    def _get_index_price(self, asset: str) -> float:
        """Fetch the USD index price for an asset (e.g. BTC-USD).
        Cached per session to avoid repeated calls."""
        if asset in self._index_cache:
            return self._index_cache[asset]
        try:
            index_name = f"{asset.lower()}_usd"
            result = self._rpc("public/get_index_price", {"index_name": index_name})
            price = float(result.get("index_price", 0))
            if price > 0:
                self._index_cache[asset] = price
            return price
        except Exception:
            return 0.0

    def _get_book_price(self, instrument: str, action: str) -> float | None:
        """Fetch live best bid/ask from Deribit order book.
        Returns best ask for BUY, best bid for SELL.
        Falls back to mark_price when best bid/ask is unavailable."""
        try:
            result = self._rpc("public/get_order_book", {
                "instrument_name": instrument, "depth": 1,
            })
            if action == "BUY":
                price = float(result.get("best_ask_price", 0))
            else:
                price = float(result.get("best_bid_price", 0))
            if price and price > 0:
                return price
            # Fallback to mark_price when book is empty
            mark = float(result.get("mark_price", 0))
            return mark if mark > 0 else None
        except Exception:
            return None

    @staticmethod
    def _align_tick(price: float, tick_size: float = 0.0005) -> float:
        """Round a BTC price to the nearest tick size."""
        if tick_size <= 0:
            return price
        return round(round(price / tick_size) * tick_size, 10)

    def _usd_to_btc(self, price_usd: float, asset: str) -> float:
        """Convert a USD option price to BTC using the index price.
        Falls back to returning the USD price if index is unavailable."""
        index = self._get_index_price(asset)
        if index <= 0:
            return price_usd
        return self._align_tick(price_usd / index)

    def authenticate(self) -> bool:
        if self.token:
            return True
        try:
            result = self._rpc("public/auth", {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            self.token = result.get("access_token")
            return self.token is not None
        except Exception:
            return False

    def place_order(self, order: OrderRequest) -> OrderResult:
        t0 = time.monotonic()
        ts = _now_iso()
        # Convert USD limit price to BTC for Deribit
        asset = order.instrument.split("-")[0] if "-" in order.instrument else ""
        price_btc = self._usd_to_btc(order.price, asset) if asset else order.price
        # Snap to live order book if available (tighter price)
        book_price = self._get_book_price(order.instrument, order.action)
        if book_price is not None and book_price > 0:
            if order.action == "BUY":
                price_btc = min(price_btc, book_price) if price_btc > 0 else book_price
            else:
                price_btc = max(price_btc, book_price)
        method = "private/buy" if order.action == "BUY" else "private/sell"
        params = {
            "instrument_name": order.instrument,
            "contracts": order.quantity,
            "type": order.order_type,
        }
        if order.order_type == "limit":
            params["price"] = price_btc
        try:
            result = self._rpc(method, params)
            latency = (time.monotonic() - t0) * 1000
            order_data = result.get("order", {})
            fill_price = float(order_data.get("average_price", 0))
            slippage = _compute_slippage(order.price, fill_price, order.action)
            return OrderResult(
                order_id=order_data.get("order_id", ""),
                status=order_data.get("order_state", "error"),
                fill_price=fill_price,
                fill_quantity=int(order_data.get("filled_amount", 0)),
                instrument=order.instrument,
                action=order.action,
                exchange="deribit",
                slippage_pct=slippage,
                timestamp=ts,
                latency_ms=round(latency, 2),
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            return OrderResult(
                order_id="", status="error", fill_price=0.0,
                fill_quantity=0, instrument=order.instrument,
                action=order.action, exchange="deribit", error=str(e),
                timestamp=ts, latency_ms=round(latency, 2),
            )

    def get_order_status(self, order_id: str) -> OrderResult:
        try:
            result = self._rpc("private/get_order_state", {"order_id": order_id})
            return OrderResult(
                order_id=order_id,
                status=result.get("order_state", "unknown"),
                fill_price=float(result.get("average_price", 0)),
                fill_quantity=int(result.get("filled_amount", 0)),
                instrument=result.get("instrument_name", ""),
                action="BUY" if result.get("direction") == "buy" else "SELL",
                exchange="deribit",
            )
        except Exception:
            return OrderResult(
                order_id=order_id, status="unknown", fill_price=0.0,
                fill_quantity=0, instrument="", action="", exchange="deribit",
            )

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._rpc("private/cancel", {"order_id": order_id})
            return True
        except Exception:
            return False


class AevoExecutor(BaseExecutor):
    """Executes orders on Aevo via REST API with EIP-712 L2 signing.
    Aevo is an OP Stack L2 — every order must carry an EIP-712 signature
    signed by the trading key. The REST HMAC headers authenticate the
    request; the payload signature authorises the on-chain settlement.
    Retries on transient errors (429, 502, 503, timeout)."""

    # EIP-712 domain separators (per Aevo SDK)
    _DOMAIN_MAINNET = {"name": "Aevo Mainnet", "version": "1", "chainId": 1}
    _DOMAIN_TESTNET = {"name": "Aevo Testnet", "version": "1", "chainId": 11155111}

    # EIP-712 Order struct type definition
    _ORDER_TYPES = {
        "Order": [
            {"name": "maker", "type": "address"},
            {"name": "isBuy", "type": "bool"},
            {"name": "limitPrice", "type": "uint256"},
            {"name": "amount", "type": "uint256"},
            {"name": "salt", "type": "uint256"},
            {"name": "instrument", "type": "uint256"},
            {"name": "timestamp", "type": "uint256"},
        ],
    }

    def __init__(self, api_key: str, api_secret: str,
                 signing_key: str, wallet_address: str,
                 testnet: bool = False):
        if not _HAS_ETH_ACCOUNT:
            raise ImportError(
                "eth-account is required for Aevo L2 signing. "
                "Install it: pip install eth-account"
            )
        self.api_key = api_key
        self.api_secret = api_secret
        self.signing_key = signing_key
        self.wallet_address = wallet_address
        self.testnet = testnet
        self.base_url = (
            "https://api-testnet.aevo.xyz"
            if testnet
            else "https://api.aevo.xyz"
        )
        self._domain = self._DOMAIN_TESTNET if testnet else self._DOMAIN_MAINNET
        # Cache: human-readable instrument name → numeric instrument ID
        self._instrument_cache: dict[str, int] = {}

    def _sign_hmac(self, timestamp: str, method: str, path: str, body: str) -> str:
        """HMAC-SHA256 for REST API authentication headers."""
        message = f"{timestamp}{method}{path}{body}"
        return hmac.new(
            self.api_secret.encode(), message.encode(), hashlib.sha256,
        ).hexdigest()

    def _headers(self, method: str = "GET", path: str = "/", body: str = "") -> dict:
        ts = str(int(time.time()))
        return {
            "AEVO-KEY": self.api_key,
            "AEVO-TIMESTAMP": ts,
            "AEVO-SIGNATURE": self._sign_hmac(ts, method, path, body),
            "Content-Type": "application/json",
        }

    def _sign_order(self, instrument_id: int, is_buy: bool,
                    limit_price: float, quantity: int,
                    timestamp: int) -> tuple[int, str]:
        """EIP-712 L2 order signing. Returns (salt, signature_hex)."""
        salt = random.randint(0, 10**10)
        message_data = {
            "maker": self.wallet_address,
            "isBuy": is_buy,
            "limitPrice": int(limit_price * 10**6),
            "amount": int(quantity * 10**6),
            "salt": salt,
            "instrument": instrument_id,
            "timestamp": timestamp,
        }
        signed = _EthAccount.sign_typed_data(
            self.signing_key,
            domain_data=self._domain,
            message_types=self._ORDER_TYPES,
            message_data=message_data,
        )
        return salt, "0x" + signed.signature.hex()

    def _resolve_instrument_id(self, instrument_name: str) -> int:
        """Look up numeric instrument ID from Aevo /markets endpoint.
        Caches results for the session."""
        if instrument_name in self._instrument_cache:
            return self._instrument_cache[instrument_name]
        try:
            resp = requests.get(
                f"{self.base_url}/markets",
                headers=self._headers("GET", "/markets"),
                params={"instrument_name": instrument_name},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            # Response can be a list of markets or a single market
            if isinstance(data, list):
                for market in data:
                    name = market.get("instrument_name", "")
                    mid = int(market.get("instrument_id", 0))
                    self._instrument_cache[name] = mid
                if instrument_name in self._instrument_cache:
                    return self._instrument_cache[instrument_name]
            elif isinstance(data, dict):
                mid = int(data.get("instrument_id", 0))
                self._instrument_cache[instrument_name] = mid
                return mid
        except Exception:
            pass
        raise ValueError(
            f"Could not resolve Aevo instrument ID for '{instrument_name}'. "
            "Verify the instrument exists on Aevo."
        )

    def authenticate(self) -> bool:
        return bool(self.api_key and self.api_secret
                     and self.signing_key and self.wallet_address)

    def place_order(self, order: OrderRequest) -> OrderResult:
        t0 = time.monotonic()
        ts = _now_iso()
        try:
            instrument_id = self._resolve_instrument_id(order.instrument)
            is_buy = order.action == "BUY"
            timestamp = int(time.time())
            salt, signature = self._sign_order(
                instrument_id, is_buy, order.price, order.quantity, timestamp,
            )
            payload = {
                "maker": self.wallet_address,
                "is_buy": is_buy,
                "instrument": instrument_id,
                "limit_price": str(int(order.price * 10**6)),
                "amount": str(int(order.quantity * 10**6)),
                "salt": str(salt),
                "signature": signature,
                "timestamp": timestamp,
            }
            if order.order_type == "limit":
                payload["post_only"] = True
            body = json.dumps(payload)

            def _call():
                resp = requests.post(
                    f"{self.base_url}/orders",
                    data=body,
                    headers=self._headers("POST", "/orders", body),
                    timeout=10,
                )
                resp.raise_for_status()
                return resp.json()

            data = _retry(_call)
            latency = (time.monotonic() - t0) * 1000
            fill_price = float(data.get("avg_price", 0))
            slippage = _compute_slippage(order.price, fill_price, order.action)
            return OrderResult(
                order_id=data.get("order_id", ""),
                status=data.get("status", "error"),
                fill_price=fill_price,
                fill_quantity=int(data.get("filled", 0)),
                instrument=order.instrument,
                action=order.action,
                exchange="aevo",
                slippage_pct=slippage,
                timestamp=ts,
                latency_ms=round(latency, 2),
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            return OrderResult(
                order_id="", status="error", fill_price=0.0,
                fill_quantity=0, instrument=order.instrument,
                action=order.action, exchange="aevo", error=str(e),
                timestamp=ts, latency_ms=round(latency, 2),
            )

    def get_order_status(self, order_id: str) -> OrderResult:
        try:
            resp = requests.get(
                f"{self.base_url}/orders/{order_id}",
                headers=self._headers("GET", f"/orders/{order_id}"),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return OrderResult(
                order_id=order_id,
                status=data.get("status", "unknown"),
                fill_price=float(data.get("avg_price", 0)),
                fill_quantity=int(data.get("filled", 0)),
                instrument=data.get("instrument_name", ""),
                action="BUY" if data.get("is_buy") else "SELL",
                exchange="aevo",
            )
        except Exception:
            return OrderResult(
                order_id=order_id, status="unknown", fill_price=0.0,
                fill_quantity=0, instrument="", action="", exchange="aevo",
            )

    def cancel_order(self, order_id: str) -> bool:
        try:
            resp = requests.delete(
                f"{self.base_url}/orders/{order_id}",
                headers=self._headers("DELETE", f"/orders/{order_id}"),
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception:
            return False


# --- Slippage ---

def _compute_slippage(limit_price: float, fill_price: float, action: str) -> float:
    """Compute slippage as a percentage. Positive = worse than limit."""
    if limit_price <= 0:
        return 0.0
    if action == "BUY":
        return ((fill_price - limit_price) / limit_price) * 100
    else:
        return ((limit_price - fill_price) / limit_price) * 100


def check_slippage(result: OrderResult, max_slippage_pct: float) -> bool:
    """Return True if slippage is within acceptable bounds."""
    return result.slippage_pct <= max_slippage_pct


# --- Order monitoring ---

def _monitor_order(executor: BaseExecutor, order_id: str,
                   timeout_seconds: float = 30.0,
                   poll_interval: float = 1.0) -> OrderResult:
    """Poll order status until terminal state or timeout.
    Returns the final OrderResult. On timeout, attempts to cancel the order."""
    _terminal = ("filled", "rejected", "cancelled", "error", "simulated")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = executor.get_order_status(order_id)
        if result.status in _terminal:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, max(0, remaining)))
    # Timeout: attempt cancel
    executor.cancel_order(order_id)
    return OrderResult(
        order_id=order_id, status="timeout", fill_price=0.0,
        fill_quantity=0, instrument="", action="", exchange="",
    )


def _cancel_filled_orders(results: list[OrderResult],
                          get_exec_fn) -> list[str]:
    """Cancel already-filled orders when a later leg fails.
    get_exec_fn(exchange) -> BaseExecutor for the right exchange.
    Returns list of cancelled order IDs."""
    cancelled = []
    for r in results:
        if r.status in ("filled",) and r.order_id:
            ex = get_exec_fn(r.exchange)
            if ex and ex.cancel_order(r.order_id):
                cancelled.append(r.order_id)
    return cancelled


# --- Orchestration ---

def build_execution_plan(scored, asset: str, exchange: str | None,
                         exchange_quotes: list, synth_options: dict,
                         size_multiplier: int = 1) -> ExecutionPlan:
    """Build an ExecutionPlan from a ScoredStrategy.
    When exchange is None, auto-routes each leg to the best exchange via leg_divergences.
    size_multiplier scales all leg quantities (default 1)."""
    strategy = scored.strategy
    plan = ExecutionPlan(
        strategy_description=strategy.description,
        strategy_type=strategy.strategy_type,
        exchange=exchange or "auto",
        asset=asset,
        expiry=strategy.expiry or "",
        dry_run=False,
    )

    # Get per-leg routing when auto-routing
    leg_routes = {}
    if exchange is None:
        leg_routes = leg_divergences(strategy, exchange_quotes, synth_options)

    for i, leg in enumerate(strategy.legs):
        # Determine exchange for this leg
        if exchange is not None:
            leg_exchange = exchange
        elif i in leg_routes:
            leg_exchange = leg_routes[i]["best_exchange"]
        else:
            quote = best_execution_price(
                exchange_quotes, leg.strike, leg.option_type.lower(), leg.action,
            )
            leg_exchange = quote.exchange if quote else "deribit"

        # Build instrument name
        if leg_exchange == "aevo":
            instrument = aevo_instrument_name(asset, leg.strike, leg.option_type)
        else:
            instrument = deribit_instrument_name(
                asset, strategy.expiry or "", leg.strike, leg.option_type,
            )

        # Get execution price
        quote = best_execution_price(
            exchange_quotes, leg.strike, leg.option_type.lower(), leg.action,
        )
        if quote is not None:
            price = quote.ask if leg.action == "BUY" else quote.bid
        else:
            price = leg.premium

        qty = leg.quantity * max(1, size_multiplier)
        plan.orders.append(OrderRequest(
            instrument=instrument,
            action=leg.action,
            quantity=qty,
            order_type="limit",
            price=price,
            exchange=leg_exchange,
            leg_index=i,
            strike=leg.strike,
            option_type=leg.option_type.lower(),
        ))

    buy_total = sum(o.price * o.quantity for o in plan.orders if o.action == "BUY")
    sell_total = sum(o.price * o.quantity for o in plan.orders if o.action == "SELL")
    plan.estimated_cost = buy_total - sell_total
    plan.estimated_max_loss = strategy.max_loss * max(1, size_multiplier)

    return plan


def validate_plan(plan: ExecutionPlan, max_loss_budget: float | None = None) -> tuple[bool, str]:
    """Validate an execution plan before submission.
    max_loss_budget: if set, reject plans whose max loss exceeds this USD amount.
    Returns (is_valid, error_message)."""
    if not plan.orders:
        return False, "No orders in plan"
    for i, order in enumerate(plan.orders):
        if not order.instrument:
            return False, f"Order {i}: empty instrument name"
        if order.price <= 0:
            return False, f"Order {i}: price must be > 0 (got {order.price})"
        if order.quantity <= 0:
            return False, f"Order {i}: quantity must be > 0 (got {order.quantity})"
        if order.action not in ("BUY", "SELL"):
            return False, f"Order {i}: invalid action '{order.action}'"
    if max_loss_budget is not None and plan.estimated_max_loss > max_loss_budget:
        return False, (
            f"Max loss ${plan.estimated_max_loss:,.2f} exceeds budget "
            f"${max_loss_budget:,.2f}"
        )
    return True, ""


def execute_plan(plan: ExecutionPlan, executor,
                 max_slippage_pct: float | None = None,
                 timeout_seconds: float | None = None) -> ExecutionReport:
    """Execute all orders in a plan sequentially.
    executor: a BaseExecutor instance, or a callable(exchange_name) -> BaseExecutor
    for auto-routing across multiple exchanges.
    max_slippage_pct: if set, halt execution if any fill exceeds this slippage.
    timeout_seconds: if set, monitor open orders until filled or timeout.
    On partial failure, auto-cancels already-filled legs."""
    report = ExecutionReport(plan=plan, started_at=_now_iso())

    is_factory = callable(executor) and not isinstance(executor, BaseExecutor)
    _cached: dict[str, BaseExecutor] = {}

    def _get_exec(exchange: str) -> BaseExecutor | None:
        if not is_factory:
            return executor
        if exchange not in _cached:
            ex = executor(exchange)
            if not ex.authenticate():
                return None
            _cached[exchange] = ex
        return _cached[exchange]

    if not is_factory:
        if not executor.authenticate():
            report.summary = "Authentication failed"
            report.finished_at = _now_iso()
            return report

    use_timeout = timeout_seconds is not None and timeout_seconds > 0

    for order in plan.orders:
        ex = _get_exec(order.exchange)
        if ex is None:
            report.summary = f"Authentication failed for {order.exchange}"
            report.all_filled = False
            report.net_cost = _compute_net_cost(report.results)
            report.finished_at = _now_iso()
            return report
        result = ex.place_order(order)

        # Monitor open orders until filled or timeout
        if (use_timeout and result.status == "open" and result.order_id):
            monitored = _monitor_order(
                ex, result.order_id,
                timeout_seconds=timeout_seconds,
                poll_interval=1.0,
            )
            result.status = monitored.status
            if monitored.fill_price > 0:
                result.fill_price = monitored.fill_price
                result.slippage_pct = _compute_slippage(
                    order.price, monitored.fill_price, order.action,
                )
            if monitored.fill_quantity > 0:
                result.fill_quantity = monitored.fill_quantity

        report.results.append(result)

        # Slippage check
        if (max_slippage_pct is not None
                and result.status in ("filled", "simulated")
                and not check_slippage(result, max_slippage_pct)):
            # Auto-cancel already-filled legs
            report.cancelled_orders = _cancel_filled_orders(
                report.results, _get_exec,
            )
            filled_legs = [r for r in report.results if r.status in ("filled", "simulated")]
            instruments = ", ".join(r.instrument for r in filled_legs)
            report.summary = (
                f"Slippage exceeded on {result.instrument}: "
                f"{result.slippage_pct:.2f}% > {max_slippage_pct:.2f}% limit. "
                f"Halted. Filled legs: {instruments}"
            )
            report.all_filled = False
            report.net_cost = _compute_net_cost(report.results)
            report.slippage_total = sum(
                r.slippage_pct for r in report.results if r.status in ("filled", "simulated")
            )
            report.finished_at = _now_iso()
            return report

        if result.status in ("error", "rejected", "timeout"):
            # Auto-cancel already-filled legs
            filled_legs = [r for r in report.results if r.status in ("filled", "simulated")]
            if filled_legs:
                report.cancelled_orders = _cancel_filled_orders(
                    filled_legs, _get_exec,
                )
                instruments = ", ".join(r.instrument for r in filled_legs)
                cancel_note = ""
                if report.cancelled_orders:
                    cancel_note = f" Cancelled {len(report.cancelled_orders)} filled leg(s)."
                report.summary = (
                    f"Partial fill — order {order.leg_index} failed: "
                    f"{result.error or result.status}.{cancel_note} "
                    f"Filled legs: {instruments}"
                )
            else:
                report.summary = (
                    f"Order {order.leg_index} failed: {result.error or result.status}"
                )
            report.all_filled = False
            report.net_cost = _compute_net_cost(report.results)
            report.finished_at = _now_iso()
            return report

    report.all_filled = all(
        r.status in ("filled", "simulated") for r in report.results
    )
    report.net_cost = _compute_net_cost(report.results)
    report.slippage_total = sum(
        r.slippage_pct for r in report.results if r.status in ("filled", "simulated")
    )

    if report.all_filled:
        mode = "simulated" if plan.dry_run else "live"
        report.summary = (
            f"All {len(report.results)} legs {mode} successfully. "
            f"Net cost: ${report.net_cost:,.2f}"
        )
    else:
        report.summary = f"Execution completed with {len(report.results)} orders"

    report.finished_at = _now_iso()
    return report


def _compute_net_cost(results: list[OrderResult]) -> float:
    """Net cost = sum(buy fills) - sum(sell fills)."""
    cost = 0.0
    for r in results:
        if r.status in ("filled", "simulated"):
            if r.action == "BUY":
                cost += r.fill_price * r.fill_quantity
            else:
                cost -= r.fill_price * r.fill_quantity
    return cost


def compute_execution_savings(plan: ExecutionPlan, synth_options: dict) -> dict:
    """Compare auto-routed execution cost against Synth theoretical price.
    Returns savings breakdown showing the edge captured by venue selection."""
    synth_cost = 0.0
    exec_cost = 0.0
    for order in plan.orders:
        # Synth theoretical price from option pricing data
        opt_key = "call_options" if order.option_type == "call" else "put_options"
        opts = synth_options.get(opt_key, {})
        synth_price = opts.get(str(int(order.strike)), order.price)
        if order.action == "BUY":
            synth_cost += synth_price * order.quantity
            exec_cost += order.price * order.quantity
        else:
            synth_cost -= synth_price * order.quantity
            exec_cost -= order.price * order.quantity
    savings = synth_cost - exec_cost
    return {
        "synth_theoretical_cost": round(synth_cost, 2),
        "execution_cost": round(exec_cost, 2),
        "savings_usd": round(savings, 2),
        "savings_pct": round((savings / synth_cost * 100) if synth_cost != 0 else 0, 2),
    }


def save_execution_log(report: ExecutionReport, filepath: str) -> None:
    """Save execution report as JSON for audit trail."""
    log = {
        "timestamp": _now_iso(),
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "strategy": report.plan.strategy_description,
        "strategy_type": report.plan.strategy_type,
        "asset": report.plan.asset,
        "exchange": report.plan.exchange,
        "mode": "dry_run" if report.plan.dry_run else "live",
        "all_filled": report.all_filled,
        "net_cost": round(report.net_cost, 2),
        "slippage_total_pct": round(report.slippage_total, 4),
        "summary": report.summary,
        "cancelled_orders": report.cancelled_orders,
        "orders": [
            {
                "instrument": o.instrument,
                "action": o.action,
                "quantity": o.quantity,
                "order_type": o.order_type,
                "limit_price": round(o.price, 2),
                "exchange": o.exchange,
            }
            for o in report.plan.orders
        ],
        "fills": [
            {
                "order_id": r.order_id,
                "instrument": r.instrument,
                "action": r.action,
                "status": r.status,
                "fill_price": round(r.fill_price, 2),
                "fill_quantity": r.fill_quantity,
                "exchange": r.exchange,
                "slippage_pct": round(r.slippage_pct, 4),
                "timestamp": r.timestamp,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
            for r in report.results
        ],
    }
    with open(filepath, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def get_executor(exchange: str | None, exchange_quotes: list,
                 dry_run: bool = False):
    """Factory: return the appropriate executor.
    dry_run=True always returns DryRunExecutor.
    exchange=None with dry_run=False returns a callable factory for per-leg routing.
    Otherwise reads credentials from environment variables."""
    if dry_run:
        return DryRunExecutor(exchange_quotes)

    if exchange is None:
        def _executor_factory(ex: str):
            return get_executor(ex, exchange_quotes, dry_run=False)
        return _executor_factory

    if exchange == "deribit":
        client_id = os.environ.get("DERIBIT_CLIENT_ID", "")
        client_secret = os.environ.get("DERIBIT_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise ValueError(
                "Deribit credentials required: set DERIBIT_CLIENT_ID and "
                "DERIBIT_CLIENT_SECRET environment variables"
            )
        testnet = os.environ.get("DERIBIT_TESTNET", "").strip() == "1"
        return DeribitExecutor(client_id, client_secret, testnet)

    if exchange == "aevo":
        api_key = os.environ.get("AEVO_API_KEY", "")
        api_secret = os.environ.get("AEVO_API_SECRET", "")
        signing_key = os.environ.get("AEVO_SIGNING_KEY", "")
        wallet_address = os.environ.get("AEVO_WALLET_ADDRESS", "")
        if not api_key or not api_secret:
            raise ValueError(
                "Aevo credentials required: set AEVO_API_KEY and "
                "AEVO_API_SECRET environment variables"
            )
        if not signing_key or not wallet_address:
            raise ValueError(
                "Aevo L2 signing credentials required: set AEVO_SIGNING_KEY "
                "(Ethereum private key) and AEVO_WALLET_ADDRESS environment variables. "
                "Generate a signing key at https://app.aevo.xyz → Enable Trading."
            )
        testnet = os.environ.get("AEVO_TESTNET", "").strip() == "1"
        return AevoExecutor(api_key, api_secret, signing_key, wallet_address, testnet)

    raise ValueError(
        f"Unknown exchange '{exchange}'. Use --exchange deribit or --exchange aevo"
    )
