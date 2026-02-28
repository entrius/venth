import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data_engine import TideChartEngine

def test_tide_chart_data():
    engine = TideChartEngine()
    data = engine.fetch_data()
    
    assert "SPY" in data
    assert "NVDA" in data
    
    spy_metrics = data["SPY"]
    assert "median_move_pct" in spy_metrics
    assert "volatility" in spy_metrics
    assert "relative_to_spy_pct" in spy_metrics
    
    # SPY should be 0 relative to itself
    assert spy_metrics["relative_to_spy_pct"] == 0.0
    
    # NVDA skew should be successfully calculated
    nvda_metrics = data["NVDA"]
    assert nvda_metrics["upside_tail_pct"] > nvda_metrics["downside_tail_pct"]
    assert type(nvda_metrics["skew_pct"]) == float
