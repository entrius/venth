# Options GPS

Turn a trader's view into one clear options decision. Inputs: **symbol**, **market view** (bullish / bearish / neutral / vol), **risk tolerance** (low / medium / high). Output: three strategy cards — **Best Match**, **Safer Alternative**, **Higher Upside** — with chance of profit, max loss, and invalidation.

## What it does

- **Screen 1 (View Setup):** User picks symbol, view, and risk; system summarizes.
- **Market Context:** Shows current price, forecast fusion state, confidence, volatility metrics, and (for vol view) implied vol vs Synth vol comparison with long/short vol bias.
- **Screen 2 (Top Plays):** Three ranked cards: Best Match (highest score for view), Safer Alternative (higher win probability), Higher Upside (higher expected payoff). Each shows why it fits, chance of profit, max loss, "Review again at" time.
- **Screen 3 (Why This Works):** Distribution view and plain-English explanation for the best match (Synth 1h + 24h fusion state, required market behavior).
- **Screen 4 (If Wrong):** Exit rule, convert/roll rule, time-based reassessment rule.

**Guardrails:** No-trade state when confidence is low, signals conflict (e.g. 1h vs 24h countermove), volatility is very high (directional views), or no vol edge exists (vol view with similar Synth/market IV).

## How it works

1. **Data:** Synth forecasts (1h and 24h prediction percentiles), option pricing, and volatility via `SynthClient`.
2. **Forecast Fusion:** Compares 1h and 24h median vs current price → **Aligned** (both same direction), **Countermove** (opposite), or **Unclear**.
3. **Implied Volatility Estimation (vol view):** Derives market IV from ATM option premiums using the Brenner-Subrahmanyam approximation: `IV ≈ premium × √(2π) / (price × √T)`. Parses actual time-to-expiry from option data; falls back to 1-day if unavailable. Compares against Synth's forecasted volatility to determine a **vol bias**: `long_vol` (Synth > IV by >15%), `short_vol` (Synth < IV by >15%), or `neutral_vol` (no edge).
4. **Strategy Generator:** Builds candidates from option strikes based on view and risk:
   - **Bullish:** Long call, call debit spread, bull put credit spread.
   - **Bearish:** Long put, put debit spread, bear call credit spread.
   - **Neutral:** Iron condor, long call butterfly, ATM call/put.
   - **Vol (long vol bias):** Long straddle (buy ATM call + put), long strangle (buy OTM call + put).
   - **Vol (short vol bias):** Short straddle (sell ATM call + put, high risk only), short strangle (sell OTM call + put, medium/high risk), iron condor (defined-risk short vol).
5. **Payoff + Probability Engine:** Uses Synth percentile distribution (CDF-weighted) at horizon to compute probability of profit (PoP) and expected value (EV) for each strategy. PnL formulas cover all strategy types including straddles and strangles.
6. **Ranking Engine:** Scores with `fit_to_view + pop + expected_return - tail_penalty`; weighting shifts by risk (low → more PoP, high → more EV). For vol view, vol bias adjusts view fit: long_vol boosts long straddle/strangle scores, short_vol boosts iron condor/short straddle scores. Fusion bonus is skipped for vol view (direction-agnostic). Picks Best Match, Safer Alternative, Higher Upside.
7. **Guardrails:** Filters no-trade when fusion is countermove/unclear with directional view, volatility exceeds threshold (directional views), confidence is too low, or vol bias is neutral (vol view — no exploitable divergence between Synth and market IV).
8. **Risk Management:** Each strategy type has a specific risk plan (invalidation trigger, adjustment/reroute rule, review schedule). Short straddle/strangle are labeled "unlimited risk" with hard stops at 2x credit loss; they are risk-gated (high-only for short straddle, medium+ for short strangle).

## Synth API usage

- **`get_prediction_percentiles(asset, horizon)`** — 1h and 24h probabilistic price forecasts; used for fusion state and for payoff/EV (outcome distribution at expiry).
- **`get_option_pricing(asset)`** — Theoretical call/put prices by strike; used to build strategies, costs, and to derive market implied volatility (vol view).
- **`get_volatility(asset, horizon)`** — Forecast and realized volatility; used in guardrails (no trade when volatility very high) and as the Synth vol signal for vol view comparison against market IV.

## Usage

```bash
# From repo root
pip install -r tools/options-gps/requirements.txt
python tools/options-gps/main.py

# Vol view directly from CLI
python tools/options-gps/main.py --symbol BTC --view vol --risk medium --no-prompt
```

Prompts: symbol (default BTC), view (bullish/bearish/neutral/vol), risk (low/medium/high). Uses mock data when no `SYNTH_API_KEY` is set.

## Exchange configuration (Market Line Shopping)

Options GPS compares Synth's theoretical option prices against exchanges (Aevo, Deribit) for **Market Line Shopping** and best execution.

**Live execution:** When API keys are set, the tool performs real network calls for live quotes:

- **Deribit:** Public REST API (`get_book_summary_by_currency`), no auth required for market data.
- **Aevo:** REST API (`/options`) when `AEVO_API_KEY` is set.

When keys are unset or calls fail, it falls back to a mock multi-exchange provider.

Environment variables:

- `AEVO_API_KEY` — for Aevo live options data
- `DERIBIT_CLIENT_ID` / `DERIBIT_CLIENT_SECRET` — optional; Deribit market data is public

**Best execution (high-signal UI):** The Top Plays screen explicitly points to the winner — which venue offers the best execution price for the recommended strategy. For each Best Match card, it shows: `Best execution: {venue} (${cost} debit/credit)`.

**Edge detection (alpha over agreement):** Confidence is adjusted based on how much Synth deviates from the market. Higher divergence from the market mean (modeled via z-score: `z = (synth - mean) / std`) increases conviction — alpha comes from disagreement, not consensus. This feeds into the overall confidence bar and guardrails.

To configure:

1. Copy `.env.example` at the repo root to `.env`.
2. Fill in your API keys.
3. Load the env file in your shell.

## Tests

From repo root: `python -m pytest tools/options-gps/tests/ -v`. No API key required (mock data).

Test coverage includes: forecast fusion, strategy generation (all views including vol), PnL calculations for all strategy types, CDF-weighted PoP/EV, ranking with vol bias, vol-specific guardrails, IV estimation, vol comparison, risk plans, hard filters, and end-to-end scripted tests.
