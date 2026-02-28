# Tide Chart: Equity Forecast Comparison

## Overview
Tide Chart is a single-pane dashboard that aggregates Synth probabilistic mock data for 5 major equities (SPY, NVDA, TSLA, AAPL, GOOGL) to identify divergence, Skew, and Relative Strength.

## Architecture
- `data_engine.py`: A `TideChartEngine` class that iterates the symbols through `SynthClient`, fetching the `24h` horizon forecast and mapping the 5th and 95th percentiles into standard percentage deltas.
- `main.py`: A `Streamlit` app that ingests the engine's payload, rendering a normalized Plotly Box trace as a proxy for the probability cones. It pairs the visual with a Pandas DataFrame representing the required Rank Table.

## How to Run
Ensure the dependencies defined in `requirements.txt` are installed. Run:
```bash
streamlit run tools/tide-chart/main.py
```

## Testing
Run the pytest suite to verify data normalization and the SPY anchoring math:
```bash
pytest tools/tide-chart/tests/
```
