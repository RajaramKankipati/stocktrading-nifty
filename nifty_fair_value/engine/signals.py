def _rr(entry, sl, target):
    """Returns risk:reward as '1:X' string, or 'N/A' if risk is zero."""
    risk = abs(entry - sl)
    reward = abs(target - entry)
    if risk <= 0:
        return "N/A"
    return f"1:{round(reward / risk, 1)}"


def generate_execution_setup(market_data, today_fv, expiry_fv, theoretical_price, call_level, put_level, straddle_range, baselines=None):
    """
    Core Execution Engine: Converts fair values and OI levels into actionable trade setups.
    Setups: Breakout Trap (C), Trend Continuation (B), Mean Reversion (A).
    Checked in order of specificity.
    """
    spot = market_data.spot
    futures = market_data.futures

    # Shifting detection (default to neutral if no baseline)
    put_shifting_up    = baselines and put_level  > baselines.get('put_level', 0)       + 10
    call_shifting_down = baselines and call_level < baselines.get('call_level', 999999) - 10

    setup = {
        "signal":      "NEUTRAL",
        "type":        "No Active Setup",
        "entry":       0.0,
        "sl":          0.0,
        "target":      0.0,
        "trailing":    "N/A",
        "risk_reward": "N/A",
    }

    # Setup C: Breakout Trap — checked first as it is the most specific condition.
    # SHORT TRAP: Spot breaks above call_level but synthetic is not supporting the move.
    if spot >= call_level + 10 and theoretical_price < call_level:
        sl, target = spot + 20, today_fv
        setup.update({
            "signal":      "SHORT",
            "type":        "Breakout Trap",
            "entry":       spot,
            "sl":          sl,
            "target":      target,
            "trailing":    "Quick exit at Today FV",
            "risk_reward": _rr(spot, sl, target),
        })
        return setup

    # LONG TRAP: Spot breaks below put_level but synthetic is not supporting the move.
    if spot <= put_level - 10 and theoretical_price > put_level:
        sl, target = spot - 20, today_fv
        setup.update({
            "signal":      "LONG",
            "type":        "Breakout Trap",
            "entry":       spot,
            "sl":          sl,
            "target":      target,
            "trailing":    "Quick exit at Today FV",
            "risk_reward": _rr(spot, sl, target),
        })
        return setup

    # Setup B: Trend Continuation (Momentum Days)
    # Note: entry conditions (spot > today_fv > expiry_fv / spot < today_fv < expiry_fv)
    # guarantee spot is already beyond expiry_fv, so the target is simply spot ± straddle_range.
    if spot > today_fv > expiry_fv and theoretical_price > futures and put_shifting_up:
        sl, target = today_fv, spot + straddle_range
        setup.update({
            "signal":      "LONG",
            "type":        "Trend Continuation",
            "entry":       spot,
            "sl":          sl,
            "target":      target,
            "trailing":    "Trail using Today FV upward shift",
            "risk_reward": _rr(spot, sl, target),
        })
        return setup

    if spot < today_fv < expiry_fv and theoretical_price < futures and call_shifting_down:
        sl, target = today_fv, spot - straddle_range
        setup.update({
            "signal":      "SHORT",
            "type":        "Trend Continuation",
            "entry":       spot,
            "sl":          sl,
            "target":      target,
            "trailing":    "Trail using Today FV downward shift",
            "risk_reward": _rr(spot, sl, target),
        })
        return setup

    # Setup A: Mean Reversion (Highest Consistency)
    if spot > today_fv * 1.003 and theoretical_price <= futures and abs(spot - call_level) < 25:
        sl, target = max(call_level + 15, spot + 0.5 * straddle_range), today_fv
        setup.update({
            "signal":      "SHORT",
            "type":        "Mean Reversion",
            "entry":       spot,
            "sl":          sl,
            "target":      target,
            "trailing":    "Trail to cost after +0.3%, then trail using Today FV",
            "risk_reward": _rr(spot, sl, target),
        })
        return setup

    if spot < today_fv * 0.997 and theoretical_price >= futures and abs(spot - put_level) < 25:
        sl, target = min(put_level - 15, spot - 0.5 * straddle_range), today_fv
        setup.update({
            "signal":      "LONG",
            "type":        "Mean Reversion",
            "entry":       spot,
            "sl":          sl,
            "target":      target,
            "trailing":    "Trail to cost after +0.3%, then trail using Today FV",
            "risk_reward": _rr(spot, sl, target),
        })
        return setup

    # Fallback: directional bias badge only, no active setup
    if spot > today_fv:
        setup["signal"] = "BULLISH"
    elif spot < today_fv:
        setup["signal"] = "BEARISH"

    return setup
