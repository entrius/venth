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
    fetch_all_data,
    calculate_metrics,
    add_relative_to_benchmark,
    rank_equities,
    get_normalized_series,
    calculate_target_probability,
    get_assets_for_horizon,
)
from gtrade import (
    get_contract_config,
    validate_trade_params,
    build_trade_summary,
    is_tradeable,
    resolve_pair_index,
    get_cached_trading_variables,
    fetch_open_trades,
    fetch_trade_history,
    get_pair_name_map,
    is_market_open,
    estimate_trade_fees,
    calculate_liquidation_price,
    GTRADE_PAIRS,
    LIQ_THRESHOLD_PCT,
    GROUP_FEES,
)

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
        trade_btn = ""
        if is_tradeable(asset):
            sq = "'"
            trade_btn = f'<button class="trade-row-btn" onclick="selectTradeAsset({sq}{asset}{sq})">Trade</button>'

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
            <td>{trade_btn}</td>
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
        "  @media (max-width: 768px) { .insights { grid-template-columns: 1fr; }\n"
        "    .title { font-size: 22px; } .dashboard { padding: 16px 12px 32px; }\n"
        "    #cone-chart { height: 320px; } .table-container { overflow-x: auto; }\n"
        "    .controls { flex-direction: column; align-items: flex-start; } }\n"
        "\n"
        "  /* Wallet & Trading */\n"
        "  .wallet-section { display: flex; align-items: center; gap: 8px; margin-left: auto; }\n"
        "  .wallet-btn { font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 500;\n"
        "    padding: 6px 16px; background: var(--bg-card); color: var(--text-secondary);\n"
        "    border: 1px solid var(--border); border-radius: 6px; cursor: pointer; transition: all 0.2s; }\n"
        "  .wallet-btn:hover { background: var(--bg-card-hover); color: var(--accent); border-color: rgba(232,212,77,0.3); }\n"
        "  .wallet-btn.connected { background: rgba(52,211,153,0.1); color: var(--positive); border-color: rgba(52,211,153,0.3); }\n"
        "  .chain-badge { font-family: 'IBM Plex Mono', monospace; font-size: 9px; padding: 2px 8px;\n"
        "    border-radius: 4px; letter-spacing: 0.5px; display: none; }\n"
        "  .chain-badge.arb-ok { background: rgba(52,211,153,0.1); color: var(--positive); border: 1px solid rgba(52,211,153,0.2); }\n"
        "  .chain-badge.arb-wrong { background: rgba(240,96,112,0.1); color: var(--negative); border: 1px solid rgba(240,96,112,0.2); }\n"
        "  .wallet-info { display: none; align-items: center; gap: 8px;\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--text-muted); }\n"
        "  .trade-container { background: var(--bg-card); border: 1px solid var(--border);\n"
        "    border-radius: 10px; padding: 20px; margin-bottom: 20px; display: none; }\n"
        "  .trade-form { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }\n"
        "  .trade-field { display: flex; flex-direction: column; gap: 4px; }\n"
        "  .trade-field label { font-family: 'IBM Plex Mono', monospace; font-size: 10px;\n"
        "    text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); }\n"
        "  .trade-field select, .trade-field input { font-family: 'IBM Plex Mono', monospace; font-size: 12px;\n"
        "    padding: 8px 12px; background: var(--bg-deep); border: 1px solid var(--border);\n"
        "    border-radius: 6px; color: var(--text-primary); outline: none; transition: border-color 0.2s; }\n"
        "  .trade-field select:focus, .trade-field input:focus { border-color: rgba(232,212,77,0.4); }\n"
        "  .trade-exec-btn { font-family: 'IBM Plex Mono', monospace; font-size: 13px; font-weight: 600;\n"
        "    padding: 12px; width: 100%; background: rgba(52,211,153,0.2); color: var(--positive);\n"
        "    border: 1px solid rgba(52,211,153,0.3); border-radius: 6px; cursor: pointer;\n"
        "    transition: all 0.2s; text-transform: uppercase; letter-spacing: 1px; margin-top: 8px; }\n"
        "  .trade-exec-btn:hover:not(:disabled) { background: rgba(52,211,153,0.3); }\n"
        "  .trade-exec-btn:disabled { opacity: 0.4; cursor: not-allowed; }\n"
        "  .trade-exec-btn.long { background: rgba(52,211,153,0.2); color: var(--positive); }\n"
        "  .trade-exec-btn.short { background: rgba(240,96,112,0.2); color: var(--negative); }\n"
        "  .trade-exec-btn.long:hover { background: rgba(52,211,153,0.3); }\n"
        "  .trade-exec-btn.short:hover { background: rgba(240,96,112,0.3); }\n"
        "  .trade-preview { margin-top: 14px; padding: 12px 16px; background: var(--bg-deep);\n"
        "    border: 1px solid var(--border); border-radius: 6px; display: none; }\n"
        "  .preview-row { display: flex; justify-content: space-between; padding: 4px 0;\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-secondary); }\n"
        "  .preview-row span:last-child { color: var(--text-primary); }\n"
        "  .trade-status { margin-top: 10px; padding: 10px 14px; border-radius: 6px;\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 11px; display: none; }\n"
        "  .trade-status.success { background: rgba(52,211,153,0.1); color: var(--positive); border: 1px solid rgba(52,211,153,0.2); }\n"
        "  .trade-status.error { background: rgba(240,96,112,0.1); color: var(--negative); border: 1px solid rgba(240,96,112,0.2); }\n"
        "  .trade-fallback { display: none; margin-top: 8px; font-size: 11px;\n"
        "    font-family: 'IBM Plex Mono', monospace; color: var(--text-muted); }\n"
        "  .trade-fallback a { color: var(--accent); text-decoration: underline; }\n"
        "  .trade-row-btn { background: rgba(52,211,153,0.12); color: var(--positive); border: 1px solid rgba(52,211,153,0.25);\n"
        "    border-radius: 4px; padding: 3px 10px; font-family: 'IBM Plex Mono', monospace; font-size: 10px;\n"
        "    cursor: pointer; transition: background 0.15s; }\n"
        "  .trade-row-btn:hover { background: rgba(52,211,153,0.25); }\n"
        "  .trade-field-row { display: flex; gap: 10px; }\n"
        "  .trade-field-row .trade-field { flex: 1; }\n"
        "  .open-trades-section { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); }\n"
        "  .open-trades-header { font-family: 'IBM Plex Mono', monospace; font-size: 10px;\n"
        "    text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin-bottom: 8px; }\n"
        "  .open-trade-row { display: flex; align-items: center; gap: 8px; padding: 8px 0;\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-secondary);\n"
        "    border-bottom: 1px solid rgba(30,42,64,0.5); }\n"
        "  .open-trade-row:last-child { border-bottom: none; }\n"
        "  .trade-row-info { flex: 1; min-width: 0; }\n"
        "  .trade-row-main { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }\n"
        "  .trade-row-pnl { margin-top: 3px; }\n"
        "  .trade-pnl { font-weight: 600; font-size: 11px; }\n"
        "  .trade-pnl.positive { color: var(--positive); }\n"
        "  .trade-pnl.negative { color: var(--negative); }\n"
        "  .history-badge { font-size: 9px; font-weight: 600; padding: 2px 6px; border-radius: 3px;\n"
        "    background: rgba(100,116,139,0.2); color: var(--text-muted); border: 1px solid rgba(100,116,139,0.3);\n"
        "    white-space: nowrap; flex-shrink: 0; }\n"
        "  .history-badge.liq-badge { background: rgba(240,96,112,0.15); color: var(--negative);\n"
        "    border-color: rgba(240,96,112,0.3); }\n"
        "  .history-row { opacity: 0.8; }\n"
        "  .close-trade-btn { background: rgba(239,68,68,0.15); color: #ef4444; border: 1px solid rgba(239,68,68,0.3);\n"
        "    border-radius: 4px; padding: 2px 6px; font-size: 10px; cursor: pointer;\n"
        "    font-family: 'IBM Plex Mono', monospace; transition: all 0.2s; }\n"
        "  .close-trade-btn:hover { background: rgba(239,68,68,0.3); border-color: #ef4444; }\n"
        "  .no-trades { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-muted); }\n"
        "  .manage-btn { background: rgba(232,212,77,0.12); color: var(--accent); border: 1px solid rgba(232,212,77,0.25);\n"
        "    border-radius: 4px; padding: 2px 8px; font-family: 'IBM Plex Mono', monospace; font-size: 9px;\n"
        "    cursor: pointer; transition: all 0.2s; letter-spacing: 0.5px; }\n"
        "  .manage-btn:hover { background: rgba(232,212,77,0.25); }\n"
        "  .manage-panel { display: none; width: 100%; padding: 10px 0 6px; margin-top: 6px;\n"
        "    border-top: 1px dashed rgba(30,42,64,0.8); }\n"
        "  .manage-panel.open { display: block; }\n"
        "  .manage-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }\n"
        "  .manage-row label { font-family: 'IBM Plex Mono', monospace; font-size: 9px; color: var(--text-muted);\n"
        "    text-transform: uppercase; letter-spacing: 0.5px; min-width: 70px; }\n"
        "  .manage-row input { font-family: 'IBM Plex Mono', monospace; font-size: 11px; padding: 4px 8px;\n"
        "    background: var(--bg-deep); border: 1px solid var(--border); border-radius: 4px;\n"
        "    color: var(--text-primary); outline: none; width: 100px; }\n"
        "  .manage-row input:focus { border-color: rgba(232,212,77,0.4); }\n"
        "  .manage-action-btn { font-family: 'IBM Plex Mono', monospace; font-size: 9px; padding: 4px 10px;\n"
        "    border-radius: 4px; cursor: pointer; transition: all 0.15s; border: 1px solid; }\n"
        "  .manage-action-btn.update { background: rgba(52,211,153,0.12); color: var(--positive);\n"
        "    border-color: rgba(52,211,153,0.25); }\n"
        "  .manage-action-btn.update:hover { background: rgba(52,211,153,0.25); }\n"
        "  .manage-action-btn.remove { background: rgba(240,96,112,0.1); color: var(--negative);\n"
        "    border-color: rgba(240,96,112,0.2); }\n"
        "  .manage-action-btn.remove:hover { background: rgba(240,96,112,0.25); }\n"
        "  .manage-action-btn.decrease { background: rgba(232,212,77,0.1); color: var(--accent);\n"
        "    border-color: rgba(232,212,77,0.25); }\n"
        "  .manage-action-btn.decrease:hover { background: rgba(232,212,77,0.2); }\n"
        "  .liq-price { font-size: 10px; color: var(--negative); opacity: 0.8; }\n"
        "  .fee-estimate { margin-top: 6px; padding: 8px 12px; background: rgba(232,212,77,0.04);\n"
        "    border: 1px solid rgba(232,212,77,0.1); border-radius: 4px;\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 10px; }\n"
        "  .fee-estimate .fee-row { display: flex; justify-content: space-between;\n"
        "    padding: 2px 0; color: var(--text-secondary); }\n"
        "  .fee-estimate .fee-row span:last-child { color: var(--text-primary); }\n"
        "  .fee-estimate .fee-total { border-top: 1px solid rgba(232,212,77,0.15);\n"
        "    margin-top: 2px; padding-top: 4px; color: var(--accent); font-weight: 500; }\n"
        "  .market-warning { margin-top: 6px; padding: 8px 12px; background: rgba(240,96,112,0.08);\n"
        "    border: 1px solid rgba(240,96,112,0.2); border-radius: 4px;\n"
        "    font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--negative); }\n"
        "  .trade-pos-size { font-family: 'IBM Plex Mono', monospace; font-size: 16px;\n"
        "    font-weight: 600; color: var(--text-primary); text-align: center;\n"
        "    padding: 10px; background: var(--bg-deep); border-radius: 6px;\n"
        "    border: 1px solid var(--border); }\n"
        "  .trade-pos-size.warning { color: var(--negative); }\n"
        "  .toast-container { position: fixed; bottom: 20px; right: 20px; z-index: 1000;\n"
        "    display: flex; flex-direction: column-reverse; gap: 8px; max-width: 380px; }\n"
        "  .toast { font-family: 'IBM Plex Sans', sans-serif; font-size: 12px;\n"
        "    padding: 12px 16px; border-radius: 8px; background: var(--bg-card);\n"
        "    border: 1px solid var(--border); color: var(--text-primary);\n"
        "    box-shadow: 0 8px 24px rgba(0,0,0,0.4); animation: toastIn 0.3s ease;\n"
        "    display: flex; align-items: flex-start; gap: 8px; word-break: break-word; }\n"
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
        "  @keyframes toastIn { from { opacity: 0; transform: translateY(10px); }\n"
        "    to { opacity: 1; transform: translateY(0); } }\n"
        "</style>\n</head>\n<body>\n"
        '<div class="dashboard">\n'
        "\n"
        '  <div class="header">\n'
        '    <div class="header-top">\n'
        '      <h1 class="title">Tide Chart</h1>\n'
        f'      <span class="badge" id="horizon-badge">{horizon_label}</span>\n'
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
        '    <div class="wallet-section">\n'
        '      <span class="chain-badge" id="chain-badge"></span>\n'
        '      <div class="wallet-info" id="wallet-info">\n'
        '        <span id="usdc-balance">0</span> USDC\n'
        '      </div>\n'
        '      <button class="wallet-btn" id="wallet-btn" onclick="connectWallet()">Connect Wallet</button>\n'
        '    </div>\n'
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
        '  <div class="trade-container" id="trade-form-section">\n'
        '    <div class="section-header">\n'
        '      <span class="section-title">Trade on gTrade</span>\n'
        '      <span class="section-line"></span>\n'
        '    </div>\n'
        '    <div class="trade-form">\n'
        '      <div class="trade-field">\n'
        '        <label for="trade-asset">Pair</label>\n'
        '        <select id="trade-asset" onchange="updateTradePreview()"></select>\n'
        '      </div>\n'
        '      <div class="trade-field">\n'
        '        <label for="trade-direction">Direction</label>\n'
        '        <select id="trade-direction" onchange="updateTradePreview()">\n'
        '          <option value="long">Long</option>\n'
        '          <option value="short">Short</option>\n'
        '        </select>\n'
        '      </div>\n'
        '      <div class="trade-field">\n'
        '        <label for="trade-leverage">Leverage</label>\n'
        '        <input type="number" id="trade-leverage" min="2" max="150" value="10" step="1" oninput="updateTradePreview()">\n'
        '      </div>\n'
        '      <div class="trade-field">\n'
        '        <label for="trade-collateral">Collateral (USDC)</label>\n'
        '        <input type="number" id="trade-collateral" min="5" step="1" placeholder="e.g. 100" oninput="updateTradePreview()">\n'
        '      </div>\n'
        '      <div class="trade-field">\n'
        '        <label for="trade-tp">Take Profit (%)</label>\n'
        '        <input type="number" id="trade-tp" min="0" max="900" step="1" placeholder="e.g. 50" oninput="updateTradePreview()">\n'
        '      </div>\n'
        '      <div class="trade-field">\n'
        '        <label for="trade-sl">Stop Loss (%)</label>\n'
        '        <input type="number" id="trade-sl" min="0" max="90" step="1" placeholder="e.g. 25" oninput="updateTradePreview()">\n'
        '      </div>\n'
        '      <div class="trade-field">\n'
        '        <label for="trade-slippage">Max Slippage (%)</label>\n'
        '        <input type="number" id="trade-slippage" min="0.1" max="5" step="0.1" value="1.5" oninput="updateTradePreview()">\n'
        '      </div>\n'
        '      <div class="trade-field" style="width:100%">\n'
        '        <label>Position Size</label>\n'
        '        <div class="trade-pos-size" id="trade-pos-size">$0.00</div>\n'
        '      </div>\n'
        '      <button class="trade-exec-btn" id="trade-exec-btn" onclick="executeTrade()" disabled>Connect Wallet</button>\n'
        '    </div>\n'
        '    <div class="trade-preview" id="trade-preview"></div>\n'
        '    <div class="trade-status" id="trade-status"></div>\n'
        '    <div class="trade-fallback" id="trade-fallback">\n'
        '      <a href="#" target="_blank" rel="noopener">Complete trade on gTrade \u2192</a>\n'
        '    </div>\n'
        '    <div class="open-trades-section">\n'
        '      <div class="open-trades-header">Open Positions</div>\n'
        '      <div id="open-trades-list"><div class="no-trades">Connect wallet to view positions</div></div>\n'
        '    </div>\n'
        '    <div class="open-trades-section">\n'
        '      <div class="open-trades-header">Trade History</div>\n'
        '      <div id="trade-history-list"><div class="no-trades">Connect wallet to view history</div></div>\n'
        '    </div>\n'
        '  </div>\n'
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
        '          <th></th>\n'
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
        "</script>\n"
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/ethers/6.7.0/ethers.umd.min.js"'
        ' crossorigin="anonymous"></script>\n'
        '<script src="/static/trading.js"></script>\n'
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

    @app.after_request
    def add_no_cache_headers(response):
        if request.path.startswith("/api/") or request.path.endswith(".js"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

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
    def gtrade_config_route():
        return jsonify(get_contract_config())

    @app.route("/api/gtrade/market-status")
    def gtrade_market_status():
        open_now, reason = is_market_open()
        return jsonify({"open": open_now, "reason": reason})

    @app.route("/api/gtrade/estimate-fees", methods=["POST"])
    def gtrade_estimate_fees():
        body = request.get_json(silent=True) or {}
        asset = body.get("asset", "")
        collateral_usd = body.get("collateral_usd", 0)
        leverage = body.get("leverage", 0)
        if not asset or collateral_usd <= 0 or leverage <= 0:
            return jsonify({"error": "Missing or invalid parameters"}), 400
        fees = estimate_trade_fees(asset, collateral_usd, leverage)
        return jsonify(fees)

    @app.route("/api/gtrade/validate-trade", methods=["POST"])
    def gtrade_validate_trade():
        body = request.get_json(silent=True) or {}
        asset = body.get("asset", "")
        direction = body.get("direction", "")
        leverage = body.get("leverage", 0)
        collateral_usd = body.get("collateral_usd", 0)

        valid, error = validate_trade_params(asset, direction, leverage, collateral_usd)
        if not valid:
            return jsonify({"valid": False, "error": error}), 400

        # Block stock trades when market is closed
        pair = GTRADE_PAIRS.get(asset, {})
        if pair.get("group") == "stocks":
            mkt_open, mkt_reason = is_market_open()
            if not mkt_open:
                return jsonify({"valid": False, "error": mkt_reason}), 400

        current_price = 0.0
        try:
            forecast = client.get_prediction_percentiles(asset, horizon="24h")
            current_price = forecast["current_price"]
        except Exception:
            pass

        fees = estimate_trade_fees(asset, collateral_usd, leverage)
        summary = build_trade_summary(asset, current_price, direction, leverage, collateral_usd)
        summary["fees"] = fees
        return jsonify({"valid": True, "summary": summary})

    @app.route("/api/gtrade/resolve-pair")
    def gtrade_resolve_pair():
        asset = request.args.get("asset", "")
        if not is_tradeable(asset):
            return jsonify({"error": f"{asset} not tradeable", "pair_index": None}), 400

        try:
            trading_vars = get_cached_trading_variables()
        except Exception:
            trading_vars = None
        pair_index = resolve_pair_index(asset, trading_vars, skip_fetch=True)
        # Include fresh price for the frontend openTrade struct
        current_price = None
        try:
            summary = client.get_asset_summary(asset)
            if summary and "current_price" in summary:
                current_price = summary["current_price"]
        except Exception:
            pass
        return jsonify({"asset": asset, "pair_index": pair_index, "current_price": current_price})

    @app.route("/api/gtrade/open-trades")
    def gtrade_open_trades():
        address = request.args.get("address", "").strip()
        if not address or len(address) != 42 or not address.startswith("0x"):
            return jsonify({"error": "Invalid Ethereum address", "trades": []}), 400
        trades = fetch_open_trades(address)
        pair_names = get_pair_name_map()
        return jsonify({"address": address, "trades": trades, "pair_names": pair_names})

    @app.route("/api/gtrade/trade-history")
    def gtrade_trade_history():
        address = request.args.get("address", "").strip()
        if not address or len(address) != 42 or not address.startswith("0x"):
            return jsonify({"error": "Invalid Ethereum address", "history": []}), 400
        history = fetch_trade_history(address)
        pair_names = get_pair_name_map()
        return jsonify({"address": address, "history": history, "pair_names": pair_names})

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
