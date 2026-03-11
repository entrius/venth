"""
Options GPS: Turn a trader's view into one clear options decision.
Uses Synth get_prediction_percentiles, get_option_pricing, get_volatility.
"""

import argparse
import json
import sys
import os
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from synth_client import SynthClient

from pipeline import (
    run_forecast_fusion,
    generate_strategies,
    rank_strategies,
    select_three_cards,
    should_no_trade,
    forecast_confidence,
    adjust_confidence_for_divergence,
    is_volatility_elevated,
    estimate_implied_vol,
    compare_volatility,
    _outcome_prices,
    _outcome_prices_and_cdf,
    _outcome_prices_with_probs,
    strategy_pnl_values,
    ScoredStrategy,
    PERCENTILE_KEYS,
    PERCENTILE_LABELS,
)
from exchanges import get_market_lines, MarketLineResult

SUPPORTED_ASSETS = ["BTC", "ETH", "SOL", "XAU", "SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]


def load_synth_data(client: SynthClient, asset: str) -> dict | None:
    """Fetch percentiles 1h/24h, option pricing, volatility.
    Returns a dict with all data needed by the pipeline, or None on failure.
    1h data is optional — if missing, p1h fields are None."""
    try:
        p24h = client.get_prediction_percentiles(asset, horizon="24h")
        options = client.get_option_pricing(asset)
        vol = client.get_volatility(asset, horizon="24h")
    except Exception:
        return None
    percentiles_list_24h = (p24h.get("forecast_future") or {}).get("percentiles") or []
    if not percentiles_list_24h:
        return None
    current = float((options.get("current_price") or p24h.get("current_price") or 0))
    if current <= 0:
        return None
    p1h_last = None
    p1h_full = []
    try:
        p1h = client.get_prediction_percentiles(asset, horizon="1h")
        p1h_full = (p1h.get("forecast_future") or {}).get("percentiles") or []
        if p1h_full:
            p1h_last = p1h_full[-1]
    except Exception:
        pass
    expiry = options.get("expiry_time", "")
    return {
        "p1h_last": p1h_last,
        "p24h_last": percentiles_list_24h[-1],
        "p24h_full": percentiles_list_24h,
        "p1h_full": p1h_full,
        "options": options,
        "vol": vol,
        "current_price": current,
        "expiry": expiry,
    }


W = 72
BAR = "\u2502"
SEP = "\u2500"
DSEP = "\u2550"  # double-line separator for major sections


def _header(title: str, width: int = W) -> str:
    pad = max(0, width - len(title) - 4)
    return f"\n\u250c\u2500\u2500 {title} {SEP * pad}\u2510"


def _footer(width: int = W) -> str:
    return f"\u2514{SEP * width}\u2518"


def _section(label: str) -> str:
    return f"{BAR}  {DSEP * 3} {label} {DSEP * max(0, 50 - len(label))}"


def _kv(key: str, val: str, indent: int = 4) -> str:
    return f"{BAR}{' ' * indent}{key + ':':.<20s} {val}"


def _bar_chart(value: float, max_val: float, width: int = 20) -> str:
    if max_val <= 0:
        return " " * width
    filled = int(abs(value) / max_val * width)
    if value >= 0:
        return "\u2588" * filled + "\u2591" * (width - filled)
    return "\u2591" * filled + " " * (width - filled)


def _confidence_bar(confidence: float, width: int = 25) -> str:
    filled = int(confidence * width)
    empty = width - filled
    if confidence >= 0.6:
        label = "HIGH"
    elif confidence >= 0.35:
        label = "MED"
    else:
        label = "LOW"
    bar_filled = '\u2588' * filled
    bar_empty = '\u2591' * empty
    return f"[{bar_filled}{bar_empty}] {confidence:.0%} {label}"


def _risk_meter(max_loss: float, current_price: float) -> str:
    """Visual risk-as-percentage-of-price meter."""
    if current_price <= 0:
        return ""
    pct = max_loss / current_price * 100
    blocks = min(10, int(pct * 2))  # 0.5% per block
    bar_filled = '\u2588' * blocks
    bar_empty = '\u2591' * (10 - blocks)
    return f"[{bar_filled}{bar_empty}] {pct:.2f}% of price"


def _pause(next_label: str, skip: bool = False):
    """Pause between screens unless --no-prompt is set."""
    if skip:
        return
    try:
        input(f"\n  Press Enter for {next_label}...")
    except EOFError:
        pass


def screen_view_setup(preset_symbol: str | None = None, preset_view: str | None = None,
                      preset_risk: str | None = None) -> tuple[str, str, str]:
    """Screen 1: symbol, view, risk. Returns (symbol, view, risk).
    Preset values from CLI flags skip the corresponding interactive prompt."""
    print(_header("Screen 1: View Setup"))
    if preset_symbol and preset_symbol.upper() in SUPPORTED_ASSETS:
        symbol = preset_symbol.upper()
        print(f"{BAR}  Symbol: {symbol} (from --symbol)")
    else:
        print(f"{BAR}  Assets: {', '.join(SUPPORTED_ASSETS)}")
        symbol = input(f"{BAR}  Enter symbol [BTC]: ").strip().upper() or "BTC"
        if symbol not in SUPPORTED_ASSETS:
            symbol = "BTC"
    valid_views = ("bullish", "bearish", "neutral", "vol")
    if preset_view and preset_view in valid_views:
        view = preset_view
        print(f"{BAR}  View: {view} (from --view)")
    else:
        print(f"{BAR}  Market view: bullish | bearish | neutral | vol")
        view = input(f"{BAR}  Enter view [bullish]: ").strip().lower() or "bullish"
        if view not in valid_views:
            view = "bullish"
    if preset_risk and preset_risk in ("low", "medium", "high"):
        risk = preset_risk
        print(f"{BAR}  Risk: {risk} (from --risk)")
    else:
        print(f"{BAR}  Risk tolerance: low | medium | high")
        risk = input(f"{BAR}  Enter risk [medium]: ").strip().lower() or "medium"
        if risk not in ("low", "medium", "high"):
            risk = "medium"
    strat_hint = {"bullish": "directional long/spread", "bearish": "directional put/spread", "neutral": "range-bound/butterfly", "vol": "straddle/strangle/iron condor"}[view]
    risk_desc = {"low": "defined-risk, higher win-rate", "medium": "balanced risk/reward", "high": "higher convexity, wider stops"}[risk]
    view_icon = {"bullish": "\u25b2", "bearish": "\u25bc", "neutral": "\u25c6", "vol": "\u2248"}[view]
    print(f"{BAR}")
    print(f"{BAR}  {DSEP * 60}")
    print(f"{BAR}    {view_icon} {symbol}  {view.upper()}  {risk.upper()} RISK")
    print(f"{BAR}  {DSEP * 60}")
    print(f"{BAR}    Strategy scan : {strat_hint}")
    print(f"{BAR}    Risk profile  : {risk_desc}")
    print(f"{BAR}    Data sources  : Synth 1h + 24h forecasts, option pricing")
    print(_footer())
    return symbol, view, risk


def screen_market_context(symbol: str, current_price: float, confidence: float,
                          fusion_state: str, vol_future: float, vol_realized: float,
                          volatility_high: bool, p1h_last: dict | None, p24h_last: dict | None,
                          no_trade_reason: str | None,
                          implied_vol: float = 0.0, vol_bias: str | None = None,
                          market_lines: MarketLineResult | None = None):
    """Screen 1b: Market context — shows current conditions before recommendations."""
    print(_header(f"Market Context: {symbol}"))
    print(_kv("Price", f"${current_price:,.2f}"))
    print(_kv("Confidence", _confidence_bar(confidence)))
    fusion_label = fusion_state.replace('_', ' ').title()
    data_note = "1h + 24h" if p1h_last else "24h only"
    print(_kv("Forecast fusion", f"{fusion_label} ({data_note})"))
    vol_label = "ELEVATED" if volatility_high else "Normal"
    vol_ratio_str = f"{vol_future / vol_realized:.2f}x" if vol_realized > 0 else "N/A"
    print(_kv("Volatility", f"fwd {vol_future:.1f}% / realized {vol_realized:.1f}% (ratio {vol_ratio_str}) [{vol_label}]"))
    if implied_vol > 0:
        iv_ratio = vol_future / implied_vol
        bias_label = (vol_bias or "").replace("_", " ").upper()
        print(_kv("Implied Vol", f"{implied_vol:.1f}% (from ATM options)"))
        print(_kv("Synth vs IV", f"{iv_ratio:.2f}x \u2192 {bias_label}"))
    if market_lines and market_lines.summaries:
        print(f"{BAR}")
        print(_section("MARKET LINE SHOPPING"))
        consensus_label = market_lines.consensus.replace('_', ' ').upper()
        print(f"{BAR}    Consensus : {consensus_label} (avg |\u0394| {market_lines.avg_divergence:.1f}pp)")
        for s in market_lines.summaries:
            call_sign = '+' if s.rich_calls >= 0 else ''
            put_sign = '+' if s.rich_puts >= 0 else ''
            print(f"{BAR}    {s.exchange:<10s}: avg |\u0394| {s.avg_abs_div:.1f}pp, max {s.max_abs_div:.1f}pp; "
                  f"calls {call_sign}{s.rich_calls:.1f}pp, puts {put_sign}{s.rich_puts:.1f}pp vs Synth")
    print(f"{BAR}")
    if p1h_last:
        p05 = float(p1h_last.get("0.05", 0))
        p50 = float(p1h_last.get("0.5", 0))
        p95 = float(p1h_last.get("0.95", 0))
        if p05 and p50 and p95:
            lo_pct = (p05 - current_price) / current_price * 100
            hi_pct = (p95 - current_price) / current_price * 100
            print(f"{BAR}    1h range  : ${p05:>10,.0f} ({lo_pct:+.1f}%)  \u2500  ${p50:>,.0f}  \u2500  ${p95:>,.0f} ({hi_pct:+.1f}%)")
    if p24h_last:
        p05 = float(p24h_last.get("0.05", 0))
        p50 = float(p24h_last.get("0.5", 0))
        p95 = float(p24h_last.get("0.95", 0))
        if p05 and p50 and p95:
            lo_pct = (p05 - current_price) / current_price * 100
            hi_pct = (p95 - current_price) / current_price * 100
            print(f"{BAR}    24h range : ${p05:>10,.0f} ({lo_pct:+.1f}%)  \u2500  ${p50:>,.0f}  \u2500  ${p95:>,.0f} ({hi_pct:+.1f}%)")
    if no_trade_reason:
        print(f"{BAR}")
        print(f"{BAR}    \u26a0  GUARDRAIL: {no_trade_reason}")
    print(_footer())


def _comparison_table(cards: list[tuple[str, ScoredStrategy | None]], current_price: float) -> list[str]:
    """Side-by-side comparison table of key metrics for all strategies."""
    active = [(lbl, c) for lbl, c in cards if c is not None]
    if not active:
        return []
    col_w = 22
    lines = []
    # Header row
    hdr = f"{'':20s}"
    for lbl, _ in active:
        hdr += f"  {lbl:>{col_w}s}"
    lines.append(f"{BAR}    {hdr}")
    sep_row = f"{'':20s}" + "".join(f"  {SEP * col_w}" for _ in active)
    lines.append(f"{BAR}    {sep_row}")
    # Strategy name
    row = f"{'Strategy':20s}"
    for _, c in active:
        row += f"  {c.strategy.description:>{col_w}s}"
    lines.append(f"{BAR}    {row}")
    # PoP
    row = f"{'PoP':20s}"
    for _, c in active:
        row += f"  {c.probability_of_profit:>{col_w}.0%}"
    lines.append(f"{BAR}    {row}")
    # EV
    row = f"{'Expected Value':20s}"
    for _, c in active:
        ev_pct = (c.expected_value / current_price * 100) if current_price > 0 else 0.0
        row += f"  {f'${c.expected_value:,.0f} ({ev_pct:+.1f}%)':>{col_w}s}"
    lines.append(f"{BAR}    {row}")
    # Max Loss
    row = f"{'Max Loss':20s}"
    for _, c in active:
        row += f"  {f'${c.strategy.max_loss:,.0f}':>{col_w}s}"
    lines.append(f"{BAR}    {row}")
    # Cost
    row = f"{'Net Cost':20s}"
    for _, c in active:
        cost = c.strategy.cost
        lbl = f"${abs(cost):,.0f} {'credit' if cost < 0 else 'debit'}"
        row += f"  {lbl:>{col_w}s}"
    lines.append(f"{BAR}    {row}")
    # Risk type
    row = f"{'Risk Type':20s}"
    for _, c in active:
        row += f"  {c.loss_profile:>{col_w}s}"
    lines.append(f"{BAR}    {row}")
    return lines


def _print_strategy_card(label: str, card: ScoredStrategy, icon: str, current_price: float = 0, asset: str = ""):
    s = card.strategy
    ev_pct = (card.expected_value / current_price * 100) if current_price > 0 else 0.0
    print(f"{BAR}")
    print(f"{BAR}  {icon} {label}: {s.description}")
    print(_section("CONSTRUCTION"))
    if s.legs:
        for leg in s.legs:
            print(f"{BAR}    {leg.action:<4s} {leg.quantity}x {asset} ${leg.strike:,.0f} {leg.option_type}  @ ${leg.premium:,.2f}")
        net_label = "Net Credit" if s.cost < 0 else "Net Debit"
        print(f"{BAR}    {net_label}: ${abs(s.cost):,.2f}  |  Expiry: {s.expiry or 'N/A'}")
    print(_section("METRICS"))
    print(f"{BAR}    PoP        : {card.probability_of_profit:.0%}")
    print(f"{BAR}    EV         : ${card.expected_value:,.0f} ({ev_pct:+.2f}%)")
    if s.max_profit > 0:
        print(f"{BAR}    Max Profit : ${s.max_profit:,.0f} — {s.max_profit_condition}")
    elif s.max_profit_condition:
        print(f"{BAR}    Profit     : {s.max_profit_condition}")
    print(f"{BAR}    Max Loss   : ${s.max_loss:,.0f} ({card.loss_profile})")
    print(f"{BAR}    Risk Meter : {_risk_meter(s.max_loss, current_price)}")
    print(f"{BAR}    Tail Risk  : ${card.tail_risk:,.0f} (worst 20% avg loss)")
    print(_section("PLAN"))
    print(f"{BAR}    Exit       : {card.invalidation_trigger}")
    print(f"{BAR}    Adjust     : {card.reroute_rule}")
    print(f"{BAR}    Review     : {card.review_again_at}")


def screen_top_plays(best: ScoredStrategy | None, safer: ScoredStrategy | None, upside: ScoredStrategy | None,
                     no_trade_reason: str | None, confidence: float = 0.0, current_price: float = 0, asset: str = ""):
    """Screen 2: Comparison table + detailed strategy cards."""
    print(_header("Screen 2: Top Plays"))
    if no_trade_reason:
        print(f"{BAR}  \u26a0  NO TRADE RECOMMENDED")
        print(f"{BAR}  Reason: {no_trade_reason}")
        print(f"{BAR}  Confidence: {_confidence_bar(confidence)}")
        print(f"{BAR}")
        print(f"{BAR}  The following are tentative alternatives (use with extreme caution):")
    print(f"{BAR}")
    # Quick comparison table
    if no_trade_reason:
        table_cards = [("~Best", best), ("~Safer", safer), ("~Upside", upside)]
    else:
        table_cards = [("Best", best), ("Safer", safer), ("Upside", upside)]
    for line in _comparison_table(table_cards, current_price):
        print(line)
    print(f"{BAR}")
    # Detailed cards
    if no_trade_reason:
        cards = [("Tentative Best", best, "~"), ("Tentative Safer", safer, "~"), ("Tentative Upside", upside, "~")]
    else:
        cards = [("Best Match", best, "\u2605"), ("Safer Alternative", safer, "\u2606"), ("Higher Upside", upside, "\u25b2")]
    for label, card, icon in cards:
        if card is None:
            continue
        _print_strategy_card(label, card, icon, current_price, asset)
    print(_footer())


def _payoff_ascii(prices: list[float], pnl: list[float], prob_labels: list[str] | None = None) -> list[str]:
    if not prices or not pnl:
        return []
    max_abs = max(abs(x) for x in pnl) or 1.0
    lines = []
    for i, price in enumerate(prices):
        v = pnl[i]
        size = int((abs(v) / max_abs) * 15)
        if v >= 0:
            bar = "\u2588" * size
            sign = "+"
        else:
            bar = "\u2591" * size
            sign = "-"
        plabel = f"({prob_labels[i]:>3s})" if prob_labels and i < len(prob_labels) else "     "
        lines.append(f"{BAR}    ${price:>10,.0f} {plabel} {bar:<16s} {sign}${abs(v):,.0f}")
    return lines


def _distribution_ascii(percentiles_last: dict, current_price: float) -> list[str]:
    """CDF visualization of price distribution."""
    if current_price <= 0:
        return []
    lines = []
    width = 20
    for k in PERCENTILE_KEYS:
        price = percentiles_last.get(k)
        if price is None:
            continue
        price = float(price)
        pct_val = float(k)
        filled = int(pct_val * width)
        empty = width - filled
        label = PERCENTILE_LABELS.get(k, k)
        marker = "  \u2190 median" if k == "0.5" else ""
        pct_from_cur = (price - current_price) / current_price * 100
        bar_filled = '\u2593' * filled
        bar_empty = '\u2591' * empty
        lines.append(f"{BAR}    ${price:>10,.0f} ({pct_from_cur:+5.1f}%)  {bar_filled}{bar_empty}  {label:>3s}{marker}")
    return lines


def _forecast_path(percentile_list: list[dict], label: str, horizon_minutes: int = 60, n_points: int = 5) -> list[str]:
    """Compact time-series table from full percentile list.
    horizon_minutes converts index positions to real time labels (60 for 1h, 1440 for 24h)."""
    if not percentile_list or len(percentile_list) < 2:
        return []
    total = len(percentile_list)
    indices = [0] + [int(total * i / (n_points - 1)) for i in range(1, n_points - 1)] + [total - 1]
    indices = sorted(set(min(idx, total - 1) for idx in indices))
    use_hours = horizon_minutes >= 120
    lines = [
        f"{BAR}  {label} Forecast Path:",
        f"{BAR}    {'':>6s}   {'5th pctl':>10s}  {'median':>10s}  {'95th pctl':>10s}",
    ]
    for idx in indices:
        step = percentile_list[idx]
        p05 = float(step.get("0.05", 0))
        p50 = float(step.get("0.5", 0))
        p95 = float(step.get("0.95", 0))
        elapsed_frac = idx / max(1, total - 1)
        elapsed = elapsed_frac * horizon_minutes
        t_label = f"{elapsed / 60:.0f}h" if use_hours else f"{elapsed:.0f}m"
        lines.append(f"{BAR}    {t_label:>6s}   ${p05:>10,.0f}  ${p50:>10,.0f}  ${p95:>10,.0f}")
    return lines


def screen_why_this_works(best: ScoredStrategy | None, fusion_state: str, current_price: float,
                          no_trade_reason: str | None, outcome_prices: list[float],
                          p24h_last: dict | None = None, p1h_last: dict | None = None,
                          p1h_full: list | None = None, p24h_full: list | None = None,
                          view: str = "", risk: str = "", asset: str = ""):
    """Screen 3: Why best match works — distribution, forecast paths, payoff, verdict."""
    print(_header("Screen 3: Why This Works"))
    if best is None:
        print(f"{BAR}  No recommendation available; see guardrails.")
        print(_footer())
        return
    s = best.strategy
    if no_trade_reason:
        print(f"{BAR}  \u26a0  Guardrail active: {no_trade_reason}")
        print(f"{BAR}     Tentative analysis only \u2014 not a trade signal.")
        print(f"{BAR}")
    # Distribution
    if p24h_last:
        print(_section("24h PRICE DISTRIBUTION"))
        for line in _distribution_ascii(p24h_last, current_price):
            print(line)
        print(f"{BAR}")
    # Forecast paths
    if p1h_full:
        print(_section("1h FORECAST PATH"))
        for line in _forecast_path(p1h_full, "1h", horizon_minutes=60):
            if "Forecast Path:" not in line:
                print(line)
        print(f"{BAR}")
    if p24h_full:
        print(_section("24h FORECAST PATH"))
        for line in _forecast_path(p24h_full, "24h", horizon_minutes=1440):
            if "Forecast Path:" not in line:
                print(line)
        print(f"{BAR}")
    # Payoff at forecast levels
    prob_with_labels = _outcome_prices_with_probs(p24h_last) if p24h_last else []
    prob_labels = [lbl for lbl, _ in prob_with_labels] if prob_with_labels else None
    pnl_curve = strategy_pnl_values(s, outcome_prices)
    print(_section(f"PAYOFF: {s.description}"))
    for line in _payoff_ascii(outcome_prices, pnl_curve, prob_labels):
        print(line)
    # Verdict
    print(f"{BAR}")
    print(_section("VERDICT"))
    st = s.strategy_type
    if st == "long_call":
        be = s.strikes[0] + s.cost
        be_dir = f"rise above ${be:,.0f} (breakeven)"
    elif st == "long_put":
        be = s.strikes[0] - s.cost
        be_dir = f"fall below ${be:,.0f} (breakeven)"
    elif st in ("call_debit_spread", "bull_put_credit_spread"):
        be = s.strikes[0] + s.cost if st == "call_debit_spread" else s.strikes[1] + s.cost
        be_dir = f"stay above ${be:,.0f} (breakeven)"
    elif st in ("put_debit_spread", "bear_call_credit_spread"):
        be = s.strikes[-1] - s.cost if st == "put_debit_spread" else s.strikes[0] - abs(s.cost)
        be_dir = f"stay below ${be:,.0f} (breakeven)"
    elif st == "iron_condor":
        be_dir = f"stay between ${s.strikes[0]:,.0f}-${s.strikes[1]:,.0f}"
    elif st == "long_call_butterfly":
        be_dir = f"pin near ${s.strikes[1]:,.0f} (center strike)"
    elif st == "long_straddle":
        be_up = s.strikes[0] + s.cost
        be_dn = s.strikes[0] - s.cost
        be_dir = f"move beyond ${be_dn:,.0f} or ${be_up:,.0f} (breakevens)"
    elif st == "long_strangle":
        be_dn = s.strikes[0] - s.cost
        be_up = s.strikes[1] + s.cost
        be_dir = f"move beyond ${be_dn:,.0f} or ${be_up:,.0f} (breakevens)"
    elif st == "short_straddle":
        credit = -s.cost
        be_up = s.strikes[0] + credit
        be_dn = s.strikes[0] - credit
        be_dir = f"stay between ${be_dn:,.0f}-${be_up:,.0f} (profit zone)"
    elif st == "short_strangle":
        credit = -s.cost
        be_dn = s.strikes[0] - credit
        be_up = s.strikes[1] + credit
        be_dir = f"stay between ${be_dn:,.0f}-${be_up:,.0f} (breakevens)"
    else:
        be_dir = "move in your favor"
    median_24h = float(p24h_last.get("0.5", 0)) if p24h_last else 0
    median_dir = "above" if median_24h > current_price else "below" if median_24h < current_price else "at"
    print(f"{BAR}    Thesis   : {asset} {view} \u2014 needs to {be_dir}")
    if median_24h > 0:
        med_pct = (median_24h - current_price) / current_price * 100
        print(f"{BAR}    Forecast : Synth 24h median ${median_24h:,.0f} ({med_pct:+.1f}%, {median_dir} current)")
    print(f"{BAR}    PoP      : {best.probability_of_profit:.0%}")
    print(f"{BAR}    Risk     : {risk} \u2014 max loss ${s.max_loss:,.0f}")
    if no_trade_reason:
        print(f"{BAR}")
        print(f"{BAR}    \u26a0  No trade recommended despite analysis. Signals are insufficient.")
    print(_footer())


def screen_if_wrong(best: ScoredStrategy | None, no_trade_reason: str | None,
                    outcome_prices: list[float] | None = None,
                    current_price: float = 0, asset: str = ""):
    """Screen 4: If wrong — exit, convert/roll, reassessment rules."""
    print(_header("Screen 4: If Wrong"))
    if best is None:
        print(f"{BAR}  No recommendation available.")
        print(_footer())
        return
    s = best.strategy
    if no_trade_reason:
        print(f"{BAR}  \u26a0  Tentative \u2014 no active trade recommended")
        print(f"{BAR}")
    # Position summary
    print(_section(f"POSITION: {s.description}"))
    if s.legs:
        for leg in s.legs:
            print(f"{BAR}    {leg.action} {leg.quantity}x {asset} ${leg.strike:,.0f} {leg.option_type} @ ${leg.premium:,.2f}")
    print(f"{BAR}    Max Loss   : ${s.max_loss:,.0f} ({best.loss_profile})")
    print(f"{BAR}    Risk Meter : {_risk_meter(s.max_loss, current_price)}")
    # Scenarios
    if outcome_prices and current_price > 0:
        pnl_values = strategy_pnl_values(s, outcome_prices)
        best_pnl = max(pnl_values) if pnl_values else 0
        worst_pnl = min(pnl_values) if pnl_values else 0
        best_price = outcome_prices[pnl_values.index(best_pnl)] if pnl_values else 0
        worst_price = outcome_prices[pnl_values.index(worst_pnl)] if pnl_values else 0
        print(_section("SCENARIOS"))
        print(f"{BAR}    Best case  : {asset} @ ${best_price:,.0f}  \u2192  P/L {'+' if best_pnl >= 0 else ''}{best_pnl:,.0f}")
        print(f"{BAR}    Worst case : {asset} @ ${worst_price:,.0f}  \u2192  P/L {'+' if worst_pnl >= 0 else ''}{worst_pnl:,.0f}")
    # Exit rules
    print(_section("EXIT RULES"))
    print(f"{BAR}    {best.invalidation_trigger}")
    # Adjustment
    print(_section("ADJUSTMENT PLAYBOOK"))
    print(f"{BAR}    {best.reroute_rule}")
    # Key levels + review
    print(_section("KEY LEVELS & REVIEW"))
    print(f"{BAR}    {best.review_again_at}")
    print(f"{BAR}    Expiry : {s.expiry or 'N/A'}")
    print(f"{BAR}    Review : at 50% time-to-expiry and on any >1% {asset} move")
    print(_footer())


def _card_to_log(card: ScoredStrategy | None) -> dict | None:
    """Serialize a strategy card for the decision log with full trade construction."""
    if card is None:
        return None
    s = card.strategy
    return {
        "description": s.description,
        "type": s.strategy_type,
        "legs": [
            {"action": leg.action, "qty": leg.quantity, "option_type": leg.option_type,
             "strike": leg.strike, "premium": round(leg.premium, 2)}
            for leg in s.legs
        ],
        "net_cost": round(s.cost, 2),
        "max_loss": round(s.max_loss, 2),
        "expiry": s.expiry or None,
        "max_profit": round(s.max_profit, 2) if s.max_profit > 0 else None,
        "max_profit_condition": s.max_profit_condition or None,
        "pop": round(card.probability_of_profit, 3),
        "ev": round(card.expected_value, 2),
        "tail_risk": round(card.tail_risk, 2),
        "loss_profile": card.loss_profile,
    }


def _parse_screen_arg(screen_arg: str) -> set[int]:
    """Parse --screen flag into set of screen numbers (1-4)."""
    if screen_arg.strip().lower() == "all":
        return {1, 2, 3, 4}
    screens: set[int] = set()
    for part in screen_arg.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 4:
            screens.add(int(part))
    return screens or {1, 2, 3, 4}


def main():
    parser = argparse.ArgumentParser(
        description="Options GPS: turn a market view into one clear options decision",
    )
    parser.add_argument("--symbol", default=None, help="Asset symbol (BTC, ETH, SOL, ...)")
    parser.add_argument("--view", default=None, choices=["bullish", "bearish", "neutral", "vol"])
    parser.add_argument("--risk", default=None, choices=["low", "medium", "high"])
    parser.add_argument("--screen", default="all",
                        help="Screens to show: comma-separated 1,2,3,4 or 'all' (default: all)")
    parser.add_argument("--no-prompt", action="store_true", dest="no_prompt",
                        help="Skip pause between screens (dump all at once)")
    args = parser.parse_args()
    screens = _parse_screen_arg(args.screen)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = SynthClient()
    if 1 in screens:
        symbol, view, risk = screen_view_setup(args.symbol, args.view, args.risk)
    else:
        symbol = (args.symbol or "BTC").upper()
        if symbol not in SUPPORTED_ASSETS:
            symbol = "BTC"
        view = args.view or "bullish"
        risk = args.risk or "medium"
    data = load_synth_data(client, symbol)
    if data is None:
        print("Could not load Synth data for", symbol)
        return 1
    p1h_last = data["p1h_last"]
    p24h_last = data["p24h_last"]
    options = data["options"]
    vol = data["vol"]
    current_price = data["current_price"]
    expiry = data["expiry"]
    p1h_full = data["p1h_full"]
    p24h_full = data["p24h_full"]
    p1h_available = p1h_last is not None
    fusion_state = run_forecast_fusion(p1h_last, p24h_last, current_price)
    vol_future = (vol.get("forecast_future") or {}).get("average_volatility") or 0
    vol_realized = (vol.get("realized") or {}).get("average_volatility") or 0
    volatility_high = is_volatility_elevated(vol_future, vol_realized)
    vol_ratio = (vol_future / vol_realized) if vol_realized > 0 else 1.0
    confidence = forecast_confidence(p24h_last, current_price)
    market_lines = get_market_lines(options, asset=symbol)
    confidence = adjust_confidence_for_divergence(confidence, market_lines.avg_divergence, market_lines.consensus)
    implied_vol = estimate_implied_vol(options) if view == "vol" else 0.0
    vol_bias = compare_volatility(vol_future, implied_vol) if view == "vol" else None
    no_trade_reason = should_no_trade(fusion_state, view, volatility_high, confidence, vol_bias=vol_bias)
    candidates = generate_strategies(options, view, risk, asset=symbol, expiry=expiry)
    outcome_prices, cdf_values = _outcome_prices_and_cdf(p24h_last)
    scored = rank_strategies(candidates, fusion_state, view, outcome_prices, risk, current_price, confidence, vol_ratio, cdf_values=cdf_values, vol_bias=vol_bias) if candidates else []
    best, safer, upside = select_three_cards(scored)
    shown_any = 1 in screens
    if shown_any:
        _pause("Market Context", args.no_prompt)
        screen_market_context(symbol, current_price, confidence, fusion_state,
                              vol_future, vol_realized, volatility_high,
                              p1h_last, p24h_last, no_trade_reason,
                              implied_vol=implied_vol, vol_bias=vol_bias,
                              market_lines=market_lines)
    if 2 in screens:
        if shown_any:
            _pause("Screen 2: Top Plays", args.no_prompt)
        screen_top_plays(best, safer, upside, no_trade_reason, confidence, current_price, asset=symbol)
        shown_any = True
    if 3 in screens:
        if shown_any:
            _pause("Screen 3: Why This Works", args.no_prompt)
        screen_why_this_works(best, fusion_state, current_price, no_trade_reason, outcome_prices,
                              p24h_last=p24h_last, p1h_last=p1h_last,
                              p1h_full=p1h_full, p24h_full=p24h_full,
                              view=view, risk=risk, asset=symbol)
        shown_any = True
    if 4 in screens:
        if shown_any:
            _pause("Screen 4: If Wrong", args.no_prompt)
        screen_if_wrong(best, no_trade_reason, outcome_prices, current_price, asset=symbol)
        shown_any = True
    if shown_any:
        _pause("Decision Log", args.no_prompt)
    decision_log = {
        "inputs": {"symbol": symbol, "view": view, "risk": risk},
        "fusion_state": fusion_state,
        "confidence": round(confidence, 3),
        "volatility": {
            "forecast": round(vol_future, 2),
            "realized": round(vol_realized, 2),
            "elevated": volatility_high,
            "implied_vol": round(implied_vol, 2) if implied_vol else None,
            "vol_bias": vol_bias,
        },
        "1h_data_available": p1h_available,
        "no_trade": no_trade_reason is not None,
        "no_trade_reason": no_trade_reason,
        "market_lines": {
            "consensus": market_lines.consensus,
            "avg_divergence": market_lines.avg_divergence,
            "max_divergence": market_lines.max_divergence,
            "exchanges": [
                {"exchange": s.exchange, "avg_abs_div": s.avg_abs_div,
                 "max_abs_div": s.max_abs_div, "rich_calls": s.rich_calls,
                 "rich_puts": s.rich_puts}
                for s in market_lines.summaries
            ],
        },
        "candidates_generated": len(candidates),
        "candidates_after_filters": len(scored),
        "best_match": _card_to_log(best),
        "safer_alt": _card_to_log(safer),
        "higher_upside": _card_to_log(upside),
    }
    print(_header("Decision Log (JSON)"))
    for line in json.dumps(decision_log, indent=2, ensure_ascii=False).split("\n"):
        print(f"{BAR}  {line}")
    print(_footer())
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
