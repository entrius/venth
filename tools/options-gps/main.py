import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from synth_client import SynthClient
from engine import OptionsGPSEngine

def main():
    print("="*50)
    print(" Options GPS: Turning Views into Trades")
    print("="*50)

    # Screen 1: View Setup
    symbol = input("Enter Asset Symbol (BTC, ETH, SPY, NVDA, TSLA, AAPL, GOOGL) [BTC]: ").strip().upper() or "BTC"
    
    print("\nSelect your market bias:")
    print("  1) Bullish")
    print("  2) Bearish")
    print("  3) Neutral")
    bias_map = {"1": "bullish", "2": "bearish", "3": "neutral"}
    bias_choice = input("Choice [1]: ").strip() or "1"
    bias = bias_map.get(bias_choice, "bullish")
    
    print("\nSelect your risk tolerance:")
    print("  1) Low")
    print("  2) Medium")
    print("  3) High")
    risk_map = {"1": "low", "2": "medium", "3": "high"}
    risk_choice = input("Choice [2]: ").strip() or "2"
    risk = risk_map.get(risk_choice, "medium")

    print(f"\n[Screen 1] You are {bias} with {risk} risk tolerance on {symbol}.")
    print("Fetching Synth probabilistic forecasts and option chains...\n")
    
    # Init SynthClient
    client = SynthClient()
    
    forecast = client.get_prediction_percentiles(symbol, horizon="24h")
    current_price = forecast["current_price"]
    final_percentiles = forecast["forecast_future"]["percentiles"][-1]
    options_chain = client.get_option_pricing(symbol)
    
    # Engine logic
    engine = OptionsGPSEngine()
    results = engine.generate_plays(
        current_price=current_price,
        final_percentiles=final_percentiles,
        options_chain=options_chain,
        bias=bias,
        risk=risk
    )
    
    if not results:
        print("No valid strategies found due to lack of confidence/liquidity.")
        return

    # Screen 2: Top Plays
    print("="*50)
    print(" [Screen 2 & 3] Top Recommended Strategies")
    print("="*50)
    
    for i, play in enumerate(results[:3], 1):
        print(f"\n--- Rank #{i}: {play['name']} ---")
        if "strikes" in play:
            print(f"Strikes:      {play['strikes']}")
        else:
            print(f"Strike:       {play['strike']}")
        
        profit_str = "Infinite" if play['max_profit'] == float('inf') else f"${play['max_profit']:,.2f}"
        print(f"Max Loss:     ${play['max_loss']:,.2f} | Max Profit: {profit_str}")
        print(f"PoP:          {play['pop']}%")
        print(f"Why it works: {play['rationale']}")
        
    # Screen 4: If Wrong
    print("\n" + "="*50)
    print(" [Screen 4] If Wrong (Contingency)")
    print("="*50)
    print("Exit Rule:    Close the position if the underlying breaks the opposite 5th percentile.")
    print("Time Rule:    Reassess position at 12 hours remaining before expiry.")
    print("Roll Rule:    If IV collapses unexpectedly, roll the spread out to the next week.")

if __name__ == "__main__":
    main()
