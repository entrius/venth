# Tide Chart

> Interactive dashboard comparing 24-hour probability cones for 5 equities using Synth forecasting data.

## Overview

Tide Chart overlays probabilistic price forecasts for SPY, NVDA, TSLA, AAPL, and GOOGL into a single comparison view. It normalizes all forecasts to percentage change, enabling direct comparison across different price levels, and generates a ranked summary table with key metrics.

The tool addresses three questions from the forecast data:
- **Directional alignment** - Are all equities moving the same way?
- **Relative magnitude** - Which equity has the widest expected range?
- **Asymmetric skew** - Is the upside or downside tail larger, individually and relative to SPY?

## How It Works

1. Fetches `get_prediction_percentiles` and `get_volatility` for each of the 5 equities (24h horizon)
2. Normalizes all 289 time steps from raw price to `% change = (percentile - current_price) / current_price * 100`
3. Computes metrics from the final time step (end of 24h window):
   - **Median Move** - 50th percentile % change
   - **Upside/Downside** - 95th and 5th percentile distances
   - **Directional Skew** - upside minus downside (positive = bullish asymmetry)
   - **Range** - total 5th-to-95th percentile width
   - **Relative to SPY** - each metric minus SPY's value
4. Ranks equities by median expected move (table columns are sortable by click)
5. Generates an interactive Plotly HTML dashboard and opens it in the browser

## Synth Endpoints Used

- `get_prediction_percentiles(asset, horizon="24h")` - Provides 289 time-step probabilistic forecast with 9 percentile levels (0.5% to 99.5%). Used for the probability cone overlay and all derived metrics.
- `get_volatility(asset, horizon="24h")` - Provides forecasted average volatility. Displayed in the ranking table as an independent risk measure.

## Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Run the tool (opens dashboard in browser)
python main.py

# Run tests
python -m pytest tests/ -v
```

## Example Output

The dashboard contains two sections:

**Probability Cone Comparison** - Interactive Plotly chart with semi-transparent bands (5th-95th percentile) and median lines for each equity. Hover to see exact values at any time step.

**Equity Rankings** - Sortable table showing price, median move %, forecasted volatility, directional skew, probability range, median vs SPY, and skew vs SPY. Click any column header to re-sort. Values are color-coded green (positive) or red (negative).

## Technical Details

- **Language:** Python 3.10+
- **Dependencies:** plotly (for chart generation)
- **Synth Assets Used:** SPY, NVDA, TSLA, AAPL, GOOGL
- **Output:** Single HTML file (requires internet for Plotly CDN and fonts; no server needed)
- **Mock Mode:** Works without API key using bundled mock data
