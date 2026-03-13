# Options GPS

Turn a trader's view into one clear options decision. Inputs: **symbol**, **market view** (bullish / bearish / neutral / vol), **risk tolerance** (low / medium / high). Output: three strategy cards — **Best Match**, **Safer Alternative**, **Higher Upside** — with chance of profit, max loss, and invalidation.

## What it does

- **Screen 1 (View Setup):** User picks symbol, view, and risk; system summarizes.
- **Market Context:** Shows current price, forecast fusion state, confidence, volatility metrics, and (for vol view) implied vol vs Synth vol comparison with long/short vol bias.
- **Screen 2 (Top Plays):** Three ranked cards: Best Match (highest score for view), Safer Alternative (higher win probability), Higher Upside (higher expected payoff). Each shows why it fits, chance of profit, max loss, "Review again at" time.
- **Screen 3 (Why This Works):** Distribution view and plain-English explanation for the best match (Synth 1h + 24h fusion state, required market behavior).
- **Screen 4 (If Wrong):** Exit rule, convert/roll rule, time-based reassessment rule.
- **Screen 5 (Execution):** When `--execute` or `--dry-run` is used, shows order plan, optional confirmation (live only), per-leg fill results with slippage and latency metrics, net cost, and execution timestamps.

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
6. **Ranking Engine:** Scores with `fit_to_view + pop + expected_return - tail_penalty`; weighting shifts by risk (low → more PoP, high → more EV). For vol view, vol bias adjusts view fit: long_vol boosts long straddle/strangle scores, short_vol boosts iron condor/short straddle scores. Fusion bonus is skipped for vol view (direction-agnostic). **Market Line Shopping** (crypto only): compares Synth fair value against Deribit/Aevo exchange prices; strategies where the market price is cheaper than fair get an additive score bonus (clamped ±0.15). Picks Best Match, Safer Alternative, Higher Upside.
7. **Guardrails:** Filters no-trade when fusion is countermove/unclear with directional view, volatility exceeds threshold (directional views), confidence is too low, or vol bias is neutral (vol view — no exploitable divergence between Synth and market IV).
8. **Risk Management:** Each strategy type has a specific risk plan (invalidation trigger, adjustment/reroute rule, review schedule). Short straddle/strangle are labeled "unlimited risk" with hard stops at 2x credit loss; they are risk-gated (high-only for short straddle, medium+ for short strangle).

## Exchange integration architecture

Data flow for crypto assets (BTC, ETH, SOL):

1. **Synth** → forecast percentiles, option pricing, volatility (via `SynthClient`).
2. **Pipeline** → strategy generation, payoff/EV, ranking (with optional exchange divergence bonus).
3. **Exchange (read)** → `exchange.py` fetches live or mock quotes from Deribit and Aevo; `leg_divergences()` computes per-leg best venue and price (lowest ask for BUY, highest bid for SELL).
4. **Execution** → `executor.py` builds an `ExecutionPlan` from the chosen strategy card, resolves instrument names per exchange (Deribit: `BTC-DDMonYY-STRIKE-C|P`; Aevo: `BTC-STRIKE-C|P`), and either simulates (dry-run) or submits orders. Deribit uses **JSON-RPC 2.0 over HTTP (POST)**; Aevo uses REST with HMAC-SHA256 signing (method + path + body in signature). When `--exchange` is not set, each leg is auto-routed to its best venue (per `leg_divergences`); live execution uses one executor per leg when routing is mixed.
5. **Order lifecycle** → Full lifecycle management: place → poll status → cancel. After placement, orders can be monitored with `--timeout` (poll until filled or cancel on expiry). Partial failures trigger automatic cancellation of already-filled legs.
6. **Safety controls** → Slippage protection (`--max-slippage`) rejects fills that deviate beyond threshold. Quantity override (`--quantity`) sets contract count for all legs.

Credentials are read from the environment (no secrets in code). Dry-run requires no credentials.

## Execution (autonomous trading)

Execution is supported only for **crypto assets** (BTC, ETH, SOL). Use `--execute` to submit live orders or `--dry-run` to simulate without placing orders.

**CLI flags:**

- **`--execute best|safer|upside`** — Which strategy card to execute (default: best). Omit to run analysis only.
- **`--dry-run`** — Simulate execution using current exchange quotes; no API keys needed and no real orders.
- **`--force`** — Allow live execution when the guardrail recommends no trade (e.g. signals unclear). Without `--force`, the CLI exits with an error in that case.
- **`--exchange deribit|aevo`** — Force all legs to one exchange. Default: auto-route each leg to the best venue.
- **`--max-slippage N`** — Max allowed slippage % per fill (reject and cancel if exceeded, 0=off).
- **`--quantity N`** — Override contract quantity for all legs (0=use strategy default).
- **`--timeout N`** — Seconds to wait for each order fill before cancelling (0=fire-and-forget).

**Environment variables (live execution only):**

| Variable | Purpose |
|----------|---------|
| `DERIBIT_CLIENT_ID` / `DERIBIT_CLIENT_SECRET` | Deribit API credentials |
| `DERIBIT_TESTNET=1` | Use Deribit testnet |
| `AEVO_API_KEY` / `AEVO_API_SECRET` | Aevo API credentials |
| `AEVO_TESTNET=1` | Use Aevo testnet |

**Safety:** When the pipeline sets a no-trade reason (e.g. low confidence, conflicting signals), live execution is refused unless `--force` is set. Dry-run is always allowed for testing. Slippage protection rejects fills exceeding `--max-slippage` threshold and auto-cancels the order. On partial multi-leg failure, already-filled legs are automatically cancelled (best-effort) to avoid dangling positions. The decision log JSON includes an `execution` block with mode, fills (including per-leg slippage %, latency ms, timestamps), cancelled orders, and net cost.

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

# Simulate execution (no API keys, no real orders)
python tools/options-gps/main.py --symbol BTC --view bullish --risk medium --dry-run --no-prompt

# Execute best strategy on exchange (requires credentials)
python tools/options-gps/main.py --symbol BTC --view bullish --risk medium --execute best --no-prompt
```

Prompts: symbol (default BTC), view (bullish/bearish/neutral/vol), risk (low/medium/high). Uses mock data when no `SYNTH_API_KEY` is set.

## Tests

From repo root: `python -m pytest tools/options-gps/tests/ -v`. No API key required (mock data).

Test coverage includes: forecast fusion, strategy generation (all views including vol), PnL calculations for all strategy types, CDF-weighted PoP/EV, ranking with vol bias, vol-specific guardrails, IV estimation, vol comparison, risk plans, hard filters, exchange data fetching/parsing, divergence computation, line shopping ranking integration, end-to-end scripted tests, **execution** (instrument names, plan build/validate, dry-run executor, order lifecycle — get_order_status/cancel_order, slippage computation and protection, execution timestamps and latency, summary message verification, quantity override, auto-routing, get_executor factory, guardrail refusal when no-trade and no --force), full-pipeline-to-dry-run E2E, multi-leg execution E2E, non-crypto skip E2E, and slippage protection E2E.
