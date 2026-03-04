# Options GPS

Turn a trader's view into one clear options decision. Inputs: **symbol**, **market view** (bullish / bearish / neutral), **risk tolerance** (low / medium / high). Output: three strategy cards — **Best Match**, **Safer Alternative**, **Higher Upside** — with chance of profit, max loss, and invalidation.

## What it does

- **Screen 1 (View Setup):** User picks symbol, view, and risk; system summarizes.
- **Screen 2 (Top Plays):** Three ranked cards: Best Match (highest score for view), Safer Alternative (higher win probability), Higher Upside (higher expected payoff). Each shows why it fits, chance of profit, max loss, "Review again at" time.
- **Screen 3 (Why This Works):** Distribution view and plain-English explanation for the best match (Synth 1h + 24h fusion state, required market behavior).
- **Screen 4 (If Wrong):** Exit rule, convert/roll rule, time-based reassessment rule.

**Guardrails:** No-trade state when confidence is low or signals conflict (e.g. 1h vs 24h countermove, or very high volatility).

## How it works

1. **Data:** Synth forecasts (1h and 24h prediction percentiles), option pricing, and volatility via `SynthClient`.
2. **Forecast Fusion:** Compares 1h and 24h median vs current price → **Aligned** (both same direction), **Countermove** (opposite), or **Unclear**.
3. **Strategy Generator:** Builds candidates from option strikes (long call/put, call/put debit spreads, iron condor for neutral/low risk) based on view and risk.
4. **Payoff + Probability Engine:** Uses Synth percentile distribution at horizon to compute probability of profit (PoP) and expected value (EV) for each strategy.
5. **Ranking Engine:** Scores with `fit_to_view + pop + expected_return - tail_penalty`; weighting shifts by risk (low → more PoP, high → more EV). Picks Best Match, Safer Alternative, Higher Upside.
6. **Guardrails:** Filters no-trade when fusion is countermove/unclear with directional view, or volatility exceeds threshold.

## Synth API usage

- **`get_prediction_percentiles(asset, horizon)`** — 1h and 24h probabilistic price forecasts; used for fusion state and for payoff/EV (outcome distribution at expiry).
- **`get_option_pricing(asset)`** — Theoretical call/put prices by strike; used to build strategies and costs.
- **`get_volatility(asset, horizon)`** — Forecast volatility; used in guardrails (no trade when volatility very high).

## Usage

```bash
# From repo root
pip install -r tools/options-gps/requirements.txt
python tools/options-gps/main.py
```

Prompts: symbol (default BTC), view (bullish/bearish/neutral), risk (low/medium/high). Uses mock data when no `SYNTH_API_KEY` is set.

## Tests

From repo root: `python -m pytest tools/options-gps/tests/ -v`. No API key required (mock data).
