"""
Options GPS decision pipeline: forecast fusion, strategy generation,
payoff/probability engine, ranking, and guardrails.
Uses Synth get_prediction_percentiles, get_option_pricing, get_volatility.
"""

from dataclasses import dataclass, field
from typing import Literal

ViewBias = Literal["bullish", "bearish", "neutral", "vol"]
RiskLevel = Literal["low", "medium", "high"]
FusionState = Literal["aligned_bullish", "aligned_bearish", "countermove", "unclear"]
VolBias = Literal["long_vol", "short_vol", "neutral_vol"]


@dataclass
class StrategyLeg:
    action: str          # "BUY" or "SELL"
    quantity: int
    option_type: str     # "Call" or "Put"
    strike: float
    premium: float


@dataclass
class StrategyCandidate:
    strategy_type: str
    direction: Literal["bullish", "bearish", "neutral"]
    description: str
    strikes: list[float]
    cost: float
    max_loss: float
    legs: list[StrategyLeg] = field(default_factory=list)
    expiry: str = ""
    max_profit: float = 0.0
    max_profit_condition: str = ""


@dataclass
class ScoredStrategy:
    strategy: StrategyCandidate
    probability_of_profit: float
    expected_value: float
    tail_risk: float
    loss_profile: str
    invalidation_trigger: str
    reroute_rule: str
    review_again_at: str
    score: float
    rationale: str


def run_forecast_fusion(percentiles_1h: dict | None, percentiles_24h: dict, current_price: float) -> FusionState:
    """Classify market state from 1h and 24h forecast percentiles (last-step dict). Uses median vs current.
    If 1h data is missing, falls back to 24h-only classification."""
    if not percentiles_24h:
        return "unclear"
    p24h = percentiles_24h.get("0.5")
    if p24h is None:
        return "unclear"
    thresh = current_price * 0.002
    up_24h = p24h > current_price + thresh
    down_24h = p24h < current_price - thresh
    if not percentiles_1h:
        if up_24h:
            return "aligned_bullish"
        if down_24h:
            return "aligned_bearish"
        return "unclear"
    p1h = percentiles_1h.get("0.5")
    if p1h is None:
        if up_24h:
            return "aligned_bullish"
        if down_24h:
            return "aligned_bearish"
        return "unclear"
    up_1h = p1h > current_price + thresh
    down_1h = p1h < current_price - thresh
    if up_1h and up_24h:
        return "aligned_bullish"
    if down_1h and down_24h:
        return "aligned_bearish"
    if (up_1h and down_24h) or (down_1h and up_24h):
        return "countermove"
    return "unclear"


def _parse_strikes(option_data: dict) -> list[float]:
    calls = option_data.get("call_options") or {}
    return sorted([float(k) for k in calls.keys()])


def generate_strategies(
    option_data: dict,
    view: ViewBias,
    risk: RiskLevel,
    asset: str = "BTC",
    expiry: str = "",
) -> list[StrategyCandidate]:
    """Build candidate strategies from option pricing and user view/risk."""
    current = float(option_data.get("current_price", 0))
    if current <= 0:
        return []
    strikes = _parse_strikes(option_data)
    if len(strikes) < 3:
        return []
    calls = {float(k): v for k, v in (option_data.get("call_options") or {}).items()}
    puts = {float(k): v for k, v in (option_data.get("put_options") or {}).items()}
    candidates: list[StrategyCandidate] = []
    atm = min(strikes, key=lambda s: abs(s - current))
    idx_atm = strikes.index(atm)
    otm_call = strikes[min(idx_atm + 2, len(strikes) - 1)] if idx_atm + 2 < len(strikes) else strikes[-1]
    otm_put = strikes[max(idx_atm - 2, 0)] if idx_atm >= 2 else strikes[0]

    def _long_call(strike, label):
        prem = float(calls[strike])
        be = strike + prem
        return StrategyCandidate(
            "long_call", "bullish", label, [strike], prem, prem,
            legs=[StrategyLeg("BUY", 1, "Call", strike, prem)],
            expiry=expiry,
            max_profit_condition=f"Unlimited upside if {asset} > ${be:,.0f} (breakeven)",
        )

    def _long_put(strike, label):
        prem = float(puts[strike])
        be = strike - prem
        return StrategyCandidate(
            "long_put", "bearish", label, [strike], prem, prem,
            legs=[StrategyLeg("BUY", 1, "Put", strike, prem)],
            expiry=expiry,
            max_profit_condition=f"Max profit if {asset} falls well below ${be:,.0f} (breakeven)",
        )

    if view == "bullish":
        if atm in calls:
            candidates.append(_long_call(atm, "Long call (ATM)"))
        if otm_call in calls and otm_call != atm:
            candidates.append(_long_call(otm_call, "Long call (OTM)"))
        if atm in calls and otm_call in calls:
            prem_buy = float(calls[atm])
            prem_sell = float(calls[otm_call])
            debit = prem_buy - prem_sell
            if debit > 0:
                width = otm_call - atm
                mp = width - debit
                candidates.append(StrategyCandidate(
                    "call_debit_spread", "bullish", "Call debit spread", [atm, otm_call], debit, debit,
                    legs=[StrategyLeg("BUY", 1, "Call", atm, prem_buy),
                          StrategyLeg("SELL", 1, "Call", otm_call, prem_sell)],
                    expiry=expiry, max_profit=mp,
                    max_profit_condition=f"${mp:,.0f} if {asset} >= ${otm_call:,.0f} at expiry",
                ))
        put_short = atm
        put_long = strikes[max(0, idx_atm - 1)]
        if put_short in puts and put_long in puts and put_short > put_long:
            prem_sell = float(puts[put_short])
            prem_buy = float(puts[put_long])
            credit = prem_sell - prem_buy
            width = put_short - put_long
            if credit > 0:
                candidates.append(StrategyCandidate(
                    "bull_put_credit_spread", "bullish", "Bull put credit spread",
                    [put_long, put_short], -credit, width - credit,
                    legs=[StrategyLeg("SELL", 1, "Put", put_short, prem_sell),
                          StrategyLeg("BUY", 1, "Put", put_long, prem_buy)],
                    expiry=expiry, max_profit=credit,
                    max_profit_condition=f"${credit:,.0f} credit kept if {asset} >= ${put_short:,.0f} at expiry",
                ))
    if view == "bearish":
        if atm in puts:
            candidates.append(_long_put(atm, "Long put (ATM)"))
        if otm_put in puts and otm_put != atm:
            candidates.append(_long_put(otm_put, "Long put (OTM)"))
        if atm in puts and otm_put in puts:
            prem_buy = float(puts[atm])
            prem_sell = float(puts[otm_put])
            debit = prem_buy - prem_sell
            if debit > 0:
                width = atm - otm_put
                mp = width - debit
                candidates.append(StrategyCandidate(
                    "put_debit_spread", "bearish", "Put debit spread", [otm_put, atm], debit, debit,
                    legs=[StrategyLeg("BUY", 1, "Put", atm, prem_buy),
                          StrategyLeg("SELL", 1, "Put", otm_put, prem_sell)],
                    expiry=expiry, max_profit=mp,
                    max_profit_condition=f"${mp:,.0f} if {asset} <= ${otm_put:,.0f} at expiry",
                ))
        call_short = atm
        call_long = strikes[min(len(strikes) - 1, idx_atm + 1)]
        if call_short in calls and call_long in calls and call_long > call_short:
            prem_sell = float(calls[call_short])
            prem_buy = float(calls[call_long])
            credit = prem_sell - prem_buy
            width = call_long - call_short
            if credit > 0:
                candidates.append(StrategyCandidate(
                    "bear_call_credit_spread", "bearish", "Bear call credit spread",
                    [call_short, call_long], -credit, width - credit,
                    legs=[StrategyLeg("SELL", 1, "Call", call_short, prem_sell),
                          StrategyLeg("BUY", 1, "Call", call_long, prem_buy)],
                    expiry=expiry, max_profit=credit,
                    max_profit_condition=f"${credit:,.0f} credit kept if {asset} <= ${call_short:,.0f} at expiry",
                ))
    if view == "neutral" or (view == "bullish" and risk == "low") or (view == "bearish" and risk == "low"):
        low_put = strikes[max(0, idx_atm - 3)]
        high_call = strikes[min(len(strikes) - 1, idx_atm + 3)]
        put_short = strikes[max(0, idx_atm - 1)]
        call_short = strikes[min(len(strikes) - 1, idx_atm + 1)]
        if low_put in puts and high_call in calls and put_short in puts and call_short in calls and low_put < current < high_call:
            prem_ps = float(puts[put_short])
            prem_pl = float(puts[low_put])
            prem_cs = float(calls[call_short])
            prem_ch = float(calls[high_call])
            credit_put = prem_ps - prem_pl
            credit_call = prem_cs - prem_ch
            credit = credit_put + credit_call
            if credit > 0:
                max_width = max(put_short - low_put, high_call - call_short)
                max_loss = max_width - credit
                candidates.append(StrategyCandidate(
                    "iron_condor", "neutral", "Iron condor (defined risk)",
                    [put_short, call_short], -credit, max_loss,
                    legs=[StrategyLeg("BUY", 1, "Put", low_put, prem_pl),
                          StrategyLeg("SELL", 1, "Put", put_short, prem_ps),
                          StrategyLeg("SELL", 1, "Call", call_short, prem_cs),
                          StrategyLeg("BUY", 1, "Call", high_call, prem_ch)],
                    expiry=expiry, max_profit=credit,
                    max_profit_condition=f"${credit:,.0f} if {asset} between ${put_short:,.0f}-${call_short:,.0f} at expiry",
                ))
    if view == "neutral":
        lower = strikes[max(0, idx_atm - 2)]
        upper = strikes[min(len(strikes) - 1, idx_atm + 2)]
        if lower in calls and atm in calls and upper in calls and lower < atm < upper:
            prem_lo = float(calls[lower])
            prem_atm = float(calls[atm])
            prem_up = float(calls[upper])
            cost = prem_lo - 2 * prem_atm + prem_up
            if cost > 0:
                mp = (atm - lower) - cost
                candidates.append(StrategyCandidate(
                    "long_call_butterfly", "neutral", "Long call butterfly",
                    [lower, atm, upper], cost, cost,
                    legs=[StrategyLeg("BUY", 1, "Call", lower, prem_lo),
                          StrategyLeg("SELL", 2, "Call", atm, prem_atm),
                          StrategyLeg("BUY", 1, "Call", upper, prem_up)],
                    expiry=expiry, max_profit=mp,
                    max_profit_condition=f"${mp:,.0f} if {asset} at ${atm:,.0f} at expiry",
                ))
        if atm in calls:
            candidates.append(_long_call(atm, "Long call (ATM)"))
        if atm in puts:
            candidates.append(_long_put(atm, "Long put (ATM)"))
    if view == "vol":
        # Long straddle: buy ATM call + ATM put
        if atm in calls and atm in puts:
            prem_c = float(calls[atm])
            prem_p = float(puts[atm])
            total = prem_c + prem_p
            be_up = atm + total
            be_dn = atm - total
            candidates.append(StrategyCandidate(
                "long_straddle", "neutral", "Long straddle (ATM)",
                [atm], total, total,
                legs=[StrategyLeg("BUY", 1, "Call", atm, prem_c),
                      StrategyLeg("BUY", 1, "Put", atm, prem_p)],
                expiry=expiry,
                max_profit_condition=f"Unlimited if {asset} moves beyond ${be_dn:,.0f}-${be_up:,.0f}",
            ))
        # Long strangle: buy OTM call + OTM put
        if otm_call in calls and otm_put in puts and otm_call != atm and otm_put != atm:
            prem_c = float(calls[otm_call])
            prem_p = float(puts[otm_put])
            total = prem_c + prem_p
            be_up = otm_call + total
            be_dn = otm_put - total
            candidates.append(StrategyCandidate(
                "long_strangle", "neutral", "Long strangle (OTM)",
                [otm_put, otm_call], total, total,
                legs=[StrategyLeg("BUY", 1, "Put", otm_put, prem_p),
                      StrategyLeg("BUY", 1, "Call", otm_call, prem_c)],
                expiry=expiry,
                max_profit_condition=f"Unlimited if {asset} moves beyond ${be_dn:,.0f}-${be_up:,.0f}",
            ))
        # Short straddle: sell ATM call + ATM put (high risk only)
        if risk == "high" and atm in calls and atm in puts:
            prem_c = float(calls[atm])
            prem_p = float(puts[atm])
            credit = prem_c + prem_p
            be_up = atm + credit
            be_dn = atm - credit
            candidates.append(StrategyCandidate(
                "short_straddle", "neutral", "Short straddle (ATM)",
                [atm], -credit, credit * 3,
                legs=[StrategyLeg("SELL", 1, "Call", atm, prem_c),
                      StrategyLeg("SELL", 1, "Put", atm, prem_p)],
                expiry=expiry, max_profit=credit,
                max_profit_condition=f"${credit:,.0f} if {asset} pins at ${atm:,.0f}; profit zone ${be_dn:,.0f}-${be_up:,.0f}",
            ))
        # Short strangle: sell OTM call + OTM put (medium/high risk)
        if risk in ("medium", "high") and otm_call in calls and otm_put in puts and otm_call != atm and otm_put != atm:
            prem_c = float(calls[otm_call])
            prem_p = float(puts[otm_put])
            credit = prem_c + prem_p
            be_up = otm_call + credit
            be_dn = otm_put - credit
            if credit > 0:
                candidates.append(StrategyCandidate(
                    "short_strangle", "neutral", "Short strangle (OTM)",
                    [otm_put, otm_call], -credit, credit * 3,
                    legs=[StrategyLeg("SELL", 1, "Put", otm_put, prem_p),
                          StrategyLeg("SELL", 1, "Call", otm_call, prem_c)],
                    expiry=expiry, max_profit=credit,
                    max_profit_condition=f"${credit:,.0f} if {asset} stays in ${be_dn:,.0f}-${be_up:,.0f}",
                ))
        # Iron condor (reuse neutral logic for vol view — defined-risk short vol)
        low_put = strikes[max(0, idx_atm - 3)]
        high_call = strikes[min(len(strikes) - 1, idx_atm + 3)]
        put_short = strikes[max(0, idx_atm - 1)]
        call_short = strikes[min(len(strikes) - 1, idx_atm + 1)]
        if low_put in puts and high_call in calls and put_short in puts and call_short in calls and low_put < current < high_call:
            prem_ps = float(puts[put_short])
            prem_pl = float(puts[low_put])
            prem_cs = float(calls[call_short])
            prem_ch = float(calls[high_call])
            credit_put = prem_ps - prem_pl
            credit_call = prem_cs - prem_ch
            credit = credit_put + credit_call
            if credit > 0:
                max_width = max(put_short - low_put, high_call - call_short)
                max_loss = max_width - credit
                candidates.append(StrategyCandidate(
                    "iron_condor", "neutral", "Iron condor (defined risk)",
                    [put_short, call_short], -credit, max_loss,
                    legs=[StrategyLeg("BUY", 1, "Put", low_put, prem_pl),
                          StrategyLeg("SELL", 1, "Put", put_short, prem_ps),
                          StrategyLeg("SELL", 1, "Call", call_short, prem_cs),
                          StrategyLeg("BUY", 1, "Call", high_call, prem_ch)],
                    expiry=expiry, max_profit=credit,
                    max_profit_condition=f"${credit:,.0f} if {asset} between ${put_short:,.0f}-${call_short:,.0f} at expiry",
                ))
    if not candidates and view == "neutral":
        if atm in calls:
            candidates.append(_long_call(atm, "Long call (ATM)"))
        if atm in puts:
            candidates.append(_long_put(atm, "Long put (ATM)"))
    return candidates


PERCENTILE_KEYS = ["0.05", "0.2", "0.35", "0.5", "0.65", "0.8", "0.95"]
PERCENTILE_CDF = [float(k) for k in PERCENTILE_KEYS]  # [0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95]
PERCENTILE_LABELS = {"0.05": "5%", "0.2": "20%", "0.35": "35%", "0.5": "50%", "0.65": "65%", "0.8": "80%", "0.95": "95%"}


def _outcome_prices(percentiles_last: dict) -> list[float]:
    """Ordered outcome prices from percentile dict (e.g. 0.05, 0.2, ..., 0.95)."""
    out = []
    for k in PERCENTILE_KEYS:
        if k in percentiles_last:
            out.append(float(percentiles_last[k]))
    return out if out else [float(percentiles_last.get("0.5", 0))]


def _outcome_prices_with_probs(percentiles_last: dict) -> list[tuple[str, float]]:
    """Return (probability_label, price) pairs for display."""
    out = []
    for k in PERCENTILE_KEYS:
        if k in percentiles_last:
            out.append((PERCENTILE_LABELS.get(k, k), float(percentiles_last[k])))
    return out if out else [("50%", float(percentiles_last.get("0.5", 0)))]


def _percentile_weights(cdf_values: list[float]) -> list[float]:
    """Probability mass each percentile point represents (midpoint rule).
    E.g. for CDF [0.05, 0.20, ...] the 5th-pctl point covers [0, 0.125] = weight 0.125."""
    n = len(cdf_values)
    weights: list[float] = []
    for i in range(n):
        left = 0.0 if i == 0 else (cdf_values[i - 1] + cdf_values[i]) / 2
        right = 1.0 if i == n - 1 else (cdf_values[i] + cdf_values[i + 1]) / 2
        weights.append(right - left)
    return weights


def _interpolated_pop(pnl_values: list[float], cdf_values: list[float]) -> float:
    """Probability of profit via CDF interpolation at zero-crossing points.
    More accurate than counting discrete profitable outcomes."""
    n = len(pnl_values)
    if n == 0:
        return 0.0
    prob_positive = 0.0
    # Left tail: [0, cdf[0]]
    if pnl_values[0] > 0:
        prob_positive += cdf_values[0]
    # Between adjacent percentiles
    for i in range(n - 1):
        p1, p2 = pnl_values[i], pnl_values[i + 1]
        c1, c2 = cdf_values[i], cdf_values[i + 1]
        segment = c2 - c1
        if p1 > 0 and p2 > 0:
            prob_positive += segment
        elif p1 <= 0 and p2 > 0:
            frac = -p1 / (p2 - p1) if p2 != p1 else 0.5
            prob_positive += segment * (1 - frac)
        elif p1 > 0 and p2 <= 0:
            frac = p1 / (p1 - p2) if p1 != p2 else 0.5
            prob_positive += segment * frac
    # Right tail: [cdf[-1], 1.0]
    if pnl_values[-1] > 0:
        prob_positive += 1.0 - cdf_values[-1]
    return prob_positive


def _outcome_prices_and_cdf(percentiles_last: dict) -> tuple[list[float], list[float]]:
    """Return (prices, cdf_values) for matched percentile keys."""
    prices: list[float] = []
    cdf_vals: list[float] = []
    for k in PERCENTILE_KEYS:
        if k in percentiles_last:
            prices.append(float(percentiles_last[k]))
            cdf_vals.append(float(k))
    if not prices:
        return [float(percentiles_last.get("0.5", 0))], [0.5]
    return prices, cdf_vals


def _payoff_long_call(s: float, strike: float) -> float:
    return max(0.0, s - strike)


def _payoff_long_put(s: float, strike: float) -> float:
    return max(0.0, strike - s)


def _payoff_call_spread(s: float, k1: float, k2: float) -> float:
    return max(0.0, min(s - k1, k2 - k1))


def _payoff_put_spread(s: float, k1: float, k2: float) -> float:
    return max(0.0, min(k2 - s, k2 - k1))


def strategy_pnl_values(strategy: StrategyCandidate, outcome_prices: list[float]) -> list[float]:
    """P/L values for each outcome price."""
    pnl_values: list[float] = []
    for s in outcome_prices:
        gross_payoff = 0.0
        if strategy.strategy_type == "long_call":
            gross_payoff = _payoff_long_call(s, strategy.strikes[0])
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "long_put":
            gross_payoff = _payoff_long_put(s, strategy.strikes[0])
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "call_debit_spread":
            gross_payoff = _payoff_call_spread(s, strategy.strikes[0], strategy.strikes[1])
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "put_debit_spread":
            gross_payoff = _payoff_put_spread(s, strategy.strikes[0], strategy.strikes[1])
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "bull_put_credit_spread":
            k_long, k_short = strategy.strikes[0], strategy.strikes[1]
            credit = -strategy.cost
            pnl_values.append(credit - max(0.0, k_short - s) + max(0.0, k_long - s))
        elif strategy.strategy_type == "bear_call_credit_spread":
            k_short, k_long = strategy.strikes[0], strategy.strikes[1]
            credit = -strategy.cost
            pnl_values.append(credit - max(0.0, s - k_short) + max(0.0, s - k_long))
        elif strategy.strategy_type == "iron_condor":
            k_put_short, k_call_short = strategy.strikes[0], strategy.strikes[1]
            p_put = max(0.0, k_put_short - s) if s < k_put_short else 0.0
            p_call = max(0.0, s - k_call_short) if s > k_call_short else 0.0
            credit = -strategy.cost
            pnl_values.append(credit - (p_put + p_call))
        elif strategy.strategy_type == "long_call_butterfly":
            k1, k2, k3 = strategy.strikes[0], strategy.strikes[1], strategy.strikes[2]
            gross_payoff = max(0.0, s - k1) - 2 * max(0.0, s - k2) + max(0.0, s - k3)
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "long_straddle":
            k = strategy.strikes[0]
            pnl_values.append(max(0.0, s - k) + max(0.0, k - s) - strategy.cost)
        elif strategy.strategy_type == "long_strangle":
            k_put, k_call = strategy.strikes[0], strategy.strikes[1]
            pnl_values.append(max(0.0, k_put - s) + max(0.0, s - k_call) - strategy.cost)
        elif strategy.strategy_type == "short_straddle":
            k = strategy.strikes[0]
            credit = -strategy.cost
            pnl_values.append(credit - max(0.0, s - k) - max(0.0, k - s))
        elif strategy.strategy_type == "short_strangle":
            k_put, k_call = strategy.strikes[0], strategy.strikes[1]
            credit = -strategy.cost
            pnl_values.append(credit - max(0.0, k_put - s) - max(0.0, s - k_call))
        else:
            pnl_values.append(0.0)
    return pnl_values


def _tail_risk_from_pnl(pnl_values: list[float]) -> float:
    """Expected loss in worst 20% scenarios (non-negative)."""
    if not pnl_values:
        return 0.0
    worst_n = max(1, len(pnl_values) // 5)
    worst = sorted(pnl_values)[:worst_n]
    avg_worst = sum(worst) / worst_n
    return max(0.0, -avg_worst)


def _loss_profile(strategy: StrategyCandidate) -> str:
    st = strategy.strategy_type
    if st in ("bull_put_credit_spread", "bear_call_credit_spread", "iron_condor", "call_debit_spread", "put_debit_spread", "long_call_butterfly"):
        return "defined risk"
    if st in ("long_straddle", "long_strangle"):
        return "premium at risk"
    if st in ("short_straddle", "short_strangle"):
        return "unlimited risk"
    return "premium at risk"


def _risk_plan(strategy: StrategyCandidate) -> tuple[str, str, str]:
    st = strategy.strategy_type
    k = strategy.strikes
    cost = strategy.cost
    exp = f" before {strategy.expiry}" if strategy.expiry else ""
    if st == "long_call":
        be = k[0] + cost
        return (
            f"Close if option value drops below ${cost * 0.5:,.0f} (50% of premium). Stop: price < ${k[0] * 0.97:,.0f}.",
            f"Sell higher-strike call to convert into vertical spread. Or roll to next expiry if thesis holds.",
            f"Breakeven: ${be:,.0f}. Review at 50% time-to-expiry{exp}."
        )
    if st == "long_put":
        be = k[0] - cost
        return (
            f"Close if option value drops below ${cost * 0.5:,.0f} (50% of premium). Stop: price > ${k[0] * 1.03:,.0f}.",
            f"Sell lower-strike put to convert into vertical spread. Or roll to next expiry if thesis holds.",
            f"Breakeven: ${be:,.0f}. Review at 50% time-to-expiry{exp}."
        )
    if st == "call_debit_spread":
        be = k[0] + cost
        mp = (k[1] - k[0] - cost) if len(k) >= 2 else cost
        return (
            f"Close if underlying drops through ${k[0]:,.0f} (long strike). Max loss: ${cost:,.0f} (debit paid).",
            f"Buy back short ${k[1]:,.0f} call to go naked long if conviction rises. Close entire spread if weakens.",
            f"Breakeven: ${be:,.0f}. Max profit: ${mp:,.0f} above ${k[1]:,.0f}. Review at 50% time-to-expiry."
        )
    if st == "put_debit_spread":
        be = k[1] - cost
        mp = (k[1] - k[0] - cost) if len(k) >= 2 else cost
        return (
            f"Close if underlying rallies through ${k[1]:,.0f} (long strike). Max loss: ${cost:,.0f} (debit paid).",
            f"Buy back short ${k[0]:,.0f} put to go naked long if conviction rises. Close entire spread if weakens.",
            f"Breakeven: ${be:,.0f}. Max profit: ${mp:,.0f} below ${k[0]:,.0f}. Review at 50% time-to-expiry."
        )
    if st == "bull_put_credit_spread":
        credit = -cost
        be = k[1] - credit
        return (
            f"Close if underlying drops below ${k[1]:,.0f} (short strike) with momentum. Max loss: ${strategy.max_loss:,.0f}.",
            f"Roll short ${k[1]:,.0f} put down and out for additional credit. Or close tested side only.",
            f"Breakeven: ${be:,.0f}. Keep full ${credit:,.0f} credit if above ${k[1]:,.0f} at expiry."
        )
    if st == "bear_call_credit_spread":
        credit = -cost
        be = k[0] + credit
        return (
            f"Close if underlying rallies above ${k[0]:,.0f} (short strike) with momentum. Max loss: ${strategy.max_loss:,.0f}.",
            f"Roll short ${k[0]:,.0f} call up and out for additional credit. Or close tested side only.",
            f"Breakeven: ${be:,.0f}. Keep full ${credit:,.0f} credit if below ${k[0]:,.0f} at expiry."
        )
    if st == "iron_condor":
        credit = -cost
        return (
            f"Close tested wing if underlying breaches ${k[0]:,.0f} (put side) or ${k[1]:,.0f} (call side).",
            f"Close threatened wing for a loss; let untested wing expire worthless. Or roll entire condor out.",
            f"Profit zone: ${k[0]:,.0f}-${k[1]:,.0f}. Max credit: ${credit:,.0f}. Review every hour."
        )
    if st == "long_call_butterfly":
        mp = strategy.max_profit if strategy.max_profit > 0 else (k[1] - k[0] - cost if len(k) >= 3 else cost)
        return (
            f"Close if underlying moves far from ${k[1]:,.0f} center strike. Max loss: ${cost:,.0f} (debit paid).",
            f"Convert to directional spread if price drifts. Sell wing closer to price for partial recovery.",
            f"Max profit: ${mp:,.0f} at ${k[1]:,.0f}. Review near midpoint and at 25% time-to-expiry."
        )
    if st == "long_straddle":
        be_up = k[0] + cost
        be_dn = k[0] - cost
        return (
            f"Close if premium decays below ${cost * 0.5:,.0f} (50%) without a move. Stop: price stuck near ${k[0]:,.0f}.",
            f"Sell one leg to convert to directional if a trend emerges. Roll to next expiry if vol stays high.",
            f"Breakevens: ${be_dn:,.0f} / ${be_up:,.0f}. Review at 50% time-to-expiry{exp}."
        )
    if st == "long_strangle":
        be_dn = k[0] - cost
        be_up = k[1] + cost
        return (
            f"Close if premium decays below ${cost * 0.5:,.0f} (50%) without a move. Stop: price stuck between ${k[0]:,.0f}-${k[1]:,.0f}.",
            f"Sell one leg to convert to directional if a trend emerges. Roll to next expiry if vol stays high.",
            f"Breakevens: ${be_dn:,.0f} / ${be_up:,.0f}. Review at 50% time-to-expiry{exp}."
        )
    if st == "short_straddle":
        credit = -cost
        be_up = k[0] + credit
        be_dn = k[0] - credit
        return (
            f"Close if underlying moves beyond ${be_dn:,.0f}-${be_up:,.0f} (breakevens). Hard stop at 2x credit loss.",
            f"Buy a wing to convert to iron butterfly if tested. Roll out for more credit if near expiry.",
            f"Profit zone: ${be_dn:,.0f}-${be_up:,.0f}. Max credit: ${credit:,.0f}. Review every hour."
        )
    if st == "short_strangle":
        credit = -cost
        be_dn = k[0] - credit
        be_up = k[1] + credit
        return (
            f"Close tested side if underlying breaches ${k[0]:,.0f} (put) or ${k[1]:,.0f} (call). Hard stop at 2x credit loss.",
            f"Buy a wing to convert to iron condor if tested. Roll out for more credit if near expiry.",
            f"Profit zone: ${k[0]:,.0f}-${k[1]:,.0f}. Max credit: ${credit:,.0f}. Review every hour."
        )
    return (
        "Close on thesis break.",
        "Reduce to smaller defined-risk structure.",
        "Review every 1h."
    )


def passes_hard_filters(strategy: StrategyCandidate, risk: RiskLevel, current_price: float) -> bool:
    """Guardrails for max loss and spread-quality quality checks."""
    if strategy.max_loss <= 0:
        return False
    max_loss_cap = {"low": 0.02, "medium": 0.04, "high": 0.08}[risk] * current_price
    if strategy.max_loss > max_loss_cap:
        return False
    st = strategy.strategy_type
    if st in ("call_debit_spread", "put_debit_spread"):
        width = abs(strategy.strikes[1] - strategy.strikes[0])
        max_debit_ratio = {"low": 0.80, "medium": 0.90, "high": 1.00}[risk]
        return strategy.cost <= width * max_debit_ratio
    if st in ("bull_put_credit_spread", "bear_call_credit_spread"):
        width = abs(strategy.strikes[1] - strategy.strikes[0])
        credit = -strategy.cost
        min_credit_ratio = {"low": 0.15, "medium": 0.10, "high": 0.05}[risk]
        return credit >= width * min_credit_ratio
    if st == "iron_condor":
        return (-strategy.cost) > 0
    if st == "long_call_butterfly":
        left = strategy.strikes[1] - strategy.strikes[0]
        right = strategy.strikes[2] - strategy.strikes[1]
        return left > 0 and right > 0 and strategy.cost <= max(left, right)
    if st == "short_straddle":
        return risk == "high" and (-strategy.cost) > 0
    if st == "short_strangle":
        return risk in ("medium", "high") and (-strategy.cost) > 0
    if st in ("long_straddle", "long_strangle"):
        return strategy.cost > 0
    return True


def compute_payoff_metrics(
    strategy: StrategyCandidate,
    outcome_prices: list[float],
    cdf_values: list[float] | None = None,
) -> tuple[float, float]:
    """Return (probability_of_profit, expected_value) for strategy under outcome distribution.
    When cdf_values are provided, uses proper probability weighting and CDF interpolation.
    Otherwise falls back to equal-weight (for tests with arbitrary price lists)."""
    n = len(outcome_prices)
    if n == 0:
        return 0.0, 0.0
    pnl_values = strategy_pnl_values(strategy, outcome_prices)
    if cdf_values and len(cdf_values) == n:
        weights = _percentile_weights(cdf_values)
        ev = sum(w * p for w, p in zip(weights, pnl_values))
        pop = _interpolated_pop(pnl_values, cdf_values)
    else:
        ev = sum(pnl_values) / n
        pop = sum(1 for x in pnl_values if x > 0) / n
    return pop, ev


_DEFINED_RISK_TYPES = frozenset({
    "call_debit_spread", "put_debit_spread",
    "bull_put_credit_spread", "bear_call_credit_spread",
    "iron_condor", "long_call_butterfly",
})


_LONG_VOL_TYPES = frozenset({"long_straddle", "long_strangle"})
_SHORT_VOL_TYPES = frozenset({"short_straddle", "short_strangle", "iron_condor"})


def rank_strategies(
    candidates: list[StrategyCandidate],
    fusion_state: FusionState,
    view: ViewBias,
    outcome_prices: list[float],
    risk: RiskLevel,
    current_price: float,
    confidence: float = 1.0,
    volatility_ratio: float = 1.0,
    cdf_values: list[float] | None = None,
    vol_bias: VolBias | None = None,
) -> list[ScoredStrategy]:
    """Score and sort strategies. Returns list of ScoredStrategy sorted by score desc.
    volatility_ratio = forecast_vol / realized_vol (1.0 = normal). When elevated,
    defined-risk strategies get a bonus and naked/premium strategies get a penalty.
    cdf_values enables probability-weighted PoP/EV when provided.
    vol_bias controls scoring for vol view: long_vol favours straddles/strangles,
    short_vol favours iron condors and short straddles/strangles."""
    vol_elevated = volatility_ratio > 1.15
    scored: list[ScoredStrategy] = []
    for c in candidates:
        if not passes_hard_filters(c, risk, current_price):
            continue
        pop, ev = compute_payoff_metrics(c, outcome_prices, cdf_values)
        pnl_values = strategy_pnl_values(c, outcome_prices)
        tail_risk = _tail_risk_from_pnl(pnl_values)
        # View matching
        if view == "vol":
            if vol_bias == "long_vol" and c.strategy_type in _SHORT_VOL_TYPES:
                continue  # don't recommend short vol when bias is long
            if vol_bias == "short_vol" and c.strategy_type in _LONG_VOL_TYPES:
                continue  # don't recommend long vol when bias is short
            view_match = 1.3 if (
                (vol_bias == "long_vol" and c.strategy_type in _LONG_VOL_TYPES)
                or (vol_bias == "short_vol" and c.strategy_type in _SHORT_VOL_TYPES)
            ) else 1.0
        else:
            view_match = 1.0 if c.direction == view else (0.4 if c.direction == "neutral" else 0.1)
        fusion_bonus = 0.0
        if view != "vol":
            if fusion_state == "aligned_bullish" and c.direction == "bullish":
                fusion_bonus = 0.3
            elif fusion_state == "aligned_bearish" and c.direction == "bearish":
                fusion_bonus = 0.3
            elif fusion_state in ("countermove", "unclear") and c.direction == "neutral":
                fusion_bonus = 0.15
        fit = view_match + fusion_bonus
        w_pop = 0.4 if risk == "low" else (0.3 if risk == "medium" else 0.2)
        w_ev = 0.2 if risk == "low" else (0.3 if risk == "medium" else 0.4)
        score = fit * 0.4 + pop * w_pop + max(0, ev) * w_ev * 0.01
        tail_penalty = (1 - pop) * 0.1 + min(0.2, tail_risk * 0.0001)
        score -= tail_penalty
        if vol_elevated and view != "vol":
            if c.strategy_type in _DEFINED_RISK_TYPES:
                score += 0.15
            else:
                score -= 0.10
        if view != "vol":
            score *= confidence
        invalidation, reroute, review_time = _risk_plan(c)
        ev_pct = (ev / current_price * 100) if current_price > 0 else 0.0
        vol_note = ""
        if view == "vol" and vol_bias:
            vol_note = f" [vol bias: {vol_bias.replace('_', ' ')}]"
        elif vol_elevated:
            vol_note = " [vol: prefer spreads]"
        rationale = f"Fit {fit:.0%}, PoP {pop:.0%}, EV ${ev:,.0f} ({ev_pct:+.2f}%){vol_note}"
        scored.append(
            ScoredStrategy(
                strategy=c,
                probability_of_profit=pop,
                expected_value=ev,
                tail_risk=tail_risk,
                loss_profile=_loss_profile(c),
                invalidation_trigger=invalidation,
                reroute_rule=reroute,
                review_again_at=review_time,
                score=max(0, score),
                rationale=rationale,
            )
        )
    return sorted(scored, key=lambda x: -x.score)


def select_three_cards(scored: list[ScoredStrategy]) -> tuple[ScoredStrategy | None, ScoredStrategy | None, ScoredStrategy | None]:
    """Pick Best Match, Safer Alternative (higher PoP or lower max_loss), Higher Upside (higher EV)."""
    if not scored:
        return None, None, None
    best = scored[0]
    remaining = scored[1:]
    safer_candidates = [
        x for x in remaining
        if x.probability_of_profit > best.probability_of_profit
        or x.strategy.max_loss < best.strategy.max_loss
    ]
    if not safer_candidates:
        safer_candidates = remaining
    safer = max(safer_candidates, key=lambda x: x.probability_of_profit) if safer_candidates else None
    upside_candidates = [
        x for x in remaining
        if x is not safer and x.expected_value > best.expected_value
    ]
    if not upside_candidates:
        upside_candidates = [x for x in remaining if x is not safer]
    upside = max(upside_candidates, key=lambda x: x.expected_value) if upside_candidates else None
    return best, safer, upside


def forecast_confidence(percentiles_last: dict, current_price: float) -> float:
    """Confidence score 0-1 from percentile dispersion. Narrower spread = higher confidence."""
    p05 = percentiles_last.get("0.05")
    p95 = percentiles_last.get("0.95")
    if p05 is None or p95 is None or current_price <= 0:
        return 0.5
    spread = (float(p95) - float(p05)) / current_price
    if spread <= 0.02:
        return 1.0
    if spread >= 0.15:
        return 0.1
    return max(0.1, 1.0 - (spread - 0.02) / 0.13)


def adjust_confidence_for_divergence(base_confidence: float, avg_divergence: float,
                                     consensus: str) -> float:
    """Adjust forecast confidence based on multi-exchange divergence.
    Strong agreement with Synth nudges confidence up; large disagreement nudges it down.
    The adjustment is intentionally small — line shopping is a contextual overlay,
    not a hard guardrail."""
    if avg_divergence <= 0:
        return base_confidence
    if consensus == "strong_agreement":
        return min(1.0, base_confidence + 0.05)
    if consensus == "moderate_agreement":
        return base_confidence
    if consensus == "weak_agreement":
        return max(0.1, base_confidence - 0.03)
    # disagreement
    return max(0.1, base_confidence - 0.07)


def is_volatility_elevated(forecast_vol: float, realized_vol: float) -> bool:
    """Adaptive volatility check: forecast is elevated if it exceeds realized by >30% or is in top regime."""
    if realized_vol <= 0:
        return forecast_vol > 60
    ratio = forecast_vol / realized_vol
    return ratio > 1.3 or forecast_vol > realized_vol + 20


_SQRT_2PI = 2.5066282746310002  # sqrt(2 * pi)


def _parse_tte_years(expiry_str: str) -> float | None:
    """Parse expiry_time string to time-to-expiry in years. Returns None on failure."""
    if not expiry_str:
        return None
    from datetime import datetime, timezone
    try:
        expiry_str = expiry_str.replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(expiry_str)
        now = datetime.now(timezone.utc)
        delta = (exp_dt - now).total_seconds()
        if delta <= 0:
            return None
        return delta / (365.25 * 86400)
    except (ValueError, TypeError):
        return None


def estimate_implied_vol(option_data: dict, time_to_expiry_years: float | None = None) -> float:
    """Estimate annualized implied volatility from ATM option premiums.
    Uses Brenner-Subrahmanyam approximation: IV ≈ premium * sqrt(2π) / (price * sqrt(T)).
    If time_to_expiry_years is not provided, parses expiry_time from option_data.
    Returns IV as a percentage (e.g. 65.0 for 65%)."""
    current = float(option_data.get("current_price", 0))
    if current <= 0:
        return 0.0
    if time_to_expiry_years is None:
        time_to_expiry_years = _parse_tte_years(option_data.get("expiry_time", ""))
    if time_to_expiry_years is None or time_to_expiry_years <= 0:
        time_to_expiry_years = 1 / 365  # fallback: 1 day
    calls = {float(k): float(v) for k, v in (option_data.get("call_options") or {}).items()}
    puts = {float(k): float(v) for k, v in (option_data.get("put_options") or {}).items()}
    if not calls:
        return 0.0
    strikes = sorted(calls.keys())
    atm = min(strikes, key=lambda s: abs(s - current))
    atm_call = calls.get(atm, 0.0)
    atm_put = puts.get(atm, 0.0)
    # Average ATM premiums for better estimate (put-call parity smoothing)
    premiums = [p for p in [atm_call, atm_put] if p > 0]
    if not premiums:
        return 0.0
    avg_premium = sum(premiums) / len(premiums)
    sqrt_t = time_to_expiry_years ** 0.5
    iv = (avg_premium * _SQRT_2PI) / (current * sqrt_t) * 100
    return iv


def compare_volatility(synth_vol: float, implied_vol: float, threshold: float = 0.15) -> VolBias:
    """Compare Synth forecasted vol vs market implied vol.
    Returns long_vol if Synth > IV by threshold, short_vol if Synth < IV, else neutral_vol.
    threshold is relative (0.15 = 15% divergence required)."""
    if implied_vol <= 0 or synth_vol <= 0:
        return "neutral_vol"
    ratio = synth_vol / implied_vol
    if ratio > 1 + threshold:
        return "long_vol"
    if ratio < 1 - threshold:
        return "short_vol"
    return "neutral_vol"


def should_no_trade(fusion_state: FusionState, view: ViewBias, volatility_high: bool, confidence: float = 1.0,
                    vol_bias: VolBias | None = None) -> str | None:
    """Guardrail: no trade when confidence low or signals conflict.
    Returns a reason string if no-trade, or None if trading is OK."""
    if view == "vol":
        # Confidence measures directional forecast tightness — irrelevant for vol trades.
        # The vol edge comes from the IV vs Synth vol divergence, guarded by vol_bias.
        if vol_bias == "neutral_vol":
            return "No vol edge — Synth forecast vol and implied vol are similar. No clear long/short vol trade."
        return None
    if volatility_high:
        return "Volatility elevated — forecast significantly above recent realized."
    if confidence < 0.25:
        return "Confidence too low — wide forecast dispersion."
    if fusion_state == "countermove" and view != "neutral":
        return "Signals conflict — 1h and 24h forecasts disagree with your view."
    if fusion_state == "unclear" and view != "neutral":
        return "Signals unclear — no strong directional conviction from forecasts."
    return None
