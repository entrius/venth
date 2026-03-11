"""Kelly Criterion position sizing for binary Polymarket bets."""

import math

KELLY_MAX_FRACTION = 0.20


def compute_kelly_sizing(synth_prob, market_prob, confidence=None, balance=None):
    """
    Compute Kelly-optimal position sizing for a binary Polymarket bet.

    For a binary outcome paying (1/p_market) on YES:
      b = (1 - p_market) / p_market   (net odds on a winning $1 bet)
      f* = (b * p_true - (1 - p_true)) / b

    Computes for both YES and NO, picks the side with positive EV,
    scales by confidence, and caps at KELLY_MAX_FRACTION.

    Returns dict with keys: side, fraction, size, ev_per_dollar
    """
    result = {"side": None, "fraction": 0.0, "size": 0.0, "ev_per_dollar": 0.0}

    if synth_prob is None or market_prob is None:
        return result
    if not math.isfinite(synth_prob) or not math.isfinite(market_prob):
        return result
    if market_prob <= 0.01 or market_prob >= 0.99:
        return result
    if synth_prob <= 0 or synth_prob >= 1:
        return result

    # YES side
    b_yes = (1 - market_prob) / market_prob
    kelly_yes = (b_yes * synth_prob - (1 - synth_prob)) / b_yes
    ev_yes = synth_prob * b_yes - (1 - synth_prob)

    # NO side
    market_no = 1 - market_prob
    b_no = (1 - market_no) / market_no
    kelly_no = (b_no * (1 - synth_prob) - synth_prob) / b_no
    ev_no = (1 - synth_prob) * b_no - synth_prob

    side = None
    raw_kelly = 0.0
    ev = 0.0

    if kelly_yes > 0 and ev_yes > 0 and (kelly_yes >= kelly_no or kelly_no <= 0):
        side = "YES"
        raw_kelly = kelly_yes
        ev = ev_yes
    elif kelly_no > 0 and ev_no > 0:
        side = "NO"
        raw_kelly = kelly_no
        ev = ev_no

    if side is None:
        return result

    conf_scale = confidence if (confidence is not None and math.isfinite(confidence)) else 0.5
    scaled = raw_kelly * conf_scale
    if not math.isfinite(scaled):
        return result
    fraction = min(scaled, KELLY_MAX_FRACTION)
    fraction = round(fraction, 3)

    size = 0.0
    if balance is not None and balance > 0:
        size = round(fraction * balance, 2)

    return {
        "side": side,
        "fraction": fraction,
        "size": size,
        "ev_per_dollar": round(ev, 4),
    }
