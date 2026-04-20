def pcr_oi(total_ce_oi, total_pe_oi):
    """
    Calculates the Put-Call Ratio based on Open Interest.
    """
    return round(total_pe_oi / total_ce_oi, 4) if total_ce_oi else None

def pcr_notional(options, lot_size=65):
    """
    Calculates the Put-Call Ratio based on Notional Value (OI * LTP * Lot Size).
    """
    ce_notional = sum(o.call_oi * o.call_ltp * lot_size for o in options)
    pe_notional = sum(o.put_oi * o.put_ltp * lot_size for o in options)
    
    return pe_notional / ce_notional if ce_notional else 0.0
