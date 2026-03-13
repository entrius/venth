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

from exchange import (
    fetch_all_exchanges,
    strategy_divergence as _strat_div,
    leg_divergences,
    best_market_price,
    compute_divergence,
    compute_edge,
)
from executor import (
    build_execution_plan,
    validate_plan,
    execute_plan,
    get_executor,
)

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


def _line_shopping_side(exchange_quotes, deribit_quotes, aevo_quotes, opts, strike, opt_type):
    """Build data for one side (call or put) of a strike row.
    Returns all original columns: fair, deribit_mid, aevo_mid, execute_venue, execute_price, edge, marker."""
    sk = str(int(strike)) if strike == int(strike) else str(strike)
    fair = float(opts.get(sk, 0))
    if fair <= 0.01:
        return None
    best_deribit = best_market_price(deribit_quotes, strike, opt_type)
    best_aevo = best_market_price(aevo_quotes, strike, opt_type)
    edge = compute_edge(fair, exchange_quotes, strike, opt_type)
    best = best_market_price(exchange_quotes, strike, opt_type)
    return {
        "fair": fair,
        "deribit_mid": best_deribit.mid if best_deribit else None,
        "aevo_mid": best_aevo.mid if best_aevo else None,
        "exec_venue": best.exchange.upper()[:3] if best else None,
        "exec_ask": best.ask if best else None,
        "z_score": edge.z_score if edge else None,
    }


def _fmt_price(val, width=7):
    """Format a price value or --- if None."""
    if val is None:
        return f"{'---':>{width}s}"
    return f"{val:>{width},.0f}"


# Column widths for line shopping table (per side)
_W = {"synth": 7, "der": 6, "aev": 6, "exec": 9, "edge": 6}
# Side = synth + sp + der + sp + aev + 2sp + exec + sp + edge
_SIDE_W = _W["synth"] + 1 + _W["der"] + 1 + _W["aev"] + 2 + _W["exec"] + 1 + _W["edge"]


def _fmt_side(side):
    """Format one side (call or put) of a strike row with all columns."""
    dash = lambda w: f"{'---':>{w}s}"
    if side is None:
        return f"{dash(_W['synth'])} {dash(_W['der'])} {dash(_W['aev'])}  {dash(_W['exec'])} {dash(_W['edge'])}"
    fair_s = _fmt_price(side["fair"], _W["synth"])
    der_s = _fmt_price(side["deribit_mid"], _W["der"])
    aev_s = _fmt_price(side["aevo_mid"], _W["aev"])
    if side["exec_venue"]:
        venue = "DER" if side["exec_venue"].startswith("DER") else "AEV"
        exec_s = f"{venue} {side['exec_ask']:>{_W['exec'] - 4},.0f}"
    else:
        exec_s = dash(_W["exec"])
    if side["z_score"] is not None:
        raw = f"{side['z_score']:+.1f}\u03c3"
        edge_s = f"{raw:>{_W['edge']}s}"
    else:
        edge_s = dash(_W["edge"])
    return f"{fair_s} {der_s} {aev_s}  {exec_s} {edge_s}"


def _print_line_shopping_table(exchange_quotes: list, synth_options: dict, current_price: float):
    """Display Market Line Shopping table with statistical edge detection.
    Call and put shown side-by-side per strike with all columns:
    Synth Fair, Deribit mid, Aevo mid, ★ Execute @ (venue + ask), Edge (z-score)."""
    call_opts = synth_options.get("call_options", {})
    put_opts = synth_options.get("put_options", {})
    all_strikes = sorted(set(float(k) for k in list(call_opts.keys()) + list(put_opts.keys())))
    if not all_strikes:
        return
    # ATM ± 2 strikes
    atm_idx = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - current_price))
    start = max(0, atm_idx - 2)
    end = min(len(all_strikes), atm_idx + 3)
    nearby = all_strikes[start:end]
    deribit_quotes = [q for q in exchange_quotes if q.exchange == "deribit"]
    aevo_quotes = [q for q in exchange_quotes if q.exchange == "aevo"]
    rows = []
    for strike in nearby:
        call_side = _line_shopping_side(exchange_quotes, deribit_quotes, aevo_quotes, call_opts, strike, "call")
        put_side = _line_shopping_side(exchange_quotes, deribit_quotes, aevo_quotes, put_opts, strike, "put")
        if not call_side and not put_side:
            continue
        rows.append((strike, call_side, put_side))
    if not rows:
        return
    print(f"{BAR}")
    print(_section("MARKET LINE SHOPPING"))
    side_hdr = (f"{'Synth':>{_W['synth']}s} {'DER':>{_W['der']}s} {'AEV':>{_W['aev']}s}"
                f"  {'* Exec':>{_W['exec']}s} {'Edge':>{_W['edge']}s}")
    strike_col = 8  # width of strike number
    atm_col = 3     # width of ATM marker
    sep = " \u2502 "
    print(f"{BAR}    {'Strike':>{strike_col}s}{'':{atm_col}s} {'CALL':^{_SIDE_W}s}{sep}{'PUT':^{_SIDE_W}s}")
    print(f"{BAR}    {'':{strike_col}s}{'':{atm_col}s} {side_hdr}{sep}{side_hdr}")
    w = strike_col + atm_col + 1 + _SIDE_W + len(sep) + _SIDE_W
    print(f"{BAR}    {SEP * w}")
    atm_strike = nearby[min(range(len(nearby)), key=lambda i: abs(nearby[i] - current_price))]
    for strike, call_side, put_side in rows:
        atm = " \u25c0 " if strike == atm_strike else "   "
        c_str = _fmt_side(call_side)
        p_str = _fmt_side(put_side)
        print(f"{BAR}    {strike:>{strike_col},.0f}{atm} {c_str}{sep}{p_str}")
    print(f"{BAR}    {SEP * w}")
    print(f"{BAR}    * Exec = best execution venue ask price (DER=Deribit, AEV=Aevo)")


def screen_market_context(symbol: str, current_price: float, confidence: float,
                          fusion_state: str, vol_future: float, vol_realized: float,
                          volatility_high: bool, p1h_last: dict | None, p24h_last: dict | None,
                          no_trade_reason: str | None,
                          implied_vol: float = 0.0, vol_bias: str | None = None,
                          exchange_quotes: list | None = None, synth_options: dict | None = None):
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
    if exchange_quotes and synth_options:
        _print_line_shopping_table(exchange_quotes, synth_options, current_price)
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


def _print_strategy_card(label: str, card: ScoredStrategy, icon: str, current_price: float = 0, asset: str = "",
                         leg_divs: dict | None = None):
    s = card.strategy
    ev_pct = (card.expected_value / current_price * 100) if current_price > 0 else 0.0
    print(f"{BAR}")
    print(f"{BAR}  {icon} {label}: {s.description}")
    print(_section("CONSTRUCTION"))
    if s.legs:
        for i, leg in enumerate(s.legs):
            print(f"{BAR}    {leg.action:<4s} {leg.quantity}x {asset} ${leg.strike:,.0f} {leg.option_type}  @ ${leg.premium:,.2f}")
            if leg_divs and i in leg_divs:
                ld = leg_divs[i]
                z = ld["z_score"]
                venue = ld["best_exchange"].upper()
                price = ld["best_price"]
                action_verb = "Buy" if leg.action == "BUY" else "Sell"
                price_type = "ask" if leg.action == "BUY" else "bid"
                edge_marker = " \u25c6" if abs(z) >= 1.0 else ""
                print(f"{BAR}      \u2605 {action_verb} @ {venue} {price_type} ${price:,.2f} \u2014 edge {z:+.1f}\u03c3{edge_marker}")
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
                     no_trade_reason: str | None, confidence: float = 0.0, current_price: float = 0, asset: str = "",
                     exchange_quotes: list | None = None, synth_options: dict | None = None):
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
        leg_divs = None
        if exchange_quotes and synth_options:
            leg_divs = leg_divergences(card.strategy, exchange_quotes, synth_options)
            if not leg_divs:
                leg_divs = None
        _print_strategy_card(label, card, icon, current_price, asset, leg_divs=leg_divs)
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


def screen_execution(
    card: ScoredStrategy,
    asset: str,
    exchange: str | None,
    exchange_quotes: list,
    synth_options: dict,
    dry_run: bool = False,
    no_prompt: bool = False,
    max_slippage: float = 0.0,
    quantity: int = 0,
    timeout: int = 0,
):
    """Screen 5: build plan, confirm (if live), execute, report. Returns ExecutionReport or None."""
    print(_header("Screen 5: Execution"))
    mode_label = "DRY RUN" if dry_run else "LIVE"
    print(f"{BAR}  Mode: {mode_label}")
    plan = build_execution_plan(
        card, asset, exchange, exchange_quotes, synth_options,
        quantity_override=quantity,
        max_slippage_pct=max_slippage,
        timeout_seconds=timeout,
    )
    plan.dry_run = dry_run
    valid, err = validate_plan(plan)
    if not valid:
        print(f"{BAR}  Pre-flight FAILED: {err}")
        print(_footer())
        return None
    print(f"{BAR}  Exchange: {plan.exchange.upper()}")
    print(f"{BAR}  Asset: {plan.asset}")
    print(f"{BAR}  Strategy: {plan.strategy_description}")
    if max_slippage > 0:
        print(f"{BAR}  Slippage Guard: max {max_slippage:.2f}%")
    if timeout > 0:
        print(f"{BAR}  Order Timeout: {timeout}s")
    if quantity > 0:
        print(f"{BAR}  Quantity Override: {quantity} contracts")
    print(f"{BAR}")
    print(_section("ORDER PLAN"))
    for order in plan.orders:
        print(f"{BAR}    Leg {order.leg_index}: {order.action} {order.quantity}x "
              f"{order.instrument} @ ${order.price:,.2f} ({order.order_type}) [{order.exchange}]")
    print(f"{BAR}")
    print(_kv("Est. Cost", f"${plan.estimated_cost:,.2f}"))
    print(_kv("Est. Max Loss", f"${plan.estimated_max_loss:,.2f}"))
    if not dry_run:
        print(f"{BAR}")
        print(f"{BAR}  WARNING: This will submit LIVE orders.")
        _pause("confirm execution", no_prompt)
    if plan.exchange == "auto" and plan.orders:
        def executor_factory(ex: str):
            return get_executor(ex, exchange_quotes, dry_run)
        report = execute_plan(plan, executor_factory)
    else:
        effective_exchange = plan.orders[0].exchange if plan.orders else "deribit"
        try:
            executor = get_executor(effective_exchange, exchange_quotes, dry_run)
        except ValueError as e:
            print(f"{BAR}  {e}")
            print(_footer())
            return None
        report = execute_plan(plan, executor)
    print(f"{BAR}")
    print(_section("RESULTS"))
    for result in report.results:
        status_icon = "\u2713" if result.status in ("filled", "simulated") else "\u2717"
        err_suffix = f" [{result.error}]" if result.error else ""
        slip_suffix = f" slip:{result.slippage_pct:+.2f}%" if result.slippage_pct else ""
        latency_suffix = f" {result.latency_ms}ms" if result.latency_ms else ""
        print(f"{BAR}    {status_icon} {result.action} {result.instrument}: "
              f"{result.status} @ ${result.fill_price:,.2f} x{result.fill_quantity}"
              f"{slip_suffix}{latency_suffix}{err_suffix}")
    if report.cancelled_orders:
        print(f"{BAR}")
        print(f"{BAR}    Auto-cancelled: {', '.join(report.cancelled_orders)}")
    print(f"{BAR}")
    print(_kv("All Filled", "Yes" if report.all_filled else "No"))
    print(_kv("Net Cost", f"${report.net_cost:,.2f}"))
    if report.started_at and report.finished_at:
        print(_kv("Started", report.started_at))
        print(_kv("Finished", report.finished_at))
    print(f"{BAR}  {report.summary}")
    print(_footer())
    return report


def _card_to_log(card: ScoredStrategy | None, exchange_divergence: float | None = None) -> dict | None:
    """Serialize a strategy card for the decision log with full trade construction."""
    if card is None:
        return None
    s = card.strategy
    result = {
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
    if exchange_divergence is not None:
        z = exchange_divergence
        result["exchange_edge_zscore"] = round(z, 2)
        result["exchange_edge_label"] = (
            "STRONG" if abs(z) >= 2.0 else
            "MODERATE" if abs(z) >= 1.0 else
            "WEAK" if abs(z) >= 0.5 else "NONE"
        )
    return result


def _refuse_execution(no_trade_reason: str | None, force: bool, doing_live: bool) -> bool:
    """True when we should refuse execution: guardrail active, no --force, and live (not dry-run)."""
    return bool(doing_live and no_trade_reason and not force)


def _parse_screen_arg(screen_arg: str) -> set[int]:
    """Parse --screen flag into set of screen numbers (1-4).
    Use 'none' to skip all analysis screens (useful with --execute to show only execution)."""
    val = screen_arg.strip().lower()
    if val == "all":
        return {1, 2, 3, 4}
    if val in ("none", "0"):
        return set()
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
                        help="Screens to show: comma-separated 1,2,3,4, 'all', or 'none' (default: all)")
    parser.add_argument("--no-prompt", action="store_true", dest="no_prompt",
                        help="Skip pause between screens (dump all at once)")
    parser.add_argument("--execute", default=None, choices=["best", "safer", "upside"],
                        help="Execute this strategy on an exchange (default: best)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Simulate execution without placing real orders")
    parser.add_argument("--force", action="store_true",
                        help="Allow execution when guardrail recommends no trade")
    parser.add_argument("--exchange", default=None, choices=["deribit", "aevo"],
                        help="Force exchange (default: auto-route per leg)")
    parser.add_argument("--max-slippage", type=float, default=0.0, dest="max_slippage",
                        help="Max allowed slippage %% (reject fill if exceeded, 0=off)")
    parser.add_argument("--quantity", type=int, default=0,
                        help="Override contract quantity for all legs (0=use strategy default)")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Seconds to wait for order fill before cancelling (0=fire-and-forget)")
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
    implied_vol = estimate_implied_vol(options) if view == "vol" else 0.0
    vol_bias = compare_volatility(vol_future, implied_vol) if view == "vol" else None
    no_trade_reason = should_no_trade(fusion_state, view, volatility_high, confidence, vol_bias=vol_bias)
    candidates = generate_strategies(options, view, risk, asset=symbol, expiry=expiry)
    outcome_prices, cdf_values = _outcome_prices_and_cdf(p24h_last)
    # Load exchange data for crypto assets
    exchange_quotes = None
    divergence_by_strategy = None
    if symbol in ("BTC", "ETH", "SOL"):
        mock_dir = os.path.join(os.path.dirname(__file__), "..", "..", "mock_data", "exchange_options")
        exchange_quotes = fetch_all_exchanges(symbol, mock_dir=mock_dir if not os.environ.get("SYNTH_API_KEY") else None)
        if exchange_quotes and candidates:
            divergence_by_strategy = {}
            for c in candidates:
                div = _strat_div(c, exchange_quotes, options)
                if div is not None:
                    divergence_by_strategy[id(c)] = div
    scored = rank_strategies(candidates, fusion_state, view, outcome_prices, risk, current_price, confidence, vol_ratio, cdf_values=cdf_values, vol_bias=vol_bias, divergence_by_strategy=divergence_by_strategy) if candidates else []
    best, safer, upside = select_three_cards(scored)
    shown_any = 1 in screens
    if shown_any:
        _pause("Market Context", args.no_prompt)
        screen_market_context(symbol, current_price, confidence, fusion_state,
                              vol_future, vol_realized, volatility_high,
                              p1h_last, p24h_last, no_trade_reason,
                              implied_vol=implied_vol, vol_bias=vol_bias,
                              exchange_quotes=exchange_quotes, synth_options=options)
    if 2 in screens:
        if shown_any:
            _pause("Screen 2: Top Plays", args.no_prompt)
        screen_top_plays(best, safer, upside, no_trade_reason, confidence, current_price, asset=symbol,
                         exchange_quotes=exchange_quotes, synth_options=options)
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
    execution_report = None
    if args.execute is not None or args.dry_run:
        if symbol not in ("BTC", "ETH", "SOL"):
            print("\nExecution only supported for crypto assets (BTC, ETH, SOL).", file=sys.stderr)
            return 1
        if not exchange_quotes:
            print("\nCannot execute: exchange data not available (crypto assets only).", file=sys.stderr)
            return 1
        doing_live = args.execute is not None and not args.dry_run
        if _refuse_execution(no_trade_reason, args.force, doing_live):
            print(f"\nGuardrail active: {no_trade_reason}", file=sys.stderr)
            print("Use --force to override and execute anyway.", file=sys.stderr)
            return 1
        card = best if (args.execute in (None, "best")) else (safer if args.execute == "safer" else upside)
        if card is None:
            print("\nCannot execute: no strategy recommendation available.", file=sys.stderr)
            return 1
        if shown_any:
            _pause("Screen 5: Execution", args.no_prompt)
        execution_report = screen_execution(
            card, symbol, args.exchange, exchange_quotes, options,
            dry_run=args.dry_run or not args.execute,
            no_prompt=args.no_prompt,
            max_slippage=args.max_slippage,
            quantity=args.quantity,
            timeout=args.timeout,
        )
        shown_any = True
        if execution_report is None:
            return 1
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
        "candidates_generated": len(candidates),
        "candidates_after_filters": len(scored),
        "exchange_data_available": bool(exchange_quotes),
        "best_match": _card_to_log(best, divergence_by_strategy.get(id(best.strategy)) if divergence_by_strategy and best else None),
        "safer_alt": _card_to_log(safer, divergence_by_strategy.get(id(safer.strategy)) if divergence_by_strategy and safer else None),
        "higher_upside": _card_to_log(upside, divergence_by_strategy.get(id(upside.strategy)) if divergence_by_strategy and upside else None),
    }
    if execution_report is not None:
        testnet = (
            os.environ.get("DERIBIT_TESTNET", "").strip() == "1"
            or os.environ.get("AEVO_TESTNET", "").strip() == "1"
        )
        decision_log["execution"] = {
            "mode": "dry_run" if execution_report.plan.dry_run else "live",
            "exchange": execution_report.plan.exchange,
            "testnet": testnet,
            "all_filled": execution_report.all_filled,
            "net_cost": round(execution_report.net_cost, 2),
            "started_at": execution_report.started_at,
            "finished_at": execution_report.finished_at,
            "max_slippage_pct": execution_report.plan.max_slippage_pct or None,
            "timeout_seconds": execution_report.plan.timeout_seconds or None,
            "quantity_override": execution_report.plan.quantity_override or None,
            "cancelled_orders": execution_report.cancelled_orders or None,
            "fills": [
                {
                    "instrument": r.instrument,
                    "action": r.action,
                    "status": r.status,
                    "fill_price": round(r.fill_price, 2),
                    "fill_quantity": r.fill_quantity,
                    "slippage_pct": round(r.slippage_pct, 4) if r.slippage_pct else None,
                    "latency_ms": r.latency_ms or None,
                    "timestamp": r.timestamp or None,
                }
                for r in execution_report.results
            ],
        }
    print(_header("Decision Log (JSON)"))
    for line in json.dumps(decision_log, indent=2, ensure_ascii=False).split("\n"):
        print(f"{BAR}  {line}")
    print(_footer())
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
