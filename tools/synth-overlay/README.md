# Synth Overlay — Polymarket & Kalshi Edge & Position Sizing Extension

Chrome extension that uses Chrome's **native Side Panel** to show Synth market context on **Polymarket** and **Kalshi**, and convert that edge into a **concrete position size**. The panel is data-first: Synth Up/Down prices, edge, confidence, signal explanation, invalidation conditions, and a **Kelly-based position sizing calculator**.

## What it does

- **Native Side Panel**: Uses Chrome Side Panel API (`chrome.sidePanel`) instead of an in-page floating overlay.
- **Data-focused edge UI**: Shows Synth Up/Down prices, YES edge, confidence, explanation, and what would invalidate the signal.
- **Multi-platform**: Works on both **Polymarket** (polymarket.com) and **Kalshi** (kalshi.com) — the two largest prediction markets. The side panel dynamically adapts column labels and scraping strategy per platform.
- **Balance-aware position sizing**: Reads (or lets the user set) wallet/account balance and recommends a **Kelly-optimal position size** based on Synth vs market probabilities and forecast confidence.
- **Synth-sourced prices only**: Displays prices from the Synth API to avoid sync issues with DOM-scraped market data.
- **Manual + auto refresh**: Refresh button in panel plus automatic 15s refresh. "Data as of" timestamp shows when the Synth data was generated.
- **Clear confidence colors**: red (&lt;40%), amber (40–70%), green (≥70%).
- **Contextual only**: Enabled on Polymarket and Kalshi pages; panel shows guidance when page/slug is unsupported.

## How it works

1. **Content script** (on `polymarket.com` and `kalshi.com`) reads the market slug from the page URL and scrapes **live prices** and **balance** from the DOM. Platform detection is automatic. The content script guards all `chrome.runtime` calls against extension context invalidation (e.g. after extension reload) — when the context becomes invalid, the observer, polling interval, and message listeners stop gracefully.
2. **Side panel page** requests context from the content script and fetches Synth edge data from local API (`GET /api/edge?slug=...&platform=...`).
3. **Panel rendering** displays Synth forecast data (prices, edge, signal, confidence, analysis, invalidation) and updates every 15s or on manual refresh. Column headers adapt to show "Poly" or "Kalshi" based on the active platform.
4. **Position Sizing card** combines Synth probabilities, market-implied odds, forecast confidence, and user balance into a Kelly-based recommendation.
5. **Background service worker** enables/disables side panel per-tab based on URL (both platforms) and runs the alert polling engine.
6. **Edge alerts** poll watched markets every 60s via `chrome.alarms`. When edge exceeds the user's threshold, a browser notification fires with asset, edge size, signal direction, and confidence. Clicking the notification focuses or opens the relevant market page on the correct platform. Notifications are suppressed when the user is already viewing the market and have a 5-minute cooldown per market to avoid spam.

## Platform support

| Platform | Domain | Market types | Assets |
|----------|--------|-------------|--------|
| **Polymarket** | polymarket.com | daily, hourly, 15min, 5min, range | BTC, ETH, SOL, XRP |
| **Kalshi** | kalshi.com | daily, 15min, range | BTC, ETH, SOL, SPY, NVDA, TSLA, AAPL, GOOGL, XAU |

### Kalshi market matching

Kalshi uses structured ticker names instead of descriptive slugs:
- `KXBTCD-26MAR1317` → BTC daily above/below (event ticker)
- `KXBTCD-26MAR1317-T70499.99` → BTC daily above/below at $70,499.99 strike (contract ticker, `-T` = threshold)
- `KXBTC-26MAR1317-B76750` → BTC range bracket at $76,750 (contract ticker, `-B` = bracket)
- `KXBTC15M-26MAR121930-30` → BTC 15-minute market
- `KXETHD-26MAR1317` → ETH daily above/below
- `KXSOLD-26MAR1317` → SOL daily above/below

Series tickers (used for browsing): `kxbtcd`, `kxbtc`, `kxbtc15m`, `kxethd`, `kxeth`, `kxsold`, `kxsol`, etc.

Legacy tickers without `KX` prefix also exist: `BTCD`, `BTC`, `ETH`, `ETHD`. To avoid collisions with Polymarket slugs (e.g. `btc-updown-5m-...`), legacy tickers are only matched when followed by a Kalshi-style date suffix (digits + letters, e.g. `-26MAR1317`).

The matcher extracts the asset from the series prefix (e.g. `KXBTCD` → BTC) and infers market type:
- Series ending in `D` (KXBTCD, KXETHD, KXSOLD) → daily above/below
- Series without `D` (KXBTC, KXETH, KXSOL) → range
- Series ending in `15M` (KXBTC15M, KXETH15M) → 15-minute

Kalshi URLs can have multiple path segments (e.g. `/markets/kxsol15m/solana-15-minutes/kxsol15m-26mar121945`); both `normalize_slug` and the content script extract the **last segment** as the ticker.

## Synth API usage

- `get_polymarket_daily(asset)` — daily up/down (24h) Synth vs Polymarket.
- `get_polymarket_hourly(asset)` — hourly up/down (1h).
- `get_polymarket_15min(asset)` — 15-minute up/down (15m).
- `get_polymarket_5min(asset)` — 5-minute up/down (5m).
- `get_polymarket_range()` — range brackets with synth vs polymarket probability per bracket.
- `get_prediction_percentiles(asset, horizon)` — used for confidence scoring (forecast spread) and optional bias in explanations.

## Position sizing & Kelly Criterion

The **Position Sizing** card in the side panel answers “how much should I bet?” for a given Polymarket market.

### Balance detection

- The content script (`content.js`) runs on `polymarket.com` and:
  - Scrapes **wallet / account balance** from compact DOM text such as `Balance 123.45 USDC` or `$123.45`.
  - Exposes this numeric balance as `balance` in the context returned to the side panel.
- In the side panel (`sidepanel.html` / `sidepanel.js`):
  - The **Balance** field is pre-filled with the scraped value when available.
  - The user can override it manually; the value is persisted in `chrome.storage.local` so it survives reloads.

If no balance can be detected or stored, the user can still enter it by hand and the sizing logic remains the same.

### Inputs used for sizing

For a given up/down market, the side panel uses:

- `p_synth` — Synth probability of **YES** (`synth_probability_up` from `/api/edge`).
- `p_market` — Market-implied probability of **YES** (from Synth server or live DOM price on Polymarket/Kalshi).
- `confidence_score` — forecast confidence from `EdgeAnalyzer` in \[0, 1].
- `balance` — user bankroll in USD/USDC (scraped or user-entered).

### Expected value per \$1

Assuming a binary payoff (cost = `p_market`, payout = 1 on success), the expected value per \$1 wager is:

- YES side: \(\mathrm{EV}_{\text{YES}} = p_{\text{synth}} - p_{\text{market}}\)
- NO side:  \(\mathrm{EV}_{\text{NO}} = (1 - p_{\text{synth}}) - (1 - p_{\text{market}})\)

The side with **positive EV** and positive Kelly fraction is preferred; if neither side has positive EV, the UI clearly shows **“No +EV”** and the recommended size is \$0.

### Kelly fraction

For each side, we compute the **Kelly fraction**:

- Market-implied odds:  
  \[
  b = \frac{1 - p_{\text{market}}}{p_{\text{market}}}
  \]
- True edge (per Kelly):  
  \[
  f^* = \frac{b \cdot p_{\text{true}} - (1 - p_{\text{true}})}{b}
  \]

Where:

- For YES, \(p_{\text{true}} = p_{\text{synth}}\), \(p_{\text{market}} = p_{\text{market}}\).
- For NO, \(p_{\text{true}} = 1 - p_{\text{synth}}\), \(p_{\text{market}} = 1 - p_{\text{market}}\).

The extension computes `f_yes` and `f_no`, discards non-positive or non-finite values, and chooses the side with:

- Positive expected value, and
- Positive Kelly fraction.

### Confidence scaling and risk cap

To keep sizing realistic and robust to noisy forecasts:

- The raw Kelly fraction is **scaled by forecast confidence**:
  \[
  f_{\text{scaled}} = \mathrm{clamp}(f^* \cdot \text{confidence\_score}, 0, f_{\max})
  \]
- `confidence_score` comes from `EdgeAnalyzer.compute_confidence` and reflects forecast distribution width across 1h/24h horizons.
- `f_max` is a hard **cap of 20%** of bankroll (0.2) to avoid extreme sizing even when edge appears very large.

The final recommended position size is then:

\[
\text{size} = \text{balance} \times f_{\text{scaled}}
\]

The UI shows:

- **Kelly Side**: `YES`, `NO`, or `No +EV`.
- **Kelly Fraction**: `f_scaled` as a percentage of balance.
- **Size**: \(\text{balance} \times f_{\text{scaled}}\) in USD.
- **EV per \$**: \(\mathrm{EV}_{\text{YES}}\) or \(\mathrm{EV}_{\text{NO}}\) in cents.

## Run locally

1. Install: `pip install -r requirements.txt` (from repo root: `pip install -r tools/synth-overlay/requirements.txt`).
2. Start server (from repo root): `python tools/synth-overlay/server.py` (or from `tools/synth-overlay`: `python server.py`). Listens on `127.0.0.1:8765`.
3. Load extension: Chrome → Extensions → Load unpacked → select `tools/synth-overlay/extension`.
4. Click the extension icon to open **Chrome Side Panel** (or pin and open from Side Panel UI). On Polymarket and Kalshi pages, the panel auto-enables.

## Verify the side panel (before recording)

1. **Check the API** (server must be running):
   ```bash
   # Polymarket slug
   curl -s "http://127.0.0.1:8765/api/edge?slug=bitcoin-up-or-down-on-february-26" | head -c 200
   # Kalshi ticker
   curl -s "http://127.0.0.1:8765/api/edge?slug=KXBTCD-26MAR1317&platform=kalshi" | head -c 200
   ```
   You should see JSON with `"signal"`, `"edge_pct"`, `"platform"`, etc. If you see `"error"` or 404, the slug is not supported for the current mock/API.

2. **Open the exact URL** in Chrome (with the extension loaded from `extension/`):
   - **Polymarket:**
     - Daily (BTC): `https://polymarket.com/event/bitcoin-up-or-down-on-february-26`
     - Hourly (ETH): `https://polymarket.com/event/ethereum-up-or-down-february-25-6pm-et`
     - 15-Min (SOL): `https://polymarket.com/event/sol-updown-15m-1772204400`
   - **Kalshi:**
     - Daily (BTC): `https://kalshi.com/markets/kxbtcd`
     - Daily (ETH): `https://kalshi.com/markets/kxethd`
     - 15-Min (BTC): `https://kalshi.com/markets/kxbtc15m`
   - The side panel requests the slug from the page and fetches Synth data from the local API. If API returns 200, panel fields populate.

3. **Interaction:**
   - Click the extension icon (or open Chrome Side Panel UI) to open the **native side panel**.
   - Panel shows: Synth Up/Down prices, edge, signal, confidence, analysis, invalidation, and data timestamp.
   - Use **↻ Refresh** for immediate sync; panel auto-refreshes every 15 seconds.

4. **If nothing appears:** Ensure (a) server is running, (b) you loaded the extension from `tools/synth-overlay/extension` (not the parent folder), (c) the address bar is exactly one of the supported URLs above. Open DevTools → Network: you should see a request to `127.0.0.1:8765/api/edge?slug=...` with status 200.

## Tests

From repo root: `python -m pytest tools/synth-overlay/tests/ -v`. Uses mock data; no API key required.
