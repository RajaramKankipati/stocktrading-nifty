def generate_execution_setup(market_data, today_fv, expiry_fv, theoretical_price, call_level, put_level, straddle_range, baselines=None):
    """
    Core Execution Engine: Converts fair values and OI levels into actionable trade setups.
    Setups: Breakout Trap (C), Trend Continuation (B), Mean Reversion (A).
    Checked in order of specificity.
    """
    spot = market_data.spot
    futures = market_data.futures
    
    # 1. Base Variables
    # Shifting detection (Default to Neutal if no baseline)
    put_shifting_up = baselines and put_level > baselines.get('put_level', 0) + 10
    call_shifting_down = baselines and call_level < baselines.get('call_level', 999999) - 10
    
    setup = {
        "signal": "NEUTRAL",
        "type": "No Active Setup",
        "entry": 0.0,
        "sl": 0.0,
        "target": 0.0,
        "trailing": "N/A",
        "risk_reward": "N/A"
    }

    # 2. Setup C: Breakout Trap (Best RR Trades)
    # Check this first as it is highly specific.
    # SHORT TRAP: Spot breaks above call_level, Synthetic NOT supporting
    if spot >= call_level + 10 and theoretical_price < call_level:
        setup.update({
            "signal": "SHORT",
            "type": "Breakout Trap",
            "entry": spot,
            "sl": spot + 20, # Recent swing high + buffer
            "target": today_fv,
            "trailing": "Quick exit at Today FV"
        })
        return setup

    # LONG TRAP: Spot breaks below put_level, Synthetic NOT supporting
    if spot <= put_level - 10 and theoretical_price > put_level:
        setup.update({
            "signal": "LONG",
            "type": "Breakout Trap",
            "entry": spot,
            "sl": spot - 20, # Recent swing low + buffer
            "target": today_fv,
            "trailing": "Quick exit at Today FV"
        })
        return setup

    # 3. Setup B: Trend Continuation (Momentum Days)
    # LONG: Spot > Today FV > Expiry FV, Synthetic > Futures, Put Level shifting up
    if spot > today_fv > expiry_fv and theoretical_price > futures and put_shifting_up:
        setup.update({
            "signal": "LONG",
            "type": "Trend Continuation",
            "entry": spot,
            "sl": today_fv,
            "target": max(expiry_fv, spot + straddle_range),
            "trailing": "Trail using Today FV upward shift"
        })
        return setup

    # SHORT: Spot < Today FV < Expiry FV, Synthetic < Futures, Call Level shifting down
    if spot < today_fv < expiry_fv and theoretical_price < futures and call_shifting_down:
        setup.update({
            "signal": "SHORT",
            "type": "Trend Continuation",
            "entry": spot,
            "sl": today_fv,
            "target": min(expiry_fv, spot - straddle_range),
            "trailing": "Trail using Today FV downward shift"
        })
        return setup

    # 4. Setup A: Mean Reversion (Highest Consistency)
    # SHORT: Spot > Today FV + 0.3%, Synthetic <= Futures, Spot near Call OI
    if spot > today_fv * 1.003 and theoretical_price <= futures and abs(spot - call_level) < 25:
        setup.update({
            "signal": "SHORT",
            "type": "Mean Reversion",
            "entry": spot,
            "sl": max(call_level + 15, spot + 0.5 * straddle_range),
            "target": today_fv,
            "trailing": f"Trail to cost after +0.3%, then trail using Today FV"
        })
        return setup

    # LONG: Spot < Today FV - 0.3%, Synthetic >= Futures, Spot near Put OI
    if spot < today_fv * 0.997 and theoretical_price >= futures and abs(spot - put_level) < 25:
        setup.update({
            "signal": "LONG",
            "type": "Mean Reversion",
            "entry": spot,
            "sl": min(put_level - 15, spot - 0.5 * straddle_range),
            "target": today_fv,
            "trailing": "Trail to cost after +0.3%, then trail using Today FV"
        })
        return setup

    # 5. Fallback signals (Keeping legacy signal logic for the badge)
    if spot > today_fv: setup["signal"] = "BULLISH"
    elif spot < today_fv: setup["signal"] = "BEARISH"
    
    return setup
