(function () {
  "use strict";

  // ── Context-invalidation guard ──────────────────────────────────────
  var _contextValid = true;
  var _pollInterval = null;
  function isContextValid() {
    if (!_contextValid) return false;
    try { void chrome.runtime.id; return true; }
    catch (_e) { _contextValid = false; teardown(); return false; }
  }
  function teardown() {
    if (_pollInterval) { clearInterval(_pollInterval); _pollInterval = null; }
    if (observer) { observer.disconnect(); }
    console.log("[Synth-Overlay] Extension context invalidated, content script stopped.");
  }

  // ── Platform detection (once at init) ───────────────────────────────
  var currentPlatform = (function () {
    var host = (window.location.hostname || "").toLowerCase();
    if (host.indexOf("kalshi.com") !== -1) return "kalshi";
    if (host.indexOf("polymarket.com") !== -1) return "polymarket";
    return null;
  })();

  // Track last known prices to detect changes
  var lastPrices = { upPrice: null, downPrice: null };

  // ── Slug extraction — strategy per platform ─────────────────────────

  function slugFromPolymarket() {
    var segments = (window.location.pathname || "").split("/").filter(Boolean);
    var first = segments[0];
    var second = segments[1] || segments[0];
    if (first === "event" || first === "market") return second || null;
    return first || null;
  }

  function slugFromKalshi() {
    var segments = (window.location.pathname || "").split("/").filter(Boolean);
    if (segments[0] === "browse" || segments[0] === "portfolio") return null;
    // Find the /markets/ or /events/ prefix index
    var baseIdx = -1;
    for (var i = 0; i < segments.length; i++) {
      if (segments[i] === "markets" || segments[i] === "events" || segments[i] === "market") {
        baseIdx = i; break;
      }
    }
    if (baseIdx >= 0 && baseIdx < segments.length - 1) {
      // Prefer the last segment — it's the most specific ticker
      // e.g. /markets/kxbtcd/bitcoin-daily/KXBTCD-26MAR1317 → KXBTCD-26MAR1317
      var last = segments[segments.length - 1];
      if (last && last !== "markets" && last !== "events" && last !== "market") return last;
      // Fallback to first segment after /markets/
      return segments[baseIdx + 1];
    }
    // Fallback: last path segment
    return segments[segments.length - 1] || null;
  }

  function slugFromPage() {
    if (currentPlatform === "kalshi") return slugFromKalshi();
    if (currentPlatform === "polymarket") return slugFromPolymarket();
    var segments = (window.location.pathname || "").split("/").filter(Boolean);
    return segments[segments.length - 1] || null;
  }

  // ── Shared helpers ──────────────────────────────────────────────────

  /**
   * Validate that a pair of binary market prices sums to roughly 100¢.
   * Allows spread of 90-110 to account for market maker spread.
   */
  function validatePricePair(up, down) {
    if (up == null || down == null) return false;
    var sum = Math.round((up + down) * 100);
    return sum >= 90 && sum <= 110;
  }

  /**
   * Recursively search an object for Polymarket outcome prices.
   * Looks for {outcomes: ["Up","Down"], outcomePrices: ["0.51","0.49"]} pattern.
   */
  function findOutcomePricesInObject(obj) {
    if (!obj || typeof obj !== "object") return null;

    if (Array.isArray(obj.outcomePrices) && Array.isArray(obj.outcomes)) {
      var upIdx = -1, downIdx = -1;
      for (var i = 0; i < obj.outcomes.length; i++) {
        var name = String(obj.outcomes[i] || "").toLowerCase().trim();
        if (name === "up" || name === "yes") upIdx = i;
        else if (name === "down" || name === "no") downIdx = i;
      }
      if (upIdx >= 0 && downIdx >= 0) {
        var upP = parseFloat(obj.outcomePrices[upIdx]);
        var downP = parseFloat(obj.outcomePrices[downIdx]);
        if (!isNaN(upP) && !isNaN(downP) && upP > 0 && downP > 0) {
          return { upPrice: upP, downPrice: downP };
        }
      }
    }

    var keys = Object.keys(obj);
    for (var j = 0; j < keys.length; j++) {
      var val = obj[keys[j]];
      if (val && typeof val === "object") {
        var result = findOutcomePricesInObject(val);
        if (result) return result;
      }
    }
    return null;
  }

  /** Try to infer a missing side from the found side. */
  function inferPair(yesPrice, noPrice) {
    if (yesPrice !== null && noPrice !== null && validatePricePair(yesPrice, noPrice)) {
      return { upPrice: yesPrice, downPrice: noPrice };
    }
    if (yesPrice !== null && yesPrice >= 0.01 && yesPrice <= 0.99) {
      return { upPrice: yesPrice, downPrice: 1 - yesPrice };
    }
    if (noPrice !== null && noPrice >= 0.01 && noPrice <= 0.99) {
      return { upPrice: 1 - noPrice, downPrice: noPrice };
    }
    return null;
  }

  // ── Polymarket price scraper ────────────────────────────────────────

  /**
   * Scrape live Polymarket prices from the DOM.
   * Returns { upPrice: 0.XX, downPrice: 0.XX } or null if not found.
   *
   * Three strategies in order of freshness:
   * 1. Compact DOM elements with anchored "Up XX¢" patterns (live React state)
   * 2. Price-only leaf elements with parent context walk (live React state)
   * 3. __NEXT_DATA__ JSON (fallback — SSR snapshot, may be stale)
   */
  function scrapePolymarketPrices() {
    var upPrice = null;
    var downPrice = null;

    // Strategy 1: Scan compact DOM elements for anchored "Up XX¢" / "Down XX¢"
    // Only considers elements with very short text (< 20 chars) to avoid false positives.
    // Regex is anchored (^...$) so entire text must match the pattern.
    var els = document.querySelectorAll("button, a, span, div, p, [role='button']");
    for (var i = 0; i < els.length; i++) {
      var text = (els[i].textContent || "").trim();
      if (text.length > 20 || text.length < 3) continue;

      if (upPrice === null) {
        var um = text.match(/^\s*(Up|Yes)\s*(\d{1,2})\s*[¢%]\s*$/i);
        if (um) {
          var up = parseInt(um[2], 10) / 100;
          if (up >= 0.01 && up <= 0.99) upPrice = up;
        }
      }
      if (downPrice === null) {
        var dm = text.match(/^\s*(Down|No)\s*(\d{1,2})\s*[¢%]\s*$/i);
        if (dm) {
          var dn = parseInt(dm[2], 10) / 100;
          if (dn >= 0.01 && dn <= 0.99) downPrice = dn;
        }
      }
      if (upPrice !== null && downPrice !== null) break;
    }

    if (upPrice !== null && downPrice !== null && validatePricePair(upPrice, downPrice)) {
      console.log("[Synth-Overlay] Prices from compact DOM:", { upPrice: upPrice, downPrice: downPrice });
      return { upPrice: upPrice, downPrice: downPrice };
    }

    // Strategy 2: Find leaf elements containing just "XX¢" or "XX%",
    // then walk up the DOM tree to find "Up" or "Down" context.
    upPrice = null;
    downPrice = null;
    for (var k = 0; k < els.length; k++) {
      var el = els[k];
      var t = (el.textContent || "").trim();
      if (!t.match(/^\d{1,2}\s*[¢%]$/)) continue;
      if (el.children.length > 1) continue;

      var price = parseInt(t, 10) / 100;
      if (price < 0.01 || price > 0.99) continue;

      var parent = el.parentElement;
      for (var d = 0; d < 4 && parent; d++) {
        var pText = (parent.textContent || "").toLowerCase();
        if (pText.length > 80) break;
        if (/\bup\b/.test(pText) && upPrice === null) { upPrice = price; break; }
        if (/\bdown\b/.test(pText) && downPrice === null) { downPrice = price; break; }
        parent = parent.parentElement;
      }
      if (upPrice !== null && downPrice !== null) break;
    }

    var inferred = inferPair(upPrice, downPrice);
    if (inferred) {
      console.log("[Synth-Overlay] Prices from leaf walk:", inferred);
      return inferred;
    }

    // Strategy 3 (FALLBACK): Parse __NEXT_DATA__ — SSR snapshot, may be stale
    // Only used when DOM scraping fails (e.g. page still loading).
    try {
      var ndEl = document.getElementById("__NEXT_DATA__");
      if (ndEl) {
        var nd = JSON.parse(ndEl.textContent);
        var fromND = findOutcomePricesInObject(nd);
        if (fromND && validatePricePair(fromND.upPrice, fromND.downPrice)) {
          console.log("[Synth-Overlay] Prices from __NEXT_DATA__ (fallback):", fromND);
          return fromND;
        }
      }
    } catch (e) {
      console.log("[Synth-Overlay] __NEXT_DATA__ parse failed:", e.message);
    }

    return null;
  }

  // ── Kalshi price scraper (trading-panel-aware) ──────────────────────

  /**
   * Extract a Yes or No price from text.
   * side: "yes" or "no" — matches Yes/Up or No/Down keywords.
   */
  function extractKalshiPrice(text, side) {
    var sidePattern = side === "yes" ? "(yes|up)" : "(no|down)";
    // Cent format: "Yes 52¢" / "Buy Yes 52¢"
    var cm = text.match(new RegExp("(?:buy\\s+)?" + sidePattern + "\\s+(\\d{1,2})\\s*[¢c%]", "i"));
    if (cm) {
      var cv = parseInt(cm[2], 10) / 100;
      if (cv >= 0.01 && cv <= 0.99) return cv;
    }
    // Dollar format: "Yes $0.52" / "Buy Yes $0.52"
    var dm = text.match(new RegExp("(?:buy\\s+)?" + sidePattern + "\\s+\\$?(0\\.\\d{2,4})", "i"));
    if (dm) {
      var dv = parseFloat(dm[2]);
      if (dv >= 0.01 && dv <= 0.99) return dv;
    }
    return null;
  }

  /**
   * Check if an element is inside the Kalshi trading panel (Buy/Sell/Amount).
   * Walks up to 8 ancestors looking for the order form container.
   */
  function isInTradingPanel(el) {
    var ancestor = el.parentElement;
    for (var up = 0; up < 8 && ancestor; up++) {
      var aText = (ancestor.textContent || "").toLowerCase();
      if (aText.length < 500 && /\bbuy\b/.test(aText) && /\bsell\b/.test(aText) && /\bamount\b/.test(aText)) {
        return true;
      }
      if (aText.length < 500 && /\bbuy\b/.test(aText) && /sign up/i.test(aText)) {
        return true;
      }
      ancestor = ancestor.parentElement;
    }
    return false;
  }

  /**
   * Scrape live prices from Kalshi's DOM.
   * Uses a two-pass approach: Pass 1 prioritises the trading panel (selected
   * contract on multi-strike pages).  Pass 2 falls back to a general scan.
   */
  function scrapeKalshiPrices() {
    var yesPrice = null;
    var noPrice = null;
    var els = document.querySelectorAll("button, a, span, div, p, [role='button'], [role='cell'], td");

    // Pass 1: PRIORITY — only accept prices inside the trading panel
    for (var i = 0; i < els.length; i++) {
      var text = (els[i].textContent || "").trim();
      if (text.length > 40 || text.length < 2) continue;
      if (yesPrice === null) {
        var yp = extractKalshiPrice(text, "yes");
        if (yp !== null && isInTradingPanel(els[i])) yesPrice = yp;
      }
      if (noPrice === null) {
        var np = extractKalshiPrice(text, "no");
        if (np !== null && isInTradingPanel(els[i])) noPrice = np;
      }
      if (yesPrice !== null && noPrice !== null) break;
    }
    if (yesPrice !== null && noPrice !== null && validatePricePair(yesPrice, noPrice)) {
      console.log("[Synth-Overlay] Kalshi prices from trading panel:", { upPrice: yesPrice, downPrice: noPrice });
      return { upPrice: yesPrice, downPrice: noPrice };
    }

    // Pass 2: FALLBACK — scan all elements
    yesPrice = null;
    noPrice = null;
    for (var j = 0; j < els.length; j++) {
      var text2 = (els[j].textContent || "").trim();
      if (text2.length > 40 || text2.length < 2) continue;
      if (yesPrice === null) {
        var yv = extractKalshiPrice(text2, "yes");
        if (yv !== null) yesPrice = yv;
      }
      if (noPrice === null) {
        var nv = extractKalshiPrice(text2, "no");
        if (nv !== null) noPrice = nv;
      }
      // Standalone price with parent context walk
      if (yesPrice === null || noPrice === null) {
        var pm = text2.match(/^(\d{1,2})\s*[¢c%]$/) || text2.match(/^\$?(0\.\d{2,4})$/);
        if (pm) {
          var rawVal = pm[1];
          var price = rawVal.indexOf(".") !== -1 ? parseFloat(rawVal) : parseInt(rawVal, 10) / 100;
          if (price >= 0.01 && price <= 0.99) {
            var parent = els[j].parentElement;
            for (var dd = 0; dd < 5 && parent; dd++) {
              var pText = (parent.textContent || "").toLowerCase();
              if (pText.length > 120) break;
              if (/\b(yes|up)\b/.test(pText) && yesPrice === null) { yesPrice = price; break; }
              if (/\b(no|down)\b/.test(pText) && noPrice === null) { noPrice = price; break; }
              parent = parent.parentElement;
            }
          }
        }
      }
      if (yesPrice !== null && noPrice !== null) break;
    }

    var inferred = inferPair(yesPrice, noPrice);
    if (inferred) {
      console.log("[Synth-Overlay] Kalshi prices from DOM:", inferred);
      return inferred;
    }

    // Pass 3: Order book pattern — "Best Yes: $0.52" / "Best Yes: 52¢"
    yesPrice = null;
    noPrice = null;
    for (var ob = 0; ob < els.length; ob++) {
      var obText = (els[ob].textContent || "").trim();
      if (obText.length > 60 || obText.length < 5) continue;
      if (yesPrice === null) {
        var ym = obText.match(/best\s+yes[:\s]+\$?(0\.\d{2,4})/i) || obText.match(/best\s+yes[:\s]+(\d{1,2})\s*[¢c%]/i);
        if (ym) {
          var yv3 = ym[1].indexOf(".") !== -1 ? parseFloat(ym[1]) : parseInt(ym[1], 10) / 100;
          if (yv3 >= 0.01 && yv3 <= 0.99) yesPrice = yv3;
        }
      }
      if (noPrice === null) {
        var nm = obText.match(/best\s+no[:\s]+\$?(0\.\d{2,4})/i) || obText.match(/best\s+no[:\s]+(\d{1,2})\s*[¢c%]/i);
        if (nm) {
          var nv3 = nm[1].indexOf(".") !== -1 ? parseFloat(nm[1]) : parseInt(nm[1], 10) / 100;
          if (nv3 >= 0.01 && nv3 <= 0.99) noPrice = nv3;
        }
      }
      if (yesPrice !== null && noPrice !== null) break;
    }
    var obInferred = inferPair(yesPrice, noPrice);
    if (obInferred) {
      console.log("[Synth-Overlay] Kalshi prices from order book pattern:", obInferred);
      return obInferred;
    }

    // Fallback: __NEXT_DATA__
    try {
      var ndEl = document.getElementById("__NEXT_DATA__");
      if (ndEl) {
        var fromND = findOutcomePricesInObject(JSON.parse(ndEl.textContent));
        if (fromND && validatePricePair(fromND.upPrice, fromND.downPrice)) return fromND;
      }
    } catch (_e) {}

    return null;
  }

  // ── Unified price scraper — dispatches per platform ─────────────────

  function scrapePrices() {
    if (currentPlatform === "kalshi") return scrapeKalshiPrices();
    return scrapePolymarketPrices();
  }

  // ── Kalshi browse-page market link scanner ──────────────────────────

  function scanKalshiMarketLinks() {
    if (currentPlatform !== "kalshi") return [];
    if (slugFromPage()) return [];
    var seen = {};
    var results = [];
    var anchors = document.querySelectorAll("a[href]");
    for (var i = 0; i < anchors.length && results.length < 10; i++) {
      var href = anchors[i].getAttribute("href") || "";
      var m = href.match(/\/(markets|events)\/((kx[a-z0-9]+|btc|eth|btcd|ethd)[a-z0-9._-]*)/i);
      if (!m) continue;
      var ticker = m[2];
      if (seen[ticker]) continue;
      seen[ticker] = true;
      var label = (anchors[i].textContent || "").trim().substring(0, 60) || ticker;
      results.push({ ticker: ticker, href: href, label: label });
    }
    return results;
  }

  /**
   * Best-effort scraper for the user's account balance.
   * Returns a float balance in dollars, or null if not found.
   */
  function scrapeBalance() {
    var root = document.body;
    if (!root) return null;

    var textNodes = root.querySelectorAll("div, span, button, p");
    var best = null;

    for (var i = 0; i < textNodes.length; i++) {
      var el = textNodes[i];
      var txt = (el.textContent || "").trim();
      if (!txt || txt.length > 40) continue;

      // Look for patterns like "Balance 123.45 USDC" or "$123.45"
      if (/balance/i.test(txt) || /USDC/i.test(txt) || /\$/i.test(txt)) {
        var m = txt.match(/(\$?\s*\d{1,3}(?:[,\d]{0,3})*(?:\.\d{1,2})?)/);
        if (m) {
          var cleaned = m[1].replace(/\$/g, "").replace(/,/g, "").trim();
          var val = parseFloat(cleaned);
          if (!isNaN(val) && val > 0) {
            best = val;
            break;
          }
        }
      }
    }

    if (best != null) {
      console.log("[Synth-Overlay] Detected balance from DOM:", best);
    }
    return best;
  }

  // ── Context builder ─────────────────────────────────────────────────

  function getContext() {
    var slug = slugFromPage();
    var livePrices = scrapePrices();
    var balance = scrapeBalance();
    var suggestedMarkets = (!slug && currentPlatform === "kalshi") ? scanKalshiMarketLinks() : [];
    return {
      slug: slug,
      url: window.location.href,
      host: window.location.hostname,
      platform: currentPlatform,
      pageUpdatedAt: Date.now(),
      livePrices: livePrices,
      balance: balance,
      suggestedMarkets: suggestedMarkets,
    };
  }

  // ── Broadcasting ────────────────────────────────────────────────────

  function safeSend(msg) {
    if (!isContextValid()) return;
    try { chrome.runtime.sendMessage(msg).catch(function() {}); }
    catch (_e) { _contextValid = false; teardown(); }
  }

  function broadcastPriceUpdate(prices) {
    if (!prices) return;
    safeSend({
      type: "synth:priceUpdate",
      prices: prices,
      slug: slugFromPage(),
      timestamp: Date.now()
    });
  }

  function checkAndBroadcastPrices() {
    if (!_contextValid) return;
    var prices = scrapePrices();
    if (!prices) return;
    if (prices.upPrice !== lastPrices.upPrice || prices.downPrice !== lastPrices.downPrice) {
      lastPrices = { upPrice: prices.upPrice, downPrice: prices.downPrice };
      broadcastPriceUpdate(prices);
    }
  }

  // ── MutationObserver ────────────────────────────────────────────────

  var observer = new MutationObserver(function(mutations) {
    if (!_contextValid) return;
    if (observer._pending) return;
    observer._pending = true;
    setTimeout(function() {
      observer._pending = false;
      checkAndBroadcastPrices();
    }, 100);
  });

  if (document.body) {
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true
    });
  }

  // ── SPA navigation detection ────────────────────────────────────────

  var lastSlug = slugFromPage();
  function checkUrlChange() {
    if (!isContextValid()) return;
    var newSlug = slugFromPage();
    if (newSlug !== lastSlug) {
      console.log("[Synth-Overlay] URL changed:", lastSlug, "->", newSlug);
      lastSlug = newSlug;
      lastPrices = { upPrice: null, downPrice: null };
      safeSend({
        type: "synth:urlChanged",
        slug: newSlug,
        url: window.location.href,
        timestamp: Date.now()
      });
      setTimeout(checkAndBroadcastPrices, 200);
    }
  }

  var origPushState = history.pushState;
  var origReplaceState = history.replaceState;
  history.pushState = function() {
    origPushState.apply(this, arguments);
    checkUrlChange();
  };
  history.replaceState = function() {
    origReplaceState.apply(this, arguments);
    checkUrlChange();
  };
  window.addEventListener("popstate", checkUrlChange);

  _pollInterval = setInterval(function() {
    if (!isContextValid()) return;
    checkAndBroadcastPrices();
    checkUrlChange();
  }, 500);

  setTimeout(checkAndBroadcastPrices, 500);

  // ── Message handler ─────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
    if (!isContextValid()) return;
    if (!message || typeof message !== "object") return;
    try {
      if (message.type === "synth:getContext") {
        sendResponse({ ok: true, context: getContext() });
      }
      if (message.type === "synth:getPrices") {
        sendResponse({ ok: true, prices: scrapePrices() });
      }
    } catch (_e) {}
  });
})();
