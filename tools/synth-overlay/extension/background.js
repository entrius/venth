// Sites where the side panel should be available
var SUPPORTED_ORIGINS = [
  "https://polymarket.com/"
];

var API_BASE = "http://127.0.0.1:8765";
var ALARM_NAME = "synth-alert-poll";
var POLL_INTERVAL_MINUTES = 1;

// Set to true during development for faster polling (10s instead of 60s)
var DEBUG_FAST_POLL = false;
var DEBUG_POLL_MS = 10000;

// Don't re-alert the same market within this window
var COOLDOWN_MS = DEBUG_FAST_POLL ? 30 * 1000 : 5 * 60 * 1000;
var LAST_ALERTED_KEY = "synth_alerts_last_alerted";

// Cooldown state is stored in chrome.storage.local so it survives
// service worker restarts (MV3 workers can be killed at any time).
function loadLastAlerted(callback) {
  chrome.storage.local.get([LAST_ALERTED_KEY], function (result) {
    callback(result[LAST_ALERTED_KEY] || {});
  });
}

function saveLastAlerted(map) {
  var obj = {};
  obj[LAST_ALERTED_KEY] = map;
  chrome.storage.local.set(obj);
}

function isSupportedUrl(url) {
  for (var i = 0; i < SUPPORTED_ORIGINS.length; i++) {
    if (url.indexOf(SUPPORTED_ORIGINS[i]) === 0) return true;
  }
  return false;
}

// ---- Side Panel ----
// The panel is disabled globally and only enabled on Polymarket tabs.
// This prevents it from appearing on new tabs or unrelated sites.

chrome.runtime.onInstalled.addListener(function () {
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  }
  if (chrome.sidePanel && chrome.sidePanel.setOptions) {
    chrome.sidePanel.setOptions({ enabled: false });
  }
  syncAlarmState();
});

// Enable or disable the side panel for a given tab based on its URL
function updateSidePanelForTab(tabId) {
  if (!chrome.sidePanel) return;
  chrome.tabs.get(tabId, function (tab) {
    if (chrome.runtime.lastError || !tab) return;
    var url = tab.url || "";
    chrome.sidePanel.setOptions({
      tabId: tabId,
      path: "sidepanel.html",
      enabled: isSupportedUrl(url)
    });
  });
}

chrome.tabs.onUpdated.addListener(function (tabId, info, tab) {
  if (!chrome.sidePanel) return;
  if (info.status === "complete" || info.url) {
    updateSidePanelForTab(tabId);
  }
});

chrome.tabs.onActivated.addListener(function (activeInfo) {
  updateSidePanelForTab(activeInfo.tabId);
});

// ---- Alert Polling Engine ----
// Polls watched markets on a timer and fires browser notifications
// when the edge exceeds the user's threshold.

function loadAlertSettings(callback) {
  chrome.storage.local.get(
    ["synth_alerts_enabled", "synth_alerts_threshold", "synth_alerts_watchlist"],
    function (result) {
      callback({
        enabled: result.synth_alerts_enabled != null ? result.synth_alerts_enabled : false,
        threshold: result.synth_alerts_threshold != null ? result.synth_alerts_threshold : 3.0,
        watchlist: result.synth_alerts_watchlist || [],
      });
    }
  );
}

var _debugTimer = null;

// Start or stop the polling timer based on current settings.
// In production we use chrome.alarms (minimum 1 min); in debug mode
// we use setInterval for faster iteration.
function syncAlarmState() {
  loadAlertSettings(function (settings) {
    if (settings.enabled && settings.watchlist.length > 0) {
      if (DEBUG_FAST_POLL) {
        if (!_debugTimer) {
          console.log("[Synth-Alerts] Debug: fast poll every " + (DEBUG_POLL_MS / 1000) + "s");
          _debugTimer = setInterval(pollWatchlist, DEBUG_POLL_MS);
        }
      } else {
        chrome.alarms.get(ALARM_NAME, function (existing) {
          if (!existing) {
            chrome.alarms.create(ALARM_NAME, { periodInMinutes: POLL_INTERVAL_MINUTES });
            console.log("[Synth-Alerts] Alarm created, polling every " + POLL_INTERVAL_MINUTES + " min");
            pollWatchlist(); // run once immediately so user doesn't wait
          }
        });
      }
    } else {
      if (DEBUG_FAST_POLL) {
        if (_debugTimer) { clearInterval(_debugTimer); _debugTimer = null; }
      } else {
        chrome.alarms.clear(ALARM_NAME);
      }
      console.log("[Synth-Alerts] Polling stopped (disabled or empty watchlist)");
    }
  });
}

// Re-sync whenever the user toggles alerts or changes the watchlist
chrome.storage.onChanged.addListener(function (changes, area) {
  if (area !== "local") return;
  if (changes.synth_alerts_enabled || changes.synth_alerts_watchlist) {
    syncAlarmState();
  }
});

chrome.alarms.onAlarm.addListener(function (alarm) {
  if (alarm.name !== ALARM_NAME) return;
  pollWatchlist();
});

function pollWatchlist() {
  loadAlertSettings(function (settings) {
    if (!settings.enabled || settings.watchlist.length === 0) return;
    console.log("[Synth-Alerts] Polling", settings.watchlist.length, "markets");
    settings.watchlist.forEach(function (item) {
      fetchAndCheck(item, settings.threshold);
    });
  });
}

// Fetch edge data for one market and fire a notification if it exceeds threshold
function fetchAndCheck(item, threshold) {
  var url = API_BASE + "/api/edge?slug=" + encodeURIComponent(item.slug);
  fetch(url)
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      if (data.error) return;
      var edgePct = data.edge_pct;
      if (edgePct == null) return;
      if (Math.abs(edgePct) < threshold) return;

      // Check cooldown so we don't spam the same market
      loadLastAlerted(function (lastAlerted) {
        var prev = lastAlerted[item.slug];
        var now = Date.now();
        if (prev && now - prev.timestamp < COOLDOWN_MS) return;

        lastAlerted[item.slug] = { edgePct: edgePct, timestamp: now };

        // Remove stale entries to keep storage clean
        for (var key in lastAlerted) {
          if (now - lastAlerted[key].timestamp > COOLDOWN_MS) {
            delete lastAlerted[key];
          }
        }

        saveLastAlerted(lastAlerted);
        fireNotification(item, data);
      });
    })
    .catch(function (err) {
      console.log("[Synth-Alerts] Fetch failed for", item.slug, err.message);
    });
}

function fmtProb(p) {
  if (p == null) return "—";
  return Math.round(p * 100) + "¢";
}

// Before firing, check two things:
// 1. Is a notification for this market already on screen? (avoid duplicates)
// 2. Is the user already looking at this market? (no need to interrupt)
function fireNotification(item, data) {
  var notifId = "synth-alert::" + item.slug;

  chrome.notifications.getAll(function (all) {
    if (all[notifId]) return; // already showing

    chrome.tabs.query({ active: true, lastFocusedWindow: true }, function (tabs) {
      var activeUrl = (tabs && tabs[0] && tabs[0].url) || "";
      if (activeUrl.indexOf("polymarket.com") !== -1 && activeUrl.indexOf(item.slug) !== -1) {
        return; // user is already on this market page
      }
      _createNotification(notifId, item, data);
    });
  });
}

// Build and show the notification with full edge details
function _createNotification(notifId, item, data) {
  var edgePct = data.edge_pct;
  var signal = data.signal || "unknown";
  var direction = signal === "underpriced" ? "Underpriced" : signal === "overpriced" ? "Overpriced" : "Edge";
  var strength = data.strength || "—";
  var conf = data.confidence_score != null ? Math.round(data.confidence_score * 100) + "%" : "—";
  var confLabel = data.confidence_score >= 0.7 ? "High" : data.confidence_score >= 0.4 ? "Med" : "Low";
  var sign = edgePct >= 0 ? "+" : "";
  var synthUp = fmtProb(data.synth_probability_up);
  var polyUp = fmtProb(data.polymarket_probability_up);

  var title = item.label + " — " + direction + " " + sign + edgePct + "pp";

  var lines = [];
  lines.push("Synth " + synthUp + " vs Poly " + polyUp + " | " + strength);
  lines.push("Confidence: " + confLabel + " (" + conf + ")");
  if (data.explanation) {
    lines.push(data.explanation.substring(0, 100));
  }
  var message = lines.join("\n");

  chrome.notifications.create(notifId, {
    type: "basic",
    iconUrl: "icon128.png",
    title: title,
    message: message,
    priority: 2,
    requireInteraction: false,
  });
  console.log("[Synth-Alerts] Notification:", title);
}

// When user clicks a notification, focus or open the relevant Polymarket page
chrome.notifications.onClicked.addListener(function (notifId) {
  if (notifId.indexOf("synth-alert::") !== 0) return;
  var slug = notifId.split("::")[1];
  if (!slug) { chrome.notifications.clear(notifId); return; }

  var targetUrl = "https://polymarket.com/event/" + slug;

  chrome.tabs.query({ url: "https://polymarket.com/*" }, function (tabs) {
    var found = null;
    for (var i = 0; i < tabs.length; i++) {
      if (tabs[i].url && tabs[i].url.indexOf(slug) !== -1) {
        found = tabs[i];
        break;
      }
    }
    if (found) {
      chrome.tabs.update(found.id, { active: true });
      chrome.windows.update(found.windowId, { focused: true });
    } else {
      chrome.tabs.create({ url: targetUrl });
    }
    chrome.notifications.clear(notifId);
  });
});

// On service worker startup, start polling if alerts are active
syncAlarmState();
pollWatchlist();
