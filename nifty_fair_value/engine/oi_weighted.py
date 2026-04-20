def oi_weighted_levels(options, spot=None, active_threshold=0.002):
    """
    PRD Module 4 §7.3: OI-weighted centroid for call resistance and put support.

    FIX: Now filters to OTM-only options when spot is provided.
    - Call resistance: centroid of CALL OI at strikes ABOVE spot only.
    - Put support:     centroid of PUT  OI at strikes BELOW spot only.

    ITM options are not resistance/support — a call strike below spot is already
    exercisable and represents a delta hedge, not a writing wall. Including all
    strikes was making the corridor 1300+ pts wide (5× the straddle), causing
    `spot_in_oi_corridor` to be trivially True and giving a free confidence point
    regardless of market structure.

    Active OI filter (0.2% of side total) is applied after the OTM filter to
    exclude negligible far-OTM positions.
    """
    total_ce_oi = sum(o.call_oi for o in options)
    total_pe_oi = sum(o.put_oi for o in options)

    ce_cutoff = total_ce_oi * active_threshold
    pe_cutoff = total_pe_oi * active_threshold

    ce_num, ce_den = 0.0, 0.0
    pe_num, pe_den = 0.0, 0.0
    ce_sq_sum, pe_sq_sum = 0.0, 0.0

    for o in options:
        # OTM call filter: only strikes above spot contribute to call resistance
        is_otm_call = (spot is None) or (o.strike > spot)
        if is_otm_call and o.call_oi >= ce_cutoff:
            ce_num    += o.strike * o.call_oi
            ce_den    += o.call_oi
            ce_sq_sum += o.call_oi ** 2

        # OTM put filter: only strikes below spot contribute to put support
        is_otm_put = (spot is None) or (o.strike < spot)
        if is_otm_put and o.put_oi >= pe_cutoff:
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
