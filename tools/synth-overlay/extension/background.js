// ---- Platform-aware background service worker ----
// Uses SynthPlatforms (loaded via importScripts) as single source of truth
// for supported origins and market URLs.

try { importScripts("platforms.js"); } catch (_e) {}

var API_BASE = "http://127.0.0.1:8765";
var ALARM_NAME = "synth-alert-poll";
var POLL_INTERVAL_MINUTES = 1;

// Storage keys (mirrored from alerts.js — background service workers
// cannot import page scripts, so we read shared chrome.storage.local keys directly)
var STORE_KEYS = {
  enabled: "synth_alerts_enabled",
  threshold: "synth_alerts_threshold",
  watchlist: "synth_alerts_watchlist",
  cooldowns: "synth_alerts_cooldowns",
  history: "synth_alerts_history",
  autoDismiss: "synth_alerts_auto_dismiss",
};

var COOLDOWN_MS = 5 * 60 * 1000;

function isSupportedUrl(url) {
  if (typeof SynthPlatforms !== "undefined") return SynthPlatforms.isSupportedUrl(url);
  return url && url.indexOf("polymarket.com") !== -1;
}

// ---- Side Panel ----

chrome.runtime.onInstalled.addListener(function () {
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  }
  syncAlarmState();
});

chrome.tabs.onUpdated.addListener(function (tabId, info, tab) {
  if (!chrome.sidePanel) return;
  if (info.status === "complete" || info.url) {
    var url = tab && tab.url ? tab.url : "";
    chrome.sidePanel.setOptions({
      tabId: tabId,
      path: "sidepanel.html",
      enabled: isSupportedUrl(url)
    });
  }
});

// Poll immediately when user switches away from a supported tab.
chrome.tabs.onActivated.addListener(function (activeInfo) {
  chrome.tabs.get(activeInfo.tabId, function (tab) {
    if (chrome.runtime.lastError || !tab) { pollWatchlist(); return; }
    if (!tab.url || !isSupportedUrl(tab.url)) {
      pollWatchlist();
    }
  });
});

// ---- Alert Storage Helpers ----

function loadAlertSettings(callback) {
  chrome.storage.local.get(
    [STORE_KEYS.enabled, STORE_KEYS.threshold, STORE_KEYS.watchlist],
    function (result) {
      callback({
        enabled: result[STORE_KEYS.enabled] != null ? result[STORE_KEYS.enabled] : false,
        threshold: result[STORE_KEYS.threshold] != null ? result[STORE_KEYS.threshold] : 3.0,
        watchlist: Array.isArray(result[STORE_KEYS.watchlist]) ? result[STORE_KEYS.watchlist] : [],
      });
    }
  );
}

function loadCooldowns(callback) {
  chrome.storage.local.get([STORE_KEYS.cooldowns], function (result) {
    callback(result[STORE_KEYS.cooldowns] || {});
  });
}

function saveCooldowns(map) {
  var now = Date.now();
  for (var key in map) {
    if (now - map[key] > COOLDOWN_MS * 2) delete map[key];
  }
  var obj = {};
  obj[STORE_KEYS.cooldowns] = map;
  chrome.storage.local.set(obj);
}

// ---- Alarm Management ----

function syncAlarmState() {
  loadAlertSettings(function (settings) {
    if (settings.enabled && settings.watchlist.length > 0) {
      chrome.alarms.get(ALARM_NAME, function (existing) {
        if (!existing) {
          chrome.alarms.create(ALARM_NAME, { periodInMinutes: POLL_INTERVAL_MINUTES });
          pollWatchlist();
        }
      });
    } else {
      chrome.alarms.clear(ALARM_NAME);
    }
  });
}

chrome.storage.onChanged.addListener(function (changes, area) {
  if (area !== "local") return;
  if (changes[STORE_KEYS.enabled] || changes[STORE_KEYS.watchlist]) {
    syncAlarmState();
    pollWatchlist();
    updateBadge();
  }
});

// ---- Badge ----
// Show the number of watched markets on the extension icon badge.
function updateBadge() {
  loadAlertSettings(function (settings) {
    var count = settings.enabled ? settings.watchlist.length : 0;
    var text = count > 0 ? String(count) : "";
    chrome.action.setBadgeText({ text: text });
    chrome.action.setBadgeBackgroundColor({ color: "#2563eb" });
    chrome.action.setBadgeTextColor({ color: "#ffffff" });
  });
}

chrome.alarms.onAlarm.addListener(function (alarm) {
  if (alarm.name === ALARM_NAME) pollWatchlist();
});

// ---- Poll Engine ----

function pollWatchlist() {
  loadAlertSettings(function (settings) {
    if (!settings.enabled || settings.watchlist.length === 0) return;
    settings.watchlist.forEach(function (item) {
      checkMarketEdge(item, settings.threshold);
    });
  });
}

function checkMarketEdge(item, threshold) {
  var platform = item.platform || "polymarket";
  var url = API_BASE + "/api/edge?slug=" + encodeURIComponent(item.slug)
    + "&platform=" + encodeURIComponent(platform);
  fetch(url)
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      if (data.error || data.edge_pct == null) return;
      if (Math.abs(data.edge_pct) < threshold) return;
      loadCooldowns(function (cooldowns) {
        var prev = cooldowns[item.slug];
        if (prev && (Date.now() - prev) < COOLDOWN_MS) return;
        suppressAndNotify(item, data, cooldowns);
      });
    })
    .catch(function () {});
}

// Skip notification if user is already viewing this market
function suppressAndNotify(item, data, cooldowns) {
  var notifId = "synth-edge::" + item.slug;

  chrome.notifications.getAll(function (all) {
    if (all[notifId]) return;

    chrome.tabs.query({ active: true, lastFocusedWindow: true }, function (tabs) {
      var activeUrl = (tabs && tabs[0] && tabs[0].url) || "";
      if (isSupportedUrl(activeUrl) && activeUrl.indexOf(item.slug) !== -1) {
        return;
      }
      cooldowns[item.slug] = Date.now();
      saveCooldowns(cooldowns);
      createEdgeNotification(notifId, item, data);
    });
  });
}

// ---- Notification ----

function fmtProb(p) {
  if (p == null) return "—";
  return Math.round(p * 100) + "¢";
}

function createEdgeNotification(notifId, item, data) {
  var edge = data.edge_pct;
  var signal = data.signal || "unknown";
  var direction = signal === "underpriced" ? "Underpriced" : signal === "overpriced" ? "Overpriced" : "Edge";
  var sign = edge >= 0 ? "+" : "";
  var strength = data.strength || "—";
  var conf = data.confidence_score != null ? Math.round(data.confidence_score * 100) + "%" : "—";
  var confLabel = data.confidence_score >= 0.7 ? "High" : data.confidence_score >= 0.4 ? "Med" : "Low";

  var platform = item.platform || "polymarket";
  var platformLabel = (typeof SynthPlatforms !== "undefined" && SynthPlatforms.get(platform))
    ? SynthPlatforms.get(platform).label : "Poly";
  var synthUp = fmtProb(data.synth_probability_up != null ? data.synth_probability_up : data.synth_probability);
  var polyUp = fmtProb(data.polymarket_probability_up != null ? data.polymarket_probability_up : data.polymarket_probability);

  var title = (item.label || item.slug) + " — " + direction + " " + sign + edge + "pp";
  var lines = [
    "Synth " + synthUp + " vs " + platformLabel + " " + polyUp + " | " + strength,
    "Confidence: " + confLabel + " (" + conf + ")",
  ];
  if (data.explanation) {
    lines.push(data.explanation.length > 120 ? data.explanation.substring(0, 117) + "…" : data.explanation);
  }

  // Save to notification history
  var historyEntry = {
    slug: item.slug,
    platform: platform,
    label: item.label || item.slug,
    title: title,
    message: lines.join("\n"),
    edgePct: edge,
    signal: signal,
    timestamp: Date.now(),
  };
  var histList = [];
  chrome.storage.local.get([STORE_KEYS.history], function (result) {
    histList = Array.isArray(result[STORE_KEYS.history]) ? result[STORE_KEYS.history] : [];
    histList.unshift(historyEntry);
    if (histList.length > 10) histList = histList.slice(0, 10);
    var hObj = {};
    hObj[STORE_KEYS.history] = histList;
    chrome.storage.local.set(hObj);
  });

  // Check auto-dismiss preference
  chrome.storage.local.get([STORE_KEYS.autoDismiss], function (result) {
    var autoDismiss = result[STORE_KEYS.autoDismiss] != null ? result[STORE_KEYS.autoDismiss] : false;
    chrome.notifications.create(notifId, {
      type: "basic",
      iconUrl: "icon128.png",
      title: title,
      message: lines.join("\n"),
      priority: 2,
      requireInteraction: !autoDismiss,
    });
  });
}

// Focus or open the correct market page for the clicked notification.
// Uses stored platform from watchlist to build the right URL.
chrome.notifications.onClicked.addListener(function (notifId) {
  if (notifId.indexOf("synth-edge::") !== 0) return;
  var slug = notifId.replace("synth-edge::", "");
  if (!slug) { chrome.notifications.clear(notifId); return; }

  // Look up the watchlist to get the platform for this slug
  chrome.storage.local.get([STORE_KEYS.watchlist], function (result) {
    var watchlist = Array.isArray(result[STORE_KEYS.watchlist]) ? result[STORE_KEYS.watchlist] : [];
    var item = null;
    for (var i = 0; i < watchlist.length; i++) {
      if (watchlist[i].slug === slug) { item = watchlist[i]; break; }
    }
    var platform = (item && item.platform) || "polymarket";
    var targetUrl = (typeof SynthPlatforms !== "undefined")
      ? SynthPlatforms.marketUrl(platform, slug)
      : "https://polymarket.com/event/" + slug;

    // Search across all supported platform tabs
    chrome.tabs.query({}, function (tabs) {
      var match = null;
      for (var j = 0; j < tabs.length; j++) {
        if (tabs[j].url && isSupportedUrl(tabs[j].url) && tabs[j].url.indexOf(slug) !== -1) {
          match = tabs[j];
          break;
        }
      }
      if (match) {
        chrome.tabs.update(match.id, { active: true });
        chrome.windows.update(match.windowId, { focused: true });
      } else {
        chrome.tabs.create({ url: targetUrl });
      }
      chrome.notifications.clear(notifId);
    });
  });
});

// Start polling on service worker startup if alerts were already enabled
syncAlarmState();
updateBadge();
