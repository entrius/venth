import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from data_engine import TideChartEngine

st.set_page_config(page_title="Tide Chart", layout="wide")

st.title("🌊 Tide Chart: Equity Forecast Comparison Dashboard")
st.markdown("A comparative dashboard overlaying Synth's probabilistic price forecasts for **SPY, NVDA, TSLA, AAPL, and GOOGL**.")

# Fetch Data
@st.cache_data(ttl=3600)
def load_data():
    engine = TideChartEngine()
    return engine.fetch_data()

data = load_data()

# ================================
# 1. The Comparison View
# ================================
st.header("1. The Comparison View")
st.markdown("Overlays the 24-hour probability cones (5th to 95th percentiles) normalized by percentage change.")

fig = go.Figure()

colors = {
    "SPY": "white",
    "NVDA": "#76b900",
    "TSLA": "#cc0000",
    "AAPL": "#999999",
    "GOOGL": "#4285f4"
}

for ticker, metrics in data.items():
    # Plot as a vertical range bar with a median marker
    fig.add_trace(go.Box(
        name=ticker,
        q1=[metrics["downside_tail_pct"]],
        median=[metrics["median_move_pct"]],
        q3=[metrics["upside_tail_pct"]],
        lowerfence=[metrics["downside_tail_pct"]],
        upperfence=[metrics["upside_tail_pct"]],
        marker_color=colors.get(ticker, "blue"),
        boxpoints=False
    ))

fig.update_layout(
    yaxis_title="Expected Percentage Change (%)",
    template="plotly_dark",
    showlegend=False,
    height=500
)

st.plotly_chart(fig, use_container_width=True)

# ================================
# 2. The Rank Table
# ================================
st.header("2. The Rank Table")
st.markdown("Sorts all 5 equities by key metrics derived from the Synth forecast.")

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

# Highlight relative strength vs SPY
def color_relative(val):
    color = "lightgreen" if val > 0 else "lightcoral"
    if val == 0: color = "white"
    return f"color: {color}"

st.dataframe(
    df.style.map(color_relative, subset=["Relative to SPY (%)", "Directional Skew (%)"]),
    use_container_width=True
)
