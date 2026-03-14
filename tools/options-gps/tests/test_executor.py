"""Tests for executor.py: autonomous execution — instrument names, plan building,
validation, dry-run simulation, execution flow, and executor factory."""

import hashlib
import hmac as hmac_mod
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
import pytest
from executor import (
    OrderRequest,
    OrderResult,
    ExecutionPlan,
    BaseExecutor,
    DryRunExecutor,
    AevoExecutor,
    DeribitExecutor,
    deribit_instrument_name,
    aevo_instrument_name,
    build_execution_plan,
    validate_plan,
    execute_plan,
    get_executor,
    save_execution_log,
    compute_execution_savings,
    check_slippage,
    _compute_slippage,
    _is_retryable,
    _monitor_order,
    _cancel_filled_orders,
)
from exchange import _parse_instrument_key
from pipeline import ScoredStrategy


def _status_result(order_id: str, status: str) -> OrderResult:
    """Helper: build a minimal OrderResult for test executors."""
    return OrderResult(order_id=order_id, status=status, fill_price=0.0,
                       fill_quantity=0, instrument="", action="", exchange="")


def _make_scored(strategy):
    return ScoredStrategy(
        strategy=strategy, probability_of_profit=0.55, expected_value=100.0,
        tail_risk=50.0, loss_profile="premium at risk",
        invalidation_trigger="Close on break", reroute_rule="Roll out",
        review_again_at="Review at 50%", score=0.8, rationale="Test",
    )


# --- 1. Instrument Names (7 tests) ---

class TestInstrumentNames:
    def test_deribit_call(self):
        assert deribit_instrument_name("BTC", "2026-02-26T08:00:00Z", 67500, "Call") == "BTC-26FEB26-67500-C"

    def test_deribit_put(self):
        assert deribit_instrument_name("ETH", "2026-03-15T08:00:00Z", 4000, "Put") == "ETH-15MAR26-4000-P"

    def test_aevo_call(self):
        assert aevo_instrument_name("BTC", 67500, "Call") == "BTC-67500-C"

    def test_aevo_put(self):
        assert aevo_instrument_name("SOL", 150, "Put") == "SOL-150-P"

    def test_deribit_roundtrip(self):
        name = deribit_instrument_name("BTC", "2026-02-26T08:00:00Z", 67500, "Call")
        strike, opt_type = _parse_instrument_key(name)
        assert strike == 67500 and opt_type == "call"

    def test_aevo_roundtrip(self):
        name = aevo_instrument_name("BTC", 68000, "Put")
        strike, opt_type = _parse_instrument_key(name)
        assert strike == 68000 and opt_type == "put"

    def test_deribit_empty_expiry(self):
        assert "UNKNOWN" in deribit_instrument_name("BTC", "", 67500, "Call")


# --- 2. Build Plan + Size Multiplier (8 tests) ---

class TestBuildPlan:
    def test_single_leg(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        assert len(plan.orders) == 1
        o = plan.orders[0]
        assert o.action == "BUY" and o.exchange == "deribit"
        assert o.strike == 67500 and o.option_type == "call"
        assert "67500" in o.instrument and plan.estimated_cost > 0

    def test_multi_leg(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        assert len(plan.orders) == 2
        actions = {o.action for o in plan.orders}
        assert actions == {"BUY", "SELL"}

    def test_aevo_names(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "aevo", sample_exchange_quotes, btc_option_data)
        assert plan.orders[0].instrument == "BTC-67500-C"

    def test_auto_route(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", None, sample_exchange_quotes, btc_option_data)
        assert plan.exchange == "auto"
        assert plan.orders[0].exchange in ("deribit", "aevo")

    def test_estimated_cost(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        buy = sum(o.price * o.quantity for o in plan.orders if o.action == "BUY")
        sell = sum(o.price * o.quantity for o in plan.orders if o.action == "SELL")
        assert plan.estimated_cost == pytest.approx(buy - sell)

    def test_size_multiplier(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data, size_multiplier=3)
        assert plan.orders[0].quantity == 3

    def test_size_scales_max_loss(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        p1 = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data, size_multiplier=1)
        p5 = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data, size_multiplier=5)
        assert p5.estimated_max_loss == pytest.approx(p1.estimated_max_loss * 5)

    def test_exchange_override_all_legs(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        assert all(o.exchange == "deribit" for o in plan.orders)


# --- 3. Validate Plan + Max Loss Budget (7 tests) ---

class TestValidatePlan:
    def test_valid(self):
        plan = ExecutionPlan(
            strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-26FEB26-67500-C", "BUY", 1, "limit", 660.0, "deribit", 0, strike=67500, option_type="call")],
        )
        assert validate_plan(plan) == (True, "")

    def test_empty_orders(self):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="")
        valid, err = validate_plan(plan)
        assert not valid and "No orders" in err

    def test_zero_price(self):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("X", "BUY", 1, "limit", 0.0, "deribit", 0, strike=67500, option_type="call")])
        assert not validate_plan(plan)[0]

    def test_zero_quantity(self):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("X", "BUY", 0, "limit", 660.0, "deribit", 0, strike=67500, option_type="call")])
        assert not validate_plan(plan)[0]

    def test_empty_instrument(self):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("", "BUY", 1, "limit", 660.0, "deribit", 0, strike=67500, option_type="call")])
        assert not validate_plan(plan)[0]

    def test_within_budget(self):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("X", "BUY", 1, "limit", 660.0, "deribit", 0, strike=67500, option_type="call")],
            estimated_max_loss=500.0)
        assert validate_plan(plan, max_loss_budget=1000.0)[0]

    def test_exceeds_budget(self):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("X", "BUY", 1, "limit", 660.0, "deribit", 0, strike=67500, option_type="call")],
            estimated_max_loss=1500.0)
        valid, err = validate_plan(plan, max_loss_budget=1000.0)
        assert not valid and "budget" in err.lower()


# --- 4. DryRunExecutor — stateful, timestamps, OrderResult (10 tests) ---

class TestDryRunExecutor:
    def test_authenticate(self, sample_exchange_quotes):
        assert DryRunExecutor(sample_exchange_quotes).authenticate() is True

    def test_place_buy(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0, strike=67500, option_type="call")
        r = executor.place_order(order)
        assert r.status == "simulated" and r.fill_price == 655.0 and r.fill_quantity == 1
        assert r.timestamp and "T" in r.timestamp  # ISO 8601
        assert r.latency_ms >= 0

    def test_place_sell(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "SELL", 1, "limit", 620.0, "dry_run", 0, strike=67500, option_type="call")
        r = executor.place_order(order)
        assert r.status == "simulated" and r.fill_price == 620.0

    def test_missing_strike_error(self, sample_exchange_quotes):
        r = DryRunExecutor(sample_exchange_quotes).place_order(
            OrderRequest("INVALID", "BUY", 1, "limit", 100.0, "dry_run", 0))
        assert r.status == "error" and r.timestamp

    def test_no_matching_quote_fallback(self, sample_exchange_quotes):
        r = DryRunExecutor(sample_exchange_quotes).place_order(
            OrderRequest("BTC-99999-C", "BUY", 1, "limit", 100.0, "dry_run", 0, strike=99999, option_type="call"))
        assert r.status == "simulated" and r.fill_price == 100.0

    def test_stateful_get_status(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0, strike=67500, option_type="call")
        placed = executor.place_order(order)
        result = executor.get_order_status(placed.order_id)
        assert isinstance(result, OrderResult)
        assert result.status == "simulated" and result.order_id == placed.order_id

    def test_unknown_id_not_found(self, sample_exchange_quotes):
        r = DryRunExecutor(sample_exchange_quotes).get_order_status("nonexistent")
        assert r.status == "not_found"

    def test_cancel_transitions_state(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        placed = executor.place_order(
            OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0, strike=67500, option_type="call"))
        assert executor.cancel_order(placed.order_id) is True
        assert executor.get_order_status(placed.order_id).status == "cancelled"

    def test_cancel_unknown_returns_false(self, sample_exchange_quotes):
        assert DryRunExecutor(sample_exchange_quotes).cancel_order("nope") is False

    def test_tracks_multiple_orders(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        r1 = executor.place_order(OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0, strike=67500, option_type="call"))
        r2 = executor.place_order(OrderRequest("BTC-67500-P", "SELL", 1, "limit", 300.0, "dry_run", 1, strike=67500, option_type="put"))
        assert r1.order_id != r2.order_id
        assert executor.get_order_status(r1.order_id).status == "simulated"
        assert executor.get_order_status(r2.order_id).status == "simulated"


# --- 5. Execute Flow + Timeout + Partial Fill (8 tests) ---

class TestExecuteFlow:
    def test_single_leg(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        report = execute_plan(plan, DryRunExecutor(sample_exchange_quotes))
        assert report.all_filled and len(report.results) == 1
        assert report.results[0].status == "simulated"
        assert report.started_at and report.finished_at

    def test_multi_leg(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        report = execute_plan(plan, DryRunExecutor(sample_exchange_quotes))
        assert report.all_filled and len(report.results) == 2

    def test_net_cost(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        report = execute_plan(plan, DryRunExecutor(sample_exchange_quotes))
        buy = sum(r.fill_price * r.fill_quantity for r in report.results if r.action == "BUY")
        sell = sum(r.fill_price * r.fill_quantity for r in report.results if r.action == "SELL")
        assert report.net_cost == pytest.approx(buy - sell)

    def test_summary_message(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        report = execute_plan(plan, DryRunExecutor(sample_exchange_quotes))
        assert "simulated" in report.summary and "Net cost" in report.summary

    def test_timeout_skips_simulated(self, sample_exchange_quotes):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "dry_run", 0, strike=67500, option_type="call")], dry_run=True)
        report = execute_plan(plan, DryRunExecutor(sample_exchange_quotes), timeout_seconds=5.0)
        assert report.all_filled and report.results[0].status == "simulated"

    def test_timeout_triggers_for_open(self):
        class OpenExecutor(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order):
                return OrderResult(order_id="open-1", status="open", fill_price=0.0,
                    fill_quantity=0, instrument=order.instrument, action=order.action, exchange="test")
            def get_order_status(self, oid): return _status_result(oid, "open")
            def cancel_order(self, oid): return True

        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="test", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "test", 0, strike=67500, option_type="call")])
        report = execute_plan(plan, OpenExecutor(), timeout_seconds=0.1)
        assert not report.all_filled and report.results[0].status == "timeout"

    def test_partial_fill_auto_cancels(self, sample_exchange_quotes):
        call_count = {"n": 0}
        class PartialExec(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return OrderResult(order_id="fill-1", status="filled", fill_price=100.0,
                        fill_quantity=1, instrument=order.instrument, action=order.action, exchange="test")
                return OrderResult(order_id="", status="error", fill_price=0.0,
                    fill_quantity=0, instrument=order.instrument, action=order.action, exchange="test", error="rejected")
            def get_order_status(self, oid): return _status_result(oid, "filled")
            def cancel_order(self, oid): return True

        plan = ExecutionPlan(strategy_description="T", strategy_type="call_debit_spread", exchange="test", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "test", 0, strike=67500, option_type="call"),
                OrderRequest("BTC-68000-C", "SELL", 1, "limit", 385.0, "test", 1, strike=68000, option_type="call"),
            ])
        report = execute_plan(plan, PartialExec())
        assert not report.all_filled and "Partial fill" in report.summary
        assert len(report.cancelled_orders) > 0

    def test_factory_routes_per_leg(self, sample_exchange_quotes):
        plan = ExecutionPlan(strategy_description="T", strategy_type="call_debit_spread", exchange="auto", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "aevo", 0, strike=67500, option_type="call"),
                OrderRequest("BTC-26FEB26-68000-C", "SELL", 1, "limit", 385.0, "deribit", 1, strike=68000, option_type="call"),
            ], dry_run=True)
        report = execute_plan(plan, lambda ex: DryRunExecutor(sample_exchange_quotes))
        assert report.all_filled and len(report.results) == 2


# --- 6. Get Executor (7 tests) ---

class TestGetExecutor:
    def test_dry_run(self, sample_exchange_quotes):
        assert isinstance(get_executor("deribit", sample_exchange_quotes, dry_run=True), DryRunExecutor)

    def test_dry_run_ignores_exchange(self, sample_exchange_quotes):
        assert isinstance(get_executor("aevo", sample_exchange_quotes, dry_run=True), DryRunExecutor)

    def test_auto_route_dry_run(self, sample_exchange_quotes):
        assert isinstance(get_executor(None, sample_exchange_quotes, dry_run=True), DryRunExecutor)

    def test_auto_route_returns_factory(self, sample_exchange_quotes):
        result = get_executor(None, sample_exchange_quotes, dry_run=False)
        assert callable(result) and not isinstance(result, DryRunExecutor)

    def test_missing_deribit_creds(self, sample_exchange_quotes, monkeypatch):
        monkeypatch.delenv("DERIBIT_CLIENT_ID", raising=False)
        monkeypatch.delenv("DERIBIT_CLIENT_SECRET", raising=False)
        with pytest.raises(ValueError, match="DERIBIT_CLIENT_ID"):
            get_executor("deribit", sample_exchange_quotes, dry_run=False)

    def test_missing_aevo_creds(self, sample_exchange_quotes, monkeypatch):
        monkeypatch.delenv("AEVO_API_KEY", raising=False)
        monkeypatch.delenv("AEVO_API_SECRET", raising=False)
        monkeypatch.delenv("AEVO_SIGNING_KEY", raising=False)
        monkeypatch.delenv("AEVO_WALLET_ADDRESS", raising=False)
        with pytest.raises(ValueError, match="AEVO_API_KEY"):
            get_executor("aevo", sample_exchange_quotes, dry_run=False)

    def test_unknown_exchange(self, sample_exchange_quotes):
        with pytest.raises(ValueError, match="Unknown exchange"):
            get_executor("binance", sample_exchange_quotes, dry_run=False)


# --- 7. Slippage (8 tests) ---

class TestSlippage:
    def test_buy_worse(self):
        assert _compute_slippage(100.0, 102.0, "BUY") == pytest.approx(2.0)

    def test_buy_better(self):
        assert _compute_slippage(100.0, 98.0, "BUY") == pytest.approx(-2.0)

    def test_sell_worse(self):
        assert _compute_slippage(100.0, 98.0, "SELL") == pytest.approx(2.0)

    def test_zero_limit(self):
        assert _compute_slippage(0.0, 100.0, "BUY") == 0.0

    def test_check_within(self):
        r = OrderResult("id", "filled", 102.0, 1, "X", "BUY", "test", slippage_pct=1.5)
        assert check_slippage(r, 2.0) is True

    def test_check_exceeded(self):
        r = OrderResult("id", "filled", 105.0, 1, "X", "BUY", "test", slippage_pct=5.0)
        assert check_slippage(r, 2.0) is False

    def test_execute_halts_on_slippage(self, sample_exchange_quotes):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 600.0, "dry_run", 0, strike=67500, option_type="call")], dry_run=True)
        report = execute_plan(plan, DryRunExecutor(sample_exchange_quotes), max_slippage_pct=1.0)
        assert not report.all_filled and "Slippage exceeded" in report.summary

    def test_execute_ok_slippage(self, sample_exchange_quotes):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "dry_run", 0, strike=67500, option_type="call")], dry_run=True)
        report = execute_plan(plan, DryRunExecutor(sample_exchange_quotes), max_slippage_pct=5.0)
        assert report.all_filled


# --- 8. Execution Log (1 test — comprehensive) ---

class TestExecutionLog:
    def test_save_and_load(self, sample_strategy, sample_exchange_quotes, btc_option_data, tmp_path):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        report = execute_plan(plan, DryRunExecutor(sample_exchange_quotes))
        log_path = str(tmp_path / "exec_log.json")
        save_execution_log(report, log_path)
        with open(log_path) as f:
            log = json.load(f)
        assert log["mode"] == "dry_run" and log["asset"] == "BTC" and log["all_filled"]
        assert log["started_at"] and log["finished_at"]
        assert isinstance(log["cancelled_orders"], list)
        fill = log["fills"][0]
        assert fill["status"] == "simulated" and fill["timestamp"] and fill["latency_ms"] >= 0


# --- 9. Retry (5 tests) ---

class TestRetryable:
    def test_timeout(self):
        assert _is_retryable(requests.Timeout()) is True

    def test_connection_error(self):
        assert _is_retryable(requests.ConnectionError()) is True

    def test_value_error_not(self):
        assert _is_retryable(ValueError("bad")) is False

    def test_http_429(self):
        resp = type("R", (), {"status_code": 429})()
        assert _is_retryable(requests.HTTPError(response=resp)) is True

    def test_http_400_not(self):
        resp = type("R", (), {"status_code": 400})()
        assert _is_retryable(requests.HTTPError(response=resp)) is False


# --- 10. Monitoring + Cancel (7 tests) ---

class TestMonitoringAndCancel:
    def test_immediate_fill(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        placed = executor.place_order(
            OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0, strike=67500, option_type="call"))
        result = _monitor_order(executor, placed.order_id, timeout_seconds=5.0)
        assert result.status == "simulated"

    def test_timeout_triggers_cancel(self):
        class StuckExec(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, oid): return _status_result(oid, "open")
            def cancel_order(self, oid): return True
        assert _monitor_order(StuckExec(), "s-1", timeout_seconds=0.1, poll_interval=0.05).status == "timeout"

    def test_delayed_fill(self):
        n = {"c": 0}
        class DelayExec(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, oid):
                n["c"] += 1
                return OrderResult(oid, "filled", 100.0, 1, "X", "BUY", "t") if n["c"] >= 3 else _status_result(oid, "open")
            def cancel_order(self, oid): return True
        result = _monitor_order(DelayExec(), "d-1", timeout_seconds=5.0, poll_interval=0.01)
        assert result.status == "filled" and result.fill_price == 100.0

    def test_rejected_terminal(self):
        class RejExec(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, oid): return _status_result(oid, "rejected")
            def cancel_order(self, oid): return True
        assert _monitor_order(RejExec(), "r-1", timeout_seconds=5.0).status == "rejected"

    def test_cancel_filled_orders(self):
        results = [OrderResult("id-1", "filled", 100.0, 1, "X", "BUY", "deribit"),
                   OrderResult("id-2", "filled", 200.0, 1, "Y", "SELL", "aevo")]
        log = []
        class TrackExec(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, oid): return _status_result(oid, "filled")
            def cancel_order(self, oid): log.append(oid); return True
        assert _cancel_filled_orders(results, lambda ex: TrackExec()) == ["id-1", "id-2"]

    def test_cancel_skips_non_filled(self):
        results = [OrderResult("id-1", "error", 0.0, 0, "X", "BUY", "deribit"),
                   OrderResult("id-2", "filled", 200.0, 1, "Y", "SELL", "aevo")]
        executor = DryRunExecutor([])
        executor._orders["id-2"] = results[1]
        assert _cancel_filled_orders(results, lambda ex: executor) == ["id-2"]

    def test_cancel_failure_skips(self):
        results = [OrderResult("id-1", "filled", 100.0, 1, "X", "BUY", "deribit")]
        class FailExec(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, oid): return _status_result(oid, "filled")
            def cancel_order(self, oid): return False
        assert _cancel_filled_orders(results, lambda ex: FailExec()) == []


# --- 11. Execution Savings (3 tests) ---

class TestExecutionSavings:
    def test_savings_when_cheaper(self, btc_option_data):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="auto", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 600.0, "aevo", 0, strike=67500, option_type="call")])
        s = compute_execution_savings(plan, btc_option_data)
        assert s["savings_usd"] > 0 and s["synth_theoretical_cost"] == pytest.approx(638.43)

    def test_no_savings_at_synth_price(self, btc_option_data):
        plan = ExecutionPlan(strategy_description="T", strategy_type="long_call", exchange="auto", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 638.43, "deribit", 0, strike=67500, option_type="call")])
        assert compute_execution_savings(plan, btc_option_data)["savings_usd"] == pytest.approx(0.0)

    def test_multi_leg_savings(self, btc_option_data):
        plan = ExecutionPlan(strategy_description="T", strategy_type="call_debit_spread", exchange="auto", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67000-C", "BUY", 1, "limit", 950.0, "aevo", 0, strike=67000, option_type="call"),
                OrderRequest("BTC-68000-C", "SELL", 1, "limit", 390.0, "deribit", 1, strike=68000, option_type="call"),
            ])
        assert compute_execution_savings(plan, btc_option_data)["savings_usd"] > 0


# --- 12. Aevo L2 Signing (5 tests) ---

def _make_aevo(**kwargs):
    defaults = {
        "api_key": "test-key", "api_secret": "test-secret",
        "signing_key": "0x" + "ab" * 32,
        "wallet_address": "0x" + "cd" * 20,
        "testnet": True,
    }
    defaults.update(kwargs)
    return AevoExecutor(**defaults)


class TestAevoSigning:
    def test_hmac_four_part_signature(self):
        executor = _make_aevo()
        sig = executor._sign_hmac("12345", "POST", "/orders", '{"side":"buy"}')
        expected = hmac_mod.new(b"test-secret", b'12345POST/orders{"side":"buy"}', hashlib.sha256).hexdigest()
        assert sig == expected

    def test_headers(self):
        h = _make_aevo(api_key="my-key", api_secret="my-secret")._headers("POST", "/orders", '{}')
        assert h["AEVO-KEY"] == "my-key" and "AEVO-TIMESTAMP" in h and "AEVO-SIGNATURE" in h

    def test_empty_body(self):
        assert len(_make_aevo(api_key="k", api_secret="s")._sign_hmac("9", "GET", "/x", "")) == 64

    def test_eip712_order_signing(self):
        executor = _make_aevo()
        salt, sig = executor._sign_order(
            instrument_id=11235, is_buy=True,
            limit_price=65500.0, quantity=1, timestamp=1700000000,
        )
        assert isinstance(salt, int) and salt > 0
        assert sig.startswith("0x") and len(sig) == 132  # 0x + 65 bytes hex

    def test_authenticate_requires_all_four(self):
        full = _make_aevo()
        assert full.authenticate() is True
        partial = _make_aevo(signing_key="", wallet_address="")
        assert partial.authenticate() is False


# --- 13. Deribit Price Conversion (4 tests) ---

class TestDeribitPriceConversion:
    def test_align_tick(self):
        assert DeribitExecutor._align_tick(0.00973, 0.0005) == 0.0095
        assert DeribitExecutor._align_tick(0.00975, 0.0005) == 0.01
        assert DeribitExecutor._align_tick(0.0100, 0.0005) == 0.01

    def test_usd_to_btc(self):
        executor = DeribitExecutor("id", "secret", testnet=True)
        executor._index_cache["BTC"] = 67000.0
        assert executor._usd_to_btc(670.0, "BTC") == 0.01

    def test_usd_to_btc_fallback(self):
        executor = DeribitExecutor("id", "secret", testnet=True)
        executor._index_cache["BTC"] = 0.0
        assert executor._usd_to_btc(670.0, "BTC") == 670.0

    def test_index_cache(self):
        executor = DeribitExecutor("id", "secret", testnet=True)
        executor._index_cache["ETH"] = 3500.0
        assert executor._get_index_price("ETH") == 3500.0


# --- 14. CLI Helpers (5 tests) ---

class TestCLI:
    def test_screen_none(self):
        from main import _parse_screen_arg
        assert _parse_screen_arg("none") == set()

    def test_screen_zero(self):
        from main import _parse_screen_arg
        assert _parse_screen_arg("0") == set()

    def test_screen_all(self):
        from main import _parse_screen_arg
        assert _parse_screen_arg("all") == {1, 2, 3, 4}

    def test_refuse_execution_blocks(self):
        from main import _refuse_execution
        assert _refuse_execution(True, "Countermove", False) is not None

    def test_refuse_execution_allows_force(self):
        from main import _refuse_execution
        assert _refuse_execution(True, "Countermove", True) is None
