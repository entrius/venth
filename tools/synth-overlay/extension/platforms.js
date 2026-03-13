/**
 * Platform registry — single source of truth for supported platforms.
 *
 * Every JS file (content, background, sidepanel) imports this instead of
 * hard-coding URL checks.  Adding a third platform means adding one entry
 * here; no scattered if/else across the codebase.
 */
"use strict";

var SynthPlatforms = (function () {

  var PLATFORMS = {
    polymarket: {
      name: "polymarket",
      label: "Poly",
      origins: ["https://polymarket.com/"],
      domainHint: "polymarket.com",
      marketUrlTemplate: "https://polymarket.com/event/{slug}",
      tabSearchPattern: "https://polymarket.com/*",
    },
    kalshi: {
      name: "kalshi",
      label: "Kalshi",
      origins: ["https://kalshi.com/"],
      domainHint: "kalshi.com",
      marketUrlTemplate: "https://kalshi.com/markets/{slug}",
      tabSearchPattern: "https://kalshi.com/*",
    },
  };

  /** All origins across every platform (flat array). */
  var ALL_ORIGINS = [];
  for (var key in PLATFORMS) {
    ALL_ORIGINS = ALL_ORIGINS.concat(PLATFORMS[key].origins);
  }

  /** Check if a URL belongs to any supported platform. */
  function isSupportedUrl(url) {
    if (!url) return false;
    for (var key in PLATFORMS) {
      if (url.indexOf(PLATFORMS[key].domainHint) !== -1) return true;
    }
    return false;
  }

  /** Detect platform name from a URL string, or null. */
  function fromUrl(url) {
    if (!url) return null;
    for (var key in PLATFORMS) {
      if (url.indexOf(PLATFORMS[key].domainHint) !== -1) return PLATFORMS[key];
    }
    return null;
  }

  /** Get platform config by name. */
  function get(name) {
    return PLATFORMS[name] || null;
  }

  /** Build the market page URL for a given platform + slug. */
  function marketUrl(platformName, slug) {
    var p = PLATFORMS[platformName];
    if (!p) return null;
    return p.marketUrlTemplate.replace("{slug}", slug);
  }

  return {
    PLATFORMS: PLATFORMS,
    ALL_ORIGINS: ALL_ORIGINS,
    isSupportedUrl: isSupportedUrl,
    fromUrl: fromUrl,
    get: get,
    marketUrl: marketUrl,
  };
})();
