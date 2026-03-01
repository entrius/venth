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

/* DataFrames styling to match Gittensor Miner Tables */
[data-testid="stDataFrame"] > div {
    background-color: #0a0f1f !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 12px !important;
    padding: 1rem !important;
}

/* Table Text */
table, .stDataFrame td, .stDataFrame th {
    font-family: 'JetBrains Mono', monospace !important;
    color: #ffffff !important;
}
th {
    color: rgba(255, 255, 255, 0.5) !important;
    text-transform: uppercase !important;
    font-size: 0.75rem !important;
    letter-spacing: 0.05em !important;
    font-weight: 600 !important;
    border-bottom: 1px solid rgba(255,255,255,0.1) !important;
}
td {
    border-bottom: 1px solid rgba(255,255,255,0.05) !important;
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
        fillcolor=fill_colors.get(ticker),
        line=dict(width=2, color=colors.get(ticker)),
        hoverinfo="y+name",
        whiskerwidth=0.8,
        marker=dict(size=4, color=colors.get(ticker), outliercolor="rgba(0,0,0,0)")
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
for ticker, metrics in data.items():
    df_data.append({
        "Ticker": ticker,
        "Median Move (%)": round(metrics["median_move_pct"], 2),
        "Forecasted Volatility": round(metrics["volatility"], 4),
        "Directional Skew (%)": round(metrics["skew_pct"], 2),
        "Relative to SPY (%)": round(metrics["relative_to_spy_pct"], 2)
    })

df = pd.DataFrame(df_data).set_index("Ticker")

# Highlight relative strength vs SPY using Gittensor Status Colors
def color_relative(val):
    color = "#3fb950" if val > 0 else "#ef4444" # Merged Green / Closed Red
    if val == 0: color = "rgba(255, 255, 255, 0.7)"
    return f"color: {color}; font-weight: 600;"

st.dataframe(
    df.style.map(color_relative, subset=["Relative to SPY (%)", "Directional Skew (%)"])
            .format("{:.2f}%", subset=["Median Move (%)", "Directional Skew (%)", "Relative to SPY (%)"])
            .format("{:.4f}", subset=["Forecasted Volatility"]),
    use_container_width=True
)
