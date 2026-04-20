def classify_regime(spot, theoretical_price, max_pain_strike, directional_bias="NEUTRAL", pain_depth=None):
    """
    PRD Module 5: Three-gap regime classification.
    
    Three Gaps:
      Intraday Gap  = Spot - Theoretical_Price  (session mispricing)
      Expiry Gap    = Spot - Max_Pain           (expiry gravity)
      Alignment Gap = Theoretical_Price - Max_Pain (session vs expiry aligned?)

    Returns a dict with regime, bias, confidence, rationale, and the three gap values.
    """
    if spot is None or spot <= 0 or theoretical_price is None or theoretical_price <= 0 or max_pain_strike is None or max_pain_strike <= 0:
        return {
            "regime":        "DATA_ERROR",
            "bias":          "NEUTRAL",
            "confidence":    "NONE",
            "rationale":     "Missing critical market data (Spot/Theo/Pain). Engine paused.",
            "intraday_gap":  0.0,
            "expiry_gap":    0.0,
            "alignment_gap": 0.0,
        }

    intraday_g = round(spot - theoretical_price, 1)
    expiry_g   = round(spot - max_pain_strike, 1)
    align_g    = round(theoretical_price - max_pain_strike, 1)

    # Thresholds (from PRD §8.3)
    INTRADAY_THRESH = 20   # pts to declare intraday mispricing
    EXPIRY_THRESH   = 50   # pts to declare expiry gravity
    ALIGN_THRESH    = 80   # pts to declare divergence between session and expiry

    intraday_rich  = intraday_g >  INTRADAY_THRESH
    intraday_cheap = intraday_g < -INTRADAY_THRESH
    expiry_above   = expiry_g   >  EXPIRY_THRESH
    expiry_below   = expiry_g   < -EXPIRY_THRESH
    fairs_diverged = abs(align_g) > ALIGN_THRESH

    if intraday_rich and expiry_above:
        regime    = "DOUBLE_OVERVALUED"
        bias      = "SHORT"
        rationale = f"Spot +{intraday_g:.0f}pts above intraday fair and +{expiry_g:.0f}pts above Max Pain — both pulling down"
        confidence = "HIGH" if directional_bias == "BEARISH" else "MEDIUM"

    elif intraday_cheap and expiry_below:
        regime    = "DOUBLE_UNDERVALUED"
        bias      = "LONG"
        rationale = f"Spot {intraday_g:.0f}pts below intraday fair and {expiry_g:.0f}pts below Max Pain — both pulling up"
        confidence = "HIGH" if directional_bias == "BULLISH" else "MEDIUM"

    elif intraday_rich and expiry_below:
        regime    = "INTRADAY_TRAP"
        bias      = "SHORT"
        rationale = f"Spot stretched +{intraday_g:.0f}pts above intraday fair; expiry pullup {expiry_g:.0f}pts — likely mean reversion, not trend"
        confidence = "MEDIUM"

    elif intraday_cheap and expiry_above:
        regime    = "INTRADAY_DIP"
        bias      = "LONG"
        rationale = f"Spot {intraday_g:.0f}pts below intraday fair; expiry pull upward +{expiry_g:.0f}pts — accumulation zone"
        confidence = "MEDIUM"

    elif fairs_diverged:
        regime    = "TREND_DAY"
        bias      = "WITH_TREND" if align_g > 0 else "COUNTER_TREND"
        rationale = f"Intraday and expiry fairs diverged by {align_g:.0f}pts — trending session, fade with caution"
        confidence = "MEDIUM"

    else:
        regime    = "EQUILIBRIUM"
        bias      = "NEUTRAL"
        rationale = "Spot near both fair values — no structural edge from derivatives alone"
        confidence = "LOW"

    return {
        "regime":          regime,
        "bias":            bias,
        "confidence":      confidence,
        "rationale":       rationale,
        "intraday_gap":    intraday_g,
        "expiry_gap":      expiry_g,
        "alignment_gap":   align_g,
        "pain_well_depth": pain_depth,
    }
