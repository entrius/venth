"""
Tide Chart - Equity Forecast Comparison Dashboard.

Generates an interactive HTML dashboard comparing 24h probability cones
for 5 equities (SPY, NVDA, TSLA, AAPL, GOOGL) using Synth API data.
Opens the dashboard in the default browser.
"""

import sys
import os
import json
import webbrowser
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from synth_client import SynthClient
from chart import (
    fetch_all_data,
    calculate_metrics,
    add_relative_to_spy,
    rank_equities,
    get_normalized_series,
)

EQUITY_COLORS = {
    "SPY": {"primary": "#e8d44d", "rgb": "232,212,77"},
    "NVDA": {"primary": "#3db8e8", "rgb": "61,184,232"},
    "TSLA": {"primary": "#e85a6e", "rgb": "232,90,110"},
    "AAPL": {"primary": "#9b6de8", "rgb": "155,109,232"},
    "GOOGL": {"primary": "#4dc87a", "rgb": "77,200,122"},
}

EQUITY_LABELS = {
    "SPY": "S&P 500",
    "NVDA": "NVIDIA",
    "TSLA": "Tesla",
    "AAPL": "Apple",
    "GOOGL": "Alphabet",
}


def generate_dashboard_html(normalized_series, metrics, ranked):
    """Generate a self-contained HTML dashboard.

    Args:
        normalized_series: {asset: list of normalized percentile dicts} (289 steps)
        metrics: {asset: {median_move, upside, downside, skew, range_pct,
                          volatility, current_price, relative_median, relative_skew}}
        ranked: List of (asset, metrics_dict) sorted by median_move.

    Returns:
        str: Complete HTML document string.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build Plotly traces for probability cones
    traces = []
    for asset in ["SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]:
        series = normalized_series[asset]
        steps = list(range(len(series)))
        color = EQUITY_COLORS[asset]
        label = EQUITY_LABELS[asset]

        upper = [s.get("0.95", 0) for s in series]
        lower = [s.get("0.05", 0) for s in series]
        median = [s.get("0.5", 0) for s in series]

        # Upper bound (invisible line for fill)
        traces.append({
            "x": steps,
            "y": upper,
            "type": "scatter",
            "mode": "lines",
            "line": {"width": 0},
            "showlegend": False,
            "name": f"{asset} 95th",
            "hoverinfo": "skip",
        })

        # Lower bound with fill to upper
        traces.append({
            "x": steps,
            "y": lower,
            "type": "scatter",
            "mode": "lines",
            "line": {"width": 0},
            "fill": "tonexty",
            "fillcolor": f"rgba({color['rgb']},0.12)",
            "showlegend": False,
            "name": f"{asset} 5th",
            "hoverinfo": "skip",
        })

        # Median line
        traces.append({
            "x": steps,
            "y": median,
            "type": "scatter",
            "mode": "lines",
            "line": {"color": color["primary"], "width": 2},
            "name": f"{label} ({asset})",
            "hovertemplate": (
                f"<b>{label}</b><br>"
                "Step %{x}<br>"
                "Median: %{y:.3f}%<extra></extra>"
            ),
        })

    traces_json = json.dumps(traces)

    # Build rank table rows
    table_rows = ""
    for rank_idx, (asset, m) in enumerate(ranked, 1):
        color = EQUITY_COLORS[asset]["primary"]
        label = EQUITY_LABELS[asset]

        def fmt_val(val, suffix="%"):
            sign = "+" if val > 0 else ""
            css_class = "positive" if val > 0 else "negative" if val < 0 else "neutral"
            return f'<span class="{css_class}">{sign}{val:.3f}{suffix}</span>'

        rel_median = "-" if asset == "SPY" else fmt_val(m["relative_median"])
        rel_skew = "-" if asset == "SPY" else fmt_val(m["relative_skew"])

        table_rows += f"""
        <tr data-median="{m['median_move']}" data-vol="{m['volatility']}" data-skew="{m['skew']}" data-range="{m['range_pct']}" data-rel-median="{m.get('relative_median', 0)}" data-rel-skew="{m.get('relative_skew', 0)}">
            <td class="rank-cell">{rank_idx}</td>
            <td class="asset-cell">
                <span class="asset-dot" style="background:{color}"></span>
                <span class="asset-name">{label}</span>
                <span class="asset-ticker">{asset}</span>
            </td>
            <td class="price-cell">${m['current_price']:,.2f}</td>
            <td>{fmt_val(m['median_move'])}</td>
            <td>{m['volatility']:.2f}</td>
            <td>{fmt_val(m['skew'])}</td>
            <td>{m['range_pct']:.3f}%</td>
            <td>{rel_median}</td>
            <td>{rel_skew}</td>
        </tr>"""

    # Build directional alignment indicator
    directions = [m["median_move"] for m in metrics.values()]
    alignment_text = "All Bullish" if all(d > 0 for d in directions) else \
                     "All Bearish" if all(d < 0 for d in directions) else "Mixed"
    alignment_class = "bullish" if all(d > 0 for d in directions) else \
                      "bearish" if all(d < 0 for d in directions) else "mixed"

    # Widest range equity
    widest = max(metrics.items(), key=lambda x: x[1]["range_pct"])
    widest_name = f"{EQUITY_LABELS[widest[0]]} ({widest[1]['range_pct']:.2f}%)"

    # Most skewed equity
    most_skewed = max(metrics.items(), key=lambda x: abs(x[1]["skew"]))
    skew_dir = "upside" if most_skewed[1]["skew"] > 0 else "downside"
    skew_name = f"{EQUITY_LABELS[most_skewed[0]]} ({skew_dir})"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tide Chart - Equity Forecast Comparison</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg-deep: #0a0e17;
    --bg-card: #111827;
    --bg-card-hover: #1a2236;
    --border: #1e2a40;
    --text-primary: #f0f2f5;
    --text-secondary: #94a3b8;
    --text-muted: #5a6a82;
    --positive: #34d399;
    --negative: #f06070;
    --accent: #e8d44d;
  }}

  body {{
    font-family: 'IBM Plex Sans', sans-serif;
    background: var(--bg-deep);
    background-image: radial-gradient(ellipse at 50% 0%, rgba(30,42,64,0.5) 0%, transparent 60%);
    color: var(--text-primary);
    min-height: 100vh;
    overflow-x: hidden;
  }}

  /* Subtle grid background */
  body::before {{
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(232,212,77,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(232,212,77,0.03) 1px, transparent 1px);
    background-size: 60px 60px;
    pointer-events: none;
    z-index: 0;
  }}

  .dashboard {{
    position: relative;
    z-index: 1;
    max-width: 1280px;
    margin: 0 auto;
    padding: 32px 24px 48px;
  }}

  /* Header */
  .header {{
    margin-bottom: 28px;
  }}

  .header-top {{
    display: flex;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 8px;
  }}

  .title {{
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.5px;
    background: linear-gradient(135deg, #e8d44d 0%, #f0f2f5 50%, #94a3b8 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }}

  .badge {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--accent);
    border: 1px solid rgba(232,212,77,0.3);
    padding: 3px 8px;
    border-radius: 4px;
    margin-bottom: 4px;
  }}

  .subtitle {{
    font-size: 13px;
    color: var(--text-muted);
    font-weight: 300;
  }}

  .subtitle span {{
    color: var(--text-secondary);
  }}

  /* Insight cards */
  .insights {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 20px;
  }}

  .insight-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-left: 2px solid rgba(232,212,77,0.4);
    border-radius: 8px;
    padding: 14px 16px;
    transition: all 0.25s ease;
  }}

  .insight-card:hover {{
    background: var(--bg-card-hover);
    border-left-color: var(--accent);
    box-shadow: 0 0 20px rgba(232,212,77,0.06);
  }}

  .insight-label {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: var(--text-muted);
    margin-bottom: 6px;
    font-weight: 500;
  }}

  .insight-value {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 15px;
    font-weight: 500;
  }}

  .insight-value.bullish {{ color: var(--positive); }}
  .insight-value.bearish {{ color: var(--negative); }}
  .insight-value.mixed {{ color: var(--text-secondary); }}

  /* Chart section */
  .chart-container {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 20px;
    transition: box-shadow 0.3s ease;
  }}

  .chart-container:hover {{
    box-shadow: 0 0 30px rgba(232,212,77,0.04);
  }}

  .section-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 16px;
  }}

  .section-title {{
    font-size: 14px;
    font-weight: 500;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.8px;
  }}

  .section-line {{
    flex: 1;
    height: 1px;
    background: var(--border);
  }}

  #cone-chart {{
    width: 100%;
    height: 420px;
  }}

  .chart-hint {{
    font-size: 10px;
    color: var(--text-muted);
    text-align: right;
    margin-top: 6px;
    font-family: 'IBM Plex Mono', monospace;
    letter-spacing: 0.3px;
  }}

  /* Plotly modebar dark theme override */
  .chart-container .modebar {{
    background: transparent !important;
  }}

  .chart-container .modebar-btn path {{
    fill: var(--text-muted) !important;
  }}

  .chart-container .modebar-btn:hover path {{
    fill: var(--text-secondary) !important;
  }}

  .chart-container .modebar-btn.active path {{
    fill: var(--accent) !important;
  }}

  /* Table section */
  .table-container {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    overflow-x: auto;
    transition: box-shadow 0.3s ease;
  }}

  .table-container:hover {{
    box-shadow: 0 0 30px rgba(232,212,77,0.04);
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}

  thead th {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-muted);
    text-align: left;
    padding: 0 8px 12px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }}

  thead th:first-child {{ padding-left: 16px; }}

  /* Visual separator before "vs SPY" group */
  thead th:nth-child(8),
  tbody td:nth-child(8) {{
    border-left: 1px solid var(--border);
    padding-left: 12px;
  }}

  tbody tr {{
    transition: background 0.15s;
  }}

  tbody tr:hover {{
    background: rgba(232,212,77,0.04);
  }}

  tbody td {{
    padding: 12px 8px;
    border-bottom: 1px solid rgba(30,42,64,0.7);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    white-space: nowrap;
  }}

  tbody td:first-child {{ padding-left: 16px; }}

  .rank-cell {{
    color: var(--text-muted);
    font-size: 11px;
    width: 32px;
  }}

  .asset-cell {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: 'IBM Plex Sans', sans-serif !important;
  }}

  .asset-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }}

  .asset-name {{
    font-weight: 500;
    font-size: 13px;
    color: var(--text-primary);
  }}

  .asset-ticker {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: var(--text-muted);
    background: rgba(255,255,255,0.06);
    padding: 2px 6px;
    border-radius: 3px;
  }}

  .price-cell {{
    color: var(--text-secondary);
  }}

  .sortable {{
    cursor: pointer;
    user-select: none;
    position: relative;
    padding-right: 18px !important;
  }}

  .sortable::after {{
    content: '\u2195';
    position: absolute;
    right: 2px;
    opacity: 0.3;
    font-size: 9px;
  }}

  .sortable.asc::after {{
    content: '\u2191';
    opacity: 0.8;
  }}

  .sortable.desc::after {{
    content: '\u2193';
    opacity: 0.8;
  }}

  .sortable:hover {{
    color: var(--accent);
  }}

  .positive {{ color: var(--positive); }}
  .negative {{ color: var(--negative); }}
  .neutral {{ color: var(--text-secondary); }}

  /* Footer */
  .footer {{
    margin-top: 24px;
    text-align: center;
    font-size: 11px;
    color: var(--text-muted);
  }}

  .footer a {{
    color: var(--accent);
    text-decoration: none;
    transition: color 0.15s;
  }}

  .footer a:hover {{
    color: var(--text-primary);
  }}

  @media (max-width: 768px) {{
    .insights {{ grid-template-columns: 1fr; }}
    .title {{ font-size: 22px; }}
    .dashboard {{ padding: 16px 12px 32px; }}
    #cone-chart {{ height: 320px; }}
  }}
</style>
</head>
<body>
<div class="dashboard">

  <div class="header">
    <div class="header-top">
      <h1 class="title">Tide Chart</h1>
      <span class="badge">24h Forecast</span>
    </div>
    <p class="subtitle">Equity probability cone comparison &mdash; <span>{timestamp}</span></p>
  </div>

  <div class="insights">
    <div class="insight-card">
      <div class="insight-label">Directional Alignment</div>
      <div class="insight-value {alignment_class}">{alignment_text}</div>
    </div>
    <div class="insight-card">
      <div class="insight-label">Widest Range</div>
      <div class="insight-value">{widest_name}</div>
    </div>
    <div class="insight-card">
      <div class="insight-label">Most Asymmetric</div>
      <div class="insight-value">{skew_name}</div>
    </div>
  </div>

  <div class="chart-container">
    <div class="section-header">
      <span class="section-title">Probability Cones (5th - 95th Percentile)</span>
      <span class="section-line"></span>
    </div>
    <div id="cone-chart"></div>
    <div class="chart-hint">scroll to zoom &middot; drag to pan &middot; double-click to reset</div>
  </div>

  <div class="table-container">
    <div class="section-header">
      <span class="section-title">Equity Rankings</span>
      <span class="section-line"></span>
    </div>
    <table id="rank-table">
      <thead>
        <tr>
          <th>#</th>
          <th>Asset</th>
          <th>Price</th>
          <th class="sortable" data-sort="median" tabindex="0" role="columnheader" aria-sort="none">Median Move</th>
          <th class="sortable" data-sort="vol" tabindex="0" role="columnheader" aria-sort="none">Volatility</th>
          <th class="sortable" data-sort="skew" tabindex="0" role="columnheader" aria-sort="none">Skew</th>
          <th class="sortable" data-sort="range" tabindex="0" role="columnheader" aria-sort="none">Range</th>
          <th class="sortable" data-sort="rel-median" tabindex="0" role="columnheader" aria-sort="none">vs SPY</th>
          <th class="sortable" data-sort="rel-skew" tabindex="0" role="columnheader" aria-sort="none">Skew vs SPY</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>

  <div class="footer">
    Data from <a href="https://synthdata.co" target="_blank" rel="noopener noreferrer">Synth API</a>
    &middot; Built with Venth
  </div>

</div>

<script>
  var traces = {traces_json};
  var layout = {{
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: {{
      family: 'IBM Plex Sans, sans-serif',
      color: '#94a3b8',
      size: 11
    }},
    margin: {{ t: 8, r: 16, b: 40, l: 48 }},
    xaxis: {{
      title: {{ text: 'Time Steps (5-min intervals)', font: {{ size: 10 }} }},
      gridcolor: 'rgba(30,42,64,0.7)',
      zerolinecolor: 'rgba(30,42,64,0.9)',
      tickfont: {{ family: 'IBM Plex Mono, monospace', size: 10 }}
    }},
    yaxis: {{
      title: {{ text: '% Change from Current', font: {{ size: 10 }} }},
      gridcolor: 'rgba(30,42,64,0.7)',
      zerolinecolor: 'rgba(232,212,77,0.12)',
      zerolinewidth: 1,
      ticksuffix: '%',
      tickfont: {{ family: 'IBM Plex Mono, monospace', size: 10 }}
    }},
    legend: {{
      orientation: 'h',
      yanchor: 'bottom',
      y: 1.02,
      xanchor: 'left',
      x: 0,
      font: {{ size: 11 }},
      itemwidth: 30
    }},
    hovermode: 'x unified',
    hoverlabel: {{
      bgcolor: '#111827',
      bordercolor: '#1e2a40',
      font: {{ family: 'IBM Plex Mono, monospace', size: 11, color: '#f0f2f5' }}
    }}
  }};

  var config = {{
    responsive: true,
    displaylogo: false,
    scrollZoom: true,
    modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d', 'zoomIn2d', 'zoomOut2d']
  }};

  Plotly.newPlot('cone-chart', traces, layout, config);

  // Sortable table
  (function() {{
    var table = document.getElementById('rank-table');
    var headers = table.querySelectorAll('.sortable');
    var currentSort = null;
    var currentDir = 'desc';

    function sortBy(th) {{
      var key = th.getAttribute('data-sort');
      if (currentSort === key) {{
        currentDir = currentDir === 'desc' ? 'asc' : 'desc';
      }} else {{
        currentSort = key;
        currentDir = 'desc';
      }}

      headers.forEach(function(h) {{
        h.classList.remove('asc', 'desc');
        h.setAttribute('aria-sort', 'none');
      }});
      th.classList.add(currentDir);
      th.setAttribute('aria-sort', currentDir === 'desc' ? 'descending' : 'ascending');

      var tbody = table.querySelector('tbody');
      var rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort(function(a, b) {{
        var va = parseFloat(a.getAttribute('data-' + key)) || 0;
        var vb = parseFloat(b.getAttribute('data-' + key)) || 0;
        return currentDir === 'desc' ? vb - va : va - vb;
      }});

      rows.forEach(function(row, i) {{
        row.querySelector('.rank-cell').textContent = i + 1;
        tbody.appendChild(row);
      }});
    }}

    headers.forEach(function(th) {{
      th.addEventListener('click', function() {{ sortBy(th); }});
      th.addEventListener('keydown', function(e) {{
        if (e.key === 'Enter' || e.key === ' ') {{
          e.preventDefault();
          sortBy(th);
        }}
      }});
    }});
  }})();
</script>
</body>
</html>"""

    return html


def main():
    """Fetch data, build dashboard, open in browser."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = SynthClient()

    print("Fetching equity data...")
    data = fetch_all_data(client)

    print("Calculating metrics...")
    metrics = calculate_metrics(data)
    metrics = add_relative_to_spy(metrics)
    ranked = rank_equities(metrics, sort_by="median_move")
    normalized = get_normalized_series(data)

    print("Generating dashboard...")
    html = generate_dashboard_html(normalized, metrics, ranked)

    out_path = os.path.join(tempfile.gettempdir(), "tide_chart.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard saved to {out_path}")
    webbrowser.open(f"file://{out_path}")


if __name__ == "__main__":
    main()
