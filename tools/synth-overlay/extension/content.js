(function () {
  "use strict";

  var API_BASE = "http://127.0.0.1:8765";
  var currentSlug = null;

  function slugFromPage() {
    var path = window.location.pathname || "";
    var segments = path.split("/").filter(Boolean);
    var first = segments[0];
    var second = segments[1] || segments[0];
    if (first === "event" || first === "market") {
      return second || null;
    }
    return first || null;
  }

  function formatLabel(signal, edgePct) {
    var prefix = edgePct >= 0 ? "+" : "";
    if (signal === "fair") return "Fair " + prefix + edgePct + "%";
    return "YES Edge " + prefix + edgePct + "%";
  }

  function confidenceLabel(score) {
    if (score >= 0.7) return "High";
    if (score >= 0.4) return "Medium";
    return "Low";
  }

  function confidenceBarWidth(score) {
    return Math.max(5, Math.min(100, Math.round(score * 100)));
  }

  function formatTime(isoString) {
    if (!isoString || typeof isoString !== "string") return "";
    var d = new Date(isoString.trim());
    if (isNaN(d.getTime())) return isoString;
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var mon = months[d.getUTCMonth()];
    var day = d.getUTCDate();
    var h = d.getUTCHours();
    var m = d.getUTCMinutes();
    var ampm = h >= 12 ? "PM" : "AM";
    h = h % 12;
    if (h === 0) h = 12;
    var min = m < 10 ? "0" + m : String(m);
    return mon + " " + day + ", " + h + ":" + min + " " + ampm + " UTC";
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function createBadge(data) {
    var edge = data.edge_pct;
    var signal = data.signal;
    var strength = data.strength;
    var label = formatLabel(signal, edge);

    var hasDual = data.edge_1h_pct != null && data.edge_24h_pct != null;
    var now1h = hasDual ? formatLabel(data.signal_1h, data.edge_1h_pct) : label;
    var byClose24h = hasDual ? formatLabel(data.signal_24h, data.edge_24h_pct) : label;

    var confScore = data.confidence_score != null ? data.confidence_score : 0.5;
    var confText = confidenceLabel(confScore);
    var barWidth = confidenceBarWidth(confScore);

    var noTradeRow = data.no_trade_warning
      ? '<div class="synth-overlay-detail-row synth-overlay-no-trade">' +
        "No trade \u2014 uncertainty high or signals conflict.</div>"
      : "";

    var root = document.createElement("div");
    root.className = "synth-overlay-root";
    root.setAttribute("data-synth-overlay", "badge");

    root.innerHTML =
      '<div class="synth-overlay-badge synth-overlay-' + escapeHtml(signal) + '">' +
        '<span class="synth-overlay-label">' + escapeHtml(label) + "</span>" +
        '<span class="synth-overlay-strength">' + escapeHtml(strength) + "</span>" +
      "</div>" +
      '<div class="synth-overlay-detail" hidden>' +
        '<div class="synth-overlay-detail-row"><strong>Now (1h)</strong> ' + escapeHtml(now1h) + "</div>" +
        '<div class="synth-overlay-detail-row"><strong>By close (24h)</strong> ' + escapeHtml(byClose24h) + "</div>" +
        '<div class="synth-overlay-detail-row">Confidence: ' + escapeHtml(confText) +
          ' <div class="synth-overlay-conf-bar"><div class="synth-overlay-conf-fill" style="width:' + barWidth + '%"></div></div>' +
        "</div>" +
        noTradeRow +
        '<div class="synth-overlay-detail-meta">' + escapeHtml(formatTime(data.current_time) || "") + "</div>" +
        '<div class="synth-overlay-detail-expand">Details \u25B6</div>' +
      "</div>";

    var badge = root.querySelector(".synth-overlay-badge");
    var detail = root.querySelector(".synth-overlay-detail");
    var expandBtn = root.querySelector(".synth-overlay-detail-expand");

    if (badge) {
      badge.addEventListener("click", function (e) {
        e.stopPropagation();
        if (detail) detail.hidden = !detail.hidden;
      });
    }

    if (expandBtn) {
      expandBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        showPanel(data);
      });
    }

    return root;
  }

  function showPanel(data) {
    closePanel();
    var panel = document.createElement("div");
    panel.className = "synth-overlay-panel";
    panel.setAttribute("data-synth-overlay", "panel");

    var explanation = data.explanation || "No explanation available.";
    var invalidation = data.invalidation || "";
    var confScore = data.confidence_score != null ? data.confidence_score : 0.5;
    var barWidth = confidenceBarWidth(confScore);

    var hasDual = data.edge_1h_pct != null && data.edge_24h_pct != null;
    var now1h = hasDual ? formatLabel(data.signal_1h, data.edge_1h_pct) : formatLabel(data.signal, data.edge_pct);
    var byClose24h = hasDual ? formatLabel(data.signal_24h, data.edge_24h_pct) : formatLabel(data.signal, data.edge_pct);

    panel.innerHTML =
      '<div class="synth-overlay-panel-header">' +
        '<span class="synth-overlay-panel-title">Synth Analysis</span>' +
        '<span class="synth-overlay-panel-close">\u2715</span>' +
      "</div>" +
      '<div class="synth-overlay-panel-body">' +
        '<div class="synth-overlay-panel-section">' +
          '<div class="synth-overlay-panel-label">Signal</div>' +
          '<div class="synth-overlay-panel-row"><strong>Now (1h):</strong> ' + escapeHtml(now1h) + "</div>" +
          '<div class="synth-overlay-panel-row"><strong>By close (24h):</strong> ' + escapeHtml(byClose24h) + "</div>" +
          '<div class="synth-overlay-panel-row"><strong>Strength:</strong> ' + escapeHtml(data.strength) + "</div>" +
        "</div>" +
        '<div class="synth-overlay-panel-section">' +
          '<div class="synth-overlay-panel-label">Confidence</div>' +
          '<div class="synth-overlay-conf-bar synth-overlay-conf-bar-lg">' +
            '<div class="synth-overlay-conf-fill" style="width:' + barWidth + '%"></div>' +
          "</div>" +
          '<div class="synth-overlay-panel-row">' + escapeHtml(confidenceLabel(confScore)) +
            " (" + Math.round(confScore * 100) + "%)</div>" +
        "</div>" +
        '<div class="synth-overlay-panel-section">' +
          '<div class="synth-overlay-panel-label">Why this signal exists</div>' +
          '<div class="synth-overlay-panel-text">' + escapeHtml(explanation) + "</div>" +
        "</div>" +
        (invalidation
          ? '<div class="synth-overlay-panel-section">' +
              '<div class="synth-overlay-panel-label">What would invalidate it</div>' +
              '<div class="synth-overlay-panel-text">' + escapeHtml(invalidation) + "</div>" +
            "</div>"
          : "") +
        (data.no_trade_warning
          ? '<div class="synth-overlay-panel-section synth-overlay-no-trade">' +
              "No trade \u2014 uncertainty is high or signals conflict." +
            "</div>"
          : "") +
        '<div class="synth-overlay-panel-meta">Last update: ' +
          escapeHtml(formatTime(data.current_time) || "unknown") + "</div>" +
      "</div>";

    var closeBtn = panel.querySelector(".synth-overlay-panel-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        closePanel();
      });
    }
    document.body.appendChild(panel);
    requestAnimationFrame(function () {
      panel.classList.add("synth-overlay-panel-open");
    });
  }

  function closePanel() {
    var panels = document.querySelectorAll("[data-synth-overlay=panel]");
    for (var i = 0; i < panels.length; i++) panels[i].remove();
  }

  function injectBadge(container, data) {
    removeBadge();
    var badge = createBadge(data);
    container.appendChild(badge);
  }

  function removeBadge() {
    var badges = document.querySelectorAll("[data-synth-overlay=badge]");
    for (var i = 0; i < badges.length; i++) badges[i].remove();
    closePanel();
  }

  function findInjectionTarget() {
    return document.body;
  }

  function fetchEdge(slug) {
    return fetch(API_BASE + "/api/edge?slug=" + encodeURIComponent(slug), {
      method: "GET",
      mode: "cors",
    })
      .then(function (r) {
        if (!r.ok) return null;
        return r.json();
      })
      .catch(function () {
        return null;
      });
  }

  function run() {
    var slug = slugFromPage();
    if (!slug) {
      currentSlug = null;
      removeBadge();
      return;
    }
    var requestedSlug = slug;
    fetchEdge(slug).then(function (data) {
      if (slugFromPage() !== requestedSlug) return;
      if (!data || data.error) {
        currentSlug = null;
        removeBadge();
        return;
      }
      currentSlug = requestedSlug;
      var target = findInjectionTarget();
      if (target) injectBadge(target, data);
    });
  }

  function debounce(fn, ms) {
    var t = null;
    return function () {
      if (t) clearTimeout(t);
      t = setTimeout(fn, ms);
    };
  }

  var runDebounced = debounce(run, 400);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }

  var observer = new MutationObserver(function () {
    var slug = slugFromPage();
    if (slug === currentSlug && document.querySelector("[data-synth-overlay=badge]")) {
      return;
    }
    runDebounced();
  });
  observer.observe(document.body, { childList: true, subtree: true });

  var lastHref = window.location.href;
  setInterval(function () {
    if (window.location.href !== lastHref) {
      lastHref = window.location.href;
      currentSlug = null;
      removeBadge();
      run();
    }
  }, 500);
})();
