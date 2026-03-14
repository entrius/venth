# Tide Chart — Best Equities Application Submission

---

## 2. Technical Details (1-page)

### Architecture Overview

Tide Chart is a Flask web application with three integrated subsystems:

1. **Forecast Engine (`main.py`, `chart.py`)** — Fetches Synth prediction percentiles and volatility for all supported assets, normalizes raw price forecasts to percentage change (`(percentile - current_price) / current_price × 100`), and computes ranked metrics from the final time step: median move, upside (95th), downside (5th), directional skew, range, and relative-to-benchmark performance (SPY for equities, BTC for crypto). The chart module generates interactive Plotly probability cone traces with 5th-95th percentile bands. A probability calculator uses linear interpolation across 9 percentile levels to estimate P(price ≤ target) for any user-supplied price.

2. **Trading Engine (`gtrade.py`)** — Integrates with gTrade (Gains Network) v9 on Arbitrum One for leveraged DeFi trading. Handles pair resolution (mapping Synth tickers to gTrade pair indices via the Gains Network backend API), server-side trade validation with per-group protocol limits (leverage ranges, collateral bounds, $1,500 minimum position size), USDC allowance checking, and `openTrade` transaction construction targeting the Diamond proxy contract. Protocol guards are enforced both client-side (Execute button disabled with reason label) and server-side (validation endpoint mirrors the same rules).

3. **Frontend (`static/`)** — Single-page dashboard using Plotly.js (CDN) for interactive charting and ethers.js v6 (CDN) for wallet connection and transaction signing. The frontend handles MetaMask connection, automatic chain switching to Arbitrum One (chain ID 42161), USDC balance display, trade form with live preview, and toast notifications for transaction status. Auto-refresh polls updated forecast data every 5 minutes.

### How Synth API Is Integrated

The dashboard consumes two core Synth endpoints:
- **`get_prediction_percentiles(asset, horizon)`** — The primary data source. Provides time-step probabilistic forecasts with 9 percentile levels (0.5% to 99.5%). Powers the probability cone visualization, the ranked metrics table, and the probability calculator. Each asset's forecast is independently fetched and normalized for cross-asset comparison.
- **`get_volatility(asset, horizon)`** — Provides forecasted average volatility. Displayed as an independent risk measure in the rankings table alongside directional metrics.

### Data Consumption Approach

Tide Chart's key innovation is normalization: all assets are converted from absolute price levels to percentage change so that a $90,000 BTC forecast can be directly compared against a $600 SPY forecast in the same chart and table. Metrics are computed at the terminal time step of the forecast window, and each asset's performance is benchmarked against a category reference (SPY for equities, BTC for crypto), surfacing relative outperformance or underperformance. The probability calculator goes further — it interpolates across Synth's 9 percentile levels to provide a point estimate of the probability of reaching any user-specified price, turning the distribution curve into an actionable answer.

### Key Insights

- **Cross-asset comparison is a killer feature for equities.** Normalizing to percentage change lets traders instantly see which equity has the strongest Synth forecast outlook — something that's impossible when looking at raw price levels.
- **Forecast-to-execution in one click bridges the intelligence gap.** Most forecast tools show data and leave execution to the user. Tide Chart closes the loop: see the probability cone → check the ranked metrics → execute a leveraged trade on gTrade, all without leaving the dashboard.
- **Protocol guards prevent user errors.** By enforcing gTrade's leverage, collateral, and position size rules both client-side and server-side, the tool prevents rejected transactions and wasted gas fees.
- **Probabilistic data is more useful as a distribution than a point estimate.** The probability calculator demonstrates this — instead of just showing "median up 1.2%", the user can ask "what's the probability of reaching my specific target?" and get a numerically rigorous answer.

---

## 3. What problem does your project solve?

Retail traders interested in equities and crypto lack a tool that combines probabilistic forecasts with actionable comparison metrics and direct trade execution in one interface. Existing charting tools show historical data but no forward-looking probability distributions; existing DeFi frontends let you trade but provide no forecast intelligence. Tide Chart bridges this gap by normalizing Synth's probabilistic forecasts across all supported assets (equities, crypto, gold) into a single ranked view with interactive probability cones, a probability calculator for custom price targets, and a direct on-chain trading interface on Arbitrum via gTrade — from forecast insight to leveraged position in one seamless workflow.

## 4. What makes your project unique?

Tide Chart is the only tool that normalizes Synth's multi-asset probabilistic forecasts into a single cross-asset comparison dashboard and then lets users act on those forecasts by executing leveraged trades on-chain via gTrade, all within the same interface. The combination of visual probability cones, a ranked metrics table with relative benchmarking (vs. SPY or BTC), a probability calculator that interpolates across Synth's full percentile distribution, and integrated DeFi execution with protocol-level safety guards creates a workflow that doesn't exist elsewhere. It's not just a chart or just a trading terminal — it's the complete loop from "which asset has the best forecast?" to "I now have a leveraged position open," powered entirely by Synth's probabilistic data.
