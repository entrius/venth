(function () {
  "use strict";

  // Track last known prices to detect changes
  var lastPrices = { upPrice: null, downPrice: null };

  function slugFromPage() {
    var host = window.location.hostname || "";
    var path = window.location.pathname || "";
    var segments = path.split("/").filter(Boolean);

    if (host.indexOf("polymarket.com") !== -1) {
      var first = segments[0];
      var second = segments[1] || segments[0];
      if (first === "event" || first === "market") return second || null;
      return first || null;
    }

    return segments[segments.length - 1] || null;
  }

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

  /**
   * Scrape live Polymarket prices from the DOM.
   * Returns { upPrice: 0.XX, downPrice: 0.XX } or null if not found.
   *
   * Three strategies in order of freshness:
   * 1. Compact DOM elements with anchored "Up XX¢" patterns (live React state)
   * 2. Price-only leaf elements with parent context walk (live React state)
   * 3. __NEXT_DATA__ JSON (fallback — SSR snapshot, may be stale)
   */
  function scrapeLivePrices() {
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

    if (upPrice !== null && downPrice !== null && validatePricePair(upPrice, downPrice)) {
      console.log("[Synth-Overlay] Prices from leaf walk:", { upPrice: upPrice, downPrice: downPrice });
      return { upPrice: upPrice, downPrice: downPrice };
    }

    // If only one DOM price found, infer the other
    if (upPrice !== null && upPrice >= 0.01 && upPrice <= 0.99) {
      return { upPrice: upPrice, downPrice: 1 - upPrice };
    }
    if (downPrice !== null && downPrice >= 0.01 && downPrice <= 0.99) {
      return { upPrice: 1 - downPrice, downPrice: downPrice };
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

    // Throttle this log to avoid console spam on resolved/expired markets
    var now = Date.now();
    if (!scrapeLivePrices._lastWarn || now - scrapeLivePrices._lastWarn > 10000) {
      scrapeLivePrices._lastWarn = now;
      console.log("[Synth-Overlay] Could not scrape live prices from DOM");
    }
    return null;
  }

  /**
   * Best-effort scrape user's USDC/USD balance from the Polymarket DOM.
   * Looks for common patterns: "$1,234.56", "Balance 1234.56 USDC", portfolio value display.
   * Returns a numeric balance or null if not detected.
   */
  function scrapeBalance() {
    var candidates = document.querySelectorAll("span, div, p, strong, [data-testid]");
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      var text = (el.textContent || "").trim();
      if (text.length < 2 || text.length > 40) continue;
      // Skip elements with many children (containers, not leaf values)
      if (el.children.length > 3) continue;

      // Pattern 1: "$1,234.56" or "$12.34" standalone
      var dollarMatch = text.match(/^\$\s*([\d,]+(?:\.\d{1,2})?)\s*$/);
      if (dollarMatch) {
        // Walk up to check for balance/portfolio context
        var parent = el.parentElement;
        for (var d = 0; d < 4 && parent; d++) {
          var pText = (parent.textContent || "").toLowerCase();
          if (pText.length > 120) break;
          if (/\b(balance|portfolio|available|cash|funds|deposit)\b/.test(pText)) {
            var val = parseFloat(dollarMatch[1].replace(/,/g, ""));
            if (val > 0 && val < 1e8) return val;
          }
          parent = parent.parentElement;
        }
      }

      // Pattern 2: "1234.56 USDC" — require balance/portfolio context in ancestors
      var usdcMatch = text.match(/([\d,]+(?:\.\d{1,2})?)\s*USDC/i);
      if (usdcMatch) {
        var usdcParent = el.parentElement;
        for (var u = 0; u < 4 && usdcParent; u++) {
          var uText = (usdcParent.textContent || "").toLowerCase();
          if (uText.length > 120) break;
          if (/\b(balance|portfolio|available|cash|funds|deposit)\b/.test(uText)) {
            var usdcVal = parseFloat(usdcMatch[1].replace(/,/g, ""));
            if (usdcVal > 0 && usdcVal < 1e8) return usdcVal;
          }
          usdcParent = usdcParent.parentElement;
        }
      }

      // Pattern 3: "Balance $1,234.56" — keyword anchored in the element itself
      var balMatch = text.match(/\b(?:balance|available|portfolio)\s*:?\s*\$\s*([\d,]+(?:\.\d{1,2})?)/i);
      if (balMatch) {
        var balVal = parseFloat(balMatch[1].replace(/,/g, ""));
        if (balVal > 0 && balVal < 1e8) return balVal;
      }
    }
    return null;
  }

  function getContext() {
    var livePrices = scrapeLivePrices();
    return {
      slug: slugFromPage(),
      url: window.location.href,
      host: window.location.hostname,
      pageUpdatedAt: Date.now(),
      livePrices: livePrices,
      balance: scrapeBalance(),
    };
  }

  // Broadcast price update to extension
  function broadcastPriceUpdate(prices) {
    if (!prices) return;
    chrome.runtime.sendMessage({
      type: "synth:priceUpdate",
      prices: prices,
      slug: slugFromPage(),
      timestamp: Date.now()
    }).catch(function() {});
  }

  // Check if prices changed and broadcast if so
  function checkAndBroadcastPrices() {
    var prices = scrapeLivePrices();
    if (!prices) return;
    
    if (prices.upPrice !== lastPrices.upPrice || prices.downPrice !== lastPrices.downPrice) {
      lastPrices = { upPrice: prices.upPrice, downPrice: prices.downPrice };
      broadcastPriceUpdate(prices);
    }
  }

  // Set up MutationObserver for instant price detection
  var observer = new MutationObserver(function(mutations) {
    // Debounce: only check every 100ms max
    if (observer._pending) return;
    observer._pending = true;
    setTimeout(function() {
      observer._pending = false;
      checkAndBroadcastPrices();
    }, 100);
  });

  // Start observing DOM changes
  if (document.body) {
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true
    });
  }

  // Detect SPA navigation (Polymarket uses Next.js client-side routing)
  var lastSlug = slugFromPage();
  function checkUrlChange() {
    var newSlug = slugFromPage();
    if (newSlug !== lastSlug) {
      console.log("[Synth-Overlay] URL changed:", lastSlug, "->", newSlug);
      lastSlug = newSlug;
      lastPrices = { upPrice: null, downPrice: null };
      chrome.runtime.sendMessage({
        type: "synth:urlChanged",
        slug: newSlug,
        url: window.location.href,
        timestamp: Date.now()
      }).catch(function() {});
      // Immediately scrape and broadcast new prices
      setTimeout(checkAndBroadcastPrices, 200);
    }
  }

  // Intercept history.pushState and replaceState for SPA navigation
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

  // Also poll every 500ms as backup for any missed mutations or navigation
  setInterval(function() {
    checkAndBroadcastPrices();
    checkUrlChange();
  }, 500);

  // Initial broadcast
  setTimeout(checkAndBroadcastPrices, 500);

  // Handle requests from sidepanel
  chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
    if (!message || typeof message !== "object") return;
    if (message.type === "synth:getContext") {
      sendResponse({ ok: true, context: getContext() });
    }
    if (message.type === "synth:getPrices") {
      sendResponse({ ok: true, prices: scrapeLivePrices() });
    }
  });
})();
