def generate_execution_setup(market_data, today_fv, expiry_fv, theoretical_price,
                             call_level, put_level, straddle_range, baselines=None,
                             ls=0.0):
    """
    Core Execution Engine: Converts LS factor + OI context into actionable trade setups.

    Setup C — Breakout Trap  : spot breaks a known OI wall but synthetic disagrees
    Setup B — Trend Momentum : strong LS direction with confirming OI wall migration
    Setup A — Expiry Gravity : strong LS + futures basis confirms institutional positioning

    SL sized as straddle_range * 0.5 so risk is proportional to current volatility.
    """
    spot    = market_data.spot
    futures = market_data.futures
    basis   = round(futures - spot, 2)

    risk_unit = max(straddle_range * 0.5, 20) if straddle_range else 30

    # OI wall migration vs session open
    put_shifting_up   = baselines and put_level   > baselines.get('put_level',   0)      + 10
    call_shifting_down = baselines and call_level < baselines.get('call_level',   999999) - 10

    setup = {
        "signal"      : "NEUTRAL",
        "type"        : "No Active Setup",
        "entry"       : 0.0,
        "sl"          : 0.0,
        "target"      : 0.0,
        "trailing"    : "N/A",
        "risk_reward" : "N/A",
    }

    # ── Setup C: Breakout Trap ──────────────────────────────────────────────
    # Spot breaks an OI wall but synthetic (put-call parity price) disagrees.
    # Synthetic lags spot on traps because market makers reprice calls/puts
    # slowly — a divergence >10pts at the wall signals a false breakout.
    if call_level and spot >= call_level + 10 and theoretical_price < call_level:
        sl     = round(spot + risk_unit, 1)
        target = round(expiry_fv if expiry_fv else spot - straddle_range, 1)
        rr     = round(abs(spot - target) / abs(sl - spot), 1) if sl != spot else "N/A"
        setup.update({
            "signal"     : "SHORT",
            "type"       : "Breakout Trap",
            "entry"      : spot,
            "sl"         : sl,
            "target"     : target,
            "trailing"   : "Exit at expiry fair value",
            "risk_reward": f"1:{rr}",
        })
        return setup

    if put_level and spot <= put_level - 10 and theoretical_price > put_level:
        sl     = round(spot - risk_unit, 1)
        target = round(expiry_fv if expiry_fv else spot + straddle_range, 1)
        rr     = round(abs(target - spot) / abs(spot - sl), 1) if sl != spot else "N/A"
        setup.update({
            "signal"     : "LONG",
            "type"       : "Breakout Trap",
            "entry"      : spot,
            "sl"         : sl,
            "target"     : target,
            "trailing"   : "Exit at expiry fair value",
            "risk_reward": f"1:{rr}",
        })
        return setup

    # ── Setup B: Trend Momentum ─────────────────────────────────────────────
    # Strong LS direction (gravity pulling spot toward max pain) confirmed by
    # OI walls migrating in the same direction — writers are rolling positions.
    # ls > 0.35: spot below max pain → expiry gravity pulls UP → LONG
    # ls < -0.35: spot above max pain → expiry gravity pulls DOWN → SHORT
    if ls > 0.35 and put_shifting_up:
        sl     = round(spot - risk_unit, 1)
        target = round(spot + straddle_range, 1)
        rr     = round(abs(target - spot) / abs(spot - sl), 1) if sl != spot else "N/A"
        setup.update({
            "signal"     : "LONG",
            "type"       : "Trend Momentum",
            "entry"      : spot,
            "sl"         : sl,
            "target"     : target,
            "trailing"   : "Trail SL up as put wall migrates higher",
            "risk_reward": f"1:{rr}",
        })
        return setup

    if ls < -0.35 and call_shifting_down:
        sl     = round(spot + risk_unit, 1)
        target = round(spot - straddle_range, 1)
        rr     = round(abs(spot - target) / abs(sl - spot), 1) if sl != spot else "N/A"
        setup.update({
            "signal"     : "SHORT",
            "type"       : "Trend Momentum",
            "entry"      : spot,
            "sl"         : sl,
            "target"     : target,
            "trailing"   : "Trail SL down as call wall migrates lower",
            "risk_reward": f"1:{rr}",
        })
        return setup

    # ── Setup A: Expiry Gravity ─────────────────────────────────────────────
    # Strong LS confirmed by futures basis: positive basis (futures > spot) shows
    # institutional demand for upside; negative basis shows defensive hedging.
    # This is the highest-consistency setup — basis and LS must agree.
    if ls > 0.35 and basis > 0:
        sl     = round(spot - risk_unit, 1)
        target = round(expiry_fv if expiry_fv else spot + straddle_range * 0.7, 1)
        rr     = round(abs(target - spot) / abs(spot - sl), 1) if sl != spot else "N/A"
        setup.update({
            "signal"     : "LONG",
            "type"       : "Expiry Gravity",
            "entry"      : spot,
            "sl"         : sl,
            "target"     : target,
            "trailing"   : "Trail to cost at +0.5× risk; target expiry fair value",
            "risk_reward": f"1:{rr}",
        })
        return setup

    if ls < -0.35 and basis < 0:
        sl     = round(spot + risk_unit, 1)
        target = round(expiry_fv if expiry_fv else spot - straddle_range * 0.7, 1)
        rr     = round(abs(spot - target) / abs(sl - spot), 1) if sl != spot else "N/A"
        setup.update({
            "signal"     : "SHORT",
            "type"       : "Expiry Gravity",
            "entry"      : spot,
            "sl"         : sl,
            "target"     : target,
            "trailing"   : "Trail to cost at +0.5× risk; target expiry fair value",
            "risk_reward": f"1:{rr}",
        })
        return setup

    # ── Fallback: directional bias only ────────────────────────────────────
    if ls > 0.15:
        setup["signal"] = "BULLISH"
    elif ls < -0.15:
        setup["signal"] = "BEARISH"

    return setup
