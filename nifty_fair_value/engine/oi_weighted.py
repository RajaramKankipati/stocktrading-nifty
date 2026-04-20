def oi_weighted_levels(options, active_threshold=0.002):
    """
    PRD Module 4 §7.3: OI-weighted centroid for call side (resistance) and put side (support).

    FIX: Added active OI filter — only strikes with OI > active_threshold fraction of the
    side total are included. Deep ITM/OTM legacy strikes with negligible OI were making the
    corridor 3000+ pts wide, causing `spot_in_oi_corridor` to be trivially True and
    inflating ls_confidence. With 0.2% threshold, only genuinely active strikes contribute.

    Weight = total OI at each strike (filtered). Result is the centre of gravity of all
    writing on each side. Also computes HHI-style OI concentration (higher = stronger level).

    Returns dict with call_resistance, put_support, oi_corridor_width,
    call_oi_concentration, put_oi_concentration.
    """
    total_ce_oi = sum(o.call_oi for o in options)
    total_pe_oi = sum(o.put_oi for o in options)

    ce_cutoff = total_ce_oi * active_threshold
    pe_cutoff = total_pe_oi * active_threshold

    ce_num, ce_den = 0.0, 0.0
    pe_num, pe_den = 0.0, 0.0
    ce_sq_sum, pe_sq_sum = 0.0, 0.0

    for o in options:
        if o.call_oi >= ce_cutoff:
            ce_num    += o.strike * o.call_oi
            ce_den    += o.call_oi
            ce_sq_sum += o.call_oi ** 2

        if o.put_oi >= pe_cutoff:
            pe_num    += o.strike * o.put_oi
            pe_den    += o.put_oi
            pe_sq_sum += o.put_oi ** 2

    call_resistance = round(ce_num / ce_den, 1) if ce_den > 0 else None
    put_support     = round(pe_num / pe_den, 1) if pe_den > 0 else None

    ce_hhi = round(ce_sq_sum / (ce_den ** 2), 6) if ce_den > 0 else 0
    pe_hhi = round(pe_sq_sum / (pe_den ** 2), 6) if pe_den > 0 else 0

    corridor = round(call_resistance - put_support, 1) \
               if call_resistance is not None and put_support is not None else None

    return {
        'call_resistance'      : call_resistance,
        'put_support'          : put_support,
        'oi_corridor_width'    : corridor,
        'call_oi_concentration': ce_hhi,
        'put_oi_concentration' : pe_hhi,
    }
