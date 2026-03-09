"use strict";

/**
 * Alert settings and watchlist — shared storage schema used by
 * sidepanel.js (UI) and background.js (polling engine).
 *
 * Storage keys (chrome.storage.local):
 *   synth_alerts_enabled    : boolean
 *   synth_alerts_threshold  : number  (edge pp, default 3.0)
 *   synth_alerts_watchlist  : Array<{ slug, asset, label, addedAt }>
 *   synth_alerts_cooldowns  : Object  { slug: timestamp }
 */

var SynthAlerts = (function () {
  var KEYS = {
    enabled: "synth_alerts_enabled",
    threshold: "synth_alerts_threshold",
    watchlist: "synth_alerts_watchlist",
    cooldowns: "synth_alerts_cooldowns",
  };

  var DEFAULTS = {
    enabled: false,
    threshold: 3.0,
    watchlist: [],
  };

  var COOLDOWN_MS = 5 * 60 * 1000;
  var MAX_WATCHLIST = 20;

  // ---- Storage helpers ----

  function load(callback) {
    chrome.storage.local.get(
      [KEYS.enabled, KEYS.threshold, KEYS.watchlist],
      function (result) {
        callback({
          enabled: result[KEYS.enabled] != null ? result[KEYS.enabled] : DEFAULTS.enabled,
          threshold: result[KEYS.threshold] != null ? result[KEYS.threshold] : DEFAULTS.threshold,
          watchlist: Array.isArray(result[KEYS.watchlist]) ? result[KEYS.watchlist] : DEFAULTS.watchlist,
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
    if (isNaN(num) || num < 0.1) num = DEFAULTS.threshold;
    if (num > 50) num = 50;
    var obj = {};
    obj[KEYS.threshold] = Math.round(num * 10) / 10;
    chrome.storage.local.set(obj);
    return obj[KEYS.threshold];
  }

  function saveWatchlist(list) {
    var obj = {};
    obj[KEYS.watchlist] = list;
    chrome.storage.local.set(obj);
  }

  // ---- Watchlist ----

  function addToWatchlist(slug, asset, label, callback) {
    if (!slug) { if (callback) callback([]); return; }
    load(function (settings) {
      var exists = settings.watchlist.some(function (w) { return w.slug === slug; });
      if (exists) { if (callback) callback(settings.watchlist); return; }
      if (settings.watchlist.length >= MAX_WATCHLIST) { if (callback) callback(settings.watchlist); return; }
      settings.watchlist.push({
        slug: slug,
        asset: asset || "BTC",
        label: label || slug,
        addedAt: Date.now(),
      });
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

  // ---- Cooldowns ----

  function loadCooldowns(callback) {
    chrome.storage.local.get([KEYS.cooldowns], function (result) {
      callback(result[KEYS.cooldowns] || {});
    });
  }

  function saveCooldowns(map) {
    var obj = {};
    obj[KEYS.cooldowns] = map;
    chrome.storage.local.set(obj);
  }

  function isOnCooldown(slug, cooldowns) {
    var ts = cooldowns[slug];
    if (!ts) return false;
    return (Date.now() - ts) < COOLDOWN_MS;
  }

  function setCooldown(slug, cooldowns) {
    cooldowns[slug] = Date.now();
    pruneStaleCooldowns(cooldowns);
    saveCooldowns(cooldowns);
    return cooldowns;
  }

  function pruneStaleCooldowns(cooldowns) {
    var now = Date.now();
    for (var key in cooldowns) {
      if (now - cooldowns[key] > COOLDOWN_MS * 2) {
        delete cooldowns[key];
      }
    }
  }

  // ---- Threshold ----

  function exceedsThreshold(edgePct, threshold) {
    if (edgePct == null || threshold == null) return false;
    return Math.abs(edgePct) >= threshold;
  }

  // ---- Format ----

  function formatMarketLabel(asset, marketType) {
    var typeMap = { daily: "24h", hourly: "1h", "15min": "15m", "5min": "5m" };
    return (asset || "BTC") + " " + (typeMap[marketType] || marketType || "daily");
  }

  return {
    KEYS: KEYS,
    DEFAULTS: DEFAULTS,
    COOLDOWN_MS: COOLDOWN_MS,
    MAX_WATCHLIST: MAX_WATCHLIST,
    load: load,
    saveEnabled: saveEnabled,
    saveThreshold: saveThreshold,
    saveWatchlist: saveWatchlist,
    addToWatchlist: addToWatchlist,
    removeFromWatchlist: removeFromWatchlist,
    loadCooldowns: loadCooldowns,
    saveCooldowns: saveCooldowns,
    isOnCooldown: isOnCooldown,
    setCooldown: setCooldown,
    exceedsThreshold: exceedsThreshold,
    formatMarketLabel: formatMarketLabel,
  };
})();
