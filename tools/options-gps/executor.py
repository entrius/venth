"""Autonomous execution engine for Options GPS.
Consumes pipeline data classes and exchange pricing. Supports Deribit (JSON-RPC),
Aevo (REST + HMAC), and dry-run simulation. Auto-routing uses leg_divergences per leg.
Features: order lifecycle (place/status/cancel), slippage protection, order monitoring
with timeout, retry on transient errors, partial-fill cancellation."""

import hashlib
import hmac
import json
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import requests

from exchange import best_execution_price, leg_divergences


@dataclass
class OrderRequest:
    """Single order to send to an exchange."""
    instrument: str
    action: str       # "BUY" | "SELL"
    quantity: int
    order_type: str   # "limit" | "market"
    price: float
    exchange: str     # "deribit" | "aevo" | "dry_run"
    leg_index: int
    strike: float = 0.0
    option_type: str = ""


@dataclass
class OrderResult:
    """Result of placing one order."""
    order_id: str
    status: str       # "filled" | "open" | "rejected" | "error" | "simulated" | "cancelled"
    fill_price: float
    fill_quantity: int
    instrument: str
    action: str
    exchange: str
    error: str | None = None
    timestamp: str = ""          # ISO 8601 when fill/status was recorded
    slippage_pct: float = 0.0    # (fill_price - expected_price) / expected_price * 100
    latency_ms: int = 0          # round-trip latency in milliseconds


@dataclass
class ExecutionPlan:
    """Plan of orders to execute for one strategy."""
    strategy_description: str
    strategy_type: str
    exchange: str     # "deribit" | "aevo" | "auto"
    asset: str
    expiry: str
    orders: list[OrderRequest] = field(default_factory=list)
    estimated_cost: float = 0.0
    estimated_max_loss: float = 0.0
    dry_run: bool = False
    max_slippage_pct: float = 0.0   # 0 = no slippage check
    timeout_seconds: int = 0        # 0 = no monitoring (fire-and-forget)
    quantity_override: int = 0      # 0 = use strategy quantity


@dataclass
class ExecutionReport:
    """Result of executing a plan."""
    plan: ExecutionPlan
    results: list[OrderResult] = field(default_factory=list)
    all_filled: bool = False
    net_cost: float = 0.0
    summary: str = ""
    started_at: str = ""          # ISO 8601
    finished_at: str = ""         # ISO 8601
    cancelled_orders: list[str] = field(default_factory=list)  # order_ids cancelled on failure


def deribit_instrument_name(asset: str, expiry: str, strike: float, option_type: str) -> str:
    """Build Deribit instrument name e.g. BTC-26FEB26-67500-C."""
    strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    ot = "C" if option_type.lower() == "call" else "P"
    date_part = _format_deribit_date(expiry)
    return f"{asset}-{date_part}-{strike_str}-{ot}"


def aevo_instrument_name(asset: str, strike: float, option_type: str) -> str:
    """Build Aevo instrument name e.g. BTC-67500-C (no date)."""
    strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    ot = "C" if option_type.lower() == "call" else "P"
    return f"{asset}-{strike_str}-{ot}"


def _format_deribit_date(expiry: str) -> str:
    """Parse ISO 8601 expiry to Deribit DDMonYY e.g. 26FEB26."""
    if not expiry:
        return "UNKNOWN"
    try:
        expiry = expiry.replace("Z", "+00:00")
        dt = datetime.fromisoformat(expiry)
        return dt.strftime("%d%b%y").upper()
    except (ValueError, TypeError):
        return "UNKNOWN"


def _now_iso() -> str:
    """Current UTC time in ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def _slippage_pct(expected: float, actual: float, action: str) -> float:
    """Compute slippage as a percentage. Positive = worse than expected.
    For BUY: paying more is positive slippage. For SELL: receiving less is positive."""
    if expected <= 0:
        return 0.0
    if action == "BUY":
        return (actual - expected) / expected * 100
    return (expected - actual) / expected * 100


class BaseExecutor(ABC):
    """Abstract executor for one exchange (or dry-run).
    Full order lifecycle: authenticate → place → status → cancel."""

    @abstractmethod
    def authenticate(self) -> bool:
        pass

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        """Poll current status of a previously placed order."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successfully cancelled."""


class DryRunExecutor(BaseExecutor):
    """Simulates execution using quote data. No network calls."""

    def __init__(self, exchange_quotes: list):
        self.exchange_quotes = exchange_quotes
        self._orders: dict[str, OrderResult] = {}  # track placed orders for status queries

    def authenticate(self) -> bool:
        return True

    def place_order(self, order: OrderRequest) -> OrderResult:
        ts = _now_iso()
        if not order.strike or not order.option_type:
            return OrderResult(
                order_id=f"dry-{uuid.uuid4().hex[:8]}",
                status="error", fill_price=0.0, fill_quantity=0,
                instrument=order.instrument, action=order.action,
                exchange="dry_run", error="Missing strike or option_type on order",
                timestamp=ts,
            )
        quote = best_execution_price(
            self.exchange_quotes, order.strike, order.option_type, order.action,
        )
        if quote is None:
            fill_price = order.price
        else:
            fill_price = quote.ask if order.action == "BUY" else quote.bid
        slip = _slippage_pct(order.price, fill_price, order.action)
        result = OrderResult(
            order_id=f"dry-{uuid.uuid4().hex[:8]}",
            status="simulated",
            fill_price=fill_price,
            fill_quantity=order.quantity,
            instrument=order.instrument,
            action=order.action,
            exchange="dry_run",
            timestamp=ts,
            slippage_pct=round(slip, 4),
        )
        self._orders[result.order_id] = result
        return result

    def get_order_status(self, order_id: str) -> OrderResult:
        if order_id in self._orders:
            return self._orders[order_id]
        return OrderResult(
            order_id=order_id, status="error", fill_price=0.0, fill_quantity=0,
            instrument="", action="", exchange="dry_run",
            error="Order not found", timestamp=_now_iso(),
        )

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            old = self._orders[order_id]
            self._orders[order_id] = OrderResult(
                order_id=order_id, status="cancelled",
                fill_price=old.fill_price, fill_quantity=0,
                instrument=old.instrument, action=old.action,
                exchange="dry_run", timestamp=_now_iso(),
            )
            return True
        return False


def _is_retryable(err: Exception) -> bool:
    """True for transient errors worth retrying."""
    if isinstance(err, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(err, requests.HTTPError) and err.response is not None:
        return err.response.status_code in (429, 502, 503)
    return False


def _deribit_rpc(base_url: str, method: str, params: dict, token: str | None) -> dict:
    """Send one JSON-RPC 2.0 request to Deribit. POST to base_url/method with JSON-RPC body. Retries on transient errors."""
    url = f"{base_url.rstrip('/')}/{method}"
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": int(time.time() * 1000)}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"].get("message", str(data["error"])))
            return data.get("result", {})
        except Exception as e:
            if _is_retryable(e) and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise


class DeribitExecutor(BaseExecutor):
    """Executes orders on Deribit via JSON-RPC over HTTP (POST)."""

    def __init__(self, client_id: str, client_secret: str, testnet: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = (
            "https://test.deribit.com/api/v2" if testnet else "https://www.deribit.com/api/v2"
        )
        self.token: str | None = None

    def authenticate(self) -> bool:
        if self.token:
            return True
        try:
            result = _deribit_rpc(
                self.base_url,
                "public/auth",
                {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                token=None,
            )
            self.token = result.get("access_token")
            return self.token is not None
        except Exception:
            return False

    def place_order(self, order: OrderRequest) -> OrderResult:
        method = "private/buy" if order.action == "BUY" else "private/sell"
        # Use contracts for options (unambiguous); amount is in underlying for options on Deribit.
        params = {
            "instrument_name": order.instrument,
            "contracts": order.quantity,
            "type": order.order_type,
        }
        if order.order_type == "limit":
            params["price"] = order.price
        t0 = time.monotonic()
        try:
            result = _deribit_rpc(self.base_url, method, params, self.token)
            latency = int((time.monotonic() - t0) * 1000)
            order_data = result.get("order", {})
            fill_price = float(order_data.get("average_price", 0))
            slip = _slippage_pct(order.price, fill_price, order.action) if fill_price > 0 else 0.0
            return OrderResult(
                order_id=order_data.get("order_id", ""),
                status=order_data.get("order_state", "error"),
                fill_price=fill_price,
                fill_quantity=int(order_data.get("filled_amount", 0)),
                instrument=order.instrument,
                action=order.action,
                exchange="deribit",
                timestamp=_now_iso(),
                slippage_pct=round(slip, 4),
                latency_ms=latency,
            )
        except Exception as e:
            return OrderResult(
                order_id="", status="error", fill_price=0.0, fill_quantity=0,
                instrument=order.instrument, action=order.action, exchange="deribit",
                error=str(e), timestamp=_now_iso(),
            )

    def get_order_status(self, order_id: str) -> OrderResult:
        try:
            result = _deribit_rpc(
                self.base_url, "private/get_order_state",
                {"order_id": order_id}, self.token,
            )
            return OrderResult(
                order_id=result.get("order_id", order_id),
                status=result.get("order_state", "error"),
                fill_price=float(result.get("average_price", 0)),
                fill_quantity=int(result.get("filled_amount", 0)),
                instrument=result.get("instrument_name", ""),
                action="BUY" if result.get("direction") == "buy" else "SELL",
                exchange="deribit",
                timestamp=_now_iso(),
            )
        except Exception as e:
            return OrderResult(
                order_id=order_id, status="error", fill_price=0.0, fill_quantity=0,
                instrument="", action="", exchange="deribit",
                error=str(e), timestamp=_now_iso(),
            )

    def cancel_order(self, order_id: str) -> bool:
        try:
            _deribit_rpc(
                self.base_url, "private/cancel",
                {"order_id": order_id}, self.token,
            )
            return True
        except Exception:
            return False


class AevoExecutor(BaseExecutor):
    """Executes orders on Aevo via REST with HMAC-SHA256 signing."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (
            "https://api-testnet.aevo.xyz" if testnet else "https://api.aevo.xyz"
        )

    def _sign(self, timestamp: str, method: str, path: str, body: str) -> str:
        message = f"{timestamp}{method}{path}{body}"
        return hmac.new(
            self.api_secret.encode(), message.encode(), hashlib.sha256,
        ).hexdigest()

    def _headers(self, method: str = "POST", path: str = "/orders", body: str = "") -> dict:
        ts = str(int(time.time()))
        return {
            "AEVO-KEY": self.api_key,
            "AEVO-TIMESTAMP": ts,
            "AEVO-SIGNATURE": self._sign(ts, method, path, body),
            "Content-Type": "application/json",
        }

    def authenticate(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def place_order(self, order: OrderRequest) -> OrderResult:
        payload = {
            "instrument": order.instrument,
            "side": order.action.lower(),
            "quantity": order.quantity,
            "order_type": order.order_type,
        }
        if order.order_type == "limit":
            payload["price"] = order.price
        body = json.dumps(payload)
        last_err = None
        t0 = time.monotonic()
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base_url}/orders",
                    data=body,
                    headers=self._headers("POST", "/orders", body),
                    timeout=10,
                )
                resp.raise_for_status()
                latency = int((time.monotonic() - t0) * 1000)
                data = resp.json()
                fill_price = float(data.get("avg_price", 0))
                slip = _slippage_pct(order.price, fill_price, order.action) if fill_price > 0 else 0.0
                return OrderResult(
                    order_id=data.get("order_id", ""),
                    status=data.get("status", "error"),
                    fill_price=fill_price,
                    fill_quantity=int(data.get("filled", 0)),
                    instrument=order.instrument,
                    action=order.action,
                    exchange="aevo",
                    timestamp=_now_iso(),
                    slippage_pct=round(slip, 4),
                    latency_ms=latency,
                )
            except Exception as e:
                last_err = e
                if _is_retryable(e) and attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return OrderResult(
                    order_id="", status="error", fill_price=0.0, fill_quantity=0,
                    instrument=order.instrument, action=order.action, exchange="aevo",
                    error=str(e), timestamp=_now_iso(),
                )
        return OrderResult(
            order_id="", status="error", fill_price=0.0, fill_quantity=0,
            instrument=order.instrument, action=order.action, exchange="aevo",
            error=str(last_err), timestamp=_now_iso(),
        )

    def get_order_status(self, order_id: str) -> OrderResult:
        path = f"/orders/{order_id}"
        try:
            resp = requests.get(
                f"{self.base_url}{path}",
                headers=self._headers("GET", path),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return OrderResult(
                order_id=data.get("order_id", order_id),
                status=data.get("status", "error"),
                fill_price=float(data.get("avg_price", 0)),
                fill_quantity=int(data.get("filled", 0)),
                instrument=data.get("instrument", ""),
                action=data.get("side", "").upper(),
                exchange="aevo",
                timestamp=_now_iso(),
            )
        except Exception as e:
            return OrderResult(
                order_id=order_id, status="error", fill_price=0.0, fill_quantity=0,
                instrument="", action="", exchange="aevo",
                error=str(e), timestamp=_now_iso(),
            )

    def cancel_order(self, order_id: str) -> bool:
        path = f"/orders/{order_id}"
        try:
            resp = requests.delete(
                f"{self.base_url}{path}",
                headers=self._headers("DELETE", path),
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception:
            return False


def build_execution_plan(
    scored,
    asset: str,
    exchange: str | None,
    exchange_quotes: list,
    synth_options: dict,
    quantity_override: int = 0,
    max_slippage_pct: float = 0.0,
    timeout_seconds: int = 0,
) -> ExecutionPlan:
    """Build ExecutionPlan from a ScoredStrategy. When exchange is None, auto-route per leg."""
    strategy = scored.strategy
    plan = ExecutionPlan(
        strategy_description=strategy.description,
        strategy_type=strategy.strategy_type,
        exchange=exchange or "auto",
        asset=asset,
        expiry=strategy.expiry or "",
        dry_run=False,
        quantity_override=quantity_override,
        max_slippage_pct=max_slippage_pct,
        timeout_seconds=timeout_seconds,
    )
    leg_routes = {}
    if exchange is None:
        leg_routes = leg_divergences(strategy, exchange_quotes, synth_options)

    for i, leg in enumerate(strategy.legs):
        if exchange is not None:
            leg_exchange = exchange
        elif i in leg_routes:
            leg_exchange = leg_routes[i]["best_exchange"]
        else:
            quote = best_execution_price(
                exchange_quotes, leg.strike, leg.option_type.lower(), leg.action,
            )
            leg_exchange = quote.exchange if quote else "deribit"

        if leg_exchange == "aevo":
            instrument = aevo_instrument_name(asset, leg.strike, leg.option_type)
        else:
            instrument = deribit_instrument_name(
                asset, strategy.expiry or "", leg.strike, leg.option_type,
            )
        quote = best_execution_price(
            exchange_quotes, leg.strike, leg.option_type.lower(), leg.action,
        )
        if quote is not None:
            price = quote.ask if leg.action == "BUY" else quote.bid
        else:
            price = leg.premium

        qty = plan.quantity_override if plan.quantity_override > 0 else leg.quantity
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
    plan.estimated_max_loss = strategy.max_loss
    return plan


def validate_plan(plan: ExecutionPlan) -> tuple[bool, str]:
    """Validate plan before submission. Returns (is_valid, error_message)."""
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
    return True, ""


def _monitor_order(
    executor: BaseExecutor,
    order_id: str,
    timeout_seconds: int,
    poll_interval: float = 1.0,
) -> OrderResult:
    """Poll order status until filled, rejected, or timeout."""
    deadline = time.monotonic() + timeout_seconds
    last_result = None
    while time.monotonic() < deadline:
        last_result = executor.get_order_status(order_id)
        if last_result.status in ("filled", "rejected", "error", "cancelled", "simulated"):
            return last_result
        time.sleep(max(0, min(poll_interval, deadline - time.monotonic())))
    if last_result and last_result.status == "open":
        last_result.error = f"Timeout after {timeout_seconds}s — order still open"
    return last_result or OrderResult(
        order_id=order_id, status="error", fill_price=0.0, fill_quantity=0,
        instrument="", action="", exchange="",
        error=f"Timeout after {timeout_seconds}s", timestamp=_now_iso(),
    )


def _cancel_filled_orders(
    filled_results: list[OrderResult],
    get_exec: Callable[[str], BaseExecutor],
) -> list[str]:
    """Best-effort cancel of previously filled orders. Returns list of cancelled order_ids."""
    cancelled = []
    for r in filled_results:
        if r.order_id and r.status in ("filled", "simulated", "open"):
            executor = get_exec(r.exchange)
            if executor.cancel_order(r.order_id):
                cancelled.append(r.order_id)
    return cancelled


def execute_plan(
    plan: ExecutionPlan,
    executor_or_factory: BaseExecutor | Callable[[str], BaseExecutor],
) -> ExecutionReport:
    """Execute all orders. Supports slippage protection, order monitoring with timeout,
    and automatic cancellation of filled legs on partial failure.
    If executor_or_factory is callable, call it with order.exchange per order (for auto)."""
    report = ExecutionReport(plan=plan, started_at=_now_iso())
    get_executor: Callable[[str], BaseExecutor] = (
        executor_or_factory if callable(executor_or_factory) else lambda _: executor_or_factory
    )

    for order in plan.orders:
        executor = get_executor(order.exchange)
        if not executor.authenticate():
            report.summary = "Authentication failed"
            report.all_filled = False
            report.net_cost = _compute_net_cost(report.results)
            report.finished_at = _now_iso()
            return report
        result = executor.place_order(order)

        # Order monitoring: if timeout is set and order is open, poll until filled or timeout
        if plan.timeout_seconds > 0 and result.status == "open" and result.order_id:
            result = _monitor_order(executor, result.order_id, plan.timeout_seconds)
            # If still open after timeout, cancel it
            if result.status == "open":
                executor.cancel_order(result.order_id)
                result.status = "cancelled"
                result.error = f"Cancelled after {plan.timeout_seconds}s timeout"

        # Slippage protection: reject if fill slippage exceeds threshold
        if (plan.max_slippage_pct > 0
                and result.status in ("filled", "simulated")
                and result.slippage_pct > plan.max_slippage_pct):
            # Cancel this order if possible
            if result.order_id:
                executor.cancel_order(result.order_id)
            result = OrderResult(
                order_id=result.order_id,
                status="rejected",
                fill_price=result.fill_price,
                fill_quantity=result.fill_quantity,
                instrument=result.instrument,
                action=result.action,
                exchange=result.exchange,
                error=f"Slippage {result.slippage_pct:.2f}% exceeds max {plan.max_slippage_pct:.2f}%",
                timestamp=_now_iso(),
                slippage_pct=result.slippage_pct,
                latency_ms=result.latency_ms,
            )

        report.results.append(result)
        if result.status in ("error", "rejected", "cancelled"):
            filled = [r for r in report.results if r.status in ("filled", "simulated")]
            if filled:
                # Auto-cancel previously filled legs on partial failure
                report.cancelled_orders = _cancel_filled_orders(filled, get_executor)
                instruments = ", ".join(r.instrument for r in filled)
                cancel_note = (
                    f" Auto-cancelled {len(report.cancelled_orders)} filled legs."
                    if report.cancelled_orders
                    else f" WARNING: manually close filled legs: {instruments}"
                )
                report.summary = (
                    f"Partial fill — order {order.leg_index} failed: "
                    f"{result.error or result.status}.{cancel_note}"
                )
            else:
                report.summary = f"Order {order.leg_index} failed: {result.error or result.status}"
            report.all_filled = False
            report.net_cost = _compute_net_cost(report.results)
            report.finished_at = _now_iso()
            return report

    report.all_filled = all(
        r.status in ("filled", "simulated") for r in report.results
    )
    report.net_cost = _compute_net_cost(report.results)
    report.finished_at = _now_iso()
    if report.all_filled:
        mode = "simulated" if plan.dry_run else "live"
        report.summary = (
            f"All {len(report.results)} legs {mode} successfully. Net cost: ${report.net_cost:,.2f}"
        )
    else:
        report.summary = f"Execution completed with {len(report.results)} orders"
    return report


def _compute_net_cost(results: list[OrderResult]) -> float:
    cost = 0.0
    for r in results:
        if r.status in ("filled", "simulated"):
            if r.action == "BUY":
                cost += r.fill_price * r.fill_quantity
            else:
                cost -= r.fill_price * r.fill_quantity
    return cost


def get_executor(
    exchange: str,
    exchange_quotes: list,
    dry_run: bool = False,
) -> BaseExecutor:
    """Factory: return executor for the given exchange. For auto, pass per-order exchange via execute_plan's callable."""
    if dry_run:
        return DryRunExecutor(exchange_quotes)
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
        if not api_key or not api_secret:
            raise ValueError(
                "Aevo credentials required: set AEVO_API_KEY and "
                "AEVO_API_SECRET environment variables"
            )
        testnet = os.environ.get("AEVO_TESTNET", "").strip() == "1"
        return AevoExecutor(api_key, api_secret, testnet)
    raise ValueError(
        f"Unknown exchange '{exchange}'. Use --exchange deribit or --exchange aevo"
    )
