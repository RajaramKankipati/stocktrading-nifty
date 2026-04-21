from datetime import datetime


def straddle_range(true_atm_opt):
    """
    PRD Module 4 §7.4: ATM straddle value and 1-sigma expected move range.
    """
    ce_ltp = true_atm_opt.call_ltp
    pe_ltp = true_atm_opt.put_ltp
    strike = true_atm_opt.strike

    straddle      = round(ce_ltp + pe_ltp, 1)
    upper         = round(strike + straddle, 1)
    lower         = round(strike - straddle, 1)
    synthetic_fwd = round(strike + ce_ltp - pe_ltp, 2)
    skew_pull     = round(synthetic_fwd - strike, 2)

    return {
        'straddle_value': straddle,
        'expected_upper': upper,
        'expected_lower': lower,
        'synthetic_fwd' : synthetic_fwd,
        'skew_pull'     : skew_pull,
    }


def todays_fair_value(market_data, true_atm_opt, theoretical_price, r=0.065, d_yield=0.013):
    """
    PRD Module 3: Today's Fair Value — theoretical price as anchor,
    basis and VWAP as directional context, straddle for intraday range.

    FIX: basis now = futures_ltp - spot (direct cash-futures spread).
    Previous basis = futures_ltp - theoretical_price mixed monthly futures
    (DTE≈8) with weekly PCP (DTE=1), giving a wrong anchor.

    FIX: carry uses FUTURES expiry DTE with net yield (r - dividend_yield).
    Nifty dividend yield ≈1.3%, so effective carry ≈3.9%/yr.
    Previous carry used opt_dte=1 (≈4pts) when futures DTE may be 8 (≈28pts).

    FIX: vwap_deviation is None when Groww doesn't return VWAP (always 0).
    Previously 0.0 was silently surfaced as if it were a real signal.
    """
    spot         = market_data.spot
    futures_ltp  = market_data.futures
    futures_vwap = market_data.futures_vwap
    oi_chg_pct   = market_data.futures_oi_chg_pct

    # Use FUTURES expiry for carry — basis is the cash-futures spread
    fut_dte = max(
        (datetime.strptime(market_data.futures_expiry, "%Y-%m-%d").date()
         - datetime.now().date()).days,
        1
    )

    basis        = round(futures_ltp - spot, 2)
    carry_approx = round(spot * (r - d_yield) * (fut_dte / 365), 1)
    excess_basis = round(basis - carry_approx, 2)
    vwap_dev     = round(theoretical_price - futures_vwap, 2) if futures_vwap > 0 else None
    spot_dev     = round(spot - theoretical_price, 2)

    NOISE = 15
    if abs(spot_dev) <= NOISE:
        spot_signal = "AT FAIR — no mispricing edge"
    elif spot_dev > 0:
        spot_signal = f"SPOT RICH by {spot_dev:.0f} pts — short candidate"
    else:
        spot_signal = f"SPOT CHEAP by {abs(spot_dev):.0f} pts — long candidate"

    # Threshold 10 pts (previously 15): near-expiry carry is tiny (~4 pts at DTE=1)
    # so 15 was unreachable most of the time. 10 is still above normal noise.
    BIAS_THRESH = 10
    if excess_basis > BIAS_THRESH and oi_chg_pct > 0:
        bias = "BULLISH — premium + OI build"
    elif excess_basis < -BIAS_THRESH and oi_chg_pct > 0:
        bias = "BEARISH — discount + OI build"
    elif excess_basis > BIAS_THRESH and oi_chg_pct < 0:
        bias = "WEAK BULL — premium but OI unwinding"
    elif excess_basis < -BIAS_THRESH and oi_chg_pct < 0:
        bias = "WEAK BEAR — discount but OI unwinding"
    else:
        bias = "NEUTRAL — no clear futures signal"

    sr = straddle_range(true_atm_opt)

    return {
        'spot'             : spot,
        'theoretical_price': theoretical_price,
        'futures_ltp'      : futures_ltp,
        'futures_vwap'     : futures_vwap,
        'basis'            : basis,
        'carry_approx'     : carry_approx,
        'excess_basis'     : excess_basis,
        'vwap_deviation'   : vwap_dev,   # None when Groww doesn't provide VWAP
        'spot_deviation'   : spot_dev,
        'straddle_value'   : sr['straddle_value'],
        'intraday_upper'   : sr['expected_upper'],
        'intraday_lower'   : sr['expected_lower'],
        'spot_signal'      : spot_signal,
        'directional_bias' : bias,
        'oi_change_pct'    : oi_chg_pct,
        'fut_dte'          : fut_dte,
    }


def expiry_fair_value(options, true_atm_opt, spot, max_pain_strike, pain_depth, oi_levels):
    """
    PRD Module 4 §7.5: Expiry Fair Value structured output.
    """
    sr = straddle_range(true_atm_opt)

    gap_to_max_pain   = round(spot - max_pain_strike, 1)
    GRAVITY_THRESHOLD = 50

    if abs(gap_to_max_pain) <= GRAVITY_THRESHOLD:
        gravity = "NEAR MAX PAIN — expiry equilibrium zone"
    elif gap_to_max_pain > 0:
        gravity = f"ABOVE MAX PAIN by {gap_to_max_pain:.0f} pts — downside gravitational pull"
    else:
        gravity = f"BELOW MAX PAIN by {abs(gap_to_max_pain):.0f} pts — upside gravitational pull"

    call_res = oi_levels.get('call_resistance')
    put_sup  = oi_levels.get('put_support')
    in_corridor = (put_sup <= spot <= call_res) \
                  if call_res is not None and put_sup is not None else None
    in_range = sr['expected_lower'] <= spot <= sr['expected_upper']

    return {
        'max_pain'           : max_pain_strike,
        'pain_well_depth'    : pain_depth,
        'call_oi_resistance' : call_res,
        'put_oi_support'     : put_sup,
        'oi_corridor_width'  : oi_levels.get('oi_corridor_width'),
        'straddle_value'     : sr['straddle_value'],
        'expected_upper'     : sr['expected_upper'],
        'expected_lower'     : sr['expected_lower'],
        'synthetic_fwd'      : sr['synthetic_fwd'],
        'skew_pull'          : sr['skew_pull'],
        'gap_to_max_pain'    : gap_to_max_pain,
        'gravity_signal'     : gravity,
        'spot_in_oi_corridor': in_corridor,
        'spot_in_straddle'   : in_range,
    }


def today_fair(futures, synthetic, vwap):
    """
    Session fair value for backward-compat with signals.py.
    Groww does not provide futures VWAP (always 0), so averaging futures + synthetic
    was mixing a basis-inflated price with a cash-equivalent price.
    Return synthetic (OI-weighted PCP theoretical price) directly — it IS the
    session fair value and is consistent with the intraday gap and confidence checks.
    """
    if synthetic is not None and synthetic >= 10:
        return synthetic
    if futures is not None and futures >= 10:
        return futures
    return 0.0


def expiry_fair(call_oi_level, put_oi_level, max_pain):
    """
    Expiry Fair Value scalar used for LS Factor computation.

    Per PRD §7.5, Max Pain is the sole expiry gravity anchor — the three
    measures (Max Pain, OI Levels, Straddle) are kept separate in the structured
    expiry_fair_value() output. The previous composite (max_pain×0.6 + oi_mid×0.4)
    introduced systematic downside bias: Nifty's structural put skew always pulls
    the OI centroid well below spot, making LS persistently negative.
    """
    if not max_pain:
        return 0.0
    return round(float(max_pain), 2)


def ls_factor(expiry_fv, spot, straddle_value=None):
    """
    LS Factor = (Expiry Fair − Spot) / normaliser

    FIX: When straddle_value is provided, use it as the normaliser instead of
    the hardcoded 200. The straddle already accounts for current volatility level,
    making thresholds scale-invariant across different Nifty levels and IV regimes.

    With straddle normalisation:
      LS > 0.35 means expiry anchor is 35% of a 1σ move above spot — clear pull up.
      LS < 0.35 at Nifty 18000 and Nifty 28000 both mean the same proportional gravity.

    Falls back to /200 if straddle unavailable (backward compat).
    """
    if not expiry_fv or not spot:
        return 0.0
    gap = expiry_fv - spot
    denom = straddle_value if (straddle_value and straddle_value > 20) else 200
    return round(gap / denom, 4)


def ls_direction(ls):
    """
    Converts LS Factor to directional label.
    Thresholds work for both straddle-normalised and fixed-200 LS.
    """
    if ls is None:
        return "FLAT"
    if ls > 0.35:
        return "LONG"
    if ls > 0.15:
        return "WEAK LONG"
    if ls < -0.35:
        return "SHORT"
    if ls < -0.15:
        return "WEAK SHORT"
    return "FLAT"


def ls_confidence(ls, directional_bias, intraday_gap, pain_depth,
                  expiry_gap, pcr, data_reliable=True, regime_bias=None,
                  call_oi_hhi=None, put_oi_hhi=None):
    """
    Confidence score (0–5) for the LS direction signal.

    Checks:
      1. futures_bias   — Futures basis + OI change agree with LS direction
      2. strong_magnet  — Pain well depth > 1.5 (steep settlement gravity) AND
                          at least one OI side is concentrated (HHI > 0.08).
                          Scattered OI means even a steep pain well has no
                          real pin wall backing it.
      3. regime_aligned — Three-gap regime bias (LONG/SHORT) matches LS direction.
                          Replaces the broken intraday_aligned check (PCP tracks
                          spot within 3-8 pts by construction → ±20pt threshold
                          was structurally unreachable).
      4. expiry_pull    — Expiry gap > 30 pts in LS direction (spot measurably
                          displaced from Max Pain). Replaces in_oi_corridor, which
                          was trivially True after OTM-only filter (1,419pt corridor).
      5. pcr_aligned    — PCR > 1.1 for LONG / < 0.9 for SHORT

    `expiry_pull` defaults False when data unavailable (fail-safe).
    `data_reliable=False` caps score at 1 (unreliable anchor = unreliable signal).
    """
    direction = ls_direction(ls)
    is_long   = direction in ("LONG", "WEAK LONG")
    is_short  = direction in ("SHORT", "WEAK SHORT")

    bias_upper = (directional_bias or "").upper()

    # OI concentration: HHI > 0.08 means OI is concentrated at a few strikes
    # (real pin walls), not scattered noise. When HHI data is absent, don't penalise.
    OI_CONC_THRESH = 0.08
    oi_concentrated = (
        (call_oi_hhi is None and put_oi_hhi is None) or
        (call_oi_hhi is not None and call_oi_hhi > OI_CONC_THRESH) or
        (put_oi_hhi  is not None and put_oi_hhi  > OI_CONC_THRESH)
    )

    checks = {
        'futures_bias': (
            (is_long  and "BULL" in bias_upper) or
            (is_short and "BEAR" in bias_upper)
        ),
        'strong_magnet': bool(pain_depth and pain_depth > 1.5) and oi_concentrated,
        # Three-gap regime bias confirms LS direction.
        # DOUBLE_UNDERVALUED/INTRADAY_DIP → 'LONG'; DOUBLE_OVERVALUED/INTRADAY_TRAP → 'SHORT'.
        # TREND_DAY uses 'COUNTER_TREND' (session < expiry anchor → upside pull → LONG)
        # and 'WITH_TREND' (session > expiry anchor → downside gravity → SHORT).
        # Previously these two TREND_DAY labels were never mapped → check never fired
        # for TREND_DAY regime, which is the most common expiry-week state.
        'regime_aligned': (
            (is_long  and regime_bias in ('LONG',  'COUNTER_TREND')) or
            (is_short and regime_bias in ('SHORT', 'WITH_TREND'))
        ),
        # Spot measurably displaced from Max Pain in LS direction (>30 pts)
        # Replaces in_oi_corridor which was trivially True by construction
        'expiry_pull': (
            (is_long  and expiry_gap is not None and expiry_gap < -30) or
            (is_short and expiry_gap is not None and expiry_gap >  30)
        ),
        'pcr_aligned': (
            (is_long  and pcr is not None and pcr > 1.1) or
            (is_short and pcr is not None and pcr < 0.9)
        ),
    }

    raw_score = sum(1 for v in checks.values() if v)

    # Unreliable theoretical price or bad chain data caps score at 1
    score = min(raw_score, 1) if not data_reliable else raw_score
    level = "HIGH" if score >= 4 else "MEDIUM" if score >= 2 else "LOW"

    # Detect opposing signals that actively contradict LS direction.
    # These don't affect the score — they inform the CONFLICTED decision state.
    # PCR ONLY — futures basis is excluded because excess_basis is frequently
    # negative in the early session (futures trade at a carry discount before
    # institutional flows load in), which is a structural artifact of timing,
    # not a genuine directional signal. Using it caused false CONFLICTED states
    # in the morning when LS = LONG + PCR bullish but futures basis appeared bearish.
    conflict_sources = []
    if is_short and pcr is not None and pcr > 1.1:
        conflict_sources.append(f"PCR {pcr:.2f} bullish")
    if is_long  and pcr is not None and pcr < 0.9:
        conflict_sources.append(f"PCR {pcr:.2f} bearish")

    return {
        'direction'       : direction,
        'score'           : score,
        'max_score'       : 5,
        'level'           : level,
        'checks'          : checks,
        'data_reliable'   : data_reliable,
        'conflict'        : len(conflict_sources) > 0,
        'conflict_sources': conflict_sources,
    }


def decision_point(ls, confidence):
    """
    Converts LS Factor + confidence into a single actionable decision.

    LS sets the direction and magnitude. Score gates conviction.
    The old design required intraday_aligned (check #3) as a hard entry trigger,
    but that check was structurally unreachable (PCP theoretical ≈ spot by
    construction). Removed: entry now gates on score alone.

    CONFLICTED (checked before gravity gates):
      LS direction actively opposed by PCR or futures basis — two market
      forces pointing in opposite directions. Trading into this is picking
      a side before the conflict is resolved. Wait for one force to capitulate.

    Strong gravity  |LS| > 0.35:
      score 4–5 → ENTER (high conviction)
      score 3   → ENTER REDUCED SIZE (moderate conviction)
      score 2   → WAIT — bias confirmed but needs more structure
      score ≤ 1 → SKIP

    Weak gravity  0.15 < |LS| ≤ 0.35:
      score ≥ 3 → WATCH — wait for LS to cross 0.35 before sizing
      score < 3 → NO TRADE

    data_reliable=False blocks ENTER at any score; best outcome is WAIT.
    """
    if ls is None:
        return {'action': 'NO TRADE', 'detail': 'No data', 'style': 'neutral'}

    direction        = confidence.get('direction', 'FLAT')
    score            = confidence.get('score', 0)
    data_reliable    = confidence.get('data_reliable', True)
    conflict         = confidence.get('conflict', False)
    conflict_sources = confidence.get('conflict_sources', [])

    abs_ls   = abs(ls)
    is_long  = direction in ('LONG', 'WEAK LONG')
    is_short = direction in ('SHORT', 'WEAK SHORT')
    side     = 'LONG' if is_long else 'SHORT'

    if direction == 'FLAT':
        return {
            'action': 'NO TRADE',
            'detail': 'LS near zero — no expiry gravity to trade',
            'style' : 'neutral',
        }

    # Conflicted: LS direction opposed by PCR or futures basis.
    # |LS| > 0.15 ensures there is at least weak gravity to conflict with.
    # This fires before the gravity gates — even strong LS must wait for
    # the opposing force to resolve before entry is warranted.
    if conflict and abs_ls > 0.15:
        reason = " + ".join(conflict_sources)
        return {
            'action': f'WAIT — CONFLICTED ({side})',
            'detail': f'LS points {side} ({ls:+.3f}) but {reason} — opposing forces, wait for one side to capitulate',
            'style' : 'conflicted',
        }

    # Unreliable data gate: structural signal may be right but anchor is broken
    if not data_reliable:
        if abs_ls > 0.35 and score >= 3:
            return {
                'action': f'WAIT — {side} BIAS (DATA UNRELIABLE)',
                'detail': f'Structural setup suggests {side} but theoretical price validation failed',
                'style' : 'wait',
            }
        return {
            'action': 'SKIP',
            'detail': 'Theoretical price is unreliable — no trade until methods converge',
            'style' : 'skip',
        }

    # Strong gravity  |LS| > 0.35
    if abs_ls > 0.35:
        if score >= 4:
            return {
                'action': f'ENTER {side}',
                'detail': f'{score}/5 signals confirm — strong expiry gravity with structural backing',
                'style' : 'strong',
            }
        if score == 3:
            return {
                'action': f'ENTER {side} — REDUCED SIZE',
                'detail': f'3/5 signals confirm — enter smaller, exit at max pain or theoretical price',
                'style' : 'moderate',
            }
        if score == 2:
            return {
                'action': f'WAIT — {side} BIAS',
                'detail': f'Strong gravity (LS {ls:+.3f}) but only 2/5 confirms — wait for regime or PCR to align',
                'style' : 'wait',
            }
        return {
            'action': 'SKIP',
            'detail': f'Gravity shows {side} but only {score}/5 signals confirm — setup not mature',
            'style' : 'skip',
        }

    # Weak gravity  0.15 < |LS| ≤ 0.35
    if score >= 3:
        return {
            'action': f'WATCH — {side}',
            'detail': f'{score}/5 signals agree but gravity thin (LS {ls:+.3f}) — wait for LS > 0.35 before sizing',
            'style' : 'watch',
        }

    return {
        'action': 'NO TRADE',
        'detail': f'Gravity weak (LS {ls:+.3f}) with only {score}/5 confirms',
        'style' : 'neutral',
    }


def calculate_straddle_range(atm_call_ltp, atm_put_ltp):
    """Returns ATM straddle premium — market's 1σ expected move."""
    if not atm_call_ltp or not atm_put_ltp:
        return 0.0
    return round(atm_call_ltp + atm_put_ltp, 1)


def basis_analysis(futures_ltp, theoretical_price, futures_vwap, spot,
                   oi_chg_pct=0, days_to_expiry=7, r=0.065):
    """PRD Module 3 legacy basis analysis (kept for backward-compat)."""
    if not futures_ltp or not theoretical_price or not spot:
        return {
            "basis": 0.0, "carry_approx": 0.0, "excess_basis": 0.0,
            "vwap_deviation": 0.0, "spot_deviation": 0.0,
            "spot_signal": "DATA_MISSING", "directional_bias": "NEUTRAL",
        }

    basis        = round(futures_ltp - theoretical_price, 2)
    carry_approx = round(spot * r * (days_to_expiry / 365), 1)
    excess_basis = round(basis - carry_approx, 2)
    vwap_dev     = round(theoretical_price - futures_vwap, 2) if futures_vwap and futures_vwap > 0 else 0.0
    spot_dev     = round(spot - theoretical_price, 2)

    if abs(spot_dev) <= 15:
        spot_signal = "AT FAIR"
    elif spot_dev > 0:
        spot_signal = f"SPOT RICH +{spot_dev:.0f}pts"
    else:
        spot_signal = f"SPOT CHEAP {spot_dev:.0f}pts"

    if excess_basis > 15 and oi_chg_pct > 0:
        directional_bias = "BULLISH"
    elif excess_basis < -15 and oi_chg_pct > 0:
        directional_bias = "BEARISH"
    elif excess_basis > 15 and oi_chg_pct < 0:
        directional_bias = "WEAK BULL"
    elif excess_basis < -15 and oi_chg_pct < 0:
        directional_bias = "WEAK BEAR"
    else:
        directional_bias = "NEUTRAL"

    return {
        "basis": basis, "carry_approx": carry_approx, "excess_basis": excess_basis,
        "vwap_deviation": vwap_dev, "spot_deviation": spot_dev,
        "spot_signal": spot_signal, "directional_bias": directional_bias,
    }
