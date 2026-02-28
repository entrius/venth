import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from synth_client import SynthClient

class TideChartEngine:
    def __init__(self):
        self.client = SynthClient()
        self.tickers = ["SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]
        
    def fetch_data(self):
        results = {}
        for ticker in self.tickers:
            forecast = self.client.get_prediction_percentiles(ticker, horizon="24h")
            vol = self.client.get_volatility(ticker, horizon="24h")
            
            cur_price = forecast["current_price"]
            final_percentiles = forecast["forecast_future"]["percentiles"][-1]
            p05 = final_percentiles["0.05"]
            p50 = final_percentiles["0.5"]
            p95 = final_percentiles["0.95"]
            
            # Normalize to percent changes
            move_5th = ((p05 - cur_price) / cur_price) * 100
            move_50th = ((p50 - cur_price) / cur_price) * 100
            move_95th = ((p95 - cur_price) / cur_price) * 100
            
            # Skew: Upside Tail magnitude / Downside Tail magnitude
            upside_tail = move_95th - move_50th
            downside_tail = move_50th - move_5th  # Keep positive for comparison
            skew = upside_tail - downside_tail
            
            volatility = vol["forecast_future"]["average_volatility"]
            
            results[ticker] = {
                "current_price": cur_price,
                "median_move_pct": move_50th,
                "upside_tail_pct": move_95th,
                "downside_tail_pct": move_5th,
                "skew_pct": skew,
                "volatility": volatility
            }
        
        # Calculate Relative to SPY strength
        spy_median = results["SPY"]["median_move_pct"]
        for ticker in self.tickers:
            results[ticker]["relative_to_spy_pct"] = results[ticker]["median_move_pct"] - spy_median
            
        return results
