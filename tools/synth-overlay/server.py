"""
Local API server for the Synth Overlay extension.
Serves edge data from SynthClient; extension calls this from Polymarket and Kalshi pages.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "../.."))
if _here not in sys.path:
    sys.path.insert(0, _here)

from flask import Flask, jsonify, request

from synth_client import SynthClient

from analyzer import EdgeAnalyzer
from edge import edge_from_range_bracket, signal_from_edge
from matcher import (
    asset_from_kalshi_ticker,
    asset_from_slug,
    detect_platform,
    get_market_type,
    normalize_slug,
    resolve,
    registry,
    ERR_INVALID_INPUT,
    ERR_NORMALIZE_FAILED,
    ERR_UNKNOWN_PLATFORM,
    ERR_UNSUPPORTED_MARKET,
    PLATFORM_KALSHI,
    PLATFORM_POLYMARKET,
)

app = Flask(__name__)
_client: SynthClient | None = None


def get_client() -> SynthClient:
    global _client
    if _client is None:
        _client = SynthClient()
    return _client


@app.after_request
def cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/health", methods=["GET", "OPTIONS"])
def health():
    if request.method == "OPTIONS":
        return "", 204
    return jsonify({"status": "ok", "mock": get_client().mock_mode})


_HORIZON_MAP = {"5min": "5min", "15min": "15min", "hourly": "1h", "daily": "24h"}
_TF_KEYS = {"5min": "5m", "15min": "15m", "hourly": "1h", "daily": "24h"}


def _compute_all_timeframe_edges(client: SynthClient, asset: str, live_prob_up=None, primary_market_type=None):
    """Compute simple edge for all 4 timeframes so the UI can show them all."""
    fetchers = {
        "5min": client.get_polymarket_5min,
        "15min": client.get_polymarket_15min,
        "hourly": client.get_polymarket_hourly,
        "daily": client.get_polymarket_daily,
    }
    primary_tf = _TF_KEYS.get(primary_market_type)
    timeframes = {}
    for mtype, fetcher in fetchers.items():
        tf_key = _TF_KEYS[mtype]
        try:
            data = fetcher(asset)
            synth = data.get("synth_probability_up")
            poly = data.get("polymarket_probability_up")
            if live_prob_up is not None and tf_key == primary_tf:
                poly = live_prob_up
            if synth is not None and poly is not None:
                edge = round((float(synth) - float(poly)) * 100, 1)
                sig = signal_from_edge(edge)
                timeframes[tf_key] = {"edge_pct": edge, "signal": sig}
        except Exception:
            pass
    return timeframes


def _fetch_updown_pair(client: SynthClient, asset: str, market_type: str) -> tuple[dict, dict]:
    """Fetch primary + reference up/down data for cross-horizon context."""
    fetchers = {
        "5min": (client.get_polymarket_5min, client.get_polymarket_15min),
        "15min": (client.get_polymarket_15min, client.get_polymarket_hourly),
        "hourly": (client.get_polymarket_hourly, client.get_polymarket_daily),
        "daily": (client.get_polymarket_daily, client.get_polymarket_hourly),
    }
    primary_fn, ref_fn = fetchers[market_type]
    primary = primary_fn(asset)
    try:
        reference = ref_fn(asset)
    except Exception:
        reference = None
    return primary, reference


def _handle_updown_market(
    client: SynthClient, slug: str, asset: str, market_type: str,
    live_prob_up: float | None = None, platform: str = "polymarket",
):
    """Handle up/down markets for any supported asset and horizon.

    Args:
        live_prob_up: Real-time market price scraped from DOM. If provided,
                      overrides the API's polymarket_probability_up for edge calculation.
        platform: Source platform ("polymarket" or "kalshi").
    """
    primary_data, reference_data = _fetch_updown_pair(client, asset, market_type)

    # Override Polymarket probability with live DOM price if available
    if live_prob_up is not None:
        primary_data = dict(primary_data)  # Copy to avoid mutating cached data
        primary_data["polymarket_probability_up"] = live_prob_up

    pct_1h = None
    pct_24h = None
    try:
        pct_1h = client.get_prediction_percentiles(asset, horizon="1h")
        pct_24h = client.get_prediction_percentiles(asset, horizon="24h")
    except Exception:
        pass

    primary_horizon = _HORIZON_MAP[market_type]

    # Daily/hourly: preserve dual-horizon analysis (1h vs 24h cross-comparison)
    if market_type in ("daily", "hourly") and reference_data:
        # primary_data already has live_prob_up override applied above
        daily = primary_data if market_type == "daily" else reference_data
        hourly = reference_data if market_type == "daily" else primary_data
        analyzer = EdgeAnalyzer(daily, hourly, pct_1h, pct_24h)
        result = analyzer.analyze(primary_horizon=primary_horizon)
        primary_src = daily if market_type == "daily" else hourly
        return jsonify({
            "slug": slug,
            "asset": asset,
            "horizon": primary_horizon,
            "market_type": market_type,
            "platform": platform,
            "edge_pct": result.primary.edge_pct,
            "signal": result.primary.signal,
            "strength": result.strength,
            "confidence_score": result.confidence_score,
            "edge_1h_pct": result.secondary.edge_pct if primary_horizon == "24h" else result.primary.edge_pct,
            "signal_1h": result.secondary.signal if primary_horizon == "24h" else result.primary.signal,
            "edge_24h_pct": result.primary.edge_pct if primary_horizon == "24h" else result.secondary.edge_pct,
            "signal_24h": result.primary.signal if primary_horizon == "24h" else result.secondary.signal,
            "no_trade_warning": result.no_trade,
            "explanation": result.explanation,
            "invalidation": result.invalidation,
            "synth_probability_up": primary_src.get("synth_probability_up"),
            "polymarket_probability_up": primary_src.get("polymarket_probability_up"),
            "current_time": primary_src.get("current_time"),
            "live_price_used": live_prob_up is not None,
            "timeframes": _compute_all_timeframe_edges(client, asset, live_prob_up, market_type),
        })

    # 5min/15min: single-horizon analysis with optional reference context
    analyzer = EdgeAnalyzer(primary_data, reference_data, pct_1h, pct_24h)
    result = analyzer.analyze_single_horizon(primary_data, horizon=primary_horizon)

    resp = {
        "slug": slug,
        "asset": asset,
        "horizon": primary_horizon,
        "market_type": market_type,
        "platform": platform,
        "edge_pct": result.primary.edge_pct,
        "signal": result.primary.signal,
        "strength": result.strength,
        "confidence_score": result.confidence_score,
        "no_trade_warning": result.no_trade,
        "explanation": result.explanation,
        "invalidation": result.invalidation,
        "synth_probability_up": primary_data.get("synth_probability_up"),
        "polymarket_probability_up": primary_data.get("polymarket_probability_up"),
        "current_time": primary_data.get("current_time"),
        "live_price_used": live_prob_up is not None,
    }
    # Include reference horizon context when available
    if reference_data and result.secondary:
        resp["ref_horizon"] = _HORIZON_MAP.get(
            {"5min": "15min", "15min": "hourly"}.get(market_type, ""), ""
        )
        resp["ref_edge_pct"] = result.secondary.edge_pct
        resp["ref_signal"] = result.secondary.signal
    resp["timeframes"] = _compute_all_timeframe_edges(client, asset, live_prob_up, market_type)
    return jsonify(resp)


@app.route("/api/platforms", methods=["GET", "OPTIONS"])
def platforms():
    """Introspection: list registered platforms, supported assets, market types."""
    if request.method == "OPTIONS":
        return "", 204
    return jsonify({
        "platforms": registry.capabilities(),
        "all_supported_assets": sorted(registry.all_supported_assets()),
    })


@app.route("/api/edge", methods=["GET", "OPTIONS"])
def edge():
    if request.method == "OPTIONS":
        return "", 204
    raw = request.args.get("slug") or request.args.get("url") or ""
    platform_hint = request.args.get("platform")
    # Structured resolve: slug + asset + market_type + platform + diagnostics
    result = registry.resolve(raw, platform_hint)
    if not result.ok:
        code = result.error_code
        if code == ERR_INVALID_INPUT or code == ERR_NORMALIZE_FAILED:
            return jsonify({"error": result.error, "error_code": code}), 400
        if code == ERR_UNKNOWN_PLATFORM:
            return jsonify({"error": result.error, "error_code": code, "slug": result.slug}), 400
        # ERR_UNSUPPORTED_MARKET or anything else → 404
        return jsonify({"error": result.error or "Unsupported market", "error_code": code, "slug": result.slug}), 404
    slug = result.slug
    asset = result.asset
    market_type = result.market_type
    platform = result.platform
    # Live market price scraped from DOM (real-time, avoids API latency)
    live_prob_up = request.args.get("live_prob_up")
    if live_prob_up:
        try:
            live_prob_up = float(live_prob_up)
        except (ValueError, TypeError):
            live_prob_up = None
    try:
        client = get_client()
        if market_type in ("daily", "hourly", "15min", "5min"):
            return _handle_updown_market(client, slug, asset, market_type, live_prob_up, platform)
        # Kalshi "range" contracts are binary Yes/No — route through up/down handler
        if market_type == "range" and platform == PLATFORM_KALSHI:
            return _handle_updown_market(client, slug, asset, "daily", live_prob_up, platform)
        # Polymarket range (multi-bracket)
        data = client.get_polymarket_range()
        if not isinstance(data, list):
            return jsonify({"error": "Invalid range data"}), 500
        bracket_title = request.args.get("bracket_title")
        brackets = [b for b in data if (b.get("slug") or "").strip() == slug]
        if not brackets:
            return jsonify({"error": "No brackets for slug", "slug": slug}), 404
        selected = None
        if bracket_title:
            matched = [b for b in brackets if (b.get("title") or "").strip() == bracket_title.strip()]
            if matched:
                selected = matched[0]
        if selected is None:
            selected = max(
                brackets,
                key=lambda b: float(b.get("polymarket_probability") or 0),
            )
        pct_24h = None
        try:
            pct_24h = client.get_prediction_percentiles(asset, horizon="24h")
        except Exception:
            pass
        analyzer = EdgeAnalyzer()
        result = analyzer.analyze_range(selected, brackets, pct_24h)
        bracket_edges = []
        for bracket in brackets:
            b_edge, b_signal, b_strength = edge_from_range_bracket(bracket)
            bracket_edges.append(
                {
                    "title": bracket.get("title"),
                    "edge_pct": b_edge,
                    "signal": b_signal,
                    "strength": b_strength,
                    "synth_probability": bracket.get("synth_probability"),
                    "polymarket_probability": bracket.get("polymarket_probability"),
                }
            )
        return jsonify({
            "slug": selected.get("slug"),
            "horizon": "24h",
            "platform": platform,
            "bracket_title": selected.get("title"),
            "edge_pct": result.primary.edge_pct,
            "signal": result.primary.signal,
            "strength": result.strength,
            "confidence_score": result.confidence_score,
            "no_trade_warning": result.no_trade,
            "explanation": result.explanation,
            "invalidation": result.invalidation,
            "synth_probability": selected.get("synth_probability"),
            "polymarket_probability": selected.get("polymarket_probability"),
            "current_time": selected.get("current_time"),
            "range_brackets": bracket_edges,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Server error", "detail": str(e)}), 500


def main():
    import warnings
    warnings.filterwarnings("ignore", message="No SYNTH_API_KEY")
    host = os.environ.get("SERVER_HOST", "127.0.0.1")
    app.run(host=host, port=8765, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
