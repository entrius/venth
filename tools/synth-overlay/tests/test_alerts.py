"""Tests for edge alert notification logic.

These tests verify the alert threshold, cooldown, watchlist, and notification
formatting logic that mirrors the JavaScript implementation in alerts.js
and background.js. The tests use pure Python reimplementations of the same
algorithms to ensure correctness without requiring a browser environment.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from edge import compute_edge_pct, signal_from_edge, strength_from_edge


# ---- Threshold logic (mirrors SynthAlerts.exceedsThreshold) ----

def exceeds_threshold(edge_pct, threshold):
    if edge_pct is None or threshold is None:
        return False
    return abs(edge_pct) >= threshold


def test_exceeds_threshold_positive_edge():
    assert exceeds_threshold(5.0, 3.0) is True


def test_exceeds_threshold_negative_edge():
    assert exceeds_threshold(-4.5, 3.0) is True


def test_exceeds_threshold_exactly_at_boundary():
    assert exceeds_threshold(3.0, 3.0) is True
    assert exceeds_threshold(-3.0, 3.0) is True


def test_below_threshold():
    assert exceeds_threshold(2.9, 3.0) is False
    assert exceeds_threshold(-1.0, 3.0) is False


def test_zero_edge():
    assert exceeds_threshold(0.0, 3.0) is False


def test_threshold_none_inputs():
    assert exceeds_threshold(None, 3.0) is False
    assert exceeds_threshold(5.0, None) is False
    assert exceeds_threshold(None, None) is False


def test_threshold_very_small():
    assert exceeds_threshold(0.5, 0.5) is True
    assert exceeds_threshold(0.4, 0.5) is False


# ---- Cooldown logic (mirrors background.js cooldown) ----

COOLDOWN_MS = 5 * 60 * 1000  # 5 minutes in milliseconds


def is_on_cooldown(slug, cooldowns, now_ms):
    ts = cooldowns.get(slug)
    if ts is None:
        return False
    return (now_ms - ts) < COOLDOWN_MS


def set_cooldown(slug, cooldowns, now_ms):
    cooldowns[slug] = now_ms
    return cooldowns


def test_no_cooldown_for_new_market():
    assert is_on_cooldown("btc-daily", {}, 1000000) is False


def test_cooldown_active_within_window():
    now = 1000000
    cooldowns = {"btc-daily": now - 60000}  # 1 minute ago
    assert is_on_cooldown("btc-daily", cooldowns, now) is True


def test_cooldown_expired():
    now = 1000000
    cooldowns = {"btc-daily": now - COOLDOWN_MS - 1}
    assert is_on_cooldown("btc-daily", cooldowns, now) is False


def test_cooldown_exactly_at_boundary():
    now = 1000000
    cooldowns = {"btc-daily": now - COOLDOWN_MS}
    assert is_on_cooldown("btc-daily", cooldowns, now) is False


def test_set_cooldown_records_timestamp():
    cooldowns = {}
    now = 1000000
    set_cooldown("btc-daily", cooldowns, now)
    assert cooldowns["btc-daily"] == now


def test_different_slugs_independent_cooldowns():
    now = 1000000
    cooldowns = {"btc-daily": now - 60000}
    assert is_on_cooldown("btc-daily", cooldowns, now) is True
    assert is_on_cooldown("eth-hourly", cooldowns, now) is False


# ---- Watchlist logic (mirrors SynthAlerts watchlist operations) ----

MAX_WATCHLIST = 20


def add_to_watchlist(watchlist, slug, asset, label):
    if not slug:
        return watchlist
    if any(w["slug"] == slug for w in watchlist):
        return watchlist
    if len(watchlist) >= MAX_WATCHLIST:
        return watchlist
    watchlist.append({"slug": slug, "asset": asset or "BTC", "label": label or slug})
    return watchlist


def remove_from_watchlist(watchlist, slug):
    return [w for w in watchlist if w["slug"] != slug]


def test_add_to_empty_watchlist():
    wl = add_to_watchlist([], "btc-daily", "BTC", "BTC 24h")
    assert len(wl) == 1
    assert wl[0]["slug"] == "btc-daily"


def test_add_duplicate_slug_ignored():
    wl = [{"slug": "btc-daily", "asset": "BTC", "label": "BTC 24h"}]
    wl = add_to_watchlist(wl, "btc-daily", "BTC", "BTC 24h")
    assert len(wl) == 1


def test_add_different_slug():
    wl = [{"slug": "btc-daily", "asset": "BTC", "label": "BTC 24h"}]
    wl = add_to_watchlist(wl, "eth-hourly", "ETH", "ETH 1h")
    assert len(wl) == 2


def test_add_empty_slug_rejected():
    wl = add_to_watchlist([], "", "BTC", "BTC 24h")
    assert len(wl) == 0
    wl = add_to_watchlist([], None, "BTC", "BTC 24h")
    assert len(wl) == 0


def test_watchlist_max_capacity():
    wl = [{"slug": f"market-{i}", "asset": "BTC", "label": f"M{i}"} for i in range(MAX_WATCHLIST)]
    wl = add_to_watchlist(wl, "overflow", "BTC", "Overflow")
    assert len(wl) == MAX_WATCHLIST
    assert not any(w["slug"] == "overflow" for w in wl)


def test_remove_from_watchlist():
    wl = [
        {"slug": "btc-daily", "asset": "BTC", "label": "BTC 24h"},
        {"slug": "eth-hourly", "asset": "ETH", "label": "ETH 1h"},
    ]
    wl = remove_from_watchlist(wl, "btc-daily")
    assert len(wl) == 1
    assert wl[0]["slug"] == "eth-hourly"


def test_remove_nonexistent_slug():
    wl = [{"slug": "btc-daily", "asset": "BTC", "label": "BTC 24h"}]
    wl = remove_from_watchlist(wl, "eth-hourly")
    assert len(wl) == 1


def test_remove_from_empty_watchlist():
    wl = remove_from_watchlist([], "btc-daily")
    assert len(wl) == 0


# ---- Notification formatting (mirrors background.js createEdgeNotification) ----

def format_notification_title(label, edge_pct, signal):
    direction = "Underpriced" if signal == "underpriced" else "Overpriced" if signal == "overpriced" else "Edge"
    sign = "+" if edge_pct >= 0 else ""
    return label + " — " + direction + " " + sign + str(edge_pct) + "pp"


def fmt_prob(p):
    if p is None:
        return "—"
    return str(round(p * 100)) + "¢"


def test_notification_title_underpriced():
    title = format_notification_title("BTC 24h", 5.2, "underpriced")
    assert title == "BTC 24h — Underpriced +5.2pp"


def test_notification_title_overpriced():
    title = format_notification_title("ETH 1h", -3.1, "overpriced")
    assert title == "ETH 1h — Overpriced -3.1pp"


def test_notification_title_fair():
    title = format_notification_title("SOL 15m", 0.3, "fair")
    assert title == "SOL 15m — Edge +0.3pp"


def test_fmt_prob_values():
    assert fmt_prob(0.52) == "52¢"
    assert fmt_prob(0.05) == "5¢"
    assert fmt_prob(0.999) == "100¢"
    assert fmt_prob(None) == "—"


# ---- Market label formatting (mirrors SynthAlerts.formatMarketLabel) ----

def format_market_label(asset, market_type):
    type_map = {"daily": "24h", "hourly": "1h", "15min": "15m", "5min": "5m"}
    return (asset or "BTC") + " " + type_map.get(market_type, market_type or "daily")


def test_format_market_label_daily():
    assert format_market_label("BTC", "daily") == "BTC 24h"


def test_format_market_label_hourly():
    assert format_market_label("ETH", "hourly") == "ETH 1h"


def test_format_market_label_15min():
    assert format_market_label("SOL", "15min") == "SOL 15m"


def test_format_market_label_5min():
    assert format_market_label("BTC", "5min") == "BTC 5m"


def test_format_market_label_defaults():
    assert format_market_label(None, None) == "BTC daily"
    assert format_market_label("ETH", None) == "ETH daily"


# ---- Threshold validation (mirrors SynthAlerts.saveThreshold) ----

def validate_threshold(val):
    try:
        num = float(val)
    except (ValueError, TypeError):
        return 3.0
    if num < 0.1:
        return 3.0
    if num > 50:
        return 50.0
    return round(num * 10) / 10


def test_validate_threshold_normal():
    assert validate_threshold("3.0") == 3.0
    assert validate_threshold("5.5") == 5.5


def test_validate_threshold_too_low():
    assert validate_threshold("0.05") == 3.0
    assert validate_threshold("-1") == 3.0


def test_validate_threshold_too_high():
    assert validate_threshold("100") == 50.0


def test_validate_threshold_invalid():
    assert validate_threshold("abc") == 3.0
    assert validate_threshold(None) == 3.0


def test_validate_threshold_rounding():
    assert validate_threshold("2.77") == 2.8
    assert validate_threshold("1.11") == 1.1


# ---- End-to-end: edge → threshold → notification decision ----

def should_alert(synth_prob, market_prob, threshold, slug, cooldowns, now_ms):
    """Full pipeline: compute edge, check threshold, check cooldown."""
    edge_pct = compute_edge_pct(synth_prob, market_prob)
    if not exceeds_threshold(edge_pct, threshold):
        return False, edge_pct
    if is_on_cooldown(slug, cooldowns, now_ms):
        return False, edge_pct
    return True, edge_pct


def test_should_alert_strong_edge_no_cooldown():
    alert, edge = should_alert(0.60, 0.50, 3.0, "btc-daily", {}, 1000000)
    assert alert is True
    assert edge == 10.0


def test_should_alert_weak_edge_no_alert():
    alert, edge = should_alert(0.51, 0.50, 3.0, "btc-daily", {}, 1000000)
    assert alert is False
    assert edge == 1.0


def test_should_alert_strong_edge_on_cooldown():
    now = 1000000
    cooldowns = {"btc-daily": now - 60000}
    alert, edge = should_alert(0.60, 0.50, 3.0, "btc-daily", cooldowns, now)
    assert alert is False
    assert edge == 10.0


def test_should_alert_strong_edge_cooldown_expired():
    now = 1000000
    cooldowns = {"btc-daily": now - COOLDOWN_MS - 1}
    alert, edge = should_alert(0.60, 0.50, 3.0, "btc-daily", cooldowns, now)
    assert alert is True
