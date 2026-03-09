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
- **Wallet connection** - Connect MetaMask to trade directly from the dashboard
- **gTrade integration** - Open leveraged long/short positions on equities (SPY, NVDA, TSLA, AAPL, GOOGL) via Gains Network on Arbitrum

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

## Trading Integration

Tide Chart includes built-in wallet connection and trading via gTrade (Gains Network) on Arbitrum. This enables a complete workflow: view Synth forecasts, then act on them without leaving the dashboard.

### Prerequisites for Trading

- **MetaMask** browser extension (or compatible EIP-1193 wallet)
- **Arbitrum ETH** for gas fees
- **USDC on Arbitrum** for trade collateral

### Trading Flow

1. Click **Connect Wallet** in the top-right header
2. Approve the MetaMask connection (auto-switches to Arbitrum if needed)
3. Click **Trade** on any equity row in the rankings table (SPY, NVDA, TSLA, AAPL, GOOGL)
4. Choose direction (Long/Short), set collateral, leverage (2-150x), and optional TP/SL
5. Click the submit button to open the position via gTrade smart contracts
6. The dashboard handles USDC approval automatically on first trade

### Trading Parameters

| Parameter | Range | Notes |
|-----------|-------|-------|
| Collateral | USDC amount | Minimum position size: $1,500 (collateral x leverage) |
| Leverage | 2x - 150x | Higher leverage = higher risk |
| Max Slippage | 0.1% - 5% | Default: 1% |
| Take Profit | Optional % | Auto-closes at profit target |
| Stop Loss | Optional % | Auto-closes at loss limit |

### Security

- **Non-custodial**: All transactions are signed by the user's wallet. The dashboard never has access to private keys.
- **Transparent**: Every transaction (approval, trade) requires explicit wallet confirmation.
- **On-chain**: Trades execute on gTrade's audited smart contracts on Arbitrum.

## Technical Details

- **Language:** Python 3.10+
- **Dependencies:** plotly, flask
- **Frontend CDN:** Plotly, ethers.js v6
- **Equities (24h only):** SPY, NVDA, TSLA, AAPL, GOOGL
- **Crypto + Commodities (1h & 24h):** BTC, ETH, SOL, XAU
- **Trading:** gTrade (Gains Network) on Arbitrum via GNSMultiCollatDiamond contract
- **Output:** Flask web server with Plotly CDN (requires internet for fonts/plotly/ethers)
- **Mock Mode:** Works without API key using bundled mock data (trading requires a real wallet)
