# Options GPS: Turning Views into Trades

## Overview
Options GPS is a decision-engine that turns a user's market view (bullish, bearish, or neutral) and risk tolerance (low, medium, high) into actionable options strategies without the need for manual analysis.

It integrates seamlessly with the `SynthClient` to extract:
1. `get_prediction_percentiles()`: Yields the 24-hour time-horizon price distribution percentiles (e.g., 5th percentile downside vs. 95th percentile upside capture).
2. `get_option_pricing()`: Yields the theoretical option chain strikes and prices scaled against the Synth distribution models.

## How It Works
The engine (`engine.py`) ingests the current Synth probability cone alongside the user's explicit bias.
- **Forecast Fusion**: Selects the `24h` horizon by default to accommodate meaningful options expiry gaps.
- **Strategy Generation**: Determines whether a vertical spread (low risk) or naked outright (high risk) provides the optimal capital asymmetry based on the price percentiles (0.05 vs 0.95).
- **Ranking / Outputs**: Maps the required margin/cost as Max Loss, estimates POP, and builds human-readable rationale text to satisfy Screen 1 through Screen 4 of the UX specification.

## Usage
Simply run `main.py` directly from the terminal for the interactive CLI mock-up:
```bash
python tools/options-gps/main.py
```

## Running Tests
Tests live in `tests/test_gps.py`. Execute:
```bash
pytest tools/options-gps/tests/
```
