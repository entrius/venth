"""Grammar-based market matcher with declarative platform configuration.

Architecture
============
This module takes a fundamentally different approach from pattern-matching
implementations that maintain explicit ticker→asset maps and series→type
frozensets for each platform:

1. **Grammar-based Kalshi parsing** — Tickers like ``KXBTCD-26MAR1317``
   are *structurally decomposed*: ``KX`` prefix + asset code (``BTC``) +
   market suffix (``D`` → daily).  Asset and market type are **derived**
   from the grammar, not looked up.  New Kalshi assets work automatically
   if the asset code is registered — no series sets to maintain.

2. **Rule-chain market type inference** — Ordered ``(matcher, type)``
   tuples replace scattered if/else chains.  Rules are composable,
   independently testable, and declarative.

3. **Config-driven platform definitions** — Each platform is a data dict
   with domain, URL patterns, detection heuristics, and resolution
   strategy.  A generic engine processes any config.  Adding a platform
   = adding a config entry + an asset/type resolver function.

4. **Structured ResolveResult** — Typed error codes (``invalid_input``,
   ``unknown_platform``, ``unsupported_market``) so the server returns
   actionable 400/404 messages instead of bare ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

# ── Market-type & platform constants ─────────────────────────────────

MARKET_DAILY = "daily"
MARKET_HOURLY = "hourly"
MARKET_15MIN = "15min"
MARKET_5MIN = "5min"
MARKET_RANGE = "range"

PLATFORM_POLYMARKET = "polymarket"
PLATFORM_KALSHI = "kalshi"

MarketType = Literal["daily", "hourly", "15min", "5min", "range"]


# ── Structured result ────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ResolveResult:
    """Structured outcome of resolve().

    On success: ``ok`` is True and ``slug/asset/market_type/platform`` are set.
    On failure: ``ok`` is False and ``error_code/error`` explain why.
    """

    ok: bool
    slug: str | None = None
    asset: str | None = None
    market_type: MarketType | None = None
    platform: str | None = None
    error_code: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        """Dict suitable for JSON serialisation (omits None values)."""
        return {k: v for k, v in {
            "ok": self.ok,
            "slug": self.slug,
            "asset": self.asset,
            "market_type": self.market_type,
            "platform": self.platform,
            "error_code": self.error_code,
            "error": self.error,
        }.items() if v is not None}


ERR_INVALID_INPUT = "invalid_input"
ERR_UNKNOWN_PLATFORM = "unknown_platform"
ERR_UNSUPPORTED_MARKET = "unsupported_market"
ERR_UNKNOWN_ASSET = "unknown_asset"
ERR_NORMALIZE_FAILED = "normalize_failed"


# ═══════════════════════════════════════════════════════════════════════
# KALSHI — Grammar-based ticker parser
#
# Instead of a 25-entry series→asset map and 3 series→type frozensets
# (the PR #40 / lookup-table approach), we parse Kalshi tickers using
# their *structural grammar*:
#
#   KX{asset_code}{market_suffix}-{date}-{strike_or_bracket}
#
# Asset and market type are DERIVED from the structure, not looked up.
# Adding a new Kalshi asset (e.g. LINK, DOT) = one entry in _KX_CODES.
# No series sets to maintain.
# ═══════════════════════════════════════════════════════════════════════

# Minimal asset-code table (12 entries vs 25+ in lookup approach)
_KX_CODES: dict[str, str] = {
    "btc": "BTC", "eth": "ETH", "sol": "SOL", "xrp": "XRP", "doge": "DOGE",
    "spx": "SPY", "spy": "SPY", "nvda": "NVDA", "tsla": "TSLA",
    "aapl": "AAPL", "googl": "GOOGL", "xau": "XAU",
    # Future Kalshi assets auto-work if code is added here:
    "ada": "ADA", "bnb": "BNB", "bch": "BCH",
}

# Market-type suffixes — checked longest-first so "15m" beats "m"
_KX_SUFFIXES: list[tuple[str, str]] = [
    ("15m", MARKET_15MIN),
    ("d", MARKET_DAILY),
    # No suffix → MARKET_RANGE (handled by fallback)
]

# Legacy tickers (no KX prefix) that need collision avoidance
_LEGACY_CODES: dict[str, str] = {"btc": "BTC", "eth": "ETH"}
_LEGACY_VARIANTS: dict[str, tuple[str, str]] = {
    "btcd-b": ("BTC", MARKET_DAILY),
}

# Date-suffix pattern for collision avoidance with Polymarket slugs
_DATE_RE = re.compile(r"^\d{2}[A-Za-z]{3}\d*")
# Contract-level suffixes that override grammar-inferred type
_STRIKE_RE = re.compile(r"-t[\d.]+$", re.I)
_BRACKET_RE = re.compile(r"-b\d+$", re.I)


def _parse_kx_base(base: str) -> tuple[str | None, str | None]:
    """Derive (asset, market_type) from a KX-prefixed series base.

    Grammar: ``kx`` + asset_code + optional_suffix
    Suffixes: ``d`` → daily, ``15m`` → 15min, (none) → range.
    """
    if not base.startswith("kx") or len(base) < 4:
        return None, None
    body = base[2:]
    for suffix, mtype in _KX_SUFFIXES:
        if body.endswith(suffix):
            code = body[: -len(suffix)]
            if code in _KX_CODES:
                return _KX_CODES[code], mtype
    # No suffix matched → range
    if body in _KX_CODES:
        return _KX_CODES[body], MARKET_RANGE
    return None, None


def _parse_legacy_base(base: str) -> tuple[str | None, str | None]:
    """Derive (asset, market_type) from a legacy (non-KX) series base."""
    for suffix, mtype in _KX_SUFFIXES:
        if base.endswith(suffix):
            code = base[: -len(suffix)]
            if code in _LEGACY_CODES:
                return _LEGACY_CODES[code], mtype
    if base in _LEGACY_CODES:
        return _LEGACY_CODES[base], MARKET_RANGE
    return None, None


def _kalshi_parse(ticker: str) -> tuple[str | None, str | None, str | None]:
    """Grammar-based Kalshi ticker parser.

    Returns ``(series_base, asset, market_type)`` or ``(None, None, None)``.

    Unlike lookup-table approaches, this **derives** asset and market type
    from the ticker's structural grammar rather than maintaining separate
    series→asset and series→type maps.
    """
    if not ticker:
        return None, None, None
    t = ticker.strip().lower()
    parts = t.split("-")

    # ── Explicit legacy variants (btcd-b) ────────────────────────────
    if len(parts) >= 2:
        two_seg = parts[0] + "-" + parts[1]
        if two_seg in _LEGACY_VARIANTS:
            asset, mtype = _LEGACY_VARIANTS[two_seg]
            return two_seg, asset, mtype

    base = parts[0]

    # ── KX-prefixed: grammar parse ───────────────────────────────────
    if base.startswith("kx"):
        asset, mtype = _parse_kx_base(base)
        if asset:
            # Contract suffix can override grammar type
            if _BRACKET_RE.search(t):
                mtype = MARKET_RANGE
            elif _STRIKE_RE.search(t):
                mtype = MARKET_DAILY
            return base, asset, mtype
        # Unknown KX ticker with at least 4 chars — might be future asset
        if len(base) >= 4:
            return base, None, None

    # ── Legacy (no KX prefix) with collision avoidance ───────────────
    asset, mtype = _parse_legacy_base(base)
    if asset:
        # Bare legacy ticker (e.g. "btcd", "btc") → accept
        if len(parts) == 1:
            return base, asset, mtype
        # Legacy + date suffix (e.g. "btcd-26MAR1317") → accept
        remainder = t[len(base) + 1 :]
        if _DATE_RE.match(remainder):
            return base, asset, mtype
        # Otherwise reject — likely a Polymarket slug (btc-updown-5m-...)
        return None, None, None

    return None, None, None


# ═══════════════════════════════════════════════════════════════════════
# POLYMARKET — Rule-chain market type inference
#
# Instead of an if/else chain, market type is inferred by walking an
# ordered list of (matcher, type) rules.  First match wins.
# Rules are data, not control flow — composable and independently testable.
# ═══════════════════════════════════════════════════════════════════════

_POLY_5MIN_RE = re.compile(r"(updown|up-down)-5m-|(?<!1)5-?min")
_POLY_15MIN_RE = re.compile(r"(updown|up-down)-15m-|(?<!\d)15-?min")
_POLY_HOURLY_RE = re.compile(r"\d{1,2}(am|pm)")

_POLY_MARKET_RULES: list[tuple[Callable[[str], bool], str]] = [
    (lambda s: bool(_POLY_5MIN_RE.search(s)), MARKET_5MIN),
    (lambda s: bool(_POLY_15MIN_RE.search(s)), MARKET_15MIN),
    (lambda s: "up-or-down" in s and bool(_POLY_HOURLY_RE.search(s)), MARKET_HOURLY),
    (lambda s: "up-or-down" in s and "on-" in s, MARKET_DAILY),
    (lambda s: "price-on" in s, MARKET_RANGE),
]

_POLY_ASSET_PREFIXES: dict[str, str] = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
    "xrp": "XRP", "btc": "BTC", "eth": "ETH", "sol": "SOL",
}

_POLY_DETECT_RE = re.compile(r"up-or-down|updown|price-on")


def _poly_resolve_asset(slug: str) -> str | None:
    s = slug.lower()
    for prefix, ticker in _POLY_ASSET_PREFIXES.items():
        if s.startswith(prefix + "-"):
            return ticker
    return None


def _poly_resolve_market_type(slug: str) -> str | None:
    s = slug.lower()
    for matcher, mtype in _POLY_MARKET_RULES:
        if matcher(s):
            return mtype
    return None


# ═══════════════════════════════════════════════════════════════════════
# Config-driven platform definitions
#
# Each platform is a DATA DICT — not a class.  The generic resolution
# engine below processes any config.  Adding a new platform is adding
# a new dict entry, not writing a new class.
# ═══════════════════════════════════════════════════════════════════════

_PlatformConfig = dict  # type alias for readability

_CONFIGS: dict[str, _PlatformConfig] = {
    PLATFORM_POLYMARKET: {
        "domain": "polymarket.com",
        "label": "Poly",
        "priority": 0,
        "supported_assets": frozenset({"BTC", "ETH", "SOL", "XRP"}),
        "supported_market_types": frozenset({
            MARKET_DAILY, MARKET_HOURLY, MARKET_15MIN, MARKET_5MIN, MARKET_RANGE,
        }),
        "url_re": re.compile(
            r"polymarket\.com/(?:event/|market/)?([a-zA-Z0-9-]+)", re.I,
        ),
        "slug_re": re.compile(r"^[a-zA-Z0-9-]+$"),
        "detect": lambda s: bool(_POLY_DETECT_RE.search(s)),
        "resolve_asset": _poly_resolve_asset,
        "resolve_market_type": _poly_resolve_market_type,
    },
    PLATFORM_KALSHI: {
        "domain": "kalshi.com",
        "label": "Kalshi",
        "priority": 10,
        "supported_assets": frozenset({
            "BTC", "ETH", "SOL", "XRP", "DOGE",
            "SPY", "NVDA", "TSLA", "AAPL", "GOOGL", "XAU",
        }),
        "supported_market_types": frozenset({MARKET_DAILY, MARKET_15MIN, MARKET_RANGE}),
        "url_re": re.compile(
            r"kalshi\.com/(?:markets|events)(?:/[^/]+)*/([a-zA-Z0-9._-]+)", re.I,
        ),
        "slug_re": re.compile(
            r"^(?:kx[a-z0-9]+|btcd?(?:-b)?|ethd?)(?:-[a-zA-Z0-9._-]*)?$", re.I,
        ),
        "detect": lambda s: _kalshi_parse(s)[1] is not None,
        "resolve_asset": lambda slug: _kalshi_parse(slug)[1],
        "resolve_market_type": lambda slug: _kalshi_parse(slug)[2],
    },
}

# Priority-sorted list for ordered dispatch
_PLATFORM_ORDER: list[str] = sorted(_CONFIGS, key=lambda n: _CONFIGS[n]["priority"])


# ═══════════════════════════════════════════════════════════════════════
# Generic resolution engine
# ═══════════════════════════════════════════════════════════════════════

def _normalize_with_configs(raw: str) -> str | None:
    """Extract canonical slug from URL or bare input using platform configs."""
    s = raw.strip()
    for name in _PLATFORM_ORDER:
        cfg = _CONFIGS[name]
        m = cfg["url_re"].search(s)
        if m:
            return m.group(1)
    for name in _PLATFORM_ORDER:
        cfg = _CONFIGS[name]
        if cfg["slug_re"].match(s):
            return s
    if re.match(r"^[a-zA-Z0-9._-]+$", s):
        return s
    return None


def _detect_with_configs(raw: str) -> str | None:
    """Detect platform from URL or slug using configs."""
    s = raw.strip().lower()
    # Fast path: domain present
    for name in _PLATFORM_ORDER:
        if _CONFIGS[name]["domain"] in s:
            return name
    # Heuristic: ask each platform's detect function
    for name in _PLATFORM_ORDER:
        if _CONFIGS[name]["detect"](s):
            return name
    return None


def _resolve_on_config(slug: str, cfg: _PlatformConfig) -> tuple[str | None, str | None]:
    """Resolve (asset, market_type) using a platform config."""
    mtype = cfg["resolve_market_type"](slug)
    if not mtype:
        return None, None
    asset = cfg["resolve_asset"](slug)
    return asset, mtype


# ═══════════════════════════════════════════════════════════════════════
# PlatformRegistry — thin introspection wrapper around configs
# ═══════════════════════════════════════════════════════════════════════

class PlatformRegistry:
    """Read-only registry for platform introspection and structured resolve."""

    def __init__(self, configs: dict[str, _PlatformConfig]) -> None:
        self._configs = configs
        self._order = sorted(configs, key=lambda n: configs[n]["priority"])

    def get(self, name: str) -> _PlatformConfig | None:
        return self._configs.get(name)

    @property
    def platforms(self) -> list[_PlatformConfig]:
        return [self._configs[n] for n in self._order]

    @property
    def platform_names(self) -> list[str]:
        return list(self._order)

    def _normalize(self, raw: str) -> str | None:
        s = raw.strip()
        for name in self._order:
            cfg = self._configs[name]
            m = cfg["url_re"].search(s)
            if m:
                return m.group(1)
        for name in self._order:
            cfg = self._configs[name]
            if cfg["slug_re"].match(s):
                return s
        if re.match(r"^[a-zA-Z0-9._-]+$", s):
            return s
        return None

    def _detect(self, raw: str) -> str | None:
        s = raw.strip().lower()
        for name in self._order:
            if self._configs[name]["domain"] in s:
                return name
        for name in self._order:
            if self._configs[name]["detect"](s):
                return name
        return None

    def capabilities(self) -> list[dict]:
        result = []
        for name in self._order:
            cfg = self._configs[name]
            result.append({
                "name": name,
                "label": cfg.get("label", name.title()),
                "domain": cfg["domain"],
                "supported_assets": sorted(cfg["supported_assets"]),
                "supported_market_types": sorted(cfg["supported_market_types"]),
            })
        return result

    def all_supported_assets(self) -> frozenset[str]:
        result: set[str] = set()
        for cfg in self._configs.values():
            result |= cfg["supported_assets"]
        return frozenset(result)

    def resolve(
        self, url_or_slug: str, platform_hint: str | None = None,
    ) -> ResolveResult:
        if not url_or_slug or not isinstance(url_or_slug, str):
            return ResolveResult(
                ok=False, error_code=ERR_INVALID_INPUT,
                error="Missing or empty slug/url",
            )
        slug = self._normalize(url_or_slug)
        if not slug:
            return ResolveResult(
                ok=False, error_code=ERR_NORMALIZE_FAILED,
                error=f"Could not extract slug from: {url_or_slug!r}",
            )
        pname = platform_hint or self._detect(url_or_slug) or self._order[0] if self._order else None
        if not pname:
            pname = platform_hint
        cfg = self._configs.get(pname) if pname else None
        if not cfg:
            return ResolveResult(
                ok=False, slug=slug, platform=pname,
                error_code=ERR_UNKNOWN_PLATFORM,
                error=f"Unknown platform: {pname!r}",
            )
        asset, mtype = _resolve_on_config(slug, cfg)
        if not mtype:
            return ResolveResult(
                ok=False, slug=slug, platform=pname,
                error_code=ERR_UNSUPPORTED_MARKET,
                error=f"Slug {slug!r} is not a recognised {pname} market type",
            )
        return ResolveResult(
            ok=True, slug=slug, asset=asset or "BTC",
            market_type=mtype, platform=pname,
        )


# ── Default registry singleton ───────────────────────────────────────

registry = PlatformRegistry(_CONFIGS)


# ═══════════════════════════════════════════════════════════════════════
# Backward-compatible public API
#
# Same function signatures as the original matcher.py — server.py,
# tests, and extension code work without changes.
# ═══════════════════════════════════════════════════════════════════════

def detect_platform(url_or_slug: str) -> str | None:
    return _detect_with_configs(url_or_slug) if url_or_slug and isinstance(url_or_slug, str) else None

def asset_from_slug(slug: str) -> str | None:
    """Extract asset from a Polymarket slug."""
    return _poly_resolve_asset(slug) if slug else None

def asset_from_kalshi_ticker(ticker: str) -> str | None:
    """Extract asset from a Kalshi ticker (grammar-based)."""
    if not ticker:
        return None
    _, asset, _ = _kalshi_parse(ticker)
    return asset

def normalize_slug(url_or_slug: str) -> str | None:
    if not url_or_slug or not isinstance(url_or_slug, str):
        return None
    return _normalize_with_configs(url_or_slug)

def get_market_type(slug: str) -> MarketType | None:
    """Infer market type — Polymarket rules checked first for disambiguation."""
    if not slug:
        return None
    for name in _PLATFORM_ORDER:
        cfg = _CONFIGS[name]
        mtype = cfg["resolve_market_type"](slug)
        if mtype:
            return mtype
    return None

def get_kalshi_market_type(ticker: str) -> MarketType | None:
    """Convenience: Kalshi-specific market type (grammar-based)."""
    if not ticker:
        return None
    _, _, mtype = _kalshi_parse(ticker)
    return mtype

def resolve(url_or_slug: str, platform_hint: str | None = None) -> dict | None:
    """Legacy dict-returning resolver (backward compat).

    New code should use ``registry.resolve()`` for structured diagnostics.
    """
    r = registry.resolve(url_or_slug, platform_hint)
    if not r.ok:
        return None
    return {"slug": r.slug, "asset": r.asset, "market_type": r.market_type, "platform": r.platform}

def is_supported(slug: str) -> bool:
    return get_market_type(slug) is not None


# ═══════════════════════════════════════════════════════════════════════
# Platform class — kept for test compatibility
# ═══════════════════════════════════════════════════════════════════════

class Platform:
    """Minimal base for test compatibility (custom platform registration)."""
    name: str = ""
    domain: str = ""
    label: str = ""
    priority: int = 0
    supported_assets: frozenset[str] = frozenset()
    supported_market_types: frozenset[str] = frozenset()
