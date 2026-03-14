# Options GPS — Best Options Tool Submission

---

## 2. Technical Details (1-page)

### Architecture Overview

Options GPS is a Python CLI tool built on a modular pipeline architecture with four layers:

1. **Data Layer (`SynthClient`)** — Fetches probabilistic forecasts (1h and 24h prediction percentiles), option pricing, and volatility data from the Synth API. The client supports live and mock modes, allowing development without an API key.

2. **Pipeline Layer (`pipeline.py`)** — The analytical core. It takes user inputs (symbol, view, risk) and executes a multi-stage pipeline: forecast fusion (comparing 1h and 24h medians to determine Aligned/Countermove/Unclear state), strategy generation (building candidates from option strikes per view and risk), payoff and probability computation (CDF-weighted probability of profit and expected value using Synth's percentile distribution), and ranking (scoring strategies by fit-to-view, PoP, expected return, and tail penalty).

3. **Exchange Layer (`exchange.py`)** — Fetches live quotes from Deribit (JSON-RPC 2.0) and Aevo (REST with HMAC-SHA256). Computes per-leg price divergences between Synth fair value and exchange prices, enabling Market Line Shopping — each leg is routed to the cheapest venue automatically.

4. **Execution Layer (`executor.py`)** — Builds execution plans, resolves exchange-specific instrument names, and handles the full order lifecycle (place → poll → cancel). Includes slippage protection, partial-failure rollback (auto-cancel filled legs if a later leg fails), and quantity overrides.

### How Synth API Is Integrated

The tool consumes three Synth endpoints:
- **`get_prediction_percentiles(asset, horizon)`** — The backbone of the tool. 1h and 24h percentile distributions power both the forecast fusion logic (is 1h aligned with 24h?) and the payoff engine (what is the CDF-weighted probability a given strike finishes in-the-money?).
- **`get_option_pricing(asset)`** — Theoretical call/put prices by strike. Used to build strategy candidates and compute costs. For the vol view, ATM premiums are used to derive implied volatility via the Brenner-Subrahmanyam approximation.
- **`get_volatility(asset, horizon)`** — Forecasted and realized volatility. Feeds the guardrail system (suppress trades when volatility is extreme) and provides the Synth vol signal for IV comparison in vol view.

### Data Consumption Approach

Rather than treating Synth data as a simple directional signal, Options GPS decomposes the full 9-percentile distribution into a CDF for probability-weighted payoff analysis. The 1h and 24h forecasts are fused to detect signal alignment or conflict, preventing trades when timeframes disagree. For crypto options, the tool goes further: it cross-references Synth's theoretical fair prices against live Deribit/Aevo quotes, giving strategies a score bonus when exchange prices are cheaper than Synth fair value — surfacing real-time market inefficiencies.

### Key Insights

- **Multi-timeframe fusion prevents bad trades.** Fusing 1h and 24h forecasts catches divergent signals before capital is at risk. This is one of the strongest use cases for Synth's multi-horizon data.
- **Full distribution beats point estimates.** Using all 9 percentiles for CDF-weighted PoP/EV produces more accurate risk-reward profiles than relying on a single median forecast.
- **Probabilistic data enables automated line shopping.** Because Synth provides fair value pricing, the tool can objectively compare exchange quotes and route to the best venue per leg — something not possible with directional-only signals.
- **Guardrails build user trust.** By refusing to trade when confidence is low or signals conflict, the tool demonstrates responsible use of probabilistic data and prevents forced trades in uncertain conditions.

---

## 3. What problem does your project solve?

Options trading is notoriously complex — traders must simultaneously evaluate market direction, choose a strategy, select strikes, and manage risk across multiple exchanges. Most retail traders either over-simplify (buying naked calls) or get paralyzed by the number of choices. Options GPS solves this by converting a simple three-input market view (symbol, direction, risk tolerance) into a fully ranked set of strategy recommendations, complete with probability of profit, expected value, and risk management rules — all driven by Synth's probabilistic forecasts rather than gut feel. For crypto options, it goes even further by automatically comparing prices across Deribit and Aevo and routing each leg to the cheapest venue, then executing the trade end-to-end.

## 4. What makes your project unique?

Options GPS is the only tool that fuses multi-timeframe Synth forecasts (1h + 24h) into a single coherent signal, uses the full probabilistic distribution for CDF-weighted payoff analysis, and then closes the loop with autonomous multi-exchange execution. Most options screeners stop at showing greeks or IV rank — Options GPS goes from "I'm bullish on ETH" to a fully executed, multi-leg strategy on the best-priced exchange in a single command. The guardrail system is equally distinctive: rather than blindly trading every signal, the tool explicitly refuses when Synth's own data shows conflicting timeframes, low confidence, or no vol edge, making it a responsible decision engine rather than just a trade executor.
