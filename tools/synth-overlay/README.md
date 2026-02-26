# Synth Overlay — Polymarket Edge Extension

Chrome extension that adds a live "fair value" layer on Polymarket market pages using Synth forecasts. Shows whether the current YES price is **underpriced**, **overpriced**, or **fair** and exposes edge strength (Strong / Moderate / No Edge).

## What it does

- **Inline badge** next to the decision point: YES Edge +X% (green), -X% (red), or Fair ±0.5% (neutral).
- **Detail card** on click: horizon (1h / 24h), confidence, last update.
- **Contextual only**: Overlay appears only when the page slug maps to a Synth-supported market (daily up/down, hourly up/down, or range). Unsupported markets show nothing.

## How it works

1. **Extension** (content script on `polymarket.com`) reads the page URL and extracts the market slug.
2. **Local API** (Flask on `http://127.0.0.1:8765`) is called with `GET /api/edge?slug=...`. The server uses `SynthClient` (mock or live) to load Polymarket comparison data.
3. **Edge logic** computes `edge_pct = (synth_prob - market_prob) * 100` and classifies signal (underpriced / fair / overpriced) and strength (strong / moderate / none) from thresholds.
4. **Overlay** is injected only when the API returns 200; 404 (unsupported market) keeps the page unchanged.

## Synth API usage

- `get_polymarket_daily()` — daily up/down (24h) Synth vs Polymarket.
- `get_polymarket_hourly()` — hourly up/down (1h).
- `get_polymarket_range()` — range brackets with synth vs polymarket probability per bracket.
- `get_prediction_percentiles(asset, horizon)` — used for confidence scoring (forecast spread) and optional bias in explanations; wired for both up/down and range.

## Run locally

1. Install: `pip install -r requirements.txt` (from repo root: `pip install -r tools/synth-overlay/requirements.txt`).
2. Start server (from repo root): `python tools/synth-overlay/server.py` (or from `tools/synth-overlay`: `python server.py`). Listens on `127.0.0.1:8765`.
3. Load extension: Chrome → Extensions → Load unpacked → select `tools/synth-overlay/extension`.
4. Open a Polymarket event/market URL whose slug matches a supported market (e.g. `bitcoin-up-or-down-on-february-26` for mock daily). The badge appears when the server is running and the slug is supported.

## Verify the overlay (before recording)

1. **Check the API** (server must be running):
   ```bash
   curl -s "http://127.0.0.1:8765/api/edge?slug=bitcoin-up-or-down-on-february-26" | head -c 200
   ```
   You should see JSON with `"signal"`, `"edge_pct"`, etc. If you see `"error"` or 404, the slug is not supported for the current mock/API.

2. **Open the exact URL** in Chrome (with the extension loaded from `extension/`):
   - Daily (mock): `https://polymarket.com/event/bitcoin-up-or-down-on-february-26`
   - The extension reads the slug from the path and calls the API. If the API returned 200 in step 1, the **badge** is appended to the page (near the first element with class containing "market" or "outcome", or `main`, or the top of the body).

3. **Interaction:**
   - **Badge** = inline pill (e.g. "YES Edge +2.1%" or "Fair +0.3%") with strength (Strong/Moderate).
   - **Click the badge** → toggles the **detail card** (Now 1h / By close 24h, confidence bar, optional "No trade" row).
   - **Click "Details ▶"** in the card → opens the **slide-out panel** (explanation, invalidation, full confidence).

4. **If nothing appears:** Ensure (a) server is running, (b) you loaded the extension from `tools/synth-overlay/extension` (not the parent folder), (c) the address bar is exactly one of the supported URLs above. Open DevTools → Network: you should see a request to `127.0.0.1:8765/api/edge?slug=...` with status 200.

## Tests

From repo root: `python -m pytest tools/synth-overlay/tests/ -v`. Uses mock data; no API key required.
