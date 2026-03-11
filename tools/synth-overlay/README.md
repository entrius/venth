# Synth Overlay — Polymarket Edge Extension

Chrome extension that uses Chrome's **native Side Panel** to show Synth market context on Polymarket. The panel is data-first: Synth Up/Down prices, edge, confidence, signal explanation, and invalidation conditions.

## What it does

- **Native Side Panel**: Uses Chrome Side Panel API (`chrome.sidePanel`) instead of an in-page floating overlay.
- **Data-focused UI**: Shows Synth Up/Down prices, YES edge, confidence, explanation, and what would invalidate the signal.
- **Synth-sourced prices only**: Displays prices from the Synth API to avoid sync issues with DOM-scraped market data.
- **Manual + auto refresh**: Refresh button in panel plus automatic 15s refresh. "Data as of" timestamp shows when the Synth data was generated.
- **Clear confidence colors**: red (&lt;40%), amber (40–70%), green (≥70%).
- **Contextual only**: Enabled on Polymarket pages; panel shows guidance when page/slug is unsupported.

## How it works

1. **Content script** (on `polymarket.com`) reads the market slug from the page URL.
2. **Side panel page** requests context from the content script and fetches Synth edge data from local API (`GET /api/edge?slug=...`).
3. **Panel rendering** displays Synth forecast data (prices, edge, signal, confidence, analysis, invalidation) and updates every 15s or on manual refresh.
4. **Background service worker** enables/disables side panel per-tab based on URL and runs the alert polling engine.
5. **Edge alerts** poll watched markets every 60s via `chrome.alarms`. When edge exceeds the user's threshold, a browser notification fires with asset, edge size, signal direction, and confidence. Clicking the notification focuses or opens the relevant Polymarket page. Notifications are suppressed when the user is already viewing the market and have a 5-minute cooldown per market to avoid spam.

## Position Sizing (Kelly Criterion)

The **Position Sizing** card in the side panel answers "how much should I bet?" for a given Polymarket market.

### Balance detection

The content script (`content.js`) scrapes the user's wallet/account balance from the Polymarket DOM using three context-gated strategies:

1. Standalone dollar amounts (`$1,234.56`) near `balance/portfolio/available` keywords in parent elements.
2. USDC amounts (`1234.56 USDC`) with the same ancestor-context check.
3. Inline keyword-anchored text (`Balance $1,234.56`, `Available $500.00`).

The side panel pre-fills the **Balance** field with the scraped value when available. The user can override it manually; the value persists in `chrome.storage.local` across sessions. If no balance is detected or stored, the user enters it by hand.

### Inputs

For a given up/down market, the sizing logic uses:

- **p_synth** — Synth probability of YES (`synth_probability_up` from `/api/edge`).
- **p_market** — Polymarket-implied probability of YES (live DOM price when available, otherwise API snapshot).
- **confidence_score** — forecast confidence from `EdgeAnalyzer` in [0, 1].
- **balance** — user bankroll in USD/USDC.

### Expected value per $1

For a binary payoff where you pay `p_market` per share and receive $1 on success:

- YES side: `EV_yes = p_synth × b − (1 − p_synth)` where `b = (1 − p_market) / p_market`
- NO side: `EV_no = (1 − p_synth) × b_no − p_synth` where `b_no = p_market / (1 − p_market)`

The side with positive EV and positive Kelly fraction is preferred. If neither side is +EV, the UI shows **"No +EV"** and recommended size is $0.

### Kelly fraction

For each side, the Kelly-optimal fraction of bankroll to wager:

```
b = (1 − p_market) / p_market          # net odds on a winning $1 bet
f* = (b × p_true − (1 − p_true)) / b   # optimal fraction of bankroll
```

Where `p_true = p_synth` for YES, `p_true = 1 − p_synth` for NO (with corresponding `p_market`).

### Confidence scaling and risk cap

- Raw Kelly fraction is **scaled by forecast confidence**: `f_scaled = f* × confidence_score`.
- `confidence_score` reflects forecast distribution width across horizons (from `EdgeAnalyzer.compute_confidence`).
- A hard **cap of 20%** of bankroll is enforced: `f_final = min(f_scaled, 0.20)`.
- Final position size: `size = balance × f_final`.

### UI display

- **Side**: `YES` (green), `NO` (red), or `No +EV` (gray).
- **Kelly fraction**: `f_final` as percentage of bankroll.
- **Recommended size**: `balance × f_final` in USD.
- **EV per $1**: expected value per dollar wagered, in cents.
- **Cap note**: "Position capped at 20% of bankroll" shown only when the cap is active.

### Live updates

Sizing recalculates instantly when:
- Live Polymarket prices change (via DOM observation + 1s polling).
- The user edits their balance (on every keystroke).

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
   - Use **↻ Refresh** for immediate sync; panel auto-refreshes every 15 seconds.

4. **If nothing appears:** Ensure (a) server is running, (b) you loaded the extension from `tools/synth-overlay/extension` (not the parent folder), (c) the address bar is exactly one of the supported URLs above. Open DevTools → Network: you should see a request to `127.0.0.1:8765/api/edge?slug=...` with status 200.

## Tests

From repo root: `python -m pytest tools/synth-overlay/tests/ -v`. Uses mock data; no API key required.
