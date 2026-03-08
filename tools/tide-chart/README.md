# Tide Chart

> Interactive Flask dashboard comparing probability cones for equities and crypto using Synth forecasting data.

## Overview

Tide Chart overlays probabilistic price forecasts into a single comparison view with an interactive web interface. It supports both equities (SPY, NVDA, TSLA, AAPL, GOOGL) on the 24h horizon and crypto/commodities (BTC, ETH, SOL, XAU) on both 1h and 24h horizons. All forecasts are normalized to percentage change for direct comparison across different price levels.

The tool provides:
- **Probability cones** - Interactive Plotly chart with 5th-95th percentile bands
- **Probability calculator** - Enter a target price to see the exact probability of an asset reaching it
- **Variable time horizons** - Toggle between Intraday (1H) and Next Day (24H) views
- **Live auto-refresh** - Manual refresh button and configurable 5-minute auto-refresh
- **Ranked metrics table** - Sortable table with directional alignment, skew, and relative benchmarks

## How It Works

1. Starts a Flask server serving the interactive dashboard at `http://localhost:5000`
2. Fetches `get_prediction_percentiles` and `get_volatility` for assets in the selected horizon
3. Normalizes time steps from raw price to `% change = (percentile - current_price) / current_price * 100`
4. Computes metrics from the final time step (end of forecast window):
   - **Median Move** - 50th percentile % change
   - **Upside/Downside** - 95th and 5th percentile distances
   - **Directional Skew** - upside minus downside (positive = bullish asymmetry)
   - **Range** - total 5th-to-95th percentile width
   - **Relative to Benchmark** - each metric minus benchmark (SPY for equities, BTC for crypto)
5. Ranks assets by median expected move (table columns are sortable by click)
6. Probability calculator uses linear interpolation across 9 percentile levels to estimate P(price <= target)

## Synth Endpoints Used

- `get_prediction_percentiles(asset, horizon)` - Provides time-step probabilistic forecast with 9 percentile levels (0.5% to 99.5%). Used for probability cones, metrics, and the probability calculator.
- `get_volatility(asset, horizon)` - Provides forecasted average volatility. Displayed in the ranking table as an independent risk measure.

## Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Run the dashboard server (opens browser automatically)
python main.py

# Custom port
TIDE_CHART_PORT=8080 python main.py

# Run tests
python -m pytest tests/ -v
```

## API Endpoints

- `GET /` - Serves the interactive dashboard HTML
- `GET /api/data?horizon=24h` - Returns chart traces, table rows, and insights as JSON
- `POST /api/probability` - Calculates target price probability (body: `{"asset": "SPY", "target_price": 600, "horizon": "24h"}`)

## Technical Details

- **Language:** Python 3.10+
- **Dependencies:** plotly, flask
- **Equities (24h only):** SPY, NVDA, TSLA, AAPL, GOOGL
- **Crypto + Commodities (1h & 24h):** BTC, ETH, SOL, XAU
- **Output:** Flask web server with Plotly CDN (requires internet for fonts/plotly)
- **Mock Mode:** Works without API key using bundled mock data
