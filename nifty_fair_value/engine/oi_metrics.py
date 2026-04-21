def pcr_oi(total_ce_oi, total_pe_oi):
    """
    Total-chain Put-Call Ratio by Open Interest.
    Used only for velocity/direction tracking — NOT for directional conviction
    signals because far-OTM structural hedging permanently inflates PE OI on Nifty.
    """
    return round(total_pe_oi / total_ce_oi, 4) if total_ce_oi else None


def pcr_near_atm(options, atm_strike, window=3):
    """
    Near-ATM PCR: total put OI / call OI for the ±window strikes around ATM.

    Filters out the structural far-OTM hedging noise (pension fund tail puts,
    institutional collars) that permanently inflates the total-chain PCR on Nifty
    regardless of actual directional positioning.

    Near-ATM OI is where speculative and directional writers are most active,
    so this ratio is a much cleaner read of current conviction near spot.

    Returns None when the ATM window has zero call OI (degenerate chain).
    """
    if not options:
        return None
    sorted_opts = sorted(options, key=lambda x: x.strike)
    atm_idx = min(range(len(sorted_opts)),
                  key=lambda i: abs(sorted_opts[i].strike - atm_strike))
    window_opts = sorted_opts[max(0, atm_idx - window): atm_idx + window + 1]
    total_ce = sum(o.call_oi for o in window_opts)
    total_pe = sum(o.put_oi for o in window_opts)
    return round(total_pe / total_ce, 4) if total_ce else None


def pcr_at_strike(options, strike):
    """
    PCR at a specific strike (nearest match used if exact not found).

    Used for max pain PCR: the put/call OI ratio at the max pain strike tells
    you which side of writers is defending that level as a settlement anchor.
      > 1.3  → put writers dominant → max pain is a put-writer floor
      < 0.7  → call writers dominant → max pain is a call-writer ceiling
      0.7–1.3 → balanced → no strong directional pin wall at that strike
    """
    if not options:
        return None
    opt = min(options, key=lambda x: abs(x.strike - strike))
    return round(opt.put_oi / opt.call_oi, 4) if opt.call_oi else None


def pcr_notional(options, lot_size=65):
    """
    Calculates the Put-Call Ratio based on Notional Value (OI * LTP * Lot Size).
    """
    ce_notional = sum(o.call_oi * o.call_ltp * lot_size for o in options)
    pe_notional = sum(o.put_oi * o.put_ltp * lot_size for o in options)
    return pe_notional / ce_notional if ce_notional else 0.0
