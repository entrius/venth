"""Tests for Options GPS pipeline: fusion, strategies, payoff, ranking."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import (
    run_forecast_fusion,
    generate_strategies,
    compute_payoff_metrics,
    strategy_pnl_values,
    rank_strategies,
    select_three_cards,
    should_no_trade,
    forecast_confidence,
    is_volatility_elevated,
    _outcome_prices_with_probs,
    _outcome_prices_and_cdf,
    _percentile_weights,
    _interpolated_pop,
    PERCENTILE_CDF,
    StrategyCandidate,
    StrategyLeg,
)

CURRENT = 67600.0
P1H_BULL = {"0.5": 67800, "0.05": 67400, "0.95": 68200}
P24H_BULL = {"0.5": 67900, "0.05": 67300, "0.95": 68500}
P1H_BEAR = {"0.5": 67400, "0.05": 67000, "0.95": 67800}
P24H_BEAR = {"0.5": 67300, "0.05": 66900, "0.95": 67700}
P1H_NEUTRAL = {"0.5": 67600, "0.05": 67400, "0.95": 67800}
P24H_NEUTRAL = {"0.5": 67620, "0.05": 67450, "0.95": 67800}


def test_fusion_aligned_bullish():
    state = run_forecast_fusion(P1H_BULL, P24H_BULL, CURRENT)
    assert state == "aligned_bullish"


def test_fusion_aligned_bearish():
    state = run_forecast_fusion(P1H_BEAR, P24H_BEAR, CURRENT)
    assert state == "aligned_bearish"


def test_fusion_countermove():
    state = run_forecast_fusion(P1H_BULL, P24H_BEAR, CURRENT)
    assert state == "countermove"


def test_fusion_unclear():
    state = run_forecast_fusion(P1H_NEUTRAL, P24H_NEUTRAL, CURRENT)
    assert state == "unclear"


def test_fusion_empty_1h_falls_back_to_24h():
    assert run_forecast_fusion({}, P24H_BULL, CURRENT) == "aligned_bullish"
    assert run_forecast_fusion({}, P24H_BEAR, CURRENT) == "aligned_bearish"


def test_fusion_empty_24h_returns_unclear():
    assert run_forecast_fusion(P1H_BULL, {}, CURRENT) == "unclear"


def test_fusion_none_1h_falls_back_to_24h_bullish():
    state = run_forecast_fusion(None, P24H_BULL, CURRENT)
    assert state == "aligned_bullish"


def test_fusion_none_1h_falls_back_to_24h_bearish():
    state = run_forecast_fusion(None, P24H_BEAR, CURRENT)
    assert state == "aligned_bearish"


def test_fusion_none_1h_falls_back_to_24h_unclear():
    state = run_forecast_fusion(None, P24H_NEUTRAL, CURRENT)
    assert state == "unclear"


def test_generate_strategies_bullish():
    option_data = {
        "current_price": 67723,
        "call_options": {"67000": 1000, "67500": 640, "68000": 373, "68500": 197},
        "put_options": {"67000": 140, "67500": 291, "68000": 526},
    }
    candidates = generate_strategies(option_data, "bullish", "medium")
    assert len(candidates) >= 1
    types = [c.strategy_type for c in candidates]
    assert "long_call" in types or "call_debit_spread" in types
    assert "bull_put_credit_spread" in types


def test_generate_strategies_bearish():
    option_data = {
        "current_price": 67723,
        "call_options": {"66500": 1400, "67000": 987, "67500": 640, "68000": 373},
        "put_options": {"66500": 57, "67000": 140, "67500": 291, "68000": 526},
    }
    candidates = generate_strategies(option_data, "bearish", "medium")
    assert len(candidates) >= 1
    assert any(c.direction == "bearish" for c in candidates)
    assert any(c.strategy_type == "bear_call_credit_spread" for c in candidates)


def test_generate_strategies_neutral_has_butterfly():
    option_data = {
        "current_price": 67723,
        "call_options": {"66500": 1400, "67000": 987, "67500": 640, "68000": 373, "68500": 197},
        "put_options": {"66500": 57, "67000": 140, "67500": 291, "68000": 526, "68500": 850},
    }
    candidates = generate_strategies(option_data, "neutral", "medium")
    assert any(c.strategy_type == "long_call_butterfly" for c in candidates)


def test_compute_payoff_long_call():
    strat = StrategyCandidate("long_call", "bullish", "Long call", [68000], 400, 400)
    outcomes = [67000, 67500, 68000, 68500, 69000]
    pop, ev = compute_payoff_metrics(strat, outcomes)
    assert 0 <= pop <= 1
    assert ev == -100.0


def test_iron_condor_max_loss_uses_wider_wing():
    option_data = {
        "current_price": 100.0,
        "call_options": {"90": 15.0, "97": 9.0, "100": 7.0, "104": 5.0, "112": 3.0},
        "put_options": {"90": 3.0, "97": 6.0, "100": 8.0, "104": 12.0, "112": 18.0},
    }
    candidates = generate_strategies(option_data, "neutral", "medium")
    condor = next(c for c in candidates if c.strategy_type == "iron_condor")
    assert condor.max_loss == 3.0


def test_rank_and_select_three():
    strat1 = StrategyCandidate("long_call", "bullish", "A", [68000], 400, 400)
    strat2 = StrategyCandidate("long_put", "bearish", "B", [67000], 300, 300)
    strat3 = StrategyCandidate("call_debit_spread", "bullish", "C", [67500, 68500], 300, 300)
    outcomes = [67000, 67500, 68000, 68500, 69000]
    scored = rank_strategies([strat1, strat2, strat3], "aligned_bullish", "bullish", outcomes, "medium", 68000)
    assert len(scored) == 3
    best, safer, upside = select_three_cards(scored)
    assert best is not None
    assert best.strategy.direction == "bullish"
    assert safer is not None
    assert upside is not None
    assert safer is not upside


def test_should_no_trade_countermove_bullish():
    result = should_no_trade("countermove", "bullish", False)
    assert result is not None
    assert "conflict" in result.lower() or "disagree" in result.lower()


def test_should_no_trade_unclear_neutral():
    assert should_no_trade("unclear", "neutral", False) is None


def test_should_no_trade_volatility_high():
    result = should_no_trade("aligned_bullish", "bullish", True)
    assert result is not None
    assert "volatility" in result.lower()


def test_credit_spread_pnl_positive_inside_spread():
    strat = StrategyCandidate("bull_put_credit_spread", "bullish", "Bull put", [66000, 67000], -120, 880)
    pnl = strategy_pnl_values(strat, [67500, 67000, 66500, 66000])
    assert pnl[0] > 0
    assert pnl[-1] < 0


def test_confidence_narrow_spread():
    pct = {"0.05": 67000, "0.5": 67500, "0.95": 68000}
    conf = forecast_confidence(pct, 67500)
    assert conf > 0.7


def test_confidence_wide_spread():
    pct = {"0.05": 60000, "0.5": 67500, "0.95": 80000}
    conf = forecast_confidence(pct, 67500)
    assert conf < 0.3


def test_should_no_trade_low_confidence():
    result = should_no_trade("aligned_bullish", "bullish", False, confidence=0.1)
    assert result is not None
    assert "confidence" in result.lower()


def test_should_no_trade_ok_confidence():
    assert should_no_trade("aligned_bullish", "bullish", False, confidence=0.8) is None


def test_is_volatility_elevated_adaptive():
    assert is_volatility_elevated(80, 50) is True
    assert is_volatility_elevated(55, 50) is False
    assert is_volatility_elevated(66, 50) is True


def test_is_volatility_elevated_no_realized():
    assert is_volatility_elevated(70, 0) is True
    assert is_volatility_elevated(50, 0) is False


def test_ev_percentage_in_rationale():
    strat = StrategyCandidate("long_call", "bullish", "A", [68000], 400, 400)
    outcomes = [67000, 67500, 68000, 68500, 69000]
    scored = rank_strategies([strat], "aligned_bullish", "bullish", outcomes, "medium", 68000)
    assert len(scored) >= 1
    assert "%" in scored[0].rationale


def test_vol_elevated_prefers_defined_risk():
    naked = StrategyCandidate("long_call", "bullish", "Naked call", [68000], 400, 400)
    spread = StrategyCandidate("call_debit_spread", "bullish", "Spread", [67500, 68500], 300, 300)
    outcomes = [67000, 67500, 68000, 68500, 69000]
    scored_normal = rank_strategies([naked, spread], "aligned_bullish", "bullish", outcomes, "medium", 68000, volatility_ratio=1.0)
    scored_highvol = rank_strategies([naked, spread], "aligned_bullish", "bullish", outcomes, "medium", 68000, volatility_ratio=1.5)
    normal_top = scored_normal[0].strategy.strategy_type
    highvol_top = scored_highvol[0].strategy.strategy_type
    assert highvol_top == "call_debit_spread"
    assert "vol" in scored_highvol[0].rationale.lower()


def test_generate_strategies_have_legs():
    option_data = {
        "current_price": 67723,
        "call_options": {"67000": 1000, "67500": 640, "68000": 373, "68500": 197},
        "put_options": {"67000": 140, "67500": 291, "68000": 526},
        "expiry_time": "2026-02-26 08:00:00Z",
    }
    candidates = generate_strategies(option_data, "bullish", "medium", asset="BTC", expiry="2026-02-26 08:00:00Z")
    for c in candidates:
        assert len(c.legs) >= 1, f"{c.description} has no legs"
        assert c.expiry == "2026-02-26 08:00:00Z"
        for leg in c.legs:
            assert leg.action in ("BUY", "SELL")
            assert leg.option_type in ("Call", "Put")
            assert leg.premium > 0
            assert leg.strike > 0


def test_generate_strategies_spread_has_two_legs():
    option_data = {
        "current_price": 67723,
        "call_options": {"67000": 1000, "67500": 640, "68000": 373, "68500": 197},
        "put_options": {"67000": 140, "67500": 291, "68000": 526},
    }
    candidates = generate_strategies(option_data, "bullish", "medium")
    spreads = [c for c in candidates if "spread" in c.strategy_type]
    for s in spreads:
        assert len(s.legs) == 2, f"{s.description} should have 2 legs"
        actions = {leg.action for leg in s.legs}
        assert "BUY" in actions and "SELL" in actions
        assert s.max_profit > 0
        assert s.max_profit_condition


def test_outcome_prices_with_probs():
    pct = {"0.05": 64000, "0.2": 66000, "0.35": 67000, "0.5": 67500, "0.65": 68000, "0.8": 69000, "0.95": 72000}
    result = _outcome_prices_with_probs(pct)
    assert len(result) == 7
    labels = [r[0] for r in result]
    assert labels == ["5%", "20%", "35%", "50%", "65%", "80%", "95%"]
    assert result[0][1] == 64000
    assert result[-1][1] == 72000


def test_risk_plan_has_specific_values():
    strat = StrategyCandidate(
        "long_call", "bullish", "Long call (ATM)", [68000], 400, 400,
        legs=[StrategyLeg("BUY", 1, "Call", 68000, 400)],
        expiry="2026-02-26",
    )
    from pipeline import _risk_plan
    inv, reroute, review = _risk_plan(strat)
    assert "$" in inv
    assert "200" in inv  # 50% of $400
    assert "68,400" in review  # breakeven = 68000 + 400
    assert "2026-02-26" in review


def test_parse_screen_arg():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from main import _parse_screen_arg
    assert _parse_screen_arg("all") == {1, 2, 3, 4}
    assert _parse_screen_arg("2,3") == {2, 3}
    assert _parse_screen_arg("1") == {1}
    assert _parse_screen_arg("") == {1, 2, 3, 4}  # invalid falls back to all
    assert _parse_screen_arg("5") == {1, 2, 3, 4}  # out of range falls back


def test_card_to_log_serializes_legs():
    from pipeline import ScoredStrategy
    from main import _card_to_log
    strat = StrategyCandidate(
        "long_call", "bullish", "Long call (ATM)", [68000], 400, 400,
        legs=[StrategyLeg("BUY", 1, "Call", 68000, 400.50)],
        expiry="2026-02-26", max_profit=0, max_profit_condition="Unlimited upside",
    )
    card = ScoredStrategy(strat, 0.5, 100.0, 400.0, "premium at risk",
                          "close at 50%", "roll out", "review at 50%", 0.8, "Fit 100%")
    log = _card_to_log(card)
    assert log is not None
    assert log["type"] == "long_call"
    assert len(log["legs"]) == 1
    assert log["legs"][0]["action"] == "BUY"
    assert log["legs"][0]["strike"] == 68000
    assert log["legs"][0]["premium"] == 400.50
    assert log["net_cost"] == 400
    assert log["expiry"] == "2026-02-26"
    assert log["pop"] == 0.5
    assert _card_to_log(None) is None


def test_percentile_weights_sum_to_one():
    weights = _percentile_weights(PERCENTILE_CDF)
    assert abs(sum(weights) - 1.0) < 1e-9
    assert abs(weights[0] - 0.125) < 1e-9  # [0, midpoint(0.05, 0.20)] = 0.125
    assert abs(weights[3] - 0.15) < 1e-9   # middle bins = 0.15
    assert abs(weights[-1] - 0.125) < 1e-9 # [midpoint(0.80, 0.95), 1.0] = 0.125


def test_interpolated_pop_long_call():
    """Long call at 68000, premium 400. Breakeven at 68400.
    With outcomes at CDF [0.05..0.95], PoP should interpolate at zero-crossing."""
    strat = StrategyCandidate("long_call", "bullish", "Long call", [68000], 400, 400)
    outcomes = [65000, 66500, 67500, 68000, 68500, 69500, 72000]
    pnl = strategy_pnl_values(strat, outcomes)
    # pnl: [-400, -400, -400, -400, +100, +1100, +3600]
    pop = _interpolated_pop(pnl, PERCENTILE_CDF)
    # Zero crossing between index 3 (cdf=0.50, pnl=-400) and 4 (cdf=0.65, pnl=+100)
    # frac = 400/500 = 0.80, positive portion = 0.15 * 0.20 = 0.03
    # + [0.65,0.80] = 0.15 + [0.80,0.95] = 0.15 + right tail = 0.05
    # total = 0.03 + 0.15 + 0.15 + 0.05 = 0.38
    assert 0.35 < pop < 0.42


def test_interpolated_pop_all_profitable():
    pnl = [100, 200, 300, 400, 500, 600, 700]
    pop = _interpolated_pop(pnl, PERCENTILE_CDF)
    assert abs(pop - 1.0) < 1e-9


def test_interpolated_pop_all_losing():
    pnl = [-100, -200, -300, -400, -500, -600, -700]
    pop = _interpolated_pop(pnl, PERCENTILE_CDF)
    assert abs(pop - 0.0) < 1e-9


def test_compute_payoff_with_cdf_differs_from_equal_weight():
    """With CDF values, PoP and EV should differ from naive equal-weight."""
    strat = StrategyCandidate("long_call", "bullish", "Long call", [68000], 400, 400)
    outcomes = [65000, 66500, 67500, 68000, 68500, 69500, 72000]
    pop_eq, ev_eq = compute_payoff_metrics(strat, outcomes)
    pop_cdf, ev_cdf = compute_payoff_metrics(strat, outcomes, cdf_values=PERCENTILE_CDF)
    # Equal-weight: 3/7 profitable = 42.9%, CDF-weighted should be different
    assert pop_eq != pop_cdf
    assert ev_eq != ev_cdf
    # CDF-weighted should give lower PoP here (tails overweighted in equal-weight)
    assert pop_cdf < pop_eq


def test_outcome_prices_and_cdf_returns_matched_pairs():
    pct = {"0.05": 64000, "0.2": 66000, "0.5": 67500, "0.95": 72000}
    prices, cdf = _outcome_prices_and_cdf(pct)
    assert len(prices) == len(cdf) == 4
    assert cdf == [0.05, 0.2, 0.5, 0.95]
    assert prices == [64000, 66000, 67500, 72000]


def test_outcome_prices_and_cdf_empty_fallback():
    prices, cdf = _outcome_prices_and_cdf({"0.5": 100})
    assert prices == [100]
    assert cdf == [0.5]


def test_rank_strategies_uses_cdf_when_provided():
    """rank_strategies with cdf_values should produce different scores than without."""
    strat = StrategyCandidate("long_call", "bullish", "A", [68000], 400, 400)
    outcomes = [65000, 66500, 67500, 68000, 68500, 69500, 72000]
    scored_eq = rank_strategies([strat], "aligned_bullish", "bullish", outcomes, "medium", 68000)
    scored_cdf = rank_strategies([strat], "aligned_bullish", "bullish", outcomes, "medium", 68000, cdf_values=PERCENTILE_CDF)
    assert len(scored_eq) == 1 and len(scored_cdf) == 1
    assert scored_eq[0].probability_of_profit != scored_cdf[0].probability_of_profit


def test_forecast_path_uses_real_time_labels():
    from main import _forecast_path
    fake_steps = [
        {"0.05": 100, "0.5": 105, "0.95": 110},
        {"0.05": 99, "0.5": 106, "0.95": 112},
        {"0.05": 98, "0.5": 107, "0.95": 114},
    ]
    lines_1h = _forecast_path(fake_steps, "1h", horizon_minutes=60)
    assert any("0m" in line for line in lines_1h)
    assert any("60m" in line for line in lines_1h)
    assert not any("now" in line for line in lines_1h)
    lines_24h = _forecast_path(fake_steps, "24h", horizon_minutes=1440)
    assert any("0h" in line for line in lines_24h)
    assert any("24h" in line for line in lines_24h)
    assert any("median" in line for line in lines_24h)  # column header
