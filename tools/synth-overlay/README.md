# Synth Overlay — Polymarket Edge Extension

Chrome extension that uses Chrome's **native Side Panel** to show Synth market context on Polymarket. The panel is data-first: Synth Up/Down prices, edge, confidence, signal explanation, and invalidation conditions.

## What it does

- **Native Side Panel**: Uses Chrome Side Panel API (`chrome.sidePanel`) instead of an in-page floating overlay.
- **Data-focused UI**: Shows Synth Up/Down prices, YES edge, confidence, explanation, and what would invalidate the signal.
- **Edge Alert Notifications**: Browser notifications when edge on any watched market exceeds a user-defined threshold. Alerts include asset, market type, edge size, and signal direction — click to jump straight to the Polymarket page.
- **Synth-sourced prices only**: Displays prices from the Synth API to avoid sync issues with DOM-scraped market data.
- **Manual + auto refresh**: Refresh button in panel plus automatic 30-second refresh. "Data as of" timestamp shows when the Synth data was generated.
- **Clear confidence colors**: red (&lt;40%), amber (40–70%), green (≥70%).
- **Contextual only**: Enabled on Polymarket pages; panel shows guidance when page/slug is unsupported.

## How it works

1. **Content script** (on `polymarket.com`) reads the market slug from the page URL.
2. **Side panel page** requests context from the content script and fetches Synth edge data from local API (`GET /api/edge?slug=...`).
3. **Panel rendering** displays Synth forecast data (prices, edge, signal, confidence, analysis, invalidation) and updates every 30s or on manual refresh.
4. **Background service worker** enables/disables side panel per-tab based on URL, and runs the alert polling engine.
5. **Alert engine** polls watched markets every 60s via `chrome.alarms`. When edge exceeds the user's threshold, fires a `chrome.notifications` alert. Clicking the notification opens the Polymarket page. A 5-minute cooldown per market prevents duplicate alerts.

## Synth API usage

- `get_polymarket_daily(asset)` — daily up/down (24h) Synth vs Polymarket.
- `get_polymarket_hourly(asset)` — hourly up/down (1h).
- `get_polymarket_15min(asset)` — 15-minute up/down (15m).
- `get_polymarket_5min(asset)` — 5-minute up/down (5m).
- `get_polymarket_range()` — range brackets with synth vs polymarket probability per bracket.
- `get_prediction_percentiles(asset, horizon)` — used for confidence scoring (forecast spread) and optional bias in explanations.

## Run locally

1. Install: `pip install -r requirements.txt` (from repo root: `pip install -r tools/synth-overlay/requirements.txt`).
2. Start server (from repo root): `python tools/synth-overlay/server.py` (or from `tools/synth-overlay`: `python server.py`). Listens on `127.0.0.1:8765`.
3. Load extension: Chrome → Extensions → Load unpacked → select `tools/synth-overlay/extension`.
4. Click the extension icon to open **Chrome Side Panel** (or pin and open from Side Panel UI). On Polymarket pages, the panel auto-enables.

## Verify the side panel (before recording)

1. **Check the API** (server must be running):
   ```bash
   curl -s "http://127.0.0.1:8765/api/edge?slug=bitcoin-up-or-down-on-february-26" | head -c 200
   ```
   You should see JSON with `"signal"`, `"edge_pct"`, etc. If you see `"error"` or 404, the slug is not supported for the current mock/API.

2. **Open the exact URL** in Chrome (with the extension loaded from `extension/`):
   - Daily (BTC): `https://polymarket.com/event/bitcoin-up-or-down-on-february-26`
   - Hourly (ETH): `https://polymarket.com/event/ethereum-up-or-down-february-25-6pm-et`
   - 15-Min (SOL): `https://polymarket.com/event/sol-updown-15m-1772204400`
   - The side panel requests the slug from the page and fetches Synth data from the local API. If API returns 200, panel fields populate.

3. **Interaction:**
   - Click the extension icon (or open Chrome Side Panel UI) to open the **native side panel**.
   - Panel shows: Synth Up/Down prices, edge, signal, confidence, analysis, invalidation, and data timestamp.
   - Use **↻ Refresh** for immediate sync; panel auto-refreshes every 30 seconds.

4. **Setting up alerts:**
   - In the side panel, toggle **Alerts** on.
   - Set your **Edge threshold** (default 3.0pp — only edges at or above this trigger notifications).
   - Click **+ Watch this market** on any loaded market to add it to your watchlist.
   - The background worker polls all watched markets every 60 seconds and fires browser notifications when edge exceeds your threshold.
   - Click any notification to open the corresponding Polymarket page with the side panel ready.

5. **If nothing appears:** Ensure (a) server is running, (b) you loaded the extension from `tools/synth-overlay/extension` (not the parent folder), (c) the address bar is exactly one of the supported URLs above. Open DevTools → Network: you should see a request to `127.0.0.1:8765/api/edge?slug=...` with status 200.

## Tests

From repo root: `python -m pytest tools/synth-overlay/tests/ -v`. Uses mock data; no API key required.
