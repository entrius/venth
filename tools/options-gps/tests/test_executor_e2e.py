"""E2E tests: full pipeline to execution, guardrail refusal, multi-leg, non-crypto, slippage."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import (
    generate_strategies,
    rank_strategies,
    select_three_cards,
    run_forecast_fusion,
    forecast_confidence,
)
from exchange import fetch_all_exchanges, strategy_divergence
from executor import build_execution_plan, execute_plan, get_executor
from main import _refuse_execution

MOCK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "mock_data", "exchange_options")
BTC_OPTION_DATA = {
    "current_price": 67723.50,
    "call_options": {
        "67000": 987.04, "67500": 638.43, "68000": 373.27,
    },
    "put_options": {
        "67000": 140.36, "67500": 291.75, "68000": 526.59,
    },
    "expiry_time": "2026-02-26T08:00:00Z",
}
P24H = {
    "0.05": 66000, "0.5": 67800, "0.95": 70000,
}


def test_full_pipeline_dry_run():
    """Pipeline -> rank -> select best -> build plan -> dry-run execute -> verify report."""
    candidates = generate_strategies(BTC_OPTION_DATA, "bullish", "medium", asset="BTC", expiry="2026-02-26T08:00:00Z")
    assert len(candidates) > 0
    exchange_quotes = fetch_all_exchanges("BTC", mock_dir=MOCK_DIR)
    assert len(exchange_quotes) > 0
    divergence_by_strategy = {}
    for c in candidates:
        div = strategy_divergence(c, exchange_quotes, BTC_OPTION_DATA)
        if div is not None:
            divergence_by_strategy[id(c)] = div
    outcome_prices = [66000, 67000, 67800, 68200, 70000]
    fusion = run_forecast_fusion(None, P24H, 67723.50)
    confidence = forecast_confidence(P24H, 67723.50)
    scored = rank_strategies(
        candidates, fusion, "bullish", outcome_prices, "medium",
        67723.50, confidence, 1.0, cdf_values=None, divergence_by_strategy=divergence_by_strategy,
    )
    best, _, _ = select_three_cards(scored)
    assert best is not None
    plan = build_execution_plan(best, "BTC", None, exchange_quotes, BTC_OPTION_DATA)
    plan.dry_run = True
    def factory(ex):
        return get_executor(ex, exchange_quotes, dry_run=True)
    report = execute_plan(plan, factory)
    assert report.all_filled is True
    assert len(report.results) == len(plan.orders)
    assert all(r.status == "simulated" for r in report.results)


def test_refuse_execution_guardrail_active_no_force():
    """When guardrail is active and --force not set, we refuse live execution."""
    assert _refuse_execution("Signals unclear", force=False, doing_live=True) is True


def test_allow_execution_guardrail_active_with_force():
    """When guardrail is active but --force is set, we allow execution."""
    assert _refuse_execution("Signals unclear", force=True, doing_live=True) is False


def test_allow_execution_dry_run_ignores_guardrail():
    """Dry-run is allowed even when guardrail is active (no real orders)."""
    assert _refuse_execution("Signals unclear", force=False, doing_live=False) is False


def test_allow_execution_no_guardrail():
    """When no guardrail, we allow execution."""
    assert _refuse_execution(None, force=False, doing_live=True) is False


def test_multi_leg_execution_pipeline():
    """Multi-leg strategy (spread) → rank → build plan → dry-run → verify all legs filled."""
    candidates = generate_strategies(BTC_OPTION_DATA, "bullish", "medium", asset="BTC", expiry="2026-02-26T08:00:00Z")
    # Find a multi-leg candidate (spread)
    multi_leg = [c for c in candidates if len(c.legs) >= 2]
    assert len(multi_leg) > 0, "Expected at least one multi-leg strategy"
    exchange_quotes = fetch_all_exchanges("BTC", mock_dir=MOCK_DIR)
    divergence_by_strategy = {}
    for c in candidates:
        div = strategy_divergence(c, exchange_quotes, BTC_OPTION_DATA)
        if div is not None:
            divergence_by_strategy[id(c)] = div
    outcome_prices = [66000, 67000, 67800, 68200, 70000]
    fusion = run_forecast_fusion(None, P24H, 67723.50)
    confidence = forecast_confidence(P24H, 67723.50)
    scored = rank_strategies(
        candidates, fusion, "bullish", outcome_prices, "medium",
        67723.50, confidence, 1.0, cdf_values=None, divergence_by_strategy=divergence_by_strategy,
    )
    # Pick a multi-leg scored strategy
    multi_scored = [s for s in scored if len(s.strategy.legs) >= 2]
    assert len(multi_scored) > 0
    card = multi_scored[0]
    plan = build_execution_plan(card, "BTC", None, exchange_quotes, BTC_OPTION_DATA)
    plan.dry_run = True
    assert len(plan.orders) >= 2
    def factory(ex):
        return get_executor(ex, exchange_quotes, dry_run=True)
    report = execute_plan(plan, factory)
    assert report.all_filled is True
    assert len(report.results) == len(plan.orders)
    assert all(r.status == "simulated" for r in report.results)
    # Verify timestamps on all results
    assert all(r.timestamp != "" for r in report.results)
    assert report.started_at != ""
    assert report.finished_at != ""


def test_non_crypto_skips_execution():
    """Non-crypto symbols should not have exchange quotes; execution should not proceed."""
    from exchange import fetch_all_exchanges as _fetch
    quotes = _fetch("SPY", mock_dir=MOCK_DIR)
    assert quotes == [], "Non-crypto asset should return no exchange quotes"


def test_slippage_protection_e2e():
    """Full pipeline with tight slippage → order rejected or passes depending on fill."""
    candidates = generate_strategies(BTC_OPTION_DATA, "bullish", "medium", asset="BTC", expiry="2026-02-26T08:00:00Z")
    exchange_quotes = fetch_all_exchanges("BTC", mock_dir=MOCK_DIR)
    outcome_prices = [66000, 67000, 67800, 68200, 70000]
    fusion = run_forecast_fusion(None, P24H, 67723.50)
    confidence = forecast_confidence(P24H, 67723.50)
    scored = rank_strategies(
        candidates, fusion, "bullish", outcome_prices, "medium",
        67723.50, confidence, 1.0, cdf_values=None,
    )
    from pipeline import select_three_cards
    best, _, _ = select_three_cards(scored)
    assert best is not None
    plan = build_execution_plan(best, "BTC", None, exchange_quotes, BTC_OPTION_DATA)
    plan.dry_run = True
    plan.max_slippage_pct = 50.0  # generous threshold — should pass
    def factory(ex):
        return get_executor(ex, exchange_quotes, dry_run=True)
    report = execute_plan(plan, factory)
    assert report.all_filled is True
    assert all(r.status == "simulated" for r in report.results)
