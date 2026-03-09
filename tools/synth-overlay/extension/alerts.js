"use strict";

/**
 * Alerts module: persistent settings and watchlist management.
 * Loaded by sidepanel.html (before sidepanel.js) and imported conceptually
 * by background.js via shared storage keys.
 *
 * Storage schema (chrome.storage.local):
 *   synth_alerts_enabled   : boolean
 *   synth_alerts_threshold : number  (edge pp, e.g. 3.0)
 *   synth_alerts_watchlist : Array<{ slug: string, asset: string, label: string }>
 */

var SynthAlerts = (function () {
  var KEYS = {
    enabled: "synth_alerts_enabled",
    threshold: "synth_alerts_threshold",
    watchlist: "synth_alerts_watchlist",
  };

  var DEFAULTS = {
    enabled: false,
    threshold: 3.0,
    watchlist: [],
  };

  // ---- Storage helpers ----

  function load(callback) {
    chrome.storage.local.get(
      [KEYS.enabled, KEYS.threshold, KEYS.watchlist],
      function (result) {
        callback({
          enabled: result[KEYS.enabled] != null ? result[KEYS.enabled] : DEFAULTS.enabled,
          threshold: result[KEYS.threshold] != null ? result[KEYS.threshold] : DEFAULTS.threshold,
          watchlist: result[KEYS.watchlist] || DEFAULTS.watchlist,
        });
      }
    );
  }

  function saveEnabled(val) {
    var obj = {};
    obj[KEYS.enabled] = !!val;
    chrome.storage.local.set(obj);
  }

  function saveThreshold(val) {
    var num = parseFloat(val);
    if (isNaN(num) || num < 0.5) num = DEFAULTS.threshold;
    var obj = {};
    obj[KEYS.threshold] = num;
    chrome.storage.local.set(obj);
    return num;
  }

  function saveWatchlist(list) {
    var obj = {};
    obj[KEYS.watchlist] = list;
    chrome.storage.local.set(obj);
  }

  // ---- Watchlist operations ----

  function addToWatchlist(slug, asset, label, callback) {
    load(function (settings) {
      var exists = settings.watchlist.some(function (w) { return w.slug === slug; });
      if (exists) {
        if (callback) callback(settings.watchlist);
        return;
      }
      settings.watchlist.push({ slug: slug, asset: asset, label: label });
      saveWatchlist(settings.watchlist);
      if (callback) callback(settings.watchlist);
    });
  }

  function removeFromWatchlist(slug, callback) {
    load(function (settings) {
      settings.watchlist = settings.watchlist.filter(function (w) { return w.slug !== slug; });
      saveWatchlist(settings.watchlist);
      if (callback) callback(settings.watchlist);
    });
  }

  // ---- Threshold check ----

  function exceedsThreshold(edgePct, threshold) {
    return Math.abs(edgePct) >= threshold;
  }

  // ---- Format helpers (matching sidepanel.js conventions) ----

  function formatLabel(asset, marketType) {
    var typeMap = { daily: "Daily", hourly: "Hourly", "15min": "15m", "5min": "5m" };
    return asset + " " + (typeMap[marketType] || marketType);
  }

  return {
    KEYS: KEYS,
    DEFAULTS: DEFAULTS,
    load: load,
    saveEnabled: saveEnabled,
    saveThreshold: saveThreshold,
    saveWatchlist: saveWatchlist,
    addToWatchlist: addToWatchlist,
    removeFromWatchlist: removeFromWatchlist,
    exceedsThreshold: exceedsThreshold,
    formatLabel: formatLabel,
  };
})();
