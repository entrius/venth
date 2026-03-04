"use strict";

const API_BASE = "http://127.0.0.1:8765";

const els = {
  statusText: document.getElementById("statusText"),
  synthUp: document.getElementById("synthUp"),
  synthDown: document.getElementById("synthDown"),
  polyUp: document.getElementById("polyUp"),
  polyDown: document.getElementById("polyDown"),
  deltaUp: document.getElementById("deltaUp"),
  deltaDown: document.getElementById("deltaDown"),
  edgeValue: document.getElementById("edgeValue"),
  signal5m: document.getElementById("signal5m"),
  signal15m: document.getElementById("signal15m"),
  signal1h: document.getElementById("signal1h"),
  signal24h: document.getElementById("signal24h"),
  strength: document.getElementById("strength"),
  assetName: document.getElementById("assetName"),
  marketType: document.getElementById("marketType"),
  confFill: document.getElementById("confFill"),
  confText: document.getElementById("confText"),
  analysisText: document.getElementById("analysisText"),
  noTrade: document.getElementById("noTrade"),
  invalidationText: document.getElementById("invalidationText"),
  lastUpdate: document.getElementById("lastUpdate"),
  refreshBtn: document.getElementById("refreshBtn"),
  pollProgress: document.getElementById("pollProgress"),
};

function fmtCentsFromProb(p) {
  if (p == null || p === undefined) return "—";
  return Math.round(p * 100) + "¢";
}

function fmtEdge(v) {
  if (v == null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v + "%";
}

function fmtApiTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleTimeString() + " " + d.toLocaleDateString();
}

function confidenceColor(score) {
  if (score >= 0.7) return "#22c55e";
  if (score >= 0.4) return "#f59e0b";
  return "#ef4444";
}

function fmtDelta(synth, poly) {
  if (synth == null || poly == null) return { text: "—", cls: "" };
  var diff = Math.round((synth - poly) * 100);
  var sign = diff >= 0 ? "+" : "";
  return { 
    text: sign + diff + "%", 
    cls: diff > 0 ? "positive" : diff < 0 ? "negative" : "" 
  };
}

async function activeSupportedTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs && tabs[0];
  if (!tab || !tab.url || !tab.url.startsWith("https://polymarket.com/")) return null;
  return tab;
}

async function getContextFromPage(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "synth:getContext" });
    return response && response.ok ? response.context : null;
  } catch (_e) {
    return null;
  }
}

async function fetchEdge(slug, livePrices) {
  var url = API_BASE + "/api/edge?slug=" + encodeURIComponent(slug);
  // Pass live prices to server if available for real-time edge calculation
  if (livePrices && livePrices.upPrice != null) {
    url += "&live_prob_up=" + encodeURIComponent(livePrices.upPrice);
  }
  const res = await fetch(url);
  if (!res.ok) return null;
  return await res.json();
}

function render(state) {
  els.statusText.textContent = state.status;
  els.synthUp.textContent = state.synthUp;
  els.synthDown.textContent = state.synthDown;
  els.polyUp.textContent = state.polyUp || "—";
  els.polyDown.textContent = state.polyDown || "—";
  els.deltaUp.textContent = state.deltaUp ? state.deltaUp.text : "—";
  els.deltaUp.className = "delta " + (state.deltaUp ? state.deltaUp.cls : "");
  els.deltaDown.textContent = state.deltaDown ? state.deltaDown.text : "—";
  els.deltaDown.className = "delta " + (state.deltaDown ? state.deltaDown.cls : "");
  els.edgeValue.textContent = state.edge;
  els.signal5m.textContent = state.signal5m || "—";
  els.signal15m.textContent = state.signal15m || "—";
  els.signal1h.textContent = state.signal1h || "—";
  els.signal24h.textContent = state.signal24h || "—";
  els.strength.textContent = state.strength;
  els.assetName.textContent = state.asset || "—";
  els.marketType.textContent = state.marketType || "—";
  els.analysisText.textContent = state.analysis;
  els.noTrade.classList.toggle("hidden", !state.noTrade);
  els.invalidationText.textContent = state.invalidation;
  els.lastUpdate.textContent = state.lastUpdate;
  els.confFill.style.width = state.confPct + "%";
  els.confFill.style.background = state.confColor;
  els.confText.textContent = state.confText;
}

const EMPTY = {
  synthUp: "—", synthDown: "—", polyUp: "—", polyDown: "—",
  deltaUp: null, deltaDown: null, edge: "—",
  signal5m: "—", signal15m: "—", signal1h: "—", signal24h: "—",
  strength: "—", asset: "—", marketType: "—",
  analysis: "—", noTrade: false, invalidation: "—",
  confPct: 0, confColor: "#9ca3af", confText: "—",
  lastUpdate: "—",
};

async function refresh() {
  render(Object.assign({}, EMPTY, { status: "Refreshing…" }));

  const tab = await activeSupportedTab();
  if (!tab) {
    render(Object.assign({}, EMPTY, {
      status: "Open a Polymarket event tab to view Synth data.",
      analysis: "No active market tab found.",
    }));
    return;
  }

  const ctx = await getContextFromPage(tab.id);
  if (!ctx || !ctx.slug) {
    render(Object.assign({}, EMPTY, {
      status: "Could not read market context from page.",
      analysis: "Reload the page and try refresh again.",
    }));
    return;
  }

  const edge = await fetchEdge(ctx.slug, ctx.livePrices);
  if (!edge || edge.error) {
    render(Object.assign({}, EMPTY, {
      status: "Market not supported by Synth for this slug.",
      analysis: edge && edge.error ? edge.error : "No data",
    }));
    return;
  }

  var synthProbUp = edge.synth_probability_up != null ? edge.synth_probability_up : edge.synth_probability;
  var conf = edge.confidence_score != null ? edge.confidence_score : 0.5;
  var confPct = Math.round(conf * 100);
  var horizon = edge.horizon || "24h";
  var mtype = edge.market_type || "daily";
  var asset = edge.asset || "BTC";

  // Log live price status for debugging
  console.log("[Synth-Overlay] Edge response:", { 
    live_price_used: edge.live_price_used, 
    polymarket_prob: edge.polymarket_probability_up,
    livePricesFromDOM: ctx.livePrices 
  });

  // Get Polymarket price (from API response)
  var polyProbUp = edge.polymarket_probability_up;
  var polyProbDown = polyProbUp != null ? 1 - polyProbUp : null;

  // Calculate deltas (Synth - Poly)
  var deltaUp = fmtDelta(synthProbUp, polyProbUp);
  var deltaDown = fmtDelta(synthProbUp != null ? 1 - synthProbUp : null, polyProbDown);

  // Fetch all timeframes for this asset (in parallel)
  var tfSlugs = {
    "5m": asset.toLowerCase() + "-updown-5m-" + Date.now(),
    "15m": asset.toLowerCase() + "-updown-15m-" + Date.now(),
    "1h": asset.toLowerCase() + "-updown-1h-" + Date.now(),
    "24h": asset.toLowerCase() + "-updown-24h-" + Date.now(),
  };
  
  // Build signals from response - map current market type to its slot
  var signals = { "5m": "—", "15m": "—", "1h": "—", "24h": "—" };
  var tfKey = mtype === "5min" ? "5m" : mtype === "15min" ? "15m" : mtype === "hourly" ? "1h" : "24h";
  signals[tfKey] = (edge.signal || "—") + " " + fmtEdge(edge.edge_pct);
  
  // If we have dual-horizon data, populate both
  if (edge.signal_1h && edge.signal_24h) {
    signals["1h"] = edge.signal_1h + " " + fmtEdge(edge.edge_1h_pct);
    signals["24h"] = edge.signal_24h + " " + fmtEdge(edge.edge_24h_pct);
  }

  var liveStatus = edge.live_price_used ? " (Live)" : "";
  render({
    status: "Synced — " + asset + " " + horizon + " forecast." + liveStatus,
    synthUp: fmtCentsFromProb(synthProbUp),
    synthDown: synthProbUp == null ? "—" : fmtCentsFromProb(1 - synthProbUp),
    polyUp: fmtCentsFromProb(polyProbUp),
    polyDown: fmtCentsFromProb(polyProbDown),
    deltaUp: deltaUp,
    deltaDown: deltaDown,
    edge: fmtEdge(edge.edge_pct),
    signal5m: signals["5m"],
    signal15m: signals["15m"],
    signal1h: signals["1h"],
    signal24h: signals["24h"],
    strength: edge.strength || "—",
    asset: asset,
    marketType: mtype,
    analysis: edge.explanation || "No explanation available.",
    invalidation: edge.invalidation || "—",
    noTrade: !!edge.no_trade_warning,
    confPct: confPct,
    confColor: confidenceColor(conf),
    confText: (conf >= 0.7 ? "High" : conf >= 0.4 ? "Medium" : "Low") + " (" + confPct + "%)",
    lastUpdate: fmtApiTime(edge.current_time),
  });
  
  // Reset and start poll progress animation
  startPollProgress();
}

els.refreshBtn.addEventListener("click", function() {
  stopPollProgress();
  refresh();
});

// Polling frequency: Synth API updates forecasts every ~60 seconds for short-term markets.
// We poll every 30 seconds to balance freshness vs API load.
const SYNTH_POLL_INTERVAL_MS = 30000;

// Poll progress bar animation
var pollTimer = null;
var pollStart = 0;

function startPollProgress() {
  stopPollProgress();
  pollStart = Date.now();
  els.pollProgress.style.transition = "none";
  els.pollProgress.style.width = "0%";
  // Force reflow then animate
  void els.pollProgress.offsetWidth;
  els.pollProgress.style.transition = "width " + (SYNTH_POLL_INTERVAL_MS / 1000) + "s linear";
  els.pollProgress.style.width = "100%";
}

function stopPollProgress() {
  els.pollProgress.style.transition = "none";
  els.pollProgress.style.width = "0%";
}

// Start polling
refresh();
setInterval(refresh, SYNTH_POLL_INTERVAL_MS);
