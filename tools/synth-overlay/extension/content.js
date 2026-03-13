(function () {
  "use strict";

  // Guard against extension context invalidation (e.g. after extension reload)
  var _contextValid = true;
  var _pollInterval = null;
  function isContextValid() {
    if (!_contextValid) return false;
    try {
      void chrome.runtime.id;
      return true;
    } catch (e) {
      _contextValid = false;
      teardown();
      return false;
    }
  }
  function teardown() {
    if (_pollInterval) { clearInterval(_pollInterval); _pollInterval = null; }
    if (observer) { observer.disconnect(); }
    console.log("[Synth-Overlay] Extension context invalidated, content script stopped.");
  }

  // Track last known prices to detect changes
  var lastPrices = { upPrice: null, downPrice: null };

  // Detect which platform we're on
  var currentPlatform = (function () {
    var host = (window.location.hostname || "").toLowerCase();
    if (host.indexOf("kalshi.com") !== -1) return "kalshi";
    if (host.indexOf("polymarket.com") !== -1) return "polymarket";
    return null;
  })();

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

    if (host.indexOf("kalshi.com") !== -1) {
      // Kalshi URL patterns:
      //   /markets/<series_ticker>                                → series page (e.g. kxbtcd)
      //   /markets/<market_ticker>                                → contract (e.g. KXBTCD-26MAR1317-T70499.99)
      //   /markets/<series>/<desc>/<event_ticker>                 → event (e.g. kxsol15m/solana-15-minutes/kxsol15m-26mar121945)
      //   /events/<event_ticker>                                  → event page (e.g. KXBTCD-26MAR1317)
      //   /events/<series>/<event_ticker>                         → event page (e.g. kxbtcd/KXBTCD-26MAR1317)
      if (segments[0] === "browse" || segments[0] === "portfolio") return null;
      if (segments[0] === "markets" || segments[0] === "events") {
        // Return the last segment — it's the most specific ticker
        var last = segments[segments.length - 1];
        // Don't return the route prefix itself
        if (last === "markets" || last === "events") return null;
        return last || null;
      }
      return segments[segments.length - 1] || null;
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
   * Scrape live prices from Kalshi's DOM.
   * Kalshi displays prices in dollars (e.g. "$0.52", "52¢") for Yes/No contracts.
   * Returns { upPrice: 0.XX, downPrice: 0.XX } or null if not found.
   *
   * Kalshi DOM patterns observed:
   * - Buttons/spans with "Yes $0.52" / "No $0.48" or "Buy Yes 52¢" / "Buy No 48¢"
   * - Dollar format: "$0.52" or "0.52" near Yes/No context
   * - Cent format: "52¢" near Yes/No context
   * - Order book displays with bid/ask in dollars
   *
   * On multi-strike pages (e.g. daily above/below with $71,250, $71,500, $71,750),
   * the DOM has prices for ALL strikes in a list, but the active/selected contract's
   * prices appear in the trading panel (near Buy/Sell/Amount controls).
   * We prioritize the trading panel prices.
   */
  function scrapeKalshiPrices() {
    var yesPrice = null;
    var noPrice = null;

    // Helper: check if an element is inside the trading panel
    // (ancestor contains "buy" and "sell" and "amount" — the order form)
    function isInTradingPanel(el) {
      var ancestor = el.parentElement;
      for (var up = 0; up < 8 && ancestor; up++) {
        var aText = (ancestor.textContent || "").toLowerCase();
        // Trading panel containers are typically < 500 chars and contain Buy+Sell+Amount
        if (aText.length < 500 && /\bbuy\b/.test(aText) && /\bsell\b/.test(aText) && /\bamount\b/.test(aText)) {
          return true;
        }
        // Also match panels with "sign up to trade" for logged-out users
        if (aText.length < 500 && /\bbuy\b/.test(aText) && /sign up/i.test(aText)) {
          return true;
        }
        ancestor = ancestor.parentElement;
      }
      return false;
    }

    // Helper: extract Yes/No cent or dollar price from text
    function extractPrice(text, side) {
      // side = "yes" or "no", matches Yes/Up or No/Down
      var sidePattern = side === "yes" ? "(yes|up)" : "(no|down)";
      // Cent format: "Yes 80¢" / "Buy Yes 80¢"
      var cm = text.match(new RegExp("(?:buy\\s+)?" + sidePattern + "\\s+(\\d{1,2})\\s*[¢c%]", "i"));
      if (cm) {
        var cv = parseInt(cm[2], 10) / 100;
        if (cv >= 0.01 && cv <= 0.99) return cv;
      }
      // Dollar format: "Yes $0.80" / "Buy Yes $0.80"
      var dm = text.match(new RegExp("(?:buy\\s+)?" + sidePattern + "\\s+\\$?(0\\.\\d{2,4})", "i"));
      if (dm) {
        var dv = parseFloat(dm[2]);
        if (dv >= 0.01 && dv <= 0.99) return dv;
      }
      return null;
    }

    var els = document.querySelectorAll("button, a, span, div, p, [role='button'], [role='cell'], td");

    // Pass 1: PRIORITY — scan only elements inside the trading panel
    // (the Buy/Sell/Amount card with the active contract's prices)
    for (var i = 0; i < els.length; i++) {
      var text = (els[i].textContent || "").trim();
      if (text.length > 40 || text.length < 2) continue;
      if (yesPrice === null) {
        var yp = extractPrice(text, "yes");
        if (yp !== null && isInTradingPanel(els[i])) yesPrice = yp;
      }
      if (noPrice === null) {
        var np = extractPrice(text, "no");
        if (np !== null && isInTradingPanel(els[i])) noPrice = np;
      }
      if (yesPrice !== null && noPrice !== null) break;
    }

    if (yesPrice !== null && noPrice !== null && validatePricePair(yesPrice, noPrice)) {
      console.log("[Synth-Overlay] Kalshi prices from trading panel:", { upPrice: yesPrice, downPrice: noPrice });
      return { upPrice: yesPrice, downPrice: noPrice };
    }

    // Pass 2: FALLBACK — scan all elements (single-strike pages, or panel not found)
    yesPrice = null;
    noPrice = null;
    for (var j = 0; j < els.length; j++) {
      var text2 = (els[j].textContent || "").trim();
      if (text2.length > 40 || text2.length < 2) continue;

      // Strategy 1: "Yes XX¢" / "No XX¢" (cent format)
      if (yesPrice === null) {
        var yv = extractPrice(text2, "yes");
        if (yv !== null) yesPrice = yv;
      }
      if (noPrice === null) {
        var nv = extractPrice(text2, "no");
        if (nv !== null) noPrice = nv;
      }

      // Strategy 2: Standalone price near Yes/No context in parent
      if (yesPrice === null || noPrice === null) {
        var pm = text2.match(/^(\d{1,2})\s*[¢c%]$/) ||
                 text2.match(/^\$?(0\.\d{2,4})$/);
        if (pm) {
          var rawVal = pm[1];
          var price;
          if (rawVal.indexOf(".") !== -1) {
            price = parseFloat(rawVal);
          } else {
            price = parseInt(rawVal, 10) / 100;
          }
          if (price >= 0.01 && price <= 0.99) {
            var parent = els[j].parentElement;
            for (var d = 0; d < 5 && parent; d++) {
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

    // Strategy 3: Look for Kalshi's order book / price display patterns
    if (yesPrice === null || noPrice === null) {
      for (var k = 0; k < els.length; k++) {
        var t = (els[k].textContent || "").trim();
        if (t.length > 60 || t.length < 3) continue;
        if (yesPrice === null) {
          var bestYes = t.match(/(?:best\s+)?yes[:\s]+\$?(0\.\d{2,4})/i) ||
                        t.match(/(?:best\s+)?yes[:\s]+(\d{1,2})\s*[¢c%]/i);
          if (bestYes) {
            var byVal = bestYes[1];
            yesPrice = byVal.indexOf(".") !== -1 ? parseFloat(byVal) : parseInt(byVal, 10) / 100;
          }
        }
        if (noPrice === null) {
          var bestNo = t.match(/(?:best\s+)?no[:\s]+\$?(0\.\d{2,4})/i) ||
                       t.match(/(?:best\s+)?no[:\s]+(\d{1,2})\s*[¢c%]/i);
          if (bestNo) {
            var bnVal = bestNo[1];
            noPrice = bnVal.indexOf(".") !== -1 ? parseFloat(bnVal) : parseInt(bnVal, 10) / 100;
          }
        }
        if (yesPrice !== null && noPrice !== null) break;
      }
    }

    if (yesPrice !== null && noPrice !== null && validatePricePair(yesPrice, noPrice)) {
      console.log("[Synth-Overlay] Kalshi prices from DOM:", { upPrice: yesPrice, downPrice: noPrice });
      return { upPrice: yesPrice, downPrice: noPrice };
    }

    // Infer missing side
    if (yesPrice !== null && yesPrice >= 0.01 && yesPrice <= 0.99) {
      return { upPrice: yesPrice, downPrice: 1 - yesPrice };
    }
    if (noPrice !== null && noPrice >= 0.01 && noPrice <= 0.99) {
      return { upPrice: 1 - noPrice, downPrice: noPrice };
    }

    return null;
  }

  /**
   * Platform-aware live price scraper — dispatches to the right strategy.
   */
  function scrapePrices() {
    if (currentPlatform === "kalshi") return scrapeKalshiPrices();
    return scrapeLivePrices();
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

  /**
   * On Kalshi pages without a market slug, scan the DOM for links pointing to
   * crypto prediction markets.  Returns an array of { ticker, href, label }
   * objects (max 10) – or an empty array when nothing is found / not on Kalshi.
   */
  function scanKalshiMarketLinks() {
    if (currentPlatform !== "kalshi") return [];
    var slug = slugFromPage();
    if (slug) return []; // already on a market page

    var seen = {};
    var results = [];
    var anchors = document.querySelectorAll("a[href]");
    for (var i = 0; i < anchors.length && results.length < 10; i++) {
      var href = anchors[i].getAttribute("href") || "";
      // Match links to Kalshi markets or events with crypto tickers
      // Series pages: /markets/kxbtcd, /markets/kxeth
      // Event pages: /events/KXBTCD-26MAR1317
      // Contract pages: /markets/KXBTCD-26MAR1317-T70499.99
      var m = href.match(/\/(markets|events)\/((kx[a-z0-9]+|btc|eth|btcd|ethd)[a-z0-9._-]*)/i);
      if (!m) continue;
      var ticker = m[2].toUpperCase();
      if (seen[ticker]) continue;
      seen[ticker] = true;
      var label = (anchors[i].textContent || "").trim().substring(0, 60) || ticker;
      results.push({ ticker: ticker, href: href, label: label });
    }
    return results;
  }

  function getContext() {
    var livePrices = scrapePrices();
    var balance = scrapeBalance();
    var slug = slugFromPage();
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

  // Broadcast price update to extension
  function broadcastPriceUpdate(prices) {
    if (!prices || !isContextValid()) return;
    try {
      chrome.runtime.sendMessage({
        type: "synth:priceUpdate",
        prices: prices,
        slug: slugFromPage(),
        timestamp: Date.now()
      }).catch(function() {});
    } catch (e) {
      _contextValid = false;
      teardown();
    }
  }

  // Check if prices changed and broadcast if so
  function checkAndBroadcastPrices() {
    if (!_contextValid) return;
    var prices = scrapePrices();
    if (!prices) return;
    
    if (prices.upPrice !== lastPrices.upPrice || prices.downPrice !== lastPrices.downPrice) {
      lastPrices = { upPrice: prices.upPrice, downPrice: prices.downPrice };
      broadcastPriceUpdate(prices);
    }
  }

  // Set up MutationObserver for instant price detection
  var observer = new MutationObserver(function(mutations) {
    if (!_contextValid) return;
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
    if (!isContextValid()) return;
    var newSlug = slugFromPage();
    if (newSlug !== lastSlug) {
      console.log("[Synth-Overlay] URL changed:", lastSlug, "->", newSlug);
      lastSlug = newSlug;
      lastPrices = { upPrice: null, downPrice: null };
      try {
        chrome.runtime.sendMessage({
          type: "synth:urlChanged",
          slug: newSlug,
          url: window.location.href,
          timestamp: Date.now()
        }).catch(function() {});
      } catch (e) {
        _contextValid = false;
        teardown();
        return;
      }
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
  _pollInterval = setInterval(function() {
    if (!isContextValid()) return;
    checkAndBroadcastPrices();
    checkUrlChange();
  }, 500);

  // Initial broadcast
  setTimeout(checkAndBroadcastPrices, 500);

  // Handle requests from sidepanel
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
    } catch (e) {
      // Extension context may have been invalidated between the check and response
    }
  });
})();
