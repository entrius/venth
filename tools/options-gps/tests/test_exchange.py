"""Tests for exchange.py: Market Line Shopping — fetch, parse, edge detection, and ranking integration."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from exchange import (
    ExchangeQuote,
    EdgeMetrics,
    fetch_deribit,
    fetch_aevo,
    fetch_all_exchanges,
    best_market_price,
    best_execution_price,
    compute_divergence,
    compute_edge,
    leg_divergences,
    strategy_divergence,
)
from pipeline import rank_strategies


class TestFetchParse:
    def test_deribit_mock(self, mock_exchange_dir):
        quotes = fetch_deribit("BTC", mock_dir=mock_exchange_dir)
        assert len(quotes) > 0
        assert all(q.exchange == "deribit" for q in quotes)

    def test_aevo_mock(self, mock_exchange_dir):
        quotes = fetch_aevo("BTC", mock_dir=mock_exchange_dir)
        assert len(quotes) > 0
        assert all(q.exchange == "aevo" for q in quotes)

    def test_all_exchanges_mock(self, mock_exchange_dir):
        quotes = fetch_all_exchanges("BTC", mock_dir=mock_exchange_dir)
        assert {"deribit", "aevo", "derive"} == {q.exchange for q in quotes}

    def test_non_crypto_returns_empty(self, mock_exchange_dir):
        for asset in ("XAU", "SPY", "NVDA", "TSLA", "AAPL", "GOOGL"):
            assert fetch_all_exchanges(asset, mock_dir=mock_exchange_dir) == []

    def test_mid_price(self, mock_exchange_dir):
        for q in fetch_deribit("BTC", mock_dir=mock_exchange_dir):
            assert abs(q.mid - (q.bid + q.ask) / 2) < 0.01

    def test_fields_populated(self, mock_exchange_dir):
        for q in fetch_deribit("BTC", mock_dir=mock_exchange_dir):
            assert q.strike > 0
            assert q.option_type in ("call", "put")

    def test_empty_mock_graceful(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert fetch_deribit("BTC", mock_dir=tmpdir) == []
            assert fetch_aevo("BTC", mock_dir=tmpdir) == []

    def test_invalid_json_graceful(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for fname in ("deribit_BTC.json", "aevo_BTC.json"):
                with open(os.path.join(tmpdir, fname), "w") as f:
                    f.write("not valid json {{{")
            assert fetch_deribit("BTC", mock_dir=tmpdir) == []
            assert fetch_aevo("BTC", mock_dir=tmpdir) == []


class TestBestMarketPrice:
    def test_lowest_ask(self, sample_exchange_quotes):
        best = best_market_price(sample_exchange_quotes, 67500, "call")
        assert best is not None
        assert best.exchange == "aevo"  # ask=655 < 660
        assert best.ask == 655.0

    def test_no_match(self, sample_exchange_quotes):
        assert best_market_price(sample_exchange_quotes, 99999, "call") is None

    def test_single_exchange(self):
        quotes = [ExchangeQuote("deribit", "BTC", 67500, "call", 610.0, 660.0, 635.0, 51.0)]
        assert best_market_price(quotes, 67500, "call").exchange == "deribit"

    def test_prefers_lower_ask(self):
        quotes = [
            ExchangeQuote("deribit", "BTC", 67500, "call", 610.0, 660.0, 635.0, 51.0),
            ExchangeQuote("aevo", "BTC", 67500, "call", 630.0, 650.0, 640.0, 51.0),
        ]
        assert best_market_price(quotes, 67500, "call").exchange == "aevo"

    def test_execution_buy_lowest_ask(self, sample_exchange_quotes):
        best = best_execution_price(sample_exchange_quotes, 67500, "call", "BUY")
        assert best.exchange == "aevo"  # ask=655 < 660
        assert best.ask == 655.0

    def test_execution_sell_highest_bid(self, sample_exchange_quotes):
        best = best_execution_price(sample_exchange_quotes, 67500, "call", "SELL")
        assert best.exchange == "aevo"  # bid=620 > 610
        assert best.bid == 620.0


class TestEdgeDetection:
    def test_compute_edge_basic(self, sample_exchange_quotes):
        edge = compute_edge(638.43, sample_exchange_quotes, 67500, "call")
        assert edge is not None
        assert isinstance(edge, EdgeMetrics)
        assert edge.n_sources == 3  # synth + 2 exchanges

    def test_compute_edge_z_score_positive(self, sample_exchange_quotes):
        """Synth > market_mean → positive z-score (market underpriced)."""
        edge = compute_edge(638.43, sample_exchange_quotes, 67500, "call")
        # Synth 638.43 > market_mean ~636.25 → positive
        assert edge.z_score > 0

    def test_compute_edge_z_score_negative(self, sample_exchange_quotes):
        """Synth < market_mean → negative z-score (market overpriced)."""
        # 67500 put: synth ~291.75, market ~292.5 → negative
        edge = compute_edge(291.75, sample_exchange_quotes, 67500, "put")
        assert edge is not None
        assert edge.z_score < 0

    def test_compute_edge_no_match(self, sample_exchange_quotes):
        assert compute_edge(100.0, sample_exchange_quotes, 99999, "call") is None

    def test_edge_label_strong(self):
        """Large disagreement → STRONG edge."""
        quotes = [
            ExchangeQuote("deribit", "BTC", 67500, "call", 590.0, 620.0, 605.0, 51.0),
            ExchangeQuote("aevo", "BTC", 67500, "call", 595.0, 615.0, 605.0, 51.0),
        ]
        edge = compute_edge(640.0, quotes, 67500, "call")  # 640 vs 605 = big gap
        assert edge.edge_label == "STRONG"
        assert abs(edge.z_score) >= 2.0

    def test_edge_label_none_when_close(self):
        """Prices all agree → NONE edge."""
        quotes = [
            ExchangeQuote("deribit", "BTC", 67500, "call", 634.0, 636.0, 635.0, 51.0),
            ExchangeQuote("aevo", "BTC", 67500, "call", 634.5, 636.5, 635.5, 51.0),
        ]
        edge = compute_edge(635.25, quotes, 67500, "call")  # right at market mean
        assert edge.edge_label == "NONE"
        assert abs(edge.z_score) < 0.5

    def test_edge_std_dev_floor(self):
        """When all prices identical, noise floor prevents div-by-zero."""
        quotes = [
            ExchangeQuote("deribit", "BTC", 67500, "call", 635.0, 635.0, 635.0, 51.0),
        ]
        edge = compute_edge(635.0, quotes, 67500, "call")
        assert edge is not None
        assert edge.std_dev > 0  # floor applied


class TestDivergence:
    def test_positive(self):
        assert abs(compute_divergence(100.0, 95.0) - 5.0) < 0.01

    def test_negative(self):
        assert abs(compute_divergence(100.0, 105.0) - (-5.0)) < 0.01

    def test_zero(self):
        assert compute_divergence(100.0, 100.0) == 0.0

    def test_zero_fair(self):
        assert compute_divergence(0.0, 50.0) == 0.0

    def test_leg_divergences_all_legs(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        divs = leg_divergences(multi_leg_strategy, sample_exchange_quotes, btc_option_data)
        assert len(divs) == 2
        expected_keys = {"divergence_pct", "z_score", "edge_label", "best_exchange",
                         "best_price", "synth_fair", "market_mean", "std_dev"}
        for d in divs.values():
            assert set(d.keys()) == expected_keys

    def test_leg_divergences_has_z_score(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        divs = leg_divergences(multi_leg_strategy, sample_exchange_quotes, btc_option_data)
        for d in divs.values():
            assert isinstance(d["z_score"], float)
            assert d["edge_label"] in ("STRONG", "MODERATE", "WEAK", "NONE")

    def test_leg_divergences_missing_exchange(self, sample_strategy, btc_option_data):
        assert leg_divergences(sample_strategy, [], btc_option_data) == {}

    def test_strategy_divergence_average_zscore(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        div = strategy_divergence(multi_leg_strategy, sample_exchange_quotes, btc_option_data)
        leg_divs = leg_divergences(multi_leg_strategy, sample_exchange_quotes, btc_option_data)
        expected = sum(d["z_score"] for d in leg_divs.values()) / len(leg_divs)
        assert abs(div - expected) < 0.01

    def test_strategy_divergence_none_when_no_data(self, sample_strategy):
        assert strategy_divergence(sample_strategy, [], {"call_options": {}, "put_options": {}}) is None

    def test_leg_divergences_action_aware(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        """BUY legs get lowest ask, SELL legs get highest bid."""
        divs = leg_divergences(multi_leg_strategy, sample_exchange_quotes, btc_option_data)
        # Leg 0: BUY Call 67000 → best_price should be lowest ask
        assert divs[0]["best_price"] == 1010.0  # Aevo ask < Deribit ask (1025)
        # Leg 1: SELL Call 68000 → best_price should be highest bid
        assert divs[1]["best_price"] == 360.0  # Aevo bid > Deribit bid (355)


class TestRankingIntegration:
    def test_positive_divergence_boosts_score(self, ranking_context):
        candidates, outcome_prices, fusion, confidence = ranking_context
        scored_base = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence)
        div_map = {id(c): 2.0 for c in candidates}
        scored_div = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence,
                                     divergence_by_strategy=div_map)
        assert scored_div[0].score > scored_base[0].score

    def test_negative_divergence_reduces_score(self, ranking_context):
        candidates, outcome_prices, fusion, confidence = ranking_context
        scored_base = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence)
        div_map = {id(c): -2.0 for c in candidates}
        scored_div = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence,
                                     divergence_by_strategy=div_map)
        assert scored_div[0].score < scored_base[0].score

    def test_clamped_at_015(self, ranking_context):
        candidates, outcome_prices, fusion, confidence = ranking_context
        extreme = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence,
                                  divergence_by_strategy={id(c): 50.0 for c in candidates})
        at_clamp = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence,
                                   divergence_by_strategy={id(c): 3.0 for c in candidates})
        assert abs(extreme[0].score - at_clamp[0].score) < 0.01

    def test_none_no_effect(self, ranking_context):
        candidates, outcome_prices, fusion, confidence = ranking_context
        a = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence)
        b = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence,
                            divergence_by_strategy=None)
        for sa, sb in zip(a, b):
            assert abs(sa.score - sb.score) < 0.001

    def test_empty_dict_no_effect(self, ranking_context):
        candidates, outcome_prices, fusion, confidence = ranking_context
        a = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence)
        b = rank_strategies(candidates, fusion, "bullish", outcome_prices, "medium", 67723, confidence,
                            divergence_by_strategy={})
        for sa, sb in zip(a, b):
            assert abs(sa.score - sb.score) < 0.001
