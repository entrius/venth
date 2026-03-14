# Synth Overlay — Best Prediction Markets Tool Submission

---

## 2. Technical Details (1-page)

### Architecture Overview

Synth Overlay is a Chrome extension with a Python backend, following a platform registry architecture that cleanly separates platform-specific logic from the core edge analysis. The system has three layers:

1. **Python Server (`server.py`, `analyzer.py`, `matcher.py`, `edge.py`)** — A lightweight HTTP server exposing a single edge endpoint (`GET /api/edge?slug=...&platform=...`). The `matcher.py` module implements a platform registry pattern: each platform (Polymarket, Kalshi) is a self-contained entry with its own URL patterns, asset maps, slug normalization, and market-type detection (daily, hourly, 15-min, 5-min, range). The `analyzer.py` `EdgeAnalyzer` fetches Synth data for the matched asset and horizon, computes edge, confidence, signal direction, and invalidation conditions. Adding a new prediction market platform requires adding one registry entry — no scattered if/else logic.

2. **Chrome Extension (`extension/`)** — Uses Chrome's native Side Panel API (`chrome.sidePanel`) rather than injecting DOM elements. A content script detects the active platform from the hostname, extracts the market slug via platform-specific URL parsing, and scrapes live market prices and wallet balance from the DOM. The side panel page communicates with the content script and the local Python server to render edge data, position sizing, and alerts.

3. **Alert Engine (`background.js`)** — A service worker using `chrome.alarms` to poll watched markets every 60 seconds. When edge exceeds the user's threshold, a browser notification fires with contextual info (asset, edge, signal, confidence). Notifications have a 5-minute cooldown per market and are suppressed when the user is already viewing the market.

### How Synth API Is Integrated

The server consumes multiple Synth prediction market endpoints depending on the detected market type:
- **`get_polymarket_daily/hourly/15min/5min(asset)`** — Returns Synth vs. market Up/Down probabilities at the matched time horizon. This is the primary edge source.
- **`get_polymarket_range()`** — For range-bracket markets, provides Synth vs. market probability per price bracket.
- **`get_prediction_percentiles(asset, horizon)`** — Used by the confidence scoring algorithm; forecast distribution width determines how certain the signal is.

The edge computation is straightforward: `edge = synth_probability - market_probability`. This raw edge, combined with confidence from the percentile spread, feeds both the signal explanation and the Kelly position sizer.

### Data Consumption Approach

The extension follows a "Synth-sourced prices only" principle — it never uses DOM-scraped market prices for the edge calculation, only for displaying what the market currently shows. This avoids sync issues between stale DOM data and fresh Synth data. The confidence score is derived from the width of Synth's percentile distribution: a narrow spread means high confidence (Synth is sure about the outcome), while a wide spread means low confidence. This confidence directly scales the Kelly fraction, preventing large bets on uncertain signals.

### Key Insights

- **The prediction market use case is uniquely well-suited to Synth.** Binary outcome markets have a natural probability interpretation, and Synth's probabilistic forecasts map directly to "fair" YES/NO prices — making edge computation both rigorous and intuitive.
- **Kelly sizing with confidence scaling is the responsible edge.** Raw Kelly sizing can be aggressive; scaling by Synth's confidence score and capping at 20% of bankroll makes the sizing recommendations practical and robust to forecast noise.
- **Platform registry pattern enables rapid expansion.** The clean separation of platform-specific logic means supporting new prediction market platforms (e.g. Manifold, Metaculus) is a single-file addition.
- **Native Side Panel > DOM injection.** Using Chrome's Side Panel API avoids UI breakage when Polymarket/Kalshi updates their frontend, and provides a cleaner user experience.

---

## 3. What problem does your project solve?

Prediction market traders on Polymarket and Kalshi have no easy way to compare market-implied probabilities against independent probabilistic forecasts — they're trading on gut feel, news sentiment, or stale analysis. Synth Overlay solves this by surfacing the exact numerical edge between Synth's forecasts and market prices directly in the browser as the user browses markets. It goes beyond showing the edge by answering the critical follow-up question: "how much should I bet?" — using Kelly-optimal position sizing scaled by forecast confidence and the user's actual wallet balance, preventing both over-betting on weak signals and under-betting on strong ones.

## 4. What makes your project unique?

Synth Overlay is the only tool that embeds probabilistic forecast edge directly into the prediction market browsing experience via Chrome's native Side Panel — no separate dashboard, no copy-pasting slugs, no context switching. The combination of real-time edge computation, confidence-aware Kelly position sizing, multi-platform support (Polymarket + Kalshi), and a background alert engine that notifies traders when edge exceeds their threshold creates a complete edge-detection-to-sizing workflow that doesn't exist anywhere else. The platform registry architecture also makes it trivially extensible — adding a new prediction market platform is a single registry entry on each side (Python server + JS extension), not a codebase-wide refactor.
