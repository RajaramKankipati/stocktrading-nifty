def find_true_atm(options, spot, window=8):
    """
    PRD Module 1: True ATM Detection via IV Spread Minimization.
    
    The true ATM is the strike where CE/PE implied volatility is closest to equal.
    Due to persistent downside skew, this is typically 50-100pts below spot on Nifty.
    
    Returns: (true_atm_option, iv_spread, listed_atm_strike, skew_shift)
    """
    # Sort options by strike
    if not options:
        return None, None, 0, 0

    sorted_opts = sorted(options, key=lambda x: x.strike)
    if not sorted_opts:
        return None, None, 0, 0

    strikes = [o.strike for o in sorted_opts]

    # Naive listed ATM (nearest to spot)
    try:
        listed_atm_opt = min(sorted_opts, key=lambda x: abs(x.strike - spot))
    except (ValueError, TypeError):
        return None, None, 0, 0

    listed_atm = listed_atm_opt.strike
    try:
        idx = strikes.index(listed_atm)
    except ValueError:
        return listed_atm_opt, None, listed_atm, 0

    # Candidates: window strikes on each side of listed ATM
    candidates = sorted_opts[max(0, idx - window): idx + window + 1]

    iv_spreads = {}
    for opt in candidates:
        ce_iv = opt.call_iv
        pe_iv = opt.put_iv

        # Skip if either IV is missing, zero, or anomalous
        if not ce_iv or not pe_iv or ce_iv <= 0 or pe_iv <= 0:
            continue
        if ce_iv > 200 or pe_iv > 200:  # filter clearly erroneous data (200% threshold allows event-day spikes)
            continue

        iv_spreads[opt.strike] = (abs(ce_iv - pe_iv), opt)

    if not iv_spreads:
        return listed_atm_opt, None, listed_atm, 0

    min_spread = min(v[0] for v in iv_spreads.values())

    # If all spreads are identical (e.g. broker returns CE_IV == PE_IV for every
    # strike when market is closed or data is stale), the algorithm has no signal
    # to work with — fall back to listed ATM rather than picking arbitrarily.
    all_spreads = [v[0] for v in iv_spreads.values()]
    if len(set(round(s, 6) for s in all_spreads)) == 1:
        return listed_atm_opt, round(min_spread, 4), listed_atm, 0

    # Cap: true ATM cannot stray more than 3 strikes from listed ATM.
    # A 4+ strike skew (>150 pts on weekly Nifty) is never a real IV-skew signal
    # — it means data quality is too poor to trust the minimisation.
    MAX_SKEW_STRIKES = 3
    filtered = {
        k: v for k, v in iv_spreads.items()
        if abs(k - listed_atm) <= MAX_SKEW_STRIKES * 50
    }
    if not filtered:
        filtered = iv_spreads  # safety: use full set if cap removes everything

    true_atm_strike = min(filtered, key=lambda k: filtered[k][0])
    true_atm_opt    = filtered[true_atm_strike][1]
    iv_spread       = round(filtered[true_atm_strike][0], 4)
    skew_shift      = int(listed_atm - true_atm_strike)

    return true_atm_opt, iv_spread, listed_atm, skew_shift


def get_atm_option(options, spot):
    """
    Legacy fallback: Finds the ATM option from a list of OptionData objects based on spot.
    """
    return min(options, key=lambda x: abs(x.strike - spot))
