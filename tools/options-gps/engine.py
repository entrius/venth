import optparse

class OptionsGPSEngine:
    def __init__(self):
        pass

    def get_closest_strike(self, strikes: list, target_price: float) -> str:
        filtered = [s for s in strikes if s is not None]
        return min(filtered, key=lambda x: abs(float(x) - target_price))

    def _evaluate_spread(self, chain, strike1, strike2, type_="call"):
        if type_ == "call":
            cost = chain["call_options"].get(strike1, 0) - chain["call_options"].get(strike2, 0)
        else:
            cost = chain["put_options"].get(strike1, 0) - chain["put_options"].get(strike2, 0)
        return cost

    def calc_pop(self, strike, current_price, median, is_call):
        # A pseudo-CDF approximation based on expected median move
        distance_pct = (median - float(strike)) / current_price
        if is_call:
            pop = 50.0 + (distance_pct * 1500)
        else:
            pop = 50.0 - (distance_pct * 1500)
        return min(95.0, max(5.0, round(pop, 1)))

    def generate_plays(self, current_price, final_percentiles, options_chain, bias, risk):
        strikes = sorted([s for s in options_chain["call_options"].keys()], key=float)
        atm_strike = self.get_closest_strike(strikes, current_price)
        
        atm_idx = strikes.index(atm_strike)
        otm_call_strike = strikes[min(atm_idx + 2, len(strikes)-1)]
        otm_put_strike = strikes[max(atm_idx - 2, 0)]
        
        median_expected = float(final_percentiles.get('0.5', current_price))

        plays = []

        if bias == "bullish":
            if risk in ["high", "medium"]:
                cost = options_chain["call_options"].get(atm_strike, 0)
                plays.append({
                    "name": "Long Call",
                    "strike": atm_strike,
                    "max_loss": cost,
                    "max_profit": float('inf'),
                    "pop": self.calc_pop(atm_strike, current_price, median_expected, True),
                    "rationale": f"High upside capture using a standard long {atm_strike} call if price pushes to the 95th percentile (${final_percentiles.get('0.95', 0):,.2f}).",
                    "score": 85 if risk == "high" else 70
                })
            
            if risk in ["low", "medium"]:
                cost = self._evaluate_spread(options_chain, atm_strike, otm_call_strike, "call")
                plays.append({
                    "name": "Bull Call Spread",
                    "strikes": f"{atm_strike} / {otm_call_strike}",
                    "max_loss": cost,
                    "max_profit": (float(otm_call_strike) - float(atm_strike)) - cost,
                    "pop": self.calc_pop(atm_strike, current_price, median_expected, True) + 12.5, # Spreads have intrinsically higher PoP
                    "rationale": f"Limits max loss to ${cost:,.2f} while capturing the upside up to {otm_call_strike}.",
                    "score": 90 if risk == "low" else 75
                })

        elif bias == "bearish":
            if risk in ["high", "medium"]:
                cost = options_chain["put_options"].get(atm_strike, 0)
                plays.append({
                    "name": "Long Put",
                    "strike": atm_strike,
                    "max_loss": cost,
                    "max_profit": float('inf'),
                    "pop": self.calc_pop(atm_strike, current_price, median_expected, False),
                    "rationale": f"High downside capture using a long {atm_strike} put if price drops to the 5th percentile (${final_percentiles.get('0.05', 0):,.2f}).",
                    "score": 85 if risk == "high" else 70
                })
                
            if risk in ["low", "medium"]:
                cost = self._evaluate_spread(options_chain, atm_strike, otm_put_strike, "put")
                plays.append({
                    "name": "Bear Put Spread",
                    "strikes": f"{atm_strike} / {otm_put_strike}",
                    "max_loss": cost,
                    "max_profit": (float(atm_strike) - float(otm_put_strike)) - cost,
                    "pop": self.calc_pop(atm_strike, current_price, median_expected, False) + 12.5,
                    "rationale": f"Caps your risk to ${cost:,.2f} if the trend reverses.",
                    "score": 90 if risk == "low" else 75
                })

        elif bias == "neutral":
            # Iron Condor approximation
            call_spread_credit = self._evaluate_spread(options_chain, otm_call_strike, strikes[min(atm_idx + 3, len(strikes)-1)], "call")
            put_spread_credit = self._evaluate_spread(options_chain, otm_put_strike, strikes[max(atm_idx - 3, 0)], "put")
            credit = call_spread_credit + put_spread_credit
            
            plays.append({
                "name": "Iron Condor",
                "strikes": f"Short {otm_put_strike} P / Short {otm_call_strike} C",
                "max_loss": max(float(strikes[min(atm_idx + 3, len(strikes)-1)]) - float(otm_call_strike) - call_spread_credit, 
                                float(otm_put_strike) - float(strikes[max(atm_idx - 3, 0)]) - put_spread_credit),
                "max_profit": credit,
                "pop": min(95.0, 50.0 + (credit / current_price) * 5000),
                "rationale": f"High probability of profit if price stays between {otm_put_strike} and {otm_call_strike}.",
                "score": 95 if risk == "low" else (85 if risk == "medium" else 60)
            })

            strangle_premium = options_chain["put_options"].get(otm_put_strike, 0) + options_chain["call_options"].get(otm_call_strike, 0)
            plays.append({
                "name": "Short Strangle",
                "strikes": f"{otm_put_strike} P / {otm_call_strike} C",
                "max_loss": float('inf'),
                "max_profit": strangle_premium,
                "pop": min(95.0, 60.0 + (strangle_premium / current_price) * 5000),
                "rationale": "Collects higher premium but carries infinite undefined risk if a tail event hits.",
                "score": 90 if risk == "high" else 40
            })

        # Sort by score descending
        plays.sort(key=lambda x: x["score"], reverse=True)
        return plays
