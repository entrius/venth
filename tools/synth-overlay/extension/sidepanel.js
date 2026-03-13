"use strict";

const API_BASE = "http://127.0.0.1:8765";

// Cache last Synth data for instant recalculation when live prices change
var cachedSynthData = null;
var cachedMarketType = null;
var currentSlug = null;
var currentPlatform = "polymarket";

function isSupportedUrl(url) {
  if (typeof SynthPlatforms !== "undefined") return SynthPlatforms.isSupportedUrl(url);
  return url && (url.indexOf("polymarket.com") !== -1 || url.indexOf("kalshi.com") !== -1);
}

const els = {
  statusText: document.getElementById("statusText"),
  synthUp: document.getElementById("synthUp"),
  synthDown: document.getElementById("synthDown"),
  polyUp: document.getElementById("polyUp"),
  polyDown: document.getElementById("polyDown"),
  marketLabel: document.getElementById("marketLabel"),
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
  // Kelly sizing
  balanceInput: document.getElementById("balanceInput"),
  kellySide: document.getElementById("kellySide"),
  kellyFraction: document.getElementById("kellyFraction"),
  kellySize: document.getElementById("kellySize"),
  kellyEv: document.getElementById("kellyEv"),
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
  if (!tab || !tab.url || !isSupportedUrl(tab.url)) return null;
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

async function fetchEdge(slug, livePrices, platform) {
  var url = API_BASE + "/api/edge?slug=" + encodeURIComponent(slug);
  if (platform) url += "&platform=" + encodeURIComponent(platform);
  // Pass live prices to server if available for real-time edge calculation
  if (livePrices && livePrices.upPrice != null) {
    url += "&live_prob_up=" + encodeURIComponent(livePrices.upPrice);
  }
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.json();
  } catch (_e) {
    return { error: "Cannot reach Synth server at " + API_BASE };
  }
}

function render(state) {
  els.statusText.textContent = state.status;
  els.synthUp.textContent = state.synthUp;
  els.synthDown.textContent = state.synthDown;
  els.polyUp.textContent = state.polyUp || "—";
  els.polyDown.textContent = state.polyDown || "—";
  if (els.marketLabel) els.marketLabel.textContent = state.marketLabel || "Poly";
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

// ---- Kelly position sizing ----

var BALANCE_KEY = "synth_kelly_balance";

function loadStoredBalance(callback) {
  if (!chrome.storage || !chrome.storage.local) {
    callback(null);
    return;
  }
  chrome.storage.local.get([BALANCE_KEY], function (res) {
    var v = res && typeof res[BALANCE_KEY] === "number" ? res[BALANCE_KEY] : null;
    callback(v);
  });
}

function saveStoredBalance(val) {
  if (!chrome.storage || !chrome.storage.local) return;
  if (typeof val !== "number" || !isFinite(val) || val < 0) return;
  var obj = {};
  obj[BALANCE_KEY] = val;
  chrome.storage.local.set(obj);
}

function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v));
}

function formatUsd(v) {
  if (v == null || !isFinite(v)) return "—";
  return "$" + v.toFixed(2);
}

function formatPct(v) {
  if (v == null || !isFinite(v)) return "—";
  return (v * 100).toFixed(1) + "%";
}

// Compute Kelly fraction and EV for YES/NO given Synth and market probabilities.
function computeKellySizing(opts) {
  var pSynth = opts.pSynth;
  var pMarket = opts.pMarket;
  var confidence = opts.confidence != null ? opts.confidence : 0.5;
  var balance = opts.balance;

  if (pSynth == null || pMarket == null || !isFinite(pSynth) || !isFinite(pMarket)) {
    return null;
  }
  if (pMarket <= 0 || pMarket >= 1 || pSynth <= 0 || pSynth >= 1) {
    return null;
  }

  // EV per $ for YES/NO (binary market, cost = p_market, payout = 1)
  var evYes = pSynth - pMarket;
  var pNoSynth = 1 - pSynth;
  var pNoMarket = 1 - pMarket;
  var evNo = pNoSynth - pNoMarket;

  // Kelly fractions
  function kellyFraction(trueP, marketP) {
    var b = (1 - marketP) / marketP;
    var q = 1 - trueP;
    var f = (b * trueP - q) / b;
    if (!isFinite(f)) return 0;
    return f;
  }

  var fYes = kellyFraction(pSynth, pMarket);
  var fNo = kellyFraction(pNoSynth, pNoMarket);

  var side = null;
  var rawF = 0;
  var evPerDollar = 0;

  if (evYes > 0 && fYes > 0 && (evYes >= evNo || evNo <= 0 || fNo <= 0)) {
    side = "YES";
    rawF = fYes;
    evPerDollar = evYes;
  } else if (evNo > 0 && fNo > 0) {
    side = "NO";
    rawF = fNo;
    evPerDollar = evNo;
  } else {
    return {
      side: null,
      fraction: 0,
      sizedAmount: balance != null ? 0 : null,
      evPerDollar: 0,
    };
  }

  // Scale Kelly by confidence and cap fraction
  var confScale = clamp(confidence, 0, 1);
  var maxKelly = 0.2; // 20% cap
  var scaledF = clamp(rawF * confScale, 0, maxKelly);

  var sizedAmount = null;
  if (balance != null && isFinite(balance) && balance > 0) {
    sizedAmount = balance * scaledF;
  }

  return {
    side: side,
    fraction: scaledF,
    sizedAmount: sizedAmount,
    evPerDollar: evPerDollar,
  };
}

function updateSizingUI(result, balance) {
  if (!els.kellySide || !els.kellyFraction || !els.kellySize || !els.kellyEv) return;

  if (!result) {
    els.kellySide.textContent = "—";
    els.kellyFraction.textContent = "—";
    els.kellySize.textContent = "—";
    els.kellyEv.textContent = "—";
    return;
  }

  if (!result.side || result.fraction <= 0 || !isFinite(result.fraction)) {
    els.kellySide.textContent = "No +EV";
    els.kellyFraction.textContent = "—";
    els.kellySize.textContent = balance && balance > 0 ? "$0.00" : "—";
    els.kellyEv.textContent = "≤ 0¢";
    return;
  }

  els.kellySide.textContent = result.side;
  els.kellyFraction.textContent = formatPct(result.fraction);

  if (result.sizedAmount != null && isFinite(result.sizedAmount)) {
    els.kellySize.textContent = formatUsd(result.sizedAmount);
  } else {
    els.kellySize.textContent = "Enter balance";
  }

  var cents = Math.round(result.evPerDollar * 100);
  var sign = cents >= 0 ? "+" : "";
  els.kellyEv.textContent = sign + cents + "¢";
}

function initSizing(balanceFromCtx, edge) {
  if (!els.balanceInput) return;

  loadStoredBalance(function (stored) {
    var initial = null;
    if (typeof balanceFromCtx === "number" && balanceFromCtx > 0) {
      initial = balanceFromCtx;
    } else if (typeof stored === "number" && stored > 0) {
      initial = stored;
    }

    if (initial != null) {
      els.balanceInput.value = String(initial.toFixed(2));
    }

    var balanceVal = initial;
    var pSynth = edge.synth_probability_up != null ? edge.synth_probability_up : edge.synth_probability;
    var pMarket = edge.polymarket_probability_up != null ? edge.polymarket_probability_up : null;
    var conf = edge.confidence_score != null ? edge.confidence_score : 0.5;
    var sizing = computeKellySizing({
      pSynth: pSynth,
      pMarket: pMarket,
      confidence: conf,
      balance: balanceVal,
    });
    updateSizingUI(sizing, balanceVal);
  });

  els.balanceInput.addEventListener("change", function () {
    var v = parseFloat(els.balanceInput.value);
    if (isNaN(v) || v < 0) {
      updateSizingUI(null, null);
      return;
    }
    saveStoredBalance(v);

    var pSynth = edge.synth_probability_up != null ? edge.synth_probability_up : edge.synth_probability;
    var pMarket = edge.polymarket_probability_up != null ? edge.polymarket_probability_up : null;
    var conf = edge.confidence_score != null ? edge.confidence_score : 0.5;
    var sizing = computeKellySizing({
      pSynth: pSynth,
      pMarket: pMarket,
      confidence: conf,
      balance: v,
    });
    updateSizingUI(sizing, v);
  });
}

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
      status: "Open a Polymarket or Kalshi market tab to view Synth data.",
      analysis: "No active market tab found.",
    }));
    return;
  }

  const ctx = await getContextFromPage(tab.id);
  if (!ctx || !ctx.slug) {
    // On Kalshi browse pages, show suggested markets if available
    if (ctx && ctx.suggestedMarkets && ctx.suggestedMarkets.length > 0) {
      render(Object.assign({}, EMPTY, {
        status: "Kalshi browse page — pick a market.",
        analysis: "Navigate to a specific market to see Synth data.",
      }));
      // Render suggested market buttons
      var container = document.createElement("div");
      container.style.cssText = "margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;";
      for (var si = 0; si < ctx.suggestedMarkets.length && si < 8; si++) {
        (function(mkt) {
          var btn = document.createElement("button");
          btn.textContent = mkt.label || mkt.ticker;
          btn.style.cssText = "padding:3px 8px;font-size:11px;border:1px solid #555;border-radius:4px;background:#2a2a3e;color:#e0e0f0;cursor:pointer;";
          btn.addEventListener("click", function() {
            chrome.tabs.update(tab.id, { url: "https://kalshi.com" + (mkt.href.startsWith("/") ? "" : "/") + mkt.href });
          });
          container.appendChild(btn);
        })(ctx.suggestedMarkets[si]);
      }
      els.analysisText.parentElement.appendChild(container);
      return;
    }
    render(Object.assign({}, EMPTY, {
      status: "Could not read market context from page.",
      analysis: "Reload the page and try refresh again.",
    }));
    return;
  }

  var ctxPlatform = ctx.platform || "polymarket";

  const edge = await fetchEdge(ctx.slug, ctx.livePrices, ctxPlatform);
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
  currentPlatform = ctxPlatform;
  if (typeof updateWatchBtnState === "function") updateWatchBtnState();

  // Log live price status for debugging
  console.log("[Synth-Overlay] Edge response:", { 
    live_price_used: edge.live_price_used, 
    polymarket_prob: edge.polymarket_probability_up,
    livePricesFromDOM: ctx.livePrices 
  });

  // Get Polymarket price (from API response or live DOM)
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
  var platformLabel = (typeof SynthPlatforms !== "undefined" && SynthPlatforms.get(ctxPlatform))
    ? SynthPlatforms.get(ctxPlatform).label : (ctxPlatform === "kalshi" ? "Kalshi" : "Poly");
  render({
    status: "Synced — " + asset + " " + horizon + " forecast." + liveStatus,
    marketLabel: platformLabel,
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

  // Initialize Kelly sizing panel
  initSizing(ctx.balance, edge);
  
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

// Listen for real-time price updates and URL changes from content script
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

// Also detect tab URL changes (full navigations)
chrome.tabs.onUpdated.addListener(function(tabId, changeInfo, tab) {
  if (changeInfo.url && tab.active && tab.url && isSupportedUrl(tab.url)) {
    console.log("[Synth-Overlay] Tab URL updated:", changeInfo.url);
    cachedSynthData = null;
    cachedMarketType = null;
    currentSlug = null;
    lastPollPrices = { upPrice: null, downPrice: null };
    stopPollProgress();
    refresh();
  }
});

// Fast price poll: pull live prices from content script every 1s (reliable fallback)
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

var alertEls = {
  enabled: document.getElementById("alertsEnabled"),
  body: document.getElementById("alertsBody"),
  threshold: document.getElementById("alertThreshold"),
  watchlist: document.getElementById("watchlist"),
  watchBtn: document.getElementById("watchBtn"),
  autoDismiss: document.getElementById("autoDismiss"),
  history: document.getElementById("alertHistory"),
  clearHistory: document.getElementById("clearHistory"),
};

function renderWatchlist(list) {
  alertEls.watchlist.innerHTML = "";
  if (!list || list.length === 0) {
    var hint = document.createElement("div");
    hint.className = "watch-empty";
    hint.textContent = "No markets watched yet";
    alertEls.watchlist.appendChild(hint);
    updateWatchBtnState();
    return;
  }
  list.forEach(function (item) {
    var row = document.createElement("div");
    row.className = "watch-item";
    var label = document.createElement("span");
    label.textContent = item.label || item.slug;
    var btn = document.createElement("button");
    btn.className = "watch-remove";
    btn.textContent = "\u00d7";
    btn.title = "Remove from watchlist";
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
    if (watching) {
      alertEls.watchBtn.disabled = true;
      alertEls.watchBtn.textContent = "Already watching";
    } else if (settings.watchlist.length >= SynthAlerts.MAX_WATCHLIST) {
      alertEls.watchBtn.disabled = true;
      alertEls.watchBtn.textContent = "Watchlist full (" + SynthAlerts.MAX_WATCHLIST + " max)";
    } else {
      alertEls.watchBtn.disabled = false;
      alertEls.watchBtn.textContent = "+ Watch this market";
    }
  });
}

function renderHistory(history) {
  alertEls.history.innerHTML = "";
  if (!history || history.length === 0) {
    var hint = document.createElement("div");
    hint.className = "history-empty";
    hint.textContent = "No alerts yet";
    alertEls.history.appendChild(hint);
    return;
  }
  history.forEach(function (entry) {
    var item = document.createElement("div");
    item.className = "history-item";
    var titleDiv = document.createElement("div");
    titleDiv.className = "history-title";
    titleDiv.textContent = entry.title;
    var metaDiv = document.createElement("div");
    metaDiv.className = "history-meta";
    var ago = Math.round((Date.now() - entry.timestamp) / 60000);
    metaDiv.textContent = ago <= 0 ? "Just now" : ago + "m ago";
    item.appendChild(titleDiv);
    item.appendChild(metaDiv);
    alertEls.history.appendChild(item);
  });
}

function initAlertsUI() {
  SynthAlerts.load(function (settings) {
    alertEls.enabled.checked = settings.enabled;
    alertEls.body.classList.toggle("hidden", !settings.enabled);
    alertEls.threshold.value = settings.threshold;
    renderWatchlist(settings.watchlist);
  });
  SynthAlerts.loadAutoDismiss(function (val) {
    alertEls.autoDismiss.checked = val;
  });
  SynthAlerts.loadHistory(renderHistory);
}

alertEls.enabled.addEventListener("change", function () {
  var on = alertEls.enabled.checked;
  SynthAlerts.saveEnabled(on);
  alertEls.body.classList.toggle("hidden", !on);
});

alertEls.threshold.addEventListener("change", function () {
  var clamped = SynthAlerts.saveThreshold(alertEls.threshold.value);
  alertEls.threshold.value = clamped;
});

alertEls.watchBtn.addEventListener("click", function () {
  if (!currentSlug) return;
  var asset = cachedSynthData ? (cachedSynthData.asset || "BTC") : "BTC";
  var mtype = cachedMarketType || "daily";
  var label = SynthAlerts.formatMarketLabel(asset, mtype, currentPlatform);
  SynthAlerts.addToWatchlist(currentSlug, asset, label, currentPlatform, renderWatchlist);
});

alertEls.autoDismiss.addEventListener("change", function () {
  SynthAlerts.saveAutoDismiss(alertEls.autoDismiss.checked);
});

alertEls.clearHistory.addEventListener("click", function () {
  SynthAlerts.clearHistory(function () {
    renderHistory([]);
  });
});

// Live-update history when background fires a notification
chrome.storage.onChanged.addListener(function (changes, area) {
  if (area === "local" && changes[SynthAlerts.KEYS.history]) {
    renderHistory(changes[SynthAlerts.KEYS.history].newValue || []);
  }
});

initAlertsUI();

// Start polling
refresh();
setInterval(refresh, SYNTH_POLL_INTERVAL_MS);
