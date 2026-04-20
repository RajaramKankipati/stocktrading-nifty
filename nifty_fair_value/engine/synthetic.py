import numpy as np


def synthetic_future(atm):
    """
    Single-strike PCP: Strike + (Call LTP - Put LTP).
    Kept for backward compatibility; prefer theoretical_price_pcp() for production use.
    """
    return atm.strike + (atm.call_ltp - atm.put_ltp)


def theoretical_price_pcp(options, true_atm_strike, window=8):
    """
    PRD Module 2 Method A: OI-weighted synthetic forward using put-call parity
    across near-ATM strikes.

    Weight = min(CE_OI, PE_OI) — liquidity-constrained so far-OTM strikes get near-zero weight
    automatically, without manual bandwidth selection.

    Returns
    -------
    theo_price  : float   OI-weighted theoretical price
    n_strikes   : int     Number of contributing strikes (quality indicator)
    weighted_iv : float   OI-weighted average CE IV (ATM IV proxy)
    """
    sorted_opts = sorted(options, key=lambda x: x.strike)
    strikes = [o.strike for o in sorted_opts]

    try:
        idx = strikes.index(true_atm_strike)
    except ValueError:
        idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - true_atm_strike))

    candidates = sorted_opts[max(0, idx - window): idx + window + 1]

    weighted_sum = 0.0
    weight_total = 0.0
    iv_weighted  = 0.0
    n_valid      = 0

    for opt in candidates:
        if opt.call_ltp is None or opt.put_ltp is None:
            continue
        if opt.call_ltp <= 0 or opt.put_ltp <= 0:
            continue
        if opt.call_oi is None or opt.put_oi is None:
            continue

        weight = min(opt.call_oi, opt.put_oi)
        if weight == 0:
            continue

        f_k = opt.strike + opt.call_ltp - opt.put_ltp
        weighted_sum += f_k * weight
        weight_total += weight
        iv_weighted  += (((opt.call_iv or 0.0) + (opt.put_iv or 0.0)) / 2) * weight
        n_valid      += 1

    if weight_total == 0:
        return None, 0, None

    return (
        round(weighted_sum / weight_total, 2),
        n_valid,
        round(iv_weighted / weight_total, 4)
    )


def futures_microprice(bid, ask, bid_qty, ask_qty):
    """
    PRD Module 2 Method B: Order-book microprice of Nifty futures.
    Volume-weighted mid price that tilts toward the side with greater resting quantity.

    Returns
    -------
    microprice    : float
    book_pressure : str   'BUY_HEAVY', 'SELL_HEAVY', or 'BALANCED'
    """
    if bid <= 0 or ask <= 0:
        return None, 'NO_BOOK'

    if bid_qty + ask_qty == 0:
        return round((bid + ask) / 2, 2), 'BALANCED'

    microprice = (ask * bid_qty + bid * ask_qty) / (bid_qty + ask_qty)

    buy_ratio = bid_qty / (bid_qty + ask_qty)
    if buy_ratio > 0.65:
        pressure = 'BUY_HEAVY'
    elif buy_ratio < 0.35:
        pressure = 'SELL_HEAVY'
    else:
        pressure = 'BALANCED'

    return round(microprice, 2), pressure


def breeden_litzenberger(options, spot, r=0.065, T_days=None):
    """
    PRD Module 2 Method C: Recovers risk-neutral density and implied expected value
    from the call price surface (Breeden-Litzenberger theorem).

    Best used on expiry week when the distribution is concentrated and Nifty's
    50-pt strike spacing is fine relative to expected move.

    Returns
    -------
    expected_value  : float   Risk-neutral expected Nifty at expiry
    density         : dict    {strike: normalised probability mass}
    right_tail_prob : float   Probability mass above spot (upside)
    left_tail_prob  : float   Probability mass below spot (downside)
    skew_indicator  : float   right - left (negative = downside skew)
    """
    if T_days is None or T_days <= 0:
        T_days = 1
    T = T_days / 365.0

    call_prices = {int(o.strike): o.call_ltp for o in options if o.call_ltp > 0}
    valid_strikes = sorted(call_prices.keys())

    if len(valid_strikes) < 5:
        return None, {}, None, None, None

    density = {}
    for i in range(1, len(valid_strikes) - 1):
        k    = valid_strikes[i]
        k_up = valid_strikes[i + 1]
        k_dn = valid_strikes[i - 1]

        if k_up not in call_prices or k_dn not in call_prices:
            continue

        # FIX: use per-strike central difference dK — Nifty has non-uniform spacing
        # (50pt near ATM, 100pt far OTM). Uniform dK caused BL density to be wrong
        # by 4x at far-OTM strikes, inflating tail probabilities.
        dK   = (k_up - k_dn) / 2
        d2C  = (call_prices[k_up] - 2 * call_prices[k] + call_prices[k_dn]) / (dK ** 2)
        prob = np.exp(r * T) * d2C * dK

        if prob > 0:  # negative = arbitrage in chain; discard
            density[k] = prob

    if not density:
        return None, {}, None, None, None

    total_mass   = sum(density.values())
    norm_density = {k: p / total_mass for k, p in density.items()}
    expected_val = sum(k * p for k, p in norm_density.items())

    right_tail = sum(p for k, p in norm_density.items() if k > spot)
    left_tail  = sum(p for k, p in norm_density.items() if k < spot)
    skew_ind   = round(right_tail - left_tail, 4)

    return (
        round(expected_val, 2),
        norm_density,
        round(right_tail, 4),
        round(left_tail, 4),
        skew_ind
    )


def validate_theoretical_prices(pcp_price, microprice, bl_price=None):
    """
    PRD §5.5: Compares the three theoretical price methods and flags divergence.
    Large divergence = unreliable market conditions.

    Returns
    -------
    dict with primary anchor, spread, consensus, and reliability status
    """
    prices = {'PCP': pcp_price, 'Microprice': microprice}
    if bl_price:
        prices['BL'] = bl_price

    valid = {k: v for k, v in prices.items() if v is not None and v > 0}
    values = list(valid.values())

    if not values:
        return {'primary': None, 'microprice': microprice, 'bl_price': bl_price,
                'spread': None, 'consensus': None, 'status': 'NO_DATA'}

    spread = max(values) - min(values)
    mean   = sum(values) / len(values)

    if spread < 20:
        status = 'RELIABLE'
    elif spread < 50:
        status = 'CAUTION'
    else:
        status = 'UNRELIABLE — methods diverging, check chain quality'

    return {
        'primary'   : pcp_price,
        'microprice': microprice,
        'bl_price'  : bl_price,
        'spread'    : round(spread, 2),
        'consensus' : round(mean, 2),
        'status'    : status
    }
