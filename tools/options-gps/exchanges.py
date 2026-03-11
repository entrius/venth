"""
Multi-exchange price comparison for Options GPS Market Line Shopping.
Compares Synth's theoretical option prices against exchange prices
(Aevo, Deribit, etc.) to identify divergence — like shopping for lines.
"""

from __future__ import annotations

import os
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass
class DivergenceSummary:
    """Per-exchange divergence metrics vs Synth fair value."""
    exchange: str
    avg_abs_div: float   # average absolute % divergence across all strikes
    max_abs_div: float   # maximum absolute % divergence
    rich_calls: float    # signed avg divergence for calls (+ = exchange richer)
    rich_puts: float     # signed avg divergence for puts  (+ = exchange richer)
    n_strikes: int       # number of strikes compared


MarketConsensus = Literal["strong_agreement", "moderate_agreement", "weak_agreement", "disagreement"]


@dataclass
class MarketLineResult:
    """Aggregated result across all exchanges."""
    summaries: list[DivergenceSummary]
    avg_divergence: float       # mean avg_abs_div across exchanges
    max_divergence: float       # worst max_abs_div across exchanges
    consensus: MarketConsensus  # qualitative label


class ExchangeProvider(ABC):
    """Base class for exchange option price providers."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def get_option_prices(self, asset: str, synth_options: dict) -> dict:
        """Return option prices in same format as Synth: {call_options: {strike: price}, put_options: ...}.
        synth_options is passed so mock providers can perturb from it."""
        ...


class MockExchangeProvider(ExchangeProvider):
    """Mock provider that perturbs Synth prices with a configurable bias profile."""

    def __init__(self, exchange_name: str, call_bias: float, put_bias: float, noise_scale: float, seed: int):
        self._name = exchange_name
        self._call_bias = call_bias
        self._put_bias = put_bias
        self._noise_scale = noise_scale
        self._rng = random.Random(seed)

    @property
    def name(self) -> str:
        return self._name

    def get_option_prices(self, asset: str, synth_options: dict) -> dict:
        return {
            "call_options": self._perturb(synth_options.get("call_options") or {}, self._call_bias),
            "put_options": self._perturb(synth_options.get("put_options") or {}, self._put_bias),
        }

    def _perturb(self, prices: dict, bias: float) -> dict:
        result = {}
        for strike, price in prices.items():
            price_f = float(price)
            if price_f <= 0:
                result[strike] = price_f
                continue
            noise = self._rng.gauss(0, self._noise_scale)
            factor = 1.0 + bias + noise
            result[strike] = round(max(0.01, price_f * factor), 2)
        return result


def _default_providers() -> list[ExchangeProvider]:
    """Create the default set of mock exchange providers.
    Each has a distinct pricing personality:
    - Aevo: slightly rich on calls, cheap on puts (bullish-leaning venue)
    - Deribit: balanced but wider spreads (more noise)
    - OKX: slightly cheap on calls, rich on puts (bearish-leaning venue)
    """
    has_aevo = bool(os.environ.get("AEVO_API_KEY"))
    has_deribit = bool(os.environ.get("DERIBIT_CLIENT_ID") and os.environ.get("DERIBIT_CLIENT_SECRET"))
    # When real API keys are present, real adapters would go here.
    # For now, always use mock providers.
    _ = has_aevo, has_deribit  # reserved for future live adapters
    return [
        MockExchangeProvider("Aevo", call_bias=0.03, put_bias=-0.02, noise_scale=0.02, seed=42),
        MockExchangeProvider("Deribit", call_bias=-0.01, put_bias=0.01, noise_scale=0.04, seed=137),
        MockExchangeProvider("OKX", call_bias=-0.02, put_bias=0.03, noise_scale=0.025, seed=271),
    ]


def compute_divergence(synth_options: dict, exchange_prices: dict, exchange_name: str) -> DivergenceSummary | None:
    """Compute divergence between Synth fair prices and one exchange's prices.
    Returns None if inputs are invalid or have no overlapping strikes."""
    synth_calls = {str(k): float(v) for k, v in (synth_options.get("call_options") or {}).items()}
    synth_puts = {str(k): float(v) for k, v in (synth_options.get("put_options") or {}).items()}
    ex_calls = {str(k): float(v) for k, v in (exchange_prices.get("call_options") or {}).items()}
    ex_puts = {str(k): float(v) for k, v in (exchange_prices.get("put_options") or {}).items()}

    call_divs: list[float] = []
    put_divs: list[float] = []

    for strike in synth_calls:
        synth_p = synth_calls[strike]
        ex_p = ex_calls.get(strike)
        if ex_p is not None and synth_p > 0:
            call_divs.append((ex_p - synth_p) / synth_p * 100)

    for strike in synth_puts:
        synth_p = synth_puts[strike]
        ex_p = ex_puts.get(strike)
        if ex_p is not None and synth_p > 0:
            put_divs.append((ex_p - synth_p) / synth_p * 100)

    all_divs = call_divs + put_divs
    if not all_divs:
        return None

    avg_abs = sum(abs(d) for d in all_divs) / len(all_divs)
    max_abs = max(abs(d) for d in all_divs)
    rich_calls = sum(call_divs) / len(call_divs) if call_divs else 0.0
    rich_puts = sum(put_divs) / len(put_divs) if put_divs else 0.0

    return DivergenceSummary(
        exchange=exchange_name,
        avg_abs_div=round(avg_abs, 2),
        max_abs_div=round(max_abs, 2),
        rich_calls=round(rich_calls, 2),
        rich_puts=round(rich_puts, 2),
        n_strikes=len(all_divs),
    )


def _classify_consensus(avg_div: float) -> MarketConsensus:
    if avg_div < 3.0:
        return "strong_agreement"
    if avg_div < 7.0:
        return "moderate_agreement"
    if avg_div < 15.0:
        return "weak_agreement"
    return "disagreement"


def get_market_lines(synth_options: dict, asset: str = "BTC",
                     providers: list[ExchangeProvider] | None = None) -> MarketLineResult:
    """Fetch prices from all exchanges and compute divergence vs Synth.
    Returns aggregated MarketLineResult with per-exchange summaries."""
    if providers is None:
        providers = _default_providers()

    current_price = float(synth_options.get("current_price", 0))
    if current_price <= 0 or not synth_options.get("call_options"):
        return MarketLineResult(summaries=[], avg_divergence=0.0, max_divergence=0.0, consensus="disagreement")

    summaries: list[DivergenceSummary] = []
    for provider in providers:
        try:
            ex_prices = provider.get_option_prices(asset, synth_options)
            summary = compute_divergence(synth_options, ex_prices, provider.name)
            if summary is not None:
                summaries.append(summary)
        except Exception:
            continue

    if not summaries:
        return MarketLineResult(summaries=[], avg_divergence=0.0, max_divergence=0.0, consensus="disagreement")

    avg_div = sum(s.avg_abs_div for s in summaries) / len(summaries)
    max_div = max(s.max_abs_div for s in summaries)
    consensus = _classify_consensus(avg_div)

    return MarketLineResult(
        summaries=summaries,
        avg_divergence=round(avg_div, 2),
        max_divergence=round(max_div, 2),
        consensus=consensus,
    )
