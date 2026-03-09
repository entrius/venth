import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

"""
Tide Chart - Interactive Equity & Crypto Forecast Dashboard.

Flask-based dashboard with probability cones, target price calculator,
variable time horizons (1h/24h), and live auto-refresh.
"""

import json
import webbrowser
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, Response
from synth_client import SynthClient
from chart import (
    EQUITIES,
    CRYPTO_ASSETS,
    ALL_ASSETS,
    fetch_all_data,
    calculate_metrics,
    add_relative_to_benchmark,
    rank_equities,
    get_normalized_series,
    calculate_target_probability,
    get_assets_for_horizon,
)

# Per-asset-group gTrade leverage constraints (from gTrade docs)
ASSET_GROUPS = {
    "crypto":      {"min_leverage": 1.1, "max_leverage": 500, "assets": ["BTC", "ETH", "SOL"]},
    "stocks":      {"min_leverage": 1.1, "max_leverage": 50,  "assets": ["NVDA", "TSLA", "AAPL", "GOOGL"]},
    "indices":     {"min_leverage": 1.1, "max_leverage": 50,  "assets": ["SPY"]},
    "commodities": {"min_leverage": 2,   "max_leverage": 250, "assets": ["XAU"]},
}

# Build asset -> group lookup
ASSET_TO_GROUP = {}
for _group_name, _group_info in ASSET_GROUPS.items():
    for _a in _group_info["assets"]:
        ASSET_TO_GROUP[_a] = _group_name

TRADEABLE_ASSETS = set(ALL_ASSETS)

# gTrade (Gains Network) configuration — server-side source of truth
GTRADE_CONFIG = {
    "chain_id": 42161,
    "diamond_address": "0xFF162c694eAA571f685030649814282eA457f169",
    "usdc_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "usdc_decimals": 6,
    "collateral_index": 3,
    "pair_indices": {
        "BTC": 0, "ETH": 1, "SOL": 33,
        "SPY": 86, "NVDA": 65, "TSLA": 85, "AAPL": 58, "GOOGL": 82,
        "XAU": 90,
    },
    "asset_groups": {
        group_name: {"min_leverage": info["min_leverage"], "max_leverage": info["max_leverage"], "assets": info["assets"]}
        for group_name, info in ASSET_GROUPS.items()
    },
    "min_position_size_usd": 1500,
    "gtrade_app_url": "https://gains.trade/trading",
}


def get_asset_leverage_limits(asset: str) -> tuple[float, float]:
    """Return (min_leverage, max_leverage) for an asset based on its group."""
    group = ASSET_TO_GROUP.get(asset)
    if group and group in ASSET_GROUPS:
        g = ASSET_GROUPS[group]
        return g["min_leverage"], g["max_leverage"]
    return 1.1, 50  # safe default


def validate_trade_params(params: dict) -> list[str]:
    """Validate trade parameters server-side with per-group leverage limits."""
    errors = []
    asset = params.get("asset", "")
    collateral = params.get("collateral", 0)
    leverage = params.get("leverage", 0)

    if asset not in GTRADE_CONFIG["pair_indices"]:
        errors.append(f"Asset '{asset}' is not supported for trading.")
    if not isinstance(collateral, (int, float)) or collateral <= 0:
        errors.append("Collateral must be a positive number.")
    if not isinstance(leverage, (int, float)):
        errors.append("Leverage must be a number.")
    elif asset in ASSET_TO_GROUP:
        min_lev, max_lev = get_asset_leverage_limits(asset)
        if leverage < min_lev or leverage > max_lev:
            group = ASSET_TO_GROUP[asset]
            errors.append(f"Leverage must be between {min_lev}x and {max_lev}x for {group} assets.")
    if isinstance(collateral, (int, float)) and isinstance(leverage, (int, float)) and collateral > 0 and leverage > 0:
        position_size = collateral * leverage
        if position_size < GTRADE_CONFIG["min_position_size_usd"]:
            errors.append(f"Position size ${position_size:,.2f} is below minimum ${GTRADE_CONFIG['min_position_size_usd']:,}.")
    slippage = params.get("slippage", 1.0)
    if isinstance(slippage, (int, float)) and (slippage < 0.1 or slippage > 5.0):
        errors.append("Slippage must be between 0.1% and 5.0%.")
    return errors


ASSET_COLORS = {
    "SPY": {"primary": "#e8d44d", "rgb": "232,212,77"},
    "NVDA": {"primary": "#3db8e8", "rgb": "61,184,232"},
    "TSLA": {"primary": "#e85a6e", "rgb": "232,90,110"},
    "AAPL": {"primary": "#9b6de8", "rgb": "155,109,232"},
    "GOOGL": {"primary": "#4dc87a", "rgb": "77,200,122"},
    "BTC": {"primary": "#f7931a", "rgb": "247,147,26"},
    "ETH": {"primary": "#627eea", "rgb": "98,126,234"},
    "SOL": {"primary": "#00ffa3", "rgb": "0,255,163"},
    "XAU": {"primary": "#ffd700", "rgb": "255,215,0"},
}

ASSET_LABELS = {
    "SPY": "S&P 500",
    "NVDA": "NVIDIA",
    "TSLA": "Tesla",
    "AAPL": "Apple",
    "GOOGL": "Alphabet",
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XAU": "Gold",
}

# Backwards compat aliases
EQUITY_COLORS = ASSET_COLORS
EQUITY_LABELS = ASSET_LABELS


def build_traces(normalized_series: dict, metrics: dict, time_points: list[str]) -> list[dict]:
    """Build Plotly trace dicts for probability cones."""
    traces = []
    for asset in normalized_series:
        series = normalized_series[asset]
        color = ASSET_COLORS[asset]
        label = ASSET_LABELS[asset]

        upper = [s.get("0.95", 0) for s in series]
        lower = [s.get("0.05", 0) for s in series]
        median = [s.get("0.5", 0) for s in series]

        traces.append({
            "x": time_points,
            "y": upper,
            "type": "scatter",
            "mode": "lines",
            "line": {"width": 0},
            "showlegend": False,
            "legendgroup": asset,
            "name": f"{asset} 95th",
            "hoverinfo": "skip",
        })

        traces.append({
            "x": time_points,
            "y": lower,
            "type": "scatter",
            "mode": "lines",
            "line": {"width": 0},
            "fill": "tonexty",
            "fillcolor": f"rgba({color['rgb']},0.12)",
            "showlegend": False,
            "legendgroup": asset,
            "name": f"{asset} 5th",
            "hoverinfo": "skip",
        })

        current_price = metrics[asset]["current_price"]
        hover_text = []
        for v in median:
            nom = v * current_price / 100
            sign_pct = "+" if v >= 0 else ""
            sign_nom = "+" if nom >= 0 else "-"
            hover_text.append(f"{sign_pct}{v:.2f}% ({sign_nom}${abs(nom):,.2f})")
        traces.append({
            "x": time_points,
            "y": median,
            "customdata": hover_text,
            "type": "scatter",
            "mode": "lines",
            "line": {"color": color["primary"], "width": 2},
            "legendgroup": asset,
            "name": f"{label} ({asset})",
            "hovertemplate": (
                f"<b>{label}</b><br>"
                "%{x|%I:%M %p}<br>"
                "Median: %{customdata}"
                "<extra></extra>"
            ),
        })
    return traces


def build_table_rows(ranked: list, benchmark: str) -> str:
    """Build HTML table rows for ranked assets."""
    rows = ""
    for rank_idx, (asset, m) in enumerate(ranked, 1):
        color = ASSET_COLORS[asset]["primary"]
        label = ASSET_LABELS[asset]

        def fmt_val(val, nominal=None, suffix="%"):
            sign = "+" if val > 0 else ""
            css_class = "positive" if val > 0 else "negative" if val < 0 else "neutral"
            pct_str = f"{sign}{val:.3f}{suffix}"
            if nominal is not None:
                nom_sign = "+" if nominal > 0 else "-" if nominal < 0 else ""
                nom_str = f"{nom_sign}${abs(nominal):,.2f}"
                return f'<span class="{css_class}">{pct_str} <span class="nominal">({nom_str})</span></span>'
            return f'<span class="{css_class}">{pct_str}</span>'

        rel_median = "-" if asset == benchmark else fmt_val(m["relative_median"])
        rel_skew = "-" if asset == benchmark else fmt_val(m["relative_skew"])

        trade_cell = (
            f'<td><button class="trade-cell-btn" data-asset="{asset}">Trade</button></td>'
            if asset in TRADEABLE_ASSETS
            else '<td><span class="trade-cell-na">--</span></td>'
        )

        rows += f"""
        <tr data-median="{m['median_move']}" data-vol="{m['volatility']}" data-skew="{m['skew']}" data-range="{m['range_pct']}" data-bounds="{m['price_low']}" data-rel-median="{m.get('relative_median', 0)}" data-rel-skew="{m.get('relative_skew', 0)}">
            <td class="rank-cell">{rank_idx}</td>
            <td class="asset-cell">
                <span class="asset-dot" style="background:{color}"></span>
                <span class="asset-name">{label}</span>
                <span class="asset-ticker">{asset}</span>
            </td>
            <td class="price-cell">${m['current_price']:,.2f}</td>
            <td>{fmt_val(m['median_move'], m['median_move_nominal'])}</td>
            <td>{m['volatility']:.2f}</td>
            <td>{fmt_val(m['skew'], m['skew_nominal'])}</td>
            <td>{m['range_pct']:.3f}% <span class="nominal">(${m['range_nominal']:,.2f})</span></td>
            <td>${m['price_low']:,.2f} - ${m['price_high']:,.2f}</td>
            <td>{rel_median}</td>
            <td>{rel_skew}</td>
            {trade_cell}
        </tr>"""
    return rows


def build_insights(metrics: dict) -> dict:
    """Compute insight card data from metrics."""
    directions = [m["median_move"] for m in metrics.values()]
    if all(d > 0 for d in directions):
        alignment_text, alignment_class = "All Bullish", "bullish"
    elif all(d < 0 for d in directions):
        alignment_text, alignment_class = "All Bearish", "bearish"
    else:
        alignment_text, alignment_class = "Mixed", "mixed"

    widest = max(metrics.items(), key=lambda x: x[1]["range_pct"])
    widest_name = f"{ASSET_LABELS[widest[0]]} ({widest[1]['range_pct']:.2f}%)"

    most_skewed = max(metrics.items(), key=lambda x: abs(x[1]["skew"]))
    skew_dir = "upside" if most_skewed[1]["skew"] > 0 else "downside"
    skew_name = f"{ASSET_LABELS[most_skewed[0]]} ({skew_dir})"

    return {
        "alignment_text": alignment_text,
        "alignment_class": alignment_class,
        "widest_name": widest_name,
        "skew_name": skew_name,
    }


def make_time_points(horizon: str) -> list[str]:
    """Generate ET timezone time axis for the given horizon."""
    et = ZoneInfo("America/New_York")
    now_et = datetime.now(et)
    if horizon == "1h":
        steps = 61
        interval_min = 1
    else:
        steps = 289
        interval_min = 5
    return [
        (now_et + timedelta(minutes=i * interval_min)).strftime("%Y-%m-%dT%H:%M")
        for i in range(steps)
    ]


def fetch_and_process(client, horizon: str = "24h") -> dict:
    """Fetch data, compute metrics, and build all dashboard components."""
    data = fetch_all_data(client, horizon=horizon)
    metrics = calculate_metrics(data)
    metrics, benchmark = add_relative_to_benchmark(metrics)
    ranked = rank_equities(metrics, sort_by="median_move")
    normalized = get_normalized_series(data)
    time_points = make_time_points(horizon)
    traces = build_traces(normalized, metrics, time_points)
    table_rows = build_table_rows(ranked, benchmark)
    insights = build_insights(metrics)

    assets_with_prices = {
        asset: {"current_price": info["current_price"]}
        for asset, info in data.items()
    }

    return {
        "traces": traces,
        "table_rows": table_rows,
        "insights": insights,
        "metrics": {
            asset: {k: v for k, v in m.items()}
            for asset, m in metrics.items()
        },
        "assets": assets_with_prices,
        "benchmark": benchmark,
        "horizon": horizon,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def generate_dashboard_html(client) -> str:
    """Generate the full interactive HTML dashboard."""
    result = fetch_and_process(client, "24h")
    traces_json = json.dumps(result["traces"])
    assets_json = json.dumps(result["assets"])
    horizon_label = "24h Forecast"
    benchmark = result["benchmark"]
    ins = result["insights"]
    timestamp = result["timestamp"]
    table_rows = result["table_rows"]

    # The HTML uses raw braces for JS/CSS, so we use explicit concatenation
    # where Python formatting is needed, and raw strings for JS blocks.
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '<title>Tide Chart - Forecast Comparison</title>\n'
        '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>\n'
        '<script src="https://cdn.jsdelivr.net/npm/ethers@6.13.4/dist/ethers.umd.min.js"></script>\n'
        "<style>\n"
        "  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');\n"
        "  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }\n"
        "  :root {\n"
        "    --bg-deep: #0a0e17; --bg-card: #111827; --bg-card-hover: #1a2236;\n"
        "    --border: #1e2a40; --text-primary: #f0f2f5; --text-secondary: #94a3b8;\n"
        "    --text-muted: #5a6a82; --positive: #34d399; --negative: #f06070; --accent: #e8d44d;\n"
        "  }\n"
        "  body { font-family: 'IBM Plex Sans', sans-serif; background: var(--bg-deep);\n"
        "    background-image: radial-gradient(ellipse at 50% 0%, rgba(30,42,64,0.5) 0%, transparent 60%);\n"
        "    color: var(--text-primary); min-height: 100vh; overflow-x: hidden; }\n"
        "  body::before { content: ''; position: fixed; inset: 0;\n"
        "    background-image: linear-gradient(rgba(232,212,77,0.03) 1px, transparent 1px),\n"
        "      linear-gradient(90deg, rgba(232,212,77,0.03) 1px, transparent 1px);\n"
        "    background-size: 60px 60px; pointer-events: none; z-index: 0; }\n"
        "  .dashboard { position: relative; z-index: 1; max-width: 1280px; margin: 0 auto; padding: 32px 24px 48px; }\n"
        "  .header { margin-bottom: 28px; }\n"
        "  .header-top { display: flex; align-items: flex-end; gap: 16px; margin-bottom: 8px; }\n"
        "  .title { font-size: 28px; font-weight: 600; letter-spacing: -0.5px;\n"
        "    background: linear-gradient(135deg, #e8d44d 0%, #f0f2f5 50%, #94a3b8 100%);\n"
        "    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }\n"
        "  .badge { font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 500;\n"
        "    letter-spacing: 1px; text-transform: uppercase; color: var(--accent);\n"
        "    border: 1px solid rgba(232,212,77,0.3); padding: 3px 8px; border-radius: 4px; margin-bottom: 4px; }\n"
        "  .subtitle { font-size: 13px; color: var(--text-muted); font-weight: 300; }\n"
        "  .subtitle span { color: var(--text-secondary); }\n"
        "\n"
        "  /* Controls bar */\n"
        "  .controls { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }\n"
        "  .horizon-toggle { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }\n"
        "  .horizon-btn { font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 500;\n"
        "    padding: 6px 16px; background: var(--bg-card); color: var(--text-muted); border: none;\n"
        "    cursor: pointer; transition: all 0.2s; letter-spacing: 0.5px; }\n"
        "  .horizon-btn.active { background: rgba(232,212,77,0.15); color: var(--accent);\n"
        "    box-shadow: inset 0 0 0 1px rgba(232,212,77,0.3); }\n"
        "  .horizon-btn:hover:not(.active) { background: var(--bg-card-hover); color: var(--text-secondary); }\n"
        "  .refresh-btn { font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 500;\n"
        "    padding: 6px 14px; background: var(--bg-card); color: var(--text-secondary); border: 1px solid var(--border);\n"
        "    border-radius: 6px; cursor: pointer; transition: all 0.2s; }\n"
        "  .refresh-btn:hover { background: var(--bg-card-hover); color: var(--accent); border-color: rgba(232,212,77,0.3); }\n"
        "  .refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }\n"
        "  .auto-refresh-label { font-family: 'IBM Plex Mono', monospace; font-size: 10px;\n"
        "    color: var(--text-muted); display: flex; align-items: center; gap: 6px; cursor: pointer; }\n"
        "  .auto-refresh-label input { accent-color: var(--accent); }\n"
        "  .status-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }\n"
        "  .status-dot.live { background: var(--positive); box-shadow: 0 0 6px var(--positive); }\n"
        "  .status-dot.idle { background: var(--text-muted); }\n"
        "\n"
        "  /* Calculator */\n"
        "  .calc-container { background: var(--bg-card); border: 1px solid var(--border);\n"
        "    border-radius: 10px; padding: 20px; margin-bottom: 20px; }\n"
        "  .calc-form { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }\n"
        "  .calc-field { display: flex; flex-direction: column; gap: 4px; }\n"
        "  .calc-field label { font-family: 'IBM Plex Mono', monospace; font-size: 10px;\n"
        "    text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); }\n"
        "  .calc-field select, .calc-field input { font-family: 'IBM Plex Mono', monospace; font-size: 12px;\n"
        "    padding: 8px 12px; background: var(--bg-deep); border: 1px solid var(--border);\n"
        "    border-radius: 6px; color: var(--text-primary); outline: none; transition: border-color 0.2s; }\n"
        "  .calc-field select:focus, .calc-field input:focus { border-color: rgba(232,212,77,0.4); }\n"
        "  .calc-btn { font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 500;\n"
        "    padding: 8px 20px; background: rgba(232,212,77,0.15); color: var(--accent);\n"
        "    border: 1px solid rgba(232,212,77,0.3); border-radius: 6px; cursor: pointer; transition: all 0.2s; }\n"
        "  .calc-btn:hover { background: rgba(232,212,77,0.25); }\n"
        "  .calc-result { margin-top: 14px; padding: 12px 16px; background: var(--bg-deep);\n"
        "    border: 1px solid var(--border); border-radius: 6px; display: none; }\n"
        "  .calc-result.visible { display: block; }\n"
        "  .calc-result .prob-value { font-family: 'IBM Plex Mono', monospace; font-size: 20px;\n"
        "    font-weight: 600; color: var(--accent); }\n"
        "  .calc-result .prob-desc { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }\n"
        "\n"
        "  /* Insight cards */\n"
        "  .insights { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }\n"
        "  .insight-card { background: var(--bg-card); border: 1px solid var(--border);\n"
        "    border-left: 2px solid rgba(232,212,77,0.4); border-radius: 8px;\n"
        "    padding: 14px 16px; transition: all 0.25s ease; }\n"
        "  .insight-card:hover { background: var(--bg-card-hover); border-left-color: var(--accent);\n"
        "    box-shadow: 0 0 20px rgba(232,212,77,0.06); }\n"
        "  .insight-label { font-size: 11px; text-transform: uppercase; letter-spacing: 1px;\n"
        "    color: var(--text-secondary); margin-bottom: 6px; font-weight: 500; }\n"
        "  .insight-value { font-family: 'IBM Plex Mono', monospace; font-size: 15px; font-weight: 500; }\n"
        "  .insight-value.bullish { color: var(--positive); }\n"
        "  .insight-value.bearish { color: var(--negative); }\n"
        "  .insight-value.mixed { color: var(--text-primary); }\n"
        "\n"
        "  /* Chart section */\n"
        "  .chart-container { background: var(--bg-card); border: 1px solid var(--border);\n"
        "    border-radius: 10px; padding: 20px; margin-bottom: 20px; transition: box-shadow 0.3s ease; }\n"
        "  .chart-container:hover { box-shadow: 0 0 30px rgba(232,212,77,0.04); }\n"
        "  .section-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }\n"
        "  .section-title { font-size: 15px; font-weight: 600; color: var(--text-primary);\n"
        "    text-transform: uppercase; letter-spacing: 0.6px; }\n"
        "  .section-line { flex: 1; height: 1px; background: var(--border); }\n"
        "  #cone-chart { width: 100%; height: 420px; }\n"
        "  .chart-hint { font-size: 10px; color: var(--text-muted); text-align: right; margin-top: 6px;\n"
        "    font-family: 'IBM Plex Mono', monospace; letter-spacing: 0.3px; }\n"
        "  .chart-container .modebar { background: transparent !important; }\n"
        "  .chart-container .modebar-btn path { fill: var(--text-muted) !important; }\n"
        "  .chart-container .modebar-btn:hover path { fill: var(--text-secondary) !important; }\n"
        "  .chart-container .modebar-btn.active path { fill: var(--accent) !important; }\n"
        "\n"
        "  /* Table section */\n"
        "  .table-container { background: var(--bg-card); border: 1px solid var(--border);\n"
        "    border-radius: 10px; padding: 20px; transition: box-shadow 0.3s ease; }\n"
        "  .table-container:hover { box-shadow: 0 0 30px rgba(232,212,77,0.04); }\n"
        "  table { width: 100%; border-collapse: collapse; font-size: 13px; }\n"
        "  thead th { font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 500;\n"
        "    text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted);\n"
        "    text-align: left; padding: 0 8px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }\n"
        "  thead th:first-child { padding-left: 16px; }\n"
        "  thead th:nth-child(9), tbody td:nth-child(9) { border-left: 1px solid var(--border); padding-left: 12px; }\n"
        "  tbody tr { transition: background 0.15s; }\n"
        "  tbody tr:hover { background: rgba(232,212,77,0.04); }\n"
        "  tbody td { padding: 12px 8px; border-bottom: 1px solid rgba(30,42,64,0.7);\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 12px; white-space: nowrap; }\n"
        "  tbody td:first-child { padding-left: 16px; }\n"
        "  .rank-cell { color: var(--text-muted); font-size: 11px; width: 32px; }\n"
        "  .asset-cell { display: flex; align-items: center; gap: 8px; font-family: 'IBM Plex Sans', sans-serif !important; }\n"
        "  .asset-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }\n"
        "  .asset-name { font-weight: 500; font-size: 13px; color: var(--text-primary); }\n"
        "  .asset-ticker { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--text-muted);\n"
        "    background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 3px; }\n"
        "  .price-cell { color: var(--text-secondary); }\n"
        "  .sortable { cursor: pointer; user-select: none; position: relative; }\n"
        "  .sortable .sort-arrow { display: inline-block; font-size: 12px; opacity: 0.25; margin-left: 3px;\n"
        "    letter-spacing: -2px; transition: opacity 0.15s ease, color 0.15s ease; vertical-align: middle; }\n"
        "  .sortable:hover .sort-arrow { opacity: 0.5; }\n"
        "  .sortable.asc .sort-arrow { opacity: 0.9; color: var(--accent); }\n"
        "  .sortable.desc .sort-arrow { opacity: 0.9; color: var(--accent); }\n"
        "  .sortable:hover { color: var(--accent); }\n"
        "  th[data-tip]::before { content: ''; position: absolute; top: calc(100% + 2px); left: 50%;\n"
        "    transform: translateX(-50%); border: 5px solid transparent;\n"
        "    border-bottom-color: rgba(232,212,77,0.35); opacity: 0; pointer-events: none;\n"
        "    transition: opacity 0.2s ease 0.05s; z-index: 11; }\n"
        "  th[data-tip]::after { content: attr(data-tip); position: absolute; top: calc(100% + 11px); left: 50%;\n"
        "    transform: translateX(-50%) translateY(2px); background: var(--bg-deep);\n"
        "    border: 1px solid rgba(232,212,77,0.2); color: var(--text-primary);\n"
        "    font-family: 'IBM Plex Sans', sans-serif; font-size: 11px; font-weight: 400;\n"
        "    text-transform: none; letter-spacing: 0.2px; line-height: 1.4; padding: 8px 14px;\n"
        "    border-radius: 6px; white-space: nowrap; opacity: 0; pointer-events: none;\n"
        "    transition: opacity 0.2s ease 0.05s, transform 0.2s ease 0.05s; z-index: 10;\n"
        "    box-shadow: 0 8px 24px rgba(0,0,0,0.5), 0 0 0 1px rgba(232,212,77,0.06); }\n"
        "  th[data-tip]:hover::before, th[data-tip]:focus-visible::before,\n"
        "  th[data-tip]:hover::after, th[data-tip]:focus-visible::after { opacity: 1; }\n"
        "  th[data-tip]:hover::after, th[data-tip]:focus-visible::after { transform: translateX(-50%) translateY(0); }\n"
        "  .positive { color: var(--positive); } .negative { color: var(--negative); } .neutral { color: var(--text-secondary); }\n"
        "  .nominal { font-size: 10px; color: var(--text-muted); font-weight: 400; }\n"
        "  .footer { margin-top: 24px; text-align: center; font-size: 11px; color: var(--text-muted); }\n"
        "  .footer a { color: var(--accent); text-decoration: none; transition: color 0.15s; }\n"
        "  .footer a:hover { color: var(--text-primary); }\n"
        "\n"
        "  /* Wallet connect */\n"
        "  .wallet-btn {\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 500;\n"
        "    padding: 7px 16px; background: var(--bg-card); color: var(--text-secondary);\n"
        "    border: 1px solid var(--border); border-radius: 6px; cursor: pointer;\n"
        "    transition: all 0.2s; margin-left: auto; display: flex; align-items: center; gap: 8px;\n"
        "  }\n"
        "  .wallet-btn:hover { background: var(--bg-card-hover); color: var(--accent);\n"
        "    border-color: rgba(232,212,77,0.3); }\n"
        "  .wallet-btn.connected { border-color: rgba(52,211,153,0.3); }\n"
        "  .wallet-btn.connected:hover { border-color: rgba(240,96,112,0.4); color: var(--negative); }\n"
        "  .wallet-network-badge { font-size: 9px; padding: 2px 6px; border-radius: 3px;\n"
        "    background: rgba(52,211,153,0.12); color: var(--positive); }\n"
        "  .wallet-address { color: var(--text-primary); }\n"
        "  .wallet-dot { width: 6px; height: 6px; border-radius: 50%;\n"
        "    background: var(--positive); box-shadow: 0 0 6px var(--positive); flex-shrink: 0; }\n"
        "\n"
        "  /* Toast notifications */\n"
        "  .toast-container {\n"
        "    position: fixed; bottom: 20px; right: 20px; z-index: 1000;\n"
        "    display: flex; flex-direction: column-reverse; gap: 8px; max-width: 380px;\n"
        "  }\n"
        "  .toast {\n"
        "    font-family: 'IBM Plex Sans', sans-serif; font-size: 12px; padding: 12px 16px;\n"
        "    border-radius: 8px; background: var(--bg-card); border: 1px solid var(--border);\n"
        "    color: var(--text-primary); box-shadow: 0 8px 24px rgba(0,0,0,0.4);\n"
        "    animation: toastIn 0.3s ease; display: flex; align-items: flex-start;\n"
        "    gap: 8px; word-break: break-word;\n"
        "  }\n"
        "  .toast.success { border-color: rgba(52,211,153,0.4); }\n"
        "  .toast.error { border-color: rgba(240,96,112,0.4); }\n"
        "  .toast.info { border-color: rgba(232,212,77,0.3); }\n"
        "  .toast-icon { flex-shrink: 0; font-size: 14px; line-height: 1; }\n"
        "  .toast.success .toast-icon { color: var(--positive); }\n"
        "  .toast.error .toast-icon { color: var(--negative); }\n"
        "  .toast.info .toast-icon { color: var(--accent); }\n"
        "  .toast-msg { flex: 1; line-height: 1.4; }\n"
        "  .toast-msg a { color: var(--accent); text-decoration: none; }\n"
        "  .toast-msg a:hover { text-decoration: underline; }\n"
        "  @keyframes toastIn {\n"
        "    from { opacity: 0; transform: translateY(10px); }\n"
        "    to { opacity: 1; transform: translateY(0); }\n"
        "  }\n"
        "\n"
        "  /* Trade panel */\n"
        "  .trade-panel {\n"
        "    background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px;\n"
        "    padding: 0; margin-bottom: 20px; display: none; overflow: hidden;\n"
        "    transition: box-shadow 0.3s ease;\n"
        "  }\n"
        "  .trade-panel.visible { display: block; }\n"
        "  .trade-panel:hover { box-shadow: 0 0 30px rgba(232,212,77,0.04); }\n"
        "  .trade-header {\n"
        "    display: flex; align-items: center; justify-content: space-between;\n"
        "    padding: 16px 20px; border-bottom: 1px solid var(--border);\n"
        "  }\n"
        "  .trade-header-title {\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 13px; font-weight: 600;\n"
        "    text-transform: uppercase; letter-spacing: 1px; color: var(--text-primary);\n"
        "    display: flex; align-items: center; gap: 10px;\n"
        "  }\n"
        "  .trade-asset-badge { font-size: 11px; padding: 3px 10px; border-radius: 4px;\n"
        "    background: rgba(232,212,77,0.12); color: var(--accent); }\n"
        "  .trade-close-btn {\n"
        "    background: none; border: none; color: var(--text-muted); font-size: 18px;\n"
        "    cursor: pointer; padding: 4px 8px; border-radius: 4px;\n"
        "    transition: all 0.15s; line-height: 1;\n"
        "  }\n"
        "  .trade-close-btn:hover { color: var(--negative); background: rgba(240,96,112,0.1); }\n"
        "  .trade-body { padding: 20px; }\n"
        "  .trade-connect-overlay { text-align: center; padding: 32px 20px; }\n"
        "  .trade-connect-overlay p { font-size: 13px; color: var(--text-muted); margin-bottom: 12px; }\n"
        "  .trade-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }\n"
        "  .trade-field { display: flex; flex-direction: column; gap: 4px; }\n"
        "  .trade-field.full-width { grid-column: 1 / -1; }\n"
        "  .trade-field label {\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 10px; text-transform: uppercase;\n"
        "    letter-spacing: 1px; color: var(--text-muted);\n"
        "    display: flex; justify-content: space-between; align-items: center;\n"
        "  }\n"
        "  .trade-field label .hint {\n"
        "    font-size: 10px; color: var(--text-muted); text-transform: none;\n"
        "    letter-spacing: 0; font-weight: 400;\n"
        "  }\n"
        "  .trade-field input, .trade-field select {\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 12px; padding: 8px 12px;\n"
        "    background: var(--bg-deep); border: 1px solid var(--border); border-radius: 6px;\n"
        "    color: var(--text-primary); outline: none; transition: border-color 0.2s;\n"
        "  }\n"
        "  .trade-field input:focus { border-color: rgba(232,212,77,0.4); }\n"
        "  .trade-field input.error { border-color: rgba(240,96,112,0.6); }\n"
        "  .trade-field .field-error {\n"
        "    font-size: 10px; color: var(--negative); margin-top: 2px;\n"
        "    font-family: 'IBM Plex Sans', sans-serif; min-height: 14px;\n"
        "  }\n"
        "  .trade-direction { display: flex; gap: 8px; }\n"
        "  .trade-dir-btn {\n"
        "    flex: 1; font-family: 'IBM Plex Mono', monospace; font-size: 12px; font-weight: 600;\n"
        "    padding: 10px; border: 1px solid var(--border); border-radius: 6px;\n"
        "    background: var(--bg-deep); color: var(--text-muted); cursor: pointer;\n"
        "    transition: all 0.2s; text-transform: uppercase; letter-spacing: 1px;\n"
        "  }\n"
        "  .trade-dir-btn:hover { background: var(--bg-card-hover); }\n"
        "  .trade-dir-btn.active.long {\n"
        "    background: rgba(52,211,153,0.1); color: var(--positive);\n"
        "    border-color: rgba(52,211,153,0.4);\n"
        "  }\n"
        "  .trade-dir-btn.active.short {\n"
        "    background: rgba(240,96,112,0.1); color: var(--negative);\n"
        "    border-color: rgba(240,96,112,0.4);\n"
        "  }\n"
        "  .trade-leverage-display {\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 14px; font-weight: 600;\n"
        "    color: var(--accent); text-align: center; margin-bottom: 4px;\n"
        "  }\n"
        "  .trade-slider { width: 100%; accent-color: var(--accent); cursor: pointer; }\n"
        "  .trade-slider-labels {\n"
        "    display: flex; justify-content: space-between;\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 9px; color: var(--text-muted);\n"
        "  }\n"
        "  .trade-position-size {\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 16px; font-weight: 600;\n"
        "    color: var(--text-primary); text-align: center; padding: 10px;\n"
        "    background: var(--bg-deep); border-radius: 6px; border: 1px solid var(--border);\n"
        "  }\n"
        "  .trade-position-size.warning { color: var(--negative); }\n"
        "  .trade-submit-btn {\n"
        "    width: 100%; font-family: 'IBM Plex Mono', monospace; font-size: 13px;\n"
        "    font-weight: 600; padding: 12px; border: none; border-radius: 6px;\n"
        "    cursor: pointer; transition: all 0.2s; text-transform: uppercase;\n"
        "    letter-spacing: 1px; margin-top: 8px;\n"
        "  }\n"
        "  .trade-submit-btn.long {\n"
        "    background: rgba(52,211,153,0.2); color: var(--positive);\n"
        "    border: 1px solid rgba(52,211,153,0.3);\n"
        "  }\n"
        "  .trade-submit-btn.long:hover:not(:disabled) { background: rgba(52,211,153,0.3); }\n"
        "  .trade-submit-btn.short {\n"
        "    background: rgba(240,96,112,0.2); color: var(--negative);\n"
        "    border: 1px solid rgba(240,96,112,0.3);\n"
        "  }\n"
        "  .trade-submit-btn.short:hover:not(:disabled) { background: rgba(240,96,112,0.3); }\n"
        "  .trade-submit-btn:disabled { opacity: 0.4; cursor: not-allowed; }\n"
        "  .trade-info-row {\n"
        "    display: flex; justify-content: center; gap: 24px; margin-top: 12px;\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--text-muted);\n"
        "  }\n"
        "  .trade-info-row span { display: flex; align-items: center; gap: 4px; }\n"
        "  .trade-cell-btn {\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 500;\n"
        "    padding: 4px 12px; background: rgba(232,212,77,0.1); color: var(--accent);\n"
        "    border: 1px solid rgba(232,212,77,0.25); border-radius: 4px; cursor: pointer;\n"
        "    transition: all 0.2s; text-transform: uppercase; letter-spacing: 0.5px;\n"
        "  }\n"
        "  .trade-cell-btn:hover { background: rgba(232,212,77,0.2);\n"
        "    border-color: rgba(232,212,77,0.4); }\n"
        "  .trade-cell-na { font-family: 'IBM Plex Mono', monospace; font-size: 10px;\n"
        "    color: var(--text-muted); }\n"
        "\n"
        "  @media (max-width: 768px) {\n"
        "    .insights { grid-template-columns: 1fr; }\n"
        "    .title { font-size: 22px; }\n"
        "    .dashboard { padding: 16px 12px 32px; }\n"
        "    #cone-chart { height: 320px; }\n"
        "    .table-container { overflow-x: auto; }\n"
        "    .controls { flex-direction: column; align-items: flex-start; }\n"
        "    .wallet-btn { margin-left: 0; margin-top: 4px; }\n"
        "    .trade-form-grid { grid-template-columns: 1fr; }\n"
        "    .trade-info-row { flex-direction: column; gap: 4px; align-items: center; }\n"
        "  }\n"
        "</style>\n</head>\n<body>\n"
        '<div class="dashboard">\n'
        "\n"
        '  <div class="header">\n'
        '    <div class="header-top">\n'
        '      <h1 class="title">Tide Chart</h1>\n'
        f'      <span class="badge" id="horizon-badge">{horizon_label}</span>\n'
        '      <button class="wallet-btn" id="wallet-btn">\n'
        '        <span id="wallet-btn-text">Connect Wallet</span>\n'
        '      </button>\n'
        "    </div>\n"
        f'    <p class="subtitle">Probability cone comparison &mdash; <span id="timestamp">{timestamp}</span></p>\n'
        "  </div>\n"
        "\n"
        '  <div class="controls">\n'
        '    <div class="horizon-toggle">\n'
        '      <button class="horizon-btn" data-horizon="1h" id="btn-1h">Intraday (1H)</button>\n'
        '      <button class="horizon-btn active" data-horizon="24h" id="btn-24h">Next Day (24H)</button>\n'
        "    </div>\n"
        '    <button class="refresh-btn" id="refresh-btn">\u21BB Refresh</button>\n'
        '    <label class="auto-refresh-label">\n'
        '      <input type="checkbox" id="auto-refresh-toggle">\n'
        '      <span class="status-dot idle" id="status-dot"></span>\n'
        "      Auto-refresh (5 min)\n"
        "    </label>\n"
        "  </div>\n"
        "\n"
        '  <div class="calc-container">\n'
        '    <div class="section-header">\n'
        '      <span class="section-title">Probability Calculator</span>\n'
        '      <span class="section-line"></span>\n'
        "    </div>\n"
        '    <div class="calc-form">\n'
        '      <div class="calc-field">\n'
        '        <label for="calc-asset">Asset</label>\n'
        '        <select id="calc-asset"></select>\n'
        "      </div>\n"
        '      <div class="calc-field">\n'
        '        <label for="calc-price">Target Price ($)</label>\n'
        '        <input type="number" id="calc-price" step="0.01" placeholder="e.g. 155.00">\n'
        "      </div>\n"
        '      <button class="calc-btn" id="calc-btn">Calculate</button>\n'
        "    </div>\n"
        '    <div class="calc-result" id="calc-result">\n'
        '      <div class="prob-value" id="prob-value"></div>\n'
        '      <div class="prob-desc" id="prob-desc"></div>\n'
        "    </div>\n"
        "  </div>\n"
        "\n"
        '  <div class="insights" id="insights">\n'
        '    <div class="insight-card">\n'
        '      <div class="insight-label">Directional Alignment</div>\n'
        f'      <div class="insight-value {ins["alignment_class"]}" id="insight-alignment">{ins["alignment_text"]}</div>\n'
        "    </div>\n"
        '    <div class="insight-card">\n'
        '      <div class="insight-label">Widest Range</div>\n'
        f'      <div class="insight-value" id="insight-widest">{ins["widest_name"]}</div>\n'
        "    </div>\n"
        '    <div class="insight-card">\n'
        '      <div class="insight-label">Most Asymmetric</div>\n'
        f'      <div class="insight-value" id="insight-skew">{ins["skew_name"]}</div>\n'
        "    </div>\n"
        "  </div>\n"
        "\n"
        '  <div class="chart-container">\n'
        '    <div class="section-header">\n'
        '      <span class="section-title">Probability Cones (5th - 95th Percentile)</span>\n'
        '      <span class="section-line"></span>\n'
        "    </div>\n"
        '    <div id="cone-chart"></div>\n'
        '    <div class="chart-hint">click legend to toggle assets &middot; scroll to zoom &middot; drag to pan &middot; double-click to reset</div>\n'
        "  </div>\n"
        "\n"
        '  <div class="trade-panel" id="trade-panel">\n'
        '    <div class="trade-header">\n'
        '      <div class="trade-header-title">\n'
        '        Trade <span class="trade-asset-badge" id="trade-asset-label"></span>\n'
        '      </div>\n'
        '      <button class="trade-close-btn" id="trade-close-btn">&times;</button>\n'
        '    </div>\n'
        '    <div class="trade-body">\n'
        '      <div class="trade-connect-overlay" id="trade-connect-overlay">\n'
        '        <p>Connect your wallet to start trading via gTrade</p>\n'
        '        <button class="wallet-btn" onclick="handleWalletConnect()">Connect Wallet</button>\n'
        '      </div>\n'
        '      <div id="trade-form-container" style="display:none">\n'
        '        <div class="trade-form-grid">\n'
        '          <div class="trade-field full-width">\n'
        '            <label>Direction</label>\n'
        '            <div class="trade-direction">\n'
        '              <button class="trade-dir-btn active long" id="trade-dir-long" data-dir="long">Long</button>\n'
        '              <button class="trade-dir-btn short" id="trade-dir-short" data-dir="short">Short</button>\n'
        '            </div>\n'
        '          </div>\n'
        '          <div class="trade-field">\n'
        '            <label>Collateral (USDC) <span class="hint" id="trade-balance"></span></label>\n'
        '            <input type="number" id="trade-collateral" step="0.01" min="0" placeholder="100.00">\n'
        '            <div class="field-error" id="trade-collateral-error"></div>\n'
        '          </div>\n'
        '          <div class="trade-field">\n'
        '            <label>Max Slippage (%) <span class="hint">default 1%</span></label>\n'
        '            <input type="number" id="trade-slippage" step="0.1" min="0.1" max="5" value="1.0">\n'
        '          </div>\n'
        '          <div class="trade-field full-width">\n'
        '            <label>Leverage <span class="hint" id="trade-leverage-hint">1.1x - 50x</span></label>\n'
        '            <div class="trade-leverage-display" id="trade-leverage-display">15x</div>\n'
        '            <input type="range" class="trade-slider" id="trade-leverage" min="2" max="50" value="15" step="1">\n'
        '            <div class="trade-slider-labels" id="trade-slider-labels">\n'
        '              <span id="trade-lev-min">2x</span><span id="trade-lev-max">50x</span>\n'
        '            </div>\n'
        '          </div>\n'
        '          <div class="trade-field">\n'
        '            <label>Take Profit (%)</label>\n'
        '            <input type="number" id="trade-tp" step="0.1" min="0" placeholder="Optional">\n'
        '          </div>\n'
        '          <div class="trade-field">\n'
        '            <label>Stop Loss (%)</label>\n'
        '            <input type="number" id="trade-sl" step="0.1" min="0" placeholder="Optional">\n'
        '          </div>\n'
        '          <div class="trade-field full-width">\n'
        '            <label>Position Size</label>\n'
        '            <div class="trade-position-size" id="trade-position-size">$0.00</div>\n'
        '          </div>\n'
        '          <div class="trade-field full-width">\n'
        '            <button class="trade-submit-btn long" id="trade-submit-btn" disabled>Connect Wallet</button>\n'
        '          </div>\n'
        '        </div>\n'
        '        <div class="trade-info-row">\n'
        '          <span>Min position: $1,500</span>\n'
        '          <span>Network: Arbitrum</span>\n'
        '          <span>Collateral: USDC</span>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n'
        '  </div>\n'
        "\n"
        '  <div class="table-container">\n'
        '    <div class="section-header">\n'
        '      <span class="section-title" id="table-title">Asset Rankings</span>\n'
        '      <span class="section-line"></span>\n'
        "    </div>\n"
        '    <table id="rank-table">\n'
        "      <thead>\n"
        "        <tr>\n"
        "          <th>#</th>\n"
        "          <th>Asset</th>\n"
        "          <th>Price</th>\n"
        '          <th class="sortable" data-sort="median" data-tip="Expected price change at 50th percentile" tabindex="0" role="columnheader" aria-sort="none">Median Move<span class="sort-arrow">\u25B4\u25BE</span></th>\n'
        '          <th class="sortable" data-sort="vol" data-tip="Forecasted average volatility" tabindex="0" role="columnheader" aria-sort="none">Volatility<span class="sort-arrow">\u25B4\u25BE</span></th>\n'
        '          <th class="sortable" data-sort="skew" data-tip="Upside minus downside - positive means bullish bias" tabindex="0" role="columnheader" aria-sort="none">Skew<span class="sort-arrow">\u25B4\u25BE</span></th>\n'
        '          <th class="sortable" data-sort="range" data-tip="Total width of 5th to 95th percentile band" tabindex="0" role="columnheader" aria-sort="none">Range<span class="sort-arrow">\u25B4\u25BE</span></th>\n'
        '          <th class="sortable" data-sort="bounds" data-tip="Projected price at 5th and 95th percentile" tabindex="0" role="columnheader" aria-sort="none">Bounds<span class="sort-arrow">\u25B4\u25BE</span></th>\n'
        f'          <th class="sortable" data-sort="rel-median" data-tip="Median move relative to benchmark" tabindex="0" role="columnheader" aria-sort="none" id="th-rel-median">vs {benchmark}<span class="sort-arrow">\u25B4\u25BE</span></th>\n'
        f'          <th class="sortable" data-sort="rel-skew" data-tip="Directional skew relative to benchmark" tabindex="0" role="columnheader" aria-sort="none" id="th-rel-skew">Skew vs {benchmark}<span class="sort-arrow">\u25B4\u25BE</span></th>\n'
        '          <th data-tip="Trade via gTrade on Arbitrum">Trade</th>\n'
        "        </tr>\n"
        "      </thead>\n"
        f"      <tbody id=\"rank-tbody\">{table_rows}\n"
        "      </tbody>\n"
        "    </table>\n"
        "  </div>\n"
        "\n"
        '  <div class="footer">\n'
        '    Data from <a href="https://synthdata.co" target="_blank" rel="noopener noreferrer">Synth API</a>\n'
        "    &middot; Built with Venth\n"
        "  </div>\n"
        "\n"
        "</div>\n"
        '<div class="toast-container" id="toast-container"></div>\n'
        "\n"
        "<script>\n"
        "var currentHorizon = '24h';\n"
        "var autoRefreshTimer = null;\n"
        "var AUTO_REFRESH_MS = 5 * 60 * 1000;\n"
        f"var currentAssets = {assets_json};\n"
        "\n"
        "var plotlyLayout = {\n"
        "  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',\n"
        "  font: { family: 'IBM Plex Sans, sans-serif', color: '#94a3b8', size: 11 },\n"
        "  margin: { t: 8, r: 16, b: 40, l: 48 },\n"
        "  xaxis: { title: { text: 'Time (ET)', font: { size: 10 } },\n"
        "    gridcolor: 'rgba(30,42,64,0.7)', zerolinecolor: 'rgba(30,42,64,0.9)',\n"
        "    tickformat: '%I:%M %p', tickfont: { family: 'IBM Plex Mono, monospace', size: 10 } },\n"
        "  yaxis: { title: { text: '% Change from Current', font: { size: 10 } },\n"
        "    gridcolor: 'rgba(30,42,64,0.7)', zerolinecolor: 'rgba(232,212,77,0.12)',\n"
        "    zerolinewidth: 1, ticksuffix: '%', tickfont: { family: 'IBM Plex Mono, monospace', size: 10 } },\n"
        "  legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'left', x: 0,\n"
        "    font: { size: 11 }, itemwidth: 30 },\n"
        "  dragmode: 'pan', hovermode: 'x unified',\n"
        "  hoverlabel: { bgcolor: '#111827', bordercolor: '#1e2a40',\n"
        "    font: { family: 'IBM Plex Mono, monospace', size: 11, color: '#f0f2f5' } }\n"
        "};\n"
        "\n"
        "var plotlyConfig = { responsive: true, displaylogo: false, scrollZoom: true,\n"
        "  modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d', 'zoomIn2d', 'zoomOut2d'] };\n"
        "\n"
        f"Plotly.newPlot('cone-chart', {traces_json}, plotlyLayout, plotlyConfig);\n"
        "\n"
        "var chart = document.getElementById('cone-chart');\n"
        "chart.on('plotly_legendclick', function() {\n"
        "  setTimeout(function() { Plotly.relayout('cone-chart', { 'yaxis.autorange': true }); }, 100);\n"
        "});\n"
        "chart.on('plotly_legenddoubleclick', function() {\n"
        "  setTimeout(function() { Plotly.relayout('cone-chart', { 'yaxis.autorange': true }); }, 100);\n"
        "});\n"
        "\n"
        "function populateAssetSelect() {\n"
        "  var sel = document.getElementById('calc-asset');\n"
        "  sel.innerHTML = '';\n"
        "  Object.keys(currentAssets).forEach(function(a) {\n"
        "    var opt = document.createElement('option');\n"
        "    opt.value = a; opt.textContent = a + ' ($' + currentAssets[a].current_price.toFixed(2) + ')';\n"
        "    sel.appendChild(opt);\n"
        "  });\n"
        "}\n"
        "populateAssetSelect();\n"
        "\n"
        "function refreshData(horizon) {\n"
        "  var btn = document.getElementById('refresh-btn');\n"
        "  btn.disabled = true; btn.textContent = '\u21BB Loading...';\n"
        "  fetch('/api/data?horizon=' + horizon)\n"
        "    .then(function(r) { return r.json(); })\n"
        "    .then(function(d) {\n"
        "      Plotly.react('cone-chart', d.traces, plotlyLayout, plotlyConfig);\n"
        "      document.getElementById('rank-tbody').innerHTML = d.table_rows;\n"
        "      document.getElementById('timestamp').textContent = d.timestamp;\n"
        "      document.getElementById('horizon-badge').textContent = d.horizon === '1h' ? '1h Forecast' : '24h Forecast';\n"
        "      var ins = d.insights;\n"
        "      var alignEl = document.getElementById('insight-alignment');\n"
        "      alignEl.textContent = ins.alignment_text;\n"
        "      alignEl.className = 'insight-value ' + ins.alignment_class;\n"
        "      document.getElementById('insight-widest').textContent = ins.widest_name;\n"
        "      document.getElementById('insight-skew').textContent = ins.skew_name;\n"
        "      currentAssets = d.assets;\n"
        "      populateAssetSelect();\n"
        "      initSortableTable();\n"
        "      document.getElementById('calc-result').classList.remove('visible');\n"
        "      var bm = d.benchmark || '';\n"
        "      var thRelMedian = document.getElementById('th-rel-median');\n"
        "      var thRelSkew = document.getElementById('th-rel-skew');\n"
        "      if (thRelMedian) { thRelMedian.innerHTML = 'vs ' + bm + '<span class=\"sort-arrow\">\\u25B4\\u25BE</span>'; }\n"
        "      if (thRelSkew) { thRelSkew.innerHTML = 'Skew vs ' + bm + '<span class=\"sort-arrow\">\\u25B4\\u25BE</span>'; }\n"
        "    })\n"
        "    .catch(function(e) { console.error('Refresh failed:', e); })\n"
        "    .finally(function() { btn.disabled = false; btn.textContent = '\u21BB Refresh'; });\n"
        "}\n"
        "\n"
        "document.querySelectorAll('.horizon-btn').forEach(function(b) {\n"
        "  b.addEventListener('click', function() {\n"
        "    document.querySelectorAll('.horizon-btn').forEach(function(x) { x.classList.remove('active'); });\n"
        "    b.classList.add('active');\n"
        "    currentHorizon = b.getAttribute('data-horizon');\n"
        "    refreshData(currentHorizon);\n"
        "  });\n"
        "});\n"
        "\n"
        "document.getElementById('refresh-btn').addEventListener('click', function() {\n"
        "  refreshData(currentHorizon);\n"
        "});\n"
        "\n"
        "document.getElementById('auto-refresh-toggle').addEventListener('change', function(e) {\n"
        "  var dot = document.getElementById('status-dot');\n"
        "  if (e.target.checked) {\n"
        "    dot.className = 'status-dot live';\n"
        "    autoRefreshTimer = setInterval(function() { refreshData(currentHorizon); }, AUTO_REFRESH_MS);\n"
        "  } else {\n"
        "    dot.className = 'status-dot idle';\n"
        "    if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }\n"
        "  }\n"
        "});\n"
        "\n"
        "document.getElementById('calc-btn').addEventListener('click', function() {\n"
        "  var asset = document.getElementById('calc-asset').value;\n"
        "  var price = parseFloat(document.getElementById('calc-price').value);\n"
        "  if (!asset || isNaN(price) || price <= 0) { return; }\n"
        "  fetch('/api/probability', {\n"
        "    method: 'POST',\n"
        "    headers: { 'Content-Type': 'application/json' },\n"
        "    body: JSON.stringify({ asset: asset, target_price: price, horizon: currentHorizon })\n"
        "  })\n"
        "  .then(function(r) { return r.json(); })\n"
        "  .then(function(d) {\n"
        "    if (d.error) { \n"
        "      document.getElementById('prob-value').textContent = 'Error';\n"
        "      document.getElementById('prob-desc').textContent = d.error;\n"
        "    } else {\n"
        "      var pBelow = d.probability_below.toFixed(2);\n"
        "      var pAbove = d.probability_above.toFixed(2);\n"
        "      var dir = price >= d.current_price ? 'reaching' : 'falling to';\n"
        "      document.getElementById('prob-value').textContent = pAbove + '% chance above  ·  ' + pBelow + '% chance below';\n"
        "      document.getElementById('prob-desc').textContent = \n"
        "        'Probability of ' + asset + ' ' + dir + ' $' + price.toFixed(2) + \n"
        "        ' within the ' + currentHorizon + ' forecast window (current: $' + d.current_price.toFixed(2) + ')';\n"
        "    }\n"
        "    document.getElementById('calc-result').classList.add('visible');\n"
        "  })\n"
        "  .catch(function(e) { console.error('Calc failed:', e); });\n"
        "});\n"
        "\n"
        "function initSortableTable() {\n"
        "  var table = document.getElementById('rank-table');\n"
        "  var headers = table.querySelectorAll('.sortable');\n"
        "  var currentSort = null, currentDir = 'desc';\n"
        "  function sortBy(th) {\n"
        "    var key = th.getAttribute('data-sort');\n"
        "    if (currentSort === key) { currentDir = currentDir === 'desc' ? 'asc' : 'desc'; }\n"
        "    else { currentSort = key; currentDir = 'desc'; }\n"
        "    headers.forEach(function(h) {\n"
        "      h.classList.remove('asc', 'desc'); h.setAttribute('aria-sort', 'none');\n"
        "      var arrow = h.querySelector('.sort-arrow'); if (arrow) arrow.textContent = '\u25B4\u25BE';\n"
        "    });\n"
        "    th.classList.add(currentDir);\n"
        "    th.setAttribute('aria-sort', currentDir === 'desc' ? 'descending' : 'ascending');\n"
        "    var activeArrow = th.querySelector('.sort-arrow');\n"
        "    if (activeArrow) activeArrow.textContent = currentDir === 'asc' ? '\u25B4' : '\u25BE';\n"
        "    var tbody = table.querySelector('tbody');\n"
        "    var rows = Array.from(tbody.querySelectorAll('tr'));\n"
        "    rows.sort(function(a, b) {\n"
        "      var va = parseFloat(a.getAttribute('data-' + key)) || 0;\n"
        "      var vb = parseFloat(b.getAttribute('data-' + key)) || 0;\n"
        "      return currentDir === 'desc' ? vb - va : va - vb;\n"
        "    });\n"
        "    rows.forEach(function(row, i) { row.querySelector('.rank-cell').textContent = i + 1; tbody.appendChild(row); });\n"
        "  }\n"
        "  headers.forEach(function(th) {\n"
        "    th.replaceWith(th.cloneNode(true));\n"
        "  });\n"
        "  table.querySelectorAll('.sortable').forEach(function(th) {\n"
        "    th.addEventListener('click', function() { sortBy(th); });\n"
        "    th.addEventListener('keydown', function(e) {\n"
        "      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sortBy(th); }\n"
        "    });\n"
        "  });\n"
        "}\n"
        "initSortableTable();\n"
        "\n"
        "/* ========== gTrade Configuration ========== */\n"
        "var GTRADE_CONFIG = {\n"
        "  chainId: 42161,\n"
        "  chainIdHex: '0xa4b1',\n"
        "  rpcUrl: 'https://arb1.arbitrum.io/rpc',\n"
        "  chainName: 'Arbitrum One',\n"
        "  explorerUrl: 'https://arbiscan.io',\n"
        "  diamondAddress: '0xFF162c694eAA571f685030649814282eA457f169',\n"
        "  usdcAddress: '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',\n"
        "  usdcDecimals: 6,\n"
        "  collateralIndex: 3,\n"
        "  pairIndices: { BTC: 0, ETH: 1, SOL: 33, SPY: 86, NVDA: 65, TSLA: 85, AAPL: 58, GOOGL: 82, XAU: 90 },\n"
        "  minPositionSizeUsd: 1500,\n"
        "  assetGroups: {\n"
        "    crypto:      { minLeverage: 1.1, maxLeverage: 500, assets: ['BTC', 'ETH', 'SOL'] },\n"
        "    stocks:      { minLeverage: 1.1, maxLeverage: 50,  assets: ['NVDA', 'TSLA', 'AAPL', 'GOOGL'] },\n"
        "    indices:     { minLeverage: 1.1, maxLeverage: 50,  assets: ['SPY'] },\n"
        "    commodities: { minLeverage: 2,   maxLeverage: 250, assets: ['XAU'] }\n"
        "  }\n"
        "};\n"
        "\n"
        "function getAssetLimits(asset) {\n"
        "  var groups = GTRADE_CONFIG.assetGroups;\n"
        "  for (var g in groups) {\n"
        "    if (groups[g].assets.indexOf(asset) !== -1) return groups[g];\n"
        "  }\n"
        "  return { minLeverage: 1.1, maxLeverage: 50 };\n"
        "}\n"
        "\n"
        "var DIAMOND_ABI = [\n"
        "  'function openTrade((address user, uint32 index, uint16 pairIndex, uint24 leverage, bool long, bool isOpen, uint8 collateralIndex, uint8 tradeType, uint120 collateralAmount, uint64 openPrice, uint64 tp, uint64 sl, uint192 __placeholder) _trade, uint16 _maxSlippageP, address _referrer)'\n"
        "];\n"
        "\n"
        "var ERC20_ABI = [\n"
        "  'function approve(address spender, uint256 amount) returns (bool)',\n"
        "  'function allowance(address owner, address spender) view returns (uint256)',\n"
        "  'function balanceOf(address account) view returns (uint256)'\n"
        "];\n"
        "\n"
        "/* ========== Toast Notifications ========== */\n"
        "function showToast(message, type, duration) {\n"
        "  type = type || 'info';\n"
        "  duration = duration || 5000;\n"
        "  var container = document.getElementById('toast-container');\n"
        "  var toast = document.createElement('div');\n"
        "  toast.className = 'toast ' + type;\n"
        "  var icons = { success: '\\u2713', error: '\\u2717', info: '\\u2139' };\n"
        "  toast.innerHTML = '<span class=\"toast-icon\">' + (icons[type] || icons.info) +\n"
        "    '</span><span class=\"toast-msg\">' + message + '</span>';\n"
        "  container.appendChild(toast);\n"
        "  setTimeout(function() {\n"
        "    toast.style.cssText = 'opacity:0;transform:translateY(10px);transition:all 0.3s ease';\n"
        "    setTimeout(function() { toast.remove(); }, 300);\n"
        "  }, duration);\n"
        "}\n"
        "\n"
        "/* ========== Wallet State ========== */\n"
        "var walletState = {\n"
        "  provider: null, signer: null, address: null, chainId: null, connected: false\n"
        "};\n"
        "\n"
        "function truncateAddress(addr) {\n"
        "  return addr.slice(0, 6) + '...' + addr.slice(-4);\n"
        "}\n"
        "\n"
        "function updateWalletUI() {\n"
        "  var btn = document.getElementById('wallet-btn');\n"
        "  var btnText = document.getElementById('wallet-btn-text');\n"
        "  var overlay = document.getElementById('trade-connect-overlay');\n"
        "  var form = document.getElementById('trade-form-container');\n"
        "  if (walletState.connected) {\n"
        "    btn.className = 'wallet-btn connected';\n"
        "    btnText.innerHTML = '<span class=\"wallet-dot\"></span>' +\n"
        "      '<span class=\"wallet-network-badge\">Arbitrum</span>' +\n"
        "      '<span class=\"wallet-address\">' + truncateAddress(walletState.address) + '</span>';\n"
        "    if (overlay) overlay.style.display = 'none';\n"
        "    if (form) form.style.display = 'block';\n"
        "    fetchUsdcBalance();\n"
        "  } else {\n"
        "    btn.className = 'wallet-btn';\n"
        "    btnText.textContent = 'Connect Wallet';\n"
        "    if (overlay) overlay.style.display = 'block';\n"
        "    if (form) form.style.display = 'none';\n"
        "  }\n"
        "}\n"
        "\n"
        "async function handleWalletConnect() {\n"
        "  if (walletState.connected) {\n"
        "    walletState = { provider: null, signer: null, address: null, chainId: null, connected: false };\n"
        "    updateWalletUI();\n"
        "    closeTradePanel();\n"
        "    showToast('Wallet disconnected', 'info');\n"
        "    return;\n"
        "  }\n"
        "  if (typeof window.ethereum === 'undefined') {\n"
        "    showToast('No wallet detected. <a href=\"https://metamask.io/download/\" target=\"_blank\" rel=\"noopener\">Install MetaMask</a>', 'error', 8000);\n"
        "    return;\n"
        "  }\n"
        "  try {\n"
        "    showToast('Connecting wallet...', 'info', 3000);\n"
        "    var provider = new ethers.BrowserProvider(window.ethereum);\n"
        "    await provider.send('eth_requestAccounts', []);\n"
        "    var network = await provider.getNetwork();\n"
        "    if (Number(network.chainId) !== GTRADE_CONFIG.chainId) {\n"
        "      await switchToArbitrum();\n"
        "      provider = new ethers.BrowserProvider(window.ethereum);\n"
        "    }\n"
        "    var signer = await provider.getSigner();\n"
        "    walletState = {\n"
        "      provider: provider, signer: signer,\n"
        "      address: await signer.getAddress(),\n"
        "      chainId: GTRADE_CONFIG.chainId, connected: true\n"
        "    };\n"
        "    updateWalletUI();\n"
        "    showToast('Connected: ' + truncateAddress(walletState.address), 'success');\n"
        "  } catch (err) {\n"
        "    var rejected = err.code === 4001 || (err.info && err.info.error && err.info.error.code === 4001);\n"
        "    showToast(rejected ? 'Connection rejected by user' : 'Connection failed: ' + (err.shortMessage || err.message), 'error');\n"
        "  }\n"
        "}\n"
        "\n"
        "async function switchToArbitrum() {\n"
        "  try {\n"
        "    await window.ethereum.request({\n"
        "      method: 'wallet_switchEthereumChain',\n"
        "      params: [{ chainId: GTRADE_CONFIG.chainIdHex }]\n"
        "    });\n"
        "  } catch (e) {\n"
        "    if (e.code === 4902) {\n"
        "      await window.ethereum.request({\n"
        "        method: 'wallet_addEthereumChain',\n"
        "        params: [{\n"
        "          chainId: GTRADE_CONFIG.chainIdHex,\n"
        "          chainName: GTRADE_CONFIG.chainName,\n"
        "          nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },\n"
        "          rpcUrls: [GTRADE_CONFIG.rpcUrl],\n"
        "          blockExplorerUrls: [GTRADE_CONFIG.explorerUrl]\n"
        "        }]\n"
        "      });\n"
        "    } else {\n"
        "      throw e;\n"
        "    }\n"
        "  }\n"
        "}\n"
        "\n"
        "/* ========== Wallet Event Listeners ========== */\n"
        "if (typeof window.ethereum !== 'undefined') {\n"
        "  window.ethereum.on('accountsChanged', function(accounts) {\n"
        "    if (accounts.length === 0) {\n"
        "      walletState = { provider: null, signer: null, address: null, chainId: null, connected: false };\n"
        "      updateWalletUI();\n"
        "      closeTradePanel();\n"
        "      showToast('Wallet disconnected', 'info');\n"
        "    } else if (walletState.connected) {\n"
        "      walletState.address = accounts[0];\n"
        "      updateWalletUI();\n"
        "      showToast('Account changed: ' + truncateAddress(accounts[0]), 'info');\n"
        "    }\n"
        "  });\n"
        "  window.ethereum.on('chainChanged', function(chainIdHex) {\n"
        "    var newChainId = parseInt(chainIdHex, 16);\n"
        "    walletState.chainId = newChainId;\n"
        "    if (newChainId !== GTRADE_CONFIG.chainId && walletState.connected) {\n"
        "      showToast('Wrong network. Please switch to Arbitrum for trading.', 'error', 8000);\n"
        "    } else if (newChainId === GTRADE_CONFIG.chainId && walletState.connected) {\n"
        "      handleWalletConnect();\n"
        "    }\n"
        "  });\n"
        "  // Silent reconnect on page load\n"
        "  window.ethereum.request({ method: 'eth_accounts' }).then(function(accounts) {\n"
        "    if (accounts.length > 0) handleWalletConnect();\n"
        "  }).catch(function() {});\n"
        "}\n"
        "\n"
        "/* ========== Trade Execution ========== */\n"
        "var tradePending = false;\n"
        "\n"
        "async function fetchUsdcBalance() {\n"
        "  if (!walletState.connected) return;\n"
        "  try {\n"
        "    var usdc = new ethers.Contract(GTRADE_CONFIG.usdcAddress, ERC20_ABI, walletState.provider);\n"
        "    var balance = await usdc.balanceOf(walletState.address);\n"
        "    var el = document.getElementById('trade-balance');\n"
        "    if (el) {\n"
        "      el.textContent = 'Balance: ' + parseFloat(ethers.formatUnits(balance, GTRADE_CONFIG.usdcDecimals)).toFixed(2) + ' USDC';\n"
        "    }\n"
        "  } catch (e) {\n"
        "    var el = document.getElementById('trade-balance');\n"
        "    if (el) el.textContent = '';\n"
        "  }\n"
        "}\n"
        "\n"
        "function validateTradeInputs() {\n"
        "  var collateral = parseFloat(document.getElementById('trade-collateral').value) || 0;\n"
        "  var leverage = parseInt(document.getElementById('trade-leverage').value) || 15;\n"
        "  var positionSize = collateral * leverage;\n"
        "  var errs = [];\n"
        "  if (collateral <= 0) errs.push('Enter collateral amount');\n"
        "  if (currentTradeAsset) {\n"
        "    var limits = getAssetLimits(currentTradeAsset);\n"
        "    if (leverage < limits.minLeverage || leverage > limits.maxLeverage) {\n"
        "      errs.push('Leverage must be ' + limits.minLeverage + 'x - ' + limits.maxLeverage + 'x for ' + currentTradeAsset);\n"
        "    }\n"
        "  }\n"
        "  if (positionSize < GTRADE_CONFIG.minPositionSizeUsd && collateral > 0) {\n"
        "    errs.push('Min position size: $' + GTRADE_CONFIG.minPositionSizeUsd.toLocaleString());\n"
        "  }\n"
        "  return errs;\n"
        "}\n"
        "\n"
        "function updatePositionSize() {\n"
        "  var collateral = parseFloat(document.getElementById('trade-collateral').value) || 0;\n"
        "  var leverage = parseInt(document.getElementById('trade-leverage').value) || 15;\n"
        "  var positionSize = collateral * leverage;\n"
        "  var el = document.getElementById('trade-position-size');\n"
        "  el.textContent = '$' + positionSize.toLocaleString(undefined, {\n"
        "    minimumFractionDigits: 2, maximumFractionDigits: 2\n"
        "  });\n"
        "  el.className = positionSize > 0 && positionSize < GTRADE_CONFIG.minPositionSizeUsd\n"
        "    ? 'trade-position-size warning' : 'trade-position-size';\n"
        "  var errs = validateTradeInputs();\n"
        "  document.getElementById('trade-collateral-error').textContent =\n"
        "    errs.length > 0 && collateral > 0 ? errs[0] : '';\n"
        "  document.getElementById('trade-submit-btn').disabled =\n"
        "    !walletState.connected || errs.length > 0 || tradePending;\n"
        "}\n"
        "\n"
        "async function submitTrade() {\n"
        "  if (tradePending || !walletState.connected) return;\n"
        "  var errs = validateTradeInputs();\n"
        "  if (errs.length > 0) { showToast(errs[0], 'error'); return; }\n"
        "\n"
        "  var asset = document.getElementById('trade-asset-label').textContent;\n"
        "  var isLong = document.getElementById('trade-dir-long').classList.contains('active');\n"
        "  var collateral = parseFloat(document.getElementById('trade-collateral').value);\n"
        "  var leverage = parseInt(document.getElementById('trade-leverage').value);\n"
        "  var slippage = parseFloat(document.getElementById('trade-slippage').value) || 1.0;\n"
        "\n"
        "  // Server-side validation\n"
        "  try {\n"
        "    var valResp = await fetch('/api/gtrade/validate-trade', {\n"
        "      method: 'POST',\n"
        "      headers: { 'Content-Type': 'application/json' },\n"
        "      body: JSON.stringify({ asset: asset, collateral: collateral, leverage: leverage, slippage: slippage })\n"
        "    });\n"
        "    var valData = await valResp.json();\n"
        "    if (!valData.valid) { showToast(valData.errors[0], 'error'); return; }\n"
        "    var pairIndex = valData.pair_index;\n"
        "  } catch (valErr) {\n"
        "    showToast('Validation failed: ' + valErr.message, 'error');\n"
        "    return;\n"
        "  }\n"
        "\n"
        "  var tpPercent = parseFloat(document.getElementById('trade-tp').value) || 0;\n"
        "  var slPercent = parseFloat(document.getElementById('trade-sl').value) || 0;\n"
        "  tradePending = true;\n"
        "  var submitBtn = document.getElementById('trade-submit-btn');\n"
        "  var originalText = submitBtn.textContent;\n"
        "  submitBtn.disabled = true;\n"
        "  submitBtn.textContent = 'Processing...';\n"
        "\n"
        "  try {\n"
        "    // Ensure correct network\n"
        "    var network = await walletState.provider.getNetwork();\n"
        "    if (Number(network.chainId) !== GTRADE_CONFIG.chainId) {\n"
        "      showToast('Switching to Arbitrum...', 'info', 3000);\n"
        "      await switchToArbitrum();\n"
        "      walletState.provider = new ethers.BrowserProvider(window.ethereum);\n"
        "      walletState.signer = await walletState.provider.getSigner();\n"
        "    }\n"
        "\n"
        "    // Check USDC approval\n"
        "    var collateralWei = ethers.parseUnits(collateral.toString(), GTRADE_CONFIG.usdcDecimals);\n"
        "    var usdc = new ethers.Contract(GTRADE_CONFIG.usdcAddress, ERC20_ABI, walletState.signer);\n"
        "    var allowance = await usdc.allowance(walletState.address, GTRADE_CONFIG.diamondAddress);\n"
        "    if (allowance < collateralWei) {\n"
        "      showToast('Approving USDC...', 'info', 10000);\n"
        "      submitBtn.textContent = 'Approving USDC...';\n"
        "      var approveTx = await usdc.approve(GTRADE_CONFIG.diamondAddress, ethers.MaxUint256);\n"
        "      await approveTx.wait();\n"
        "      showToast('USDC approved', 'success', 3000);\n"
        "    }\n"
        "\n"
        "    // Build trade struct\n"
        "    var currentPrice = currentAssets[asset] ? currentAssets[asset].current_price : 0;\n"
        "    var tpPrice = tpPercent > 0\n"
        "      ? BigInt(Math.round(currentPrice * (1 + (isLong ? tpPercent : -tpPercent) / 100) * 1e10))\n"
        "      : 0n;\n"
        "    var slPrice = slPercent > 0\n"
        "      ? BigInt(Math.round(currentPrice * (1 + (isLong ? -slPercent : slPercent) / 100) * 1e10))\n"
        "      : 0n;\n"
        "    var trade = {\n"
        "      user: walletState.address, index: 0, pairIndex: pairIndex,\n"
        "      leverage: leverage * 1000, long: isLong, isOpen: true,\n"
        "      collateralIndex: GTRADE_CONFIG.collateralIndex, tradeType: 0,\n"
        "      collateralAmount: collateralWei, openPrice: 0,\n"
        "      tp: tpPrice, sl: slPrice, __placeholder: 0\n"
        "    };\n"
        "\n"
        "    // Submit trade\n"
        "    showToast('Opening ' + (isLong ? 'long' : 'short') + ' ' + asset + '...', 'info', 15000);\n"
        "    submitBtn.textContent = 'Opening Trade...';\n"
        "    var diamond = new ethers.Contract(GTRADE_CONFIG.diamondAddress, DIAMOND_ABI, walletState.signer);\n"
        "    var tx = await diamond.openTrade(trade, Math.round(slippage * 1000), ethers.ZeroAddress);\n"
        "    showToast('Transaction submitted. Waiting for confirmation...', 'info', 20000);\n"
        "    var receipt = await tx.wait();\n"
        "    showToast('Trade opened! <a href=\"' + GTRADE_CONFIG.explorerUrl + '/tx/' + receipt.hash +\n"
        "      '\" target=\"_blank\" rel=\"noopener\">View on Arbiscan</a>', 'success', 10000);\n"
        "    fetchUsdcBalance();\n"
        "  } catch (err) {\n"
        "    var msg = err.shortMessage || err.message || 'Transaction failed';\n"
        "    if (err.code === 4001 || (err.info && err.info.error && err.info.error.code === 4001)) {\n"
        "      msg = 'Transaction rejected by user';\n"
        "    } else if (msg.toLowerCase().includes('insufficient')) {\n"
        "      msg = 'Insufficient funds (check USDC balance and ETH for gas)';\n"
        "    } else if (msg.toLowerCase().includes('market') || msg.toLowerCase().includes('closed')) {\n"
        "      msg = 'Market may be closed. Equity markets trade during US hours.';\n"
        "    }\n"
        "    msg += ' <a href=\"https://gains.trade/trading\" target=\"_blank\" rel=\"noopener\">Try on gTrade</a>';\n"
        "    showToast(msg, 'error', 8000);\n"
        "  } finally {\n"
        "    tradePending = false;\n"
        "    submitBtn.textContent = originalText;\n"
        "    updatePositionSize();\n"
        "  }\n"
        "}\n"
        "\n"
        "/* ========== Trade Panel UI ========== */\n"
        "var currentTradeAsset = null;\n"
        "\n"
        "function openTradePanel(asset) {\n"
        "  if (GTRADE_CONFIG.pairIndices[asset] === undefined) return;\n"
        "  currentTradeAsset = asset;\n"
        "  document.getElementById('trade-asset-label').textContent = asset;\n"
        "  document.getElementById('trade-panel').classList.add('visible');\n"
        "\n"
        "  // Set leverage slider limits based on asset group\n"
        "  var limits = getAssetLimits(asset);\n"
        "  var slider = document.getElementById('trade-leverage');\n"
        "  var minLev = Math.ceil(limits.minLeverage);\n"
        "  var maxLev = Math.floor(limits.maxLeverage);\n"
        "  slider.min = minLev;\n"
        "  slider.max = maxLev;\n"
        "  var defaultLev = Math.min(15, maxLev);\n"
        "  slider.value = defaultLev;\n"
        "  document.getElementById('trade-leverage-display').textContent = defaultLev + 'x';\n"
        "  document.getElementById('trade-leverage-hint').textContent = minLev + 'x - ' + maxLev + 'x';\n"
        "  document.getElementById('trade-lev-min').textContent = minLev + 'x';\n"
        "  document.getElementById('trade-lev-max').textContent = maxLev + 'x';\n"
        "\n"
        "  // Reset form fields\n"
        "  document.getElementById('trade-collateral').value = '';\n"
        "  document.getElementById('trade-tp').value = '';\n"
        "  document.getElementById('trade-sl').value = '';\n"
        "  document.getElementById('trade-slippage').value = '1.0';\n"
        "  document.getElementById('trade-collateral-error').textContent = '';\n"
        "\n"
        "  // Reset direction to Long\n"
        "  document.getElementById('trade-dir-long').className = 'trade-dir-btn active long';\n"
        "  document.getElementById('trade-dir-short').className = 'trade-dir-btn short';\n"
        "  var submitBtn = document.getElementById('trade-submit-btn');\n"
        "  submitBtn.className = 'trade-submit-btn long';\n"
        "  submitBtn.textContent = walletState.connected ? 'Open Long ' + asset : 'Connect Wallet';\n"
        "  submitBtn.disabled = true;\n"
        "\n"
        "  // Show form or connect overlay\n"
        "  if (walletState.connected) {\n"
        "    document.getElementById('trade-connect-overlay').style.display = 'none';\n"
        "    document.getElementById('trade-form-container').style.display = 'block';\n"
        "    fetchUsdcBalance();\n"
        "  } else {\n"
        "    document.getElementById('trade-connect-overlay').style.display = 'block';\n"
        "    document.getElementById('trade-form-container').style.display = 'none';\n"
        "  }\n"
        "  updatePositionSize();\n"
        "  document.getElementById('trade-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });\n"
        "}\n"
        "\n"
        "function closeTradePanel() {\n"
        "  document.getElementById('trade-panel').classList.remove('visible');\n"
        "  currentTradeAsset = null;\n"
        "}\n"
        "\n"
        "/* ========== Trade Panel Event Listeners ========== */\n"
        "document.querySelectorAll('.trade-dir-btn').forEach(function(btn) {\n"
        "  btn.addEventListener('click', function() {\n"
        "    document.querySelectorAll('.trade-dir-btn').forEach(function(b) {\n"
        "      b.className = 'trade-dir-btn ' + b.getAttribute('data-dir');\n"
        "    });\n"
        "    btn.classList.add('active');\n"
        "    var isLong = btn.getAttribute('data-dir') === 'long';\n"
        "    var sb = document.getElementById('trade-submit-btn');\n"
        "    sb.className = 'trade-submit-btn ' + (isLong ? 'long' : 'short');\n"
        "    sb.textContent = 'Open ' + (isLong ? 'Long' : 'Short') + ' ' +\n"
        "      document.getElementById('trade-asset-label').textContent;\n"
        "    updatePositionSize();\n"
        "  });\n"
        "});\n"
        "\n"
        "document.getElementById('trade-leverage').addEventListener('input', function(e) {\n"
        "  document.getElementById('trade-leverage-display').textContent = e.target.value + 'x';\n"
        "  updatePositionSize();\n"
        "});\n"
        "\n"
        "document.getElementById('trade-collateral').addEventListener('input', updatePositionSize);\n"
        "document.getElementById('trade-close-btn').addEventListener('click', closeTradePanel);\n"
        "document.getElementById('trade-submit-btn').addEventListener('click', submitTrade);\n"
        "document.getElementById('wallet-btn').addEventListener('click', handleWalletConnect);\n"
        "\n"
        "// Delegated click handler for trade buttons in table (survives refreshData rebuilds)\n"
        "document.getElementById('rank-tbody').addEventListener('click', function(e) {\n"
        "  var btn = e.target.closest('.trade-cell-btn');\n"
        "  if (btn) {\n"
        "    var asset = btn.getAttribute('data-asset');\n"
        "    openTradePanel(asset);\n"
        "  }\n"
        "});\n"
        "</script>\n"
        "</body>\n</html>"
    )
    return html


def create_app(client=None) -> Flask:
    """Create the Flask application with all routes."""
    if client is None:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            client = SynthClient()

    app = Flask(__name__)

    @app.route("/")
    def index():
        html = generate_dashboard_html(client)
        return Response(html, mimetype="text/html")

    @app.route("/api/data")
    def api_data():
        horizon = request.args.get("horizon", "24h")
        if horizon not in ("1h", "24h"):
            return jsonify({"error": "Invalid horizon. Use '1h' or '24h'."}), 400
        result = fetch_and_process(client, horizon)
        return jsonify({
            "traces": result["traces"],
            "table_rows": result["table_rows"],
            "insights": result["insights"],
            "assets": result["assets"],
            "benchmark": result["benchmark"],
            "horizon": horizon,
            "timestamp": result["timestamp"],
        })

    @app.route("/api/probability", methods=["POST"])
    def api_probability():
        body = request.get_json(silent=True) or {}
        asset = body.get("asset", "")
        target_price = body.get("target_price")
        horizon = body.get("horizon", "24h")

        if horizon not in ("1h", "24h"):
            return jsonify({"error": "Invalid horizon."}), 400

        valid_assets = get_assets_for_horizon(horizon)
        if asset not in valid_assets:
            return jsonify({"error": f"{asset} not available for {horizon} horizon."}), 400

        if target_price is None or not isinstance(target_price, (int, float)) or target_price <= 0:
            return jsonify({"error": "Invalid target_price. Must be a positive number."}), 400

        try:
            forecast = client.get_prediction_percentiles(asset, horizon=horizon)
            percentiles = forecast["forecast_future"]["percentiles"]
            current_price = forecast["current_price"]
            prob_below = calculate_target_probability(percentiles, target_price)
            return jsonify({
                "asset": asset,
                "target_price": target_price,
                "current_price": current_price,
                "horizon": horizon,
                "probability_below": round(prob_below, 4),
                "probability_above": round(100.0 - prob_below, 4),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/gtrade/config")
    def api_gtrade_config():
        return jsonify(GTRADE_CONFIG)

    @app.route("/api/gtrade/validate-trade", methods=["POST"])
    def api_gtrade_validate():
        body = request.get_json(silent=True) or {}
        errors = validate_trade_params(body)
        if errors:
            return jsonify({"valid": False, "errors": errors}), 400
        return jsonify({"valid": True, "pair_index": GTRADE_CONFIG["pair_indices"][body["asset"]]})

    return app


def main():
    """Start the Tide Chart dashboard server."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = SynthClient()

    app = create_app(client)
    port = int(os.environ.get("TIDE_CHART_PORT", 5000))

    print(f"Tide Chart running at http://localhost:{port}")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
