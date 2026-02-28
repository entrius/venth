# Synth Overlay: Polymarket Edge Extension

## Overview
This tool is a Chrome Extension that injects live probabilistic edge data directly onto Polymarket assets by querying a local API bridge connected to the `SynthClient`.

## Architecture
1. **API Bridge (`api.py`)**: A lightweight Python FastAPI server that uses `SynthClient.get_polymarket_daily()` to fetch the mock Synth/Polymarket divergence. It calculates the `edge_up` and issues a `BUY YES`, `BUY NO`, or `NO TRADE` recommendation.
2. **Chrome Extension (`extension/`)**: A standard manifest V3 extension with a `content.js` script that queries `http://127.0.0.1:8000/api/edge` and injects a floating UI badge onto the Polymarket DOM.

## How to Run
**1. Start the API Bridge:**
```bash
python tools/synth-overlay/api.py
```
This will run the server on `127.0.0.1:8000`.

**2. Load the Extension:**
- Open Google Chrome and navigate to `chrome://extensions/`
- Enable "Developer mode" in the top right.
- Click "Load unpacked" and select the `tools/synth-overlay/extension` folder.
- Navigate to `https://polymarket.com/event/some-event`. You will see the Synth Data Edge badge appear in the bottom right!

## Testing
Run the pytest suite to verify the API bridge calculations:
```bash
pytest tools/synth-overlay/tests/
```
