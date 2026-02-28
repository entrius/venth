import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine import OptionsGPSEngine

@pytest.fixture
def mock_market_data():
    return {
        "current_price": 67637.56,
        "final_percentiles": {
            "0.05": 66200.00,
            "0.5": 67700.00,
            "0.95": 69200.00
        },
        "options_chain": {
            "expiry_time": "2026-02-26 08:00:00Z",
            "call_options": {
                "67000": 1000.0,
                "68000": 400.0,
                "69000": 100.0
            },
            "put_options": {
                "66000": 100.0,
                "67000": 400.0,
                "68000": 1000.0
            }
        }
    }

def test_bullish_low_risk(mock_market_data):
    engine = OptionsGPSEngine()
    results = engine.generate_plays(
        current_price=mock_market_data["current_price"],
        final_percentiles=mock_market_data["final_percentiles"],
        options_chain=mock_market_data["options_chain"],
        bias="bullish",
        risk="low"
    )
    
    # Low risk bullish should prefer Bull Call Spreads over naked calls
    assert len(results) > 0
    top_play = results[0]
    assert "Spread" in top_play["name"]
    assert top_play["max_loss"] < 1000  # Spread should cap loss lower than a naked ITM call
    assert top_play["rationale"]

def test_bearish_high_risk(mock_market_data):
    engine = OptionsGPSEngine()
    results = engine.generate_plays(
        current_price=mock_market_data["current_price"],
        final_percentiles=mock_market_data["final_percentiles"],
        options_chain=mock_market_data["options_chain"],
        bias="bearish",
        risk="high"
    )
    
    # High risk bearish should prefer naked Puts for max return
    assert len(results) > 0
    top_play = results[0]
    assert top_play["name"] == "Long Put"
    
def test_neutral_medium_risk(mock_market_data):
    engine = OptionsGPSEngine()
    results = engine.generate_plays(
        current_price=mock_market_data["current_price"],
        final_percentiles=mock_market_data["final_percentiles"],
        options_chain=mock_market_data["options_chain"],
        bias="neutral",
        risk="medium"
    )
    
    # Neutral should suggest Iron Condors or similar ranged plays
    assert len(results) > 0
    top_play = results[0]
    assert "Condor" in top_play["name"] or "Butterfly" in top_play["name"] or "Short Strangle" in top_play["name"] or "Short Straddle" in top_play["name"] or "Iron" in top_play["name"] or "Credit Spread" in top_play["name"]
