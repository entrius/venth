import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from data_engine import TideChartEngine

st.set_page_config(page_title="Venth Dashboard", layout="wide")

# Gittensor UI Theme Injection
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

/* Base overriding */
html, body, [class*="css"], .stApp {
    font-family: 'Inter', -apple-system, sans-serif !important;
    background-color: #000000 !important;
    color: #ffffff !important;
}

/* Remove default padding */
.block-container {
    padding-top: 1rem !important;
    padding-bottom: 2rem !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
    max-width: 1400px !important;
}

/* Hide default streamlit headers */
header[data-testid="stHeader"] {
    display: none !important;
}

/* Typography */
h1, h2, h3, h4, p, span, div {
    font-family: 'Inter', -apple-system, sans-serif;
}
h1 {
    font-weight: 700 !important;
    letter-spacing: -0.04em !important;
    font-size: 2.5rem !important;
    padding-bottom: 0 !important;
}
h2 {
    font-weight: 600 !important;
    letter-spacing: -0.02em !important;
    margin-top: 1.5rem !important;
    padding-bottom: 0.5rem !important;
    border-bottom: 1px solid rgba(255,255,255,0.1) !important;
}

/* Raw HTML Table Styling to bypass Streamlit's Canvas Data Grid */
.table-container {
    background-color: #0a0f1f !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 12px !important;
    padding: 0 !important;
    overflow-x: auto;
    margin-top: 1rem;
}

.gittensor-table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'JetBrains Mono', monospace !important;
    color: #ffffff !important;
    background-color: transparent !important;
    font-size: 0.85rem;
    border: none !important;
}

.gittensor-table thead, .gittensor-table tbody {
    background-color: transparent !important;
    border: none !important;
}

.gittensor-table tr {
    background-color: transparent !important;
    border-bottom: 1px solid rgba(255,255,255,0.05) !important;
    transition: background-color 0.15s ease-in-out !important;
}

.gittensor-table tr:hover {
    background-color: rgba(255,255,255,0.05) !important;
}

.gittensor-table th {
    color: rgba(255, 255, 255, 0.45) !important;
    text-transform: uppercase !important;
    font-size: 0.65rem !important;
    letter-spacing: 0.05em !important;
    font-weight: 600 !important;
    border: none !important;
    border-bottom: 1px solid rgba(255,255,255,0.1) !important;
    padding: 8px 16px !important;
    text-align: left;
    background-color: transparent !important;
}

.gittensor-table td {
    padding: 8px 16px !important;
    border: none !important;
    background-color: transparent !important;
    color: rgba(255, 255, 255, 0.9) !important;
}

.gittensor-table tr:last-child {
    border-bottom: none !important;
}
</style>
""", unsafe_allow_html=True)

# Custom Gittensor Branded Header
st.markdown("""
<div style="display: flex; align-items: center; gap: 1rem; padding-bottom: 1rem; margin-top: 0.5rem;">
    <img src="https://gittensor.io/gt-logo-white.png" width="36" height="36" style="border-radius: 6px;">
    <h2 style="margin: 0; font-family: 'Inter', sans-serif; font-weight: 600; font-size: 1.5rem; letter-spacing: -0.02em; border: none !important; padding: 0 !important; margin-top: 0 !important;">Gittensor <span style="color: rgba(255,255,255,0.4); font-weight: 400;">| Venth</span></h2>
</div>
""", unsafe_allow_html=True)

st.title("Tide Chart: Equity Forecast Comparison")
st.markdown("<p style='color: rgba(255,255,255,0.6); font-size: 1.1rem; margin-top:-10px;'>A comparative dashboard overlaying Synth's probabilistic price forecasts for <b>SPY, NVDA, TSLA, AAPL, and GOOGL</b>.</p>", unsafe_allow_html=True)

# Fetch Data
@st.cache_data(ttl=3600)
def load_data():
    engine = TideChartEngine()
    return engine.fetch_data()

data = load_data()

# ================================
# The Comparison View
# ================================
st.markdown("<h3 style='margin-bottom:0.5rem;'>The Comparison View</h3>", unsafe_allow_html=True)
st.markdown("<p style='color: rgba(255,255,255,0.5); font-size:0.9rem; margin-bottom:1rem;'>Overlays the 24-hour probability cones (5th to 95th percentiles) normalized by percentage change.</p>", unsafe_allow_html=True)

fig = go.Figure()

colors = {
    "SPY": "rgba(255, 255, 255, 0.4)",
    "NVDA": "#fff30d", # Gittensor Secondary
    "TSLA": "#1d37fc", # Gittensor Primary
    "AAPL": "rgba(255, 255, 255, 0.4)",
    "GOOGL": "rgba(255, 255, 255, 0.4)"
}

fill_colors = {
    "SPY": "rgba(255, 255, 255, 0.05)",
    "NVDA": "rgba(255, 243, 13, 0.1)",
    "TSLA": "rgba(29, 55, 252, 0.15)",
    "AAPL": "rgba(255, 255, 255, 0.05)",
    "GOOGL": "rgba(255, 255, 255, 0.05)"
}

for ticker, metrics in data.items():
    # Plotting using a stylized Candlestick/Box hybrid to look more 'trading-oriented'
    fig.add_trace(go.Box(
        name=ticker,
        q1=[metrics["downside_tail_pct"]],
        median=[metrics["median_move_pct"]],
        q3=[metrics["upside_tail_pct"]],
        lowerfence=[metrics["downside_tail_pct"]],
        upperfence=[metrics["upside_tail_pct"]],
        x=[ticker],
        fillcolor="rgba(0,0,0,0.8)", # Hollow candle look
        line=dict(width=3, color=colors.get(ticker)), # Thick, bright borders
        hoverinfo="y+name",
        whiskerwidth=0.2, # Sharp wicks
        boxpoints=False, # Completely hide statistical outliers to resemble a candle
        marker=dict(size=0, color="rgba(0,0,0,0)")
    ))

fig.update_layout(
    yaxis_title="Expected Percentage Change (%)",
    template="plotly_dark",
    plot_bgcolor='rgba(0,0,0,0)',
    paper_bgcolor='rgba(0,0,0,0)',
    font=dict(family="Inter, sans-serif", color="rgba(255,255,255,0.7)"),
    showlegend=False,
    height=450,
    margin=dict(l=40, r=20, t=10, b=40),
    xaxis=dict(
        showgrid=True, 
        gridcolor='rgba(255, 255, 255, 0.03)',
        zeroline=False,
        tickfont=dict(family="JetBrains Mono, monospace", size=14, color="#ffffff")
    ),
    yaxis=dict(
        showgrid=True,
        gridcolor='rgba(255, 255, 255, 0.05)',
        zerolinecolor='rgba(255, 255, 255, 0.2)',
        zerolinewidth=2,
        tickfont=dict(family="JetBrains Mono, monospace")
    )
)

st.plotly_chart(fig, use_container_width=True)

# ================================
# The Rank Table
# ================================
st.markdown("<h3 style='margin-bottom:0.5rem; margin-top: 1rem;'>The Rank Table</h3>", unsafe_allow_html=True)
st.markdown("<p style='color: rgba(255,255,255,0.5); font-size:0.9rem; margin-bottom:1rem;'>Sorts all 5 equities by key metrics derived from the Synth forecast.</p>", unsafe_allow_html=True)

df_data = []
sorted_items = sorted(data.items(), key=lambda x: x[1]["median_move_pct"], reverse=True)

for i, (ticker, metrics) in enumerate(sorted_items):
    # CSS badge for rank (replicating Gittensor Repositories UI exactly)
    rank_html = f'<div style="width:24px;height:24px;border:1px solid rgba(255,255,255,0.15);border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:0.75rem;color:rgba(255,255,255,0.6);font-family:\'JetBrains Mono\',monospace;">{i+1}</div>'
    
    # Avatar colors
    avatar_color = "transparent"
    if ticker == "NVDA": avatar_color = "#fff30d"
    elif ticker == "TSLA": avatar_color = "#1d37fc"
    elif ticker == "SPY": avatar_color = "#ffffff"
    elif ticker == "AAPL": avatar_color = "#a8a8a8"
    elif ticker == "GOOGL": avatar_color = "#ea4335"
    
    # Ticker combo with 'Gold' badge emulation
    badge = '<span style="color:#fff30d;font-size:0.6rem;border:1px solid rgba(255,243,13,0.3);padding:2px 6px;border-radius:4px;letter-spacing:0.05em;margin-left:8px;">Gold</span>' if i < 3 else ''
    ticker_html = f'<div style="display:flex;align-items:center;gap:12px;"><div style="width:24px;height:24px;border-radius:50%;border:1px solid rgba(255,255,255,0.2);background-color:{avatar_color};box-shadow: 0 0 8px {avatar_color}40;"></div><span style="font-weight:600;color:rgba(255,255,255,0.9);">{ticker}</span>{badge}</div>'

    # Status colored relative metrics
    rel_color = "#51cf66" if metrics["relative_to_spy_pct"] > 0 else "#ff6b6b"
    if metrics["relative_to_spy_pct"] == 0: rel_color = "rgba(255,255,255,0.5)"
    
    df_data.append({
        "Rank": rank_html,
        "Asset": ticker_html,
        "Median Move ▼": f'<span style="font-weight:600;color:rgba(255,255,255,0.9);">{metrics["median_move_pct"]:.2f}%</span>',
        "Forecasted Volatility": f'<span style="color:rgba(255,255,255,0.7);">{metrics["volatility"]:.4f}</span>',
        "Directional Skew": f'<span style="color:rgba(255,255,255,0.7);">{metrics["skew_pct"]:.2f}%</span>',
        "Relative to SPY": f'<span style="color:{rel_color};font-weight:600;background-color:{rel_color}15;padding:2px 6px;border-radius:4px;">{metrics["relative_to_spy_pct"]:.2f}%</span>'
    })

df = pd.DataFrame(df_data)

# Push native HTML skipping any Streamlit grid overrides completely
html_table = df.to_html(escape=False, index=False, classes="gittensor-table")
st.markdown(f'<div class="table-container">{html_table}</div>', unsafe_allow_html=True)
