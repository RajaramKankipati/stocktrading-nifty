def max_pain(options, min_oi_fraction=0.001):
    """
    PRD Module 4 §7.2: Computes the Max Pain strike, the full pain surface, and Pain Well Depth.

    Max Pain = strike where total option writer losses are minimised.
    Pain Well Depth = 2nd-lowest loss / lowest loss.
      > 1.5 = steep well (strong settlement magnet)
      < 1.1 = flat (weak gravitational pull)

    FIX: Added negligible-OI filter. Strikes where BOTH call_oi AND put_oi are below
    min_oi_fraction of total OI are excluded from the pain calculation. Far-OTM strikes
    with near-zero open interest skew the pain surface without representing real positions,
    artificially shifting max pain away from the true settlement anchor.

    Returns
    -------
    max_pain_strike : float
    pain_surface    : dict  {strike: total_writer_loss}
    pain_depth      : float
    """
    total_oi = sum(o.call_oi + o.put_oi for o in options)
    oi_cutoff = total_oi * min_oi_fraction

    # Keep only strikes with meaningful open interest
    active_options = [o for o in options if (o.call_oi + o.put_oi) >= oi_cutoff]

    if not active_options:
        active_options = options  # fallback: use all if filter is too aggressive

    strikes = [o.strike for o in active_options]
    pain = {}

    for target in strikes:
        total_loss = 0
        for o in active_options:
            # Call writers lose if settlement > their strike
            if target > o.strike:
                total_loss += (target - o.strike) * o.call_oi
            # Put writers lose if settlement < their strike
            if target < o.strike:
                total_loss += (o.strike - target) * o.put_oi
        pain[target] = total_loss

    sorted_pain = sorted(pain.items(), key=lambda x: x[1])
    max_pain_strike = sorted_pain[0][0]

    if len(sorted_pain) >= 2:
        if sorted_pain[0][1] == 0:
            pain_depth = 1.0  # flat surface — no well, no gravitational pull
        else:
            pain_depth = round(sorted_pain[1][1] / sorted_pain[0][1], 3)
    else:
        pain_depth = 1.0

    return max_pain_strike, pain, pain_depth
