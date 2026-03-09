"use strict";

const API_BASE = "http://127.0.0.1:8765";

// Cached data for instant recalculation when live prices change
var cachedSynthData = null;
var cachedMarketType = null;
var currentSlug = null;

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
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.json();
  } catch (_e) {
    console.log("[Synth-Overlay] API unreachable — is the server running?");
    return null;
  }
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
  // Bold the primary timeframe row
  els.signal5m.parentElement.classList.toggle("primary-tf", state.primaryTf === "5m");
  els.signal15m.parentElement.classList.toggle("primary-tf", state.primaryTf === "15m");
  els.signal1h.parentElement.classList.toggle("primary-tf", state.primaryTf === "1h");
  els.signal24h.parentElement.classList.toggle("primary-tf", state.primaryTf === "24h");
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
  primaryTf: null,
  strength: "—", asset: "—", marketType: "—",
  analysis: "—", noTrade: false, invalidation: "—",
  confPct: 0, confColor: "#9ca3af", confText: "—",
  lastUpdate: "—",
};

// Calculate edge percentage from Synth and Polymarket probabilities
function calcEdgePct(synthProb, polyProb) {
  if (synthProb == null || polyProb == null) return null;
  return Math.round((synthProb - polyProb) * 100);
}

// Update UI instantly when live prices change (without full API refresh)
function updateWithLivePrice(livePrices) {
  if (!cachedSynthData || !livePrices) return;
  
  var synthProbUp = cachedSynthData.synth_probability_up;
  var polyProbUp = livePrices.upPrice;
  var polyProbDown = livePrices.downPrice;
  
  // Recalculate edge with live price
  var edgePct = calcEdgePct(synthProbUp, polyProbUp);
  var signal = edgePct > 0 ? "underpriced" : edgePct < 0 ? "overpriced" : "fair";
  
  // Update Polymarket prices
  els.polyUp.textContent = fmtCentsFromProb(polyProbUp);
  els.polyDown.textContent = fmtCentsFromProb(polyProbDown);
  
  // Update deltas
  var deltaUp = fmtDelta(synthProbUp, polyProbUp);
  var deltaDown = fmtDelta(synthProbUp != null ? 1 - synthProbUp : null, polyProbDown);
  els.deltaUp.textContent = deltaUp.text;
  els.deltaUp.className = "delta " + deltaUp.cls;
  els.deltaDown.textContent = deltaDown.text;
  els.deltaDown.className = "delta " + deltaDown.cls;
  
  // Update edge
  els.edgeValue.textContent = fmtEdge(edgePct);
  
  // Update primary timeframe signal with new edge
  var tfKey = cachedMarketType === "5min" ? "5m" : cachedMarketType === "15min" ? "15m" : 
              cachedMarketType === "hourly" ? "1h" : "24h";
  var tfMap = { "5m": els.signal5m, "15m": els.signal15m, "1h": els.signal1h, "24h": els.signal24h };
  if (tfMap[tfKey]) tfMap[tfKey].textContent = signal + " " + fmtEdge(edgePct);
  
  // Update status to show live
  els.statusText.textContent = els.statusText.textContent.replace(/ \(Live\)$/, "") + " (Live)";
  
  console.log("[Synth-Overlay] Live price update:", { polyProbUp, edgePct, signal });
}

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

  // Cache Synth data for instant live price updates
  cachedSynthData = edge;
  cachedMarketType = mtype;
  currentSlug = ctx.slug;

  // Get Polymarket price (prefer live DOM price over API response)
  var polyProbUp = ctx.livePrices ? ctx.livePrices.upPrice : edge.polymarket_probability_up;
  var polyProbDown = polyProbUp != null ? 1 - polyProbUp : null;

  // Calculate deltas (Synth - Poly)
  var deltaUp = fmtDelta(synthProbUp, polyProbUp);
  var deltaDown = fmtDelta(synthProbUp != null ? 1 - synthProbUp : null, polyProbDown);

  // Build signals from all timeframes returned by server
  var signals = { "5m": "—", "15m": "—", "1h": "—", "24h": "—" };
  var tfKey = mtype === "5min" ? "5m" : mtype === "15min" ? "15m" : mtype === "hourly" ? "1h" : "24h";
  if (edge.timeframes) {
    for (var tf in edge.timeframes) {
      var tfData = edge.timeframes[tf];
      if (tfData && tfData.signal != null) {
        signals[tf] = tfData.signal + " " + fmtEdge(tfData.edge_pct);
      }
    }
  } else {
    // Fallback: use primary edge only
    signals[tfKey] = (edge.signal || "—") + " " + fmtEdge(edge.edge_pct);
  }

  var liveStatus = ctx.livePrices ? " (Live)" : "";
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
    primaryTf: tfKey,
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

  // Update watch button state for alerts UI
  updateWatchBtnState();
}

els.refreshBtn.addEventListener("click", function() {
  stopPollProgress();
  refresh();
});

// Poll the API every 30 seconds to stay reasonably fresh without hammering the server
const SYNTH_POLL_INTERVAL_MS = 30000;

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

// Handle live price pushes and URL changes from the content script
chrome.runtime.onMessage.addListener(function(message, sender, sendResponse) {
  if (!message) return;
  if (message.type === "synth:priceUpdate") {
    console.log("[Synth-Overlay] Received live price update (push):", message.prices);
    updateWithLivePrice(message.prices);
  }
  if (message.type === "synth:urlChanged") {
    console.log("[Synth-Overlay] URL changed detected:", message.slug);
    if (message.slug !== currentSlug) {
      cachedSynthData = null;
      cachedMarketType = null;
      currentSlug = null;
      lastPollPrices = { upPrice: null, downPrice: null };
      stopPollProgress();
      refresh();
    }
  }
});

// Full navigation (not SPA) triggers a fresh fetch
chrome.tabs.onUpdated.addListener(function(tabId, changeInfo, tab) {
  if (changeInfo.url && tab.active && tab.url && tab.url.startsWith("https://polymarket.com/")) {
    console.log("[Synth-Overlay] Tab URL updated:", changeInfo.url);
    cachedSynthData = null;
    cachedMarketType = null;
    currentSlug = null;
    lastPollPrices = { upPrice: null, downPrice: null };
    stopPollProgress();
    refresh();
  }
});

// Poll live prices from content script every 1s as a reliable fallback
var lastPollPrices = { upPrice: null, downPrice: null };
setInterval(async function() {
  if (!cachedSynthData) return;
  var tab = await activeSupportedTab();
  if (!tab) return;
  try {
    var resp = await chrome.tabs.sendMessage(tab.id, { type: "synth:getPrices" });
    if (resp && resp.ok && resp.prices) {
      if (resp.prices.upPrice !== lastPollPrices.upPrice || resp.prices.downPrice !== lastPollPrices.downPrice) {
        lastPollPrices = { upPrice: resp.prices.upPrice, downPrice: resp.prices.downPrice };
        console.log("[Synth-Overlay] Live price poll update:", resp.prices);
        updateWithLivePrice(resp.prices);
      }
    }
  } catch (_e) {}
}, 1000);

// ---- Alerts UI ----
// Toggle, threshold input, and watchlist management

var alertEls = {
  enabled: document.getElementById("alertsEnabled"),
  body: document.getElementById("alertsBody"),
  threshold: document.getElementById("alertThreshold"),
  watchlist: document.getElementById("watchlist"),
  watchBtn: document.getElementById("watchBtn"),
};

var MAX_WATCHLIST = 15;

function renderWatchlist(list) {
  alertEls.watchlist.innerHTML = "";
  if (list.length === 0) {
    var hint = document.createElement("div");
    hint.className = "watch-empty";
    hint.textContent = "No markets watched yet";
    alertEls.watchlist.appendChild(hint);
  }
  list.forEach(function (item) {
    var row = document.createElement("div");
    row.className = "watch-item";
    var label = document.createElement("span");
    label.textContent = item.label || item.slug;
    var btn = document.createElement("button");
    btn.className = "watch-remove";
    btn.textContent = "\u00d7";
    btn.title = "Remove";
    btn.addEventListener("click", function () {
      SynthAlerts.removeFromWatchlist(item.slug, renderWatchlist);
    });
    row.appendChild(label);
    row.appendChild(btn);
    alertEls.watchlist.appendChild(row);
  });
  updateWatchBtnState();
}

function updateWatchBtnState() {
  if (!currentSlug) {
    alertEls.watchBtn.disabled = true;
    alertEls.watchBtn.textContent = "No market loaded";
    return;
  }
  SynthAlerts.load(function (settings) {
    var watching = settings.watchlist.some(function (w) { return w.slug === currentSlug; });
    alertEls.watchBtn.disabled = watching;
    alertEls.watchBtn.textContent = watching ? "Already watching" : "+ Watch this market";
  });
}

// Initialize alerts UI from saved settings
SynthAlerts.load(function (settings) {
  alertEls.enabled.checked = settings.enabled;
  alertEls.body.classList.toggle("hidden", !settings.enabled);
  alertEls.threshold.value = settings.threshold;
  renderWatchlist(settings.watchlist);
});

alertEls.enabled.addEventListener("change", function () {
  var on = alertEls.enabled.checked;
  SynthAlerts.saveEnabled(on);
  alertEls.body.classList.toggle("hidden", !on);
});

alertEls.threshold.addEventListener("change", function () {
  SynthAlerts.saveThreshold(alertEls.threshold.value);
});

alertEls.watchBtn.addEventListener("click", function () {
  if (!currentSlug) return;
  SynthAlerts.load(function (settings) {
    if (settings.watchlist.length >= MAX_WATCHLIST) {
      alertEls.watchBtn.textContent = "Watchlist full (" + MAX_WATCHLIST + " max)";
      return;
    }
    var asset = cachedSynthData ? (cachedSynthData.asset || "BTC") : "BTC";
    var mtype = cachedMarketType || "daily";
    var label = SynthAlerts.formatLabel(asset, mtype);
    SynthAlerts.addToWatchlist(currentSlug, asset, label, renderWatchlist);
  });
});

// Start polling
refresh();
setInterval(refresh, SYNTH_POLL_INTERVAL_MS);
