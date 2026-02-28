"""
Example tool using the Synth API client.

This is a starter template — replace this with your actual tool logic.
The SynthClient automatically uses mock data when no API key is set,
so you can develop and test without a real Synth API key.
"""

import sys
import os

# Add project root to path so we can import synth_client
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from synth_client import SynthClient


def main():
    client = SynthClient()

    # Example: get BTC prediction percentiles (24h horizon)
    forecast = client.get_prediction_percentiles("BTC", horizon="24h")
    current_price = forecast["current_price"]
    percentiles = forecast["forecast_future"]["percentiles"]

    print(f"BTC Current Price: ${current_price:,.2f}")
    print(f"Number of forecast time steps: {len(percentiles)}")

    # Show the final time step's percentiles (end of 24h window)
    final = percentiles[-1]
    print(f"\n24h Forecast Percentiles:")
    print(f"  5th percentile:  ${final['0.05']:,.2f}")
    print(f"  50th percentile: ${final['0.5']:,.2f}")
    print(f"  95th percentile: ${final['0.95']:,.2f}")

    # Example: get volatility
    vol = client.get_volatility("BTC", horizon="24h")
    avg_vol = vol["forecast_future"]["average_volatility"]
    print(f"\nForecasted 24h Average Volatility: {avg_vol:.6f}")

    # Example: get option pricing
    options = client.get_option_pricing("BTC")
    print(f"\nOptions Expiry: {options['expiry_time']}")
    call_strikes = list(options["call_options"].keys())[:5]
    print(f"First 5 call strikes: {call_strikes}")


if __name__ == "__main__":
    main()
