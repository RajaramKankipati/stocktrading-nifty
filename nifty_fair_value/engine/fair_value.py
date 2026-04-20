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


def todays_fair_value(market_data, true_atm_opt, theoretical_price, r=0.065):
    """
    PRD Module 3: Today's Fair Value — theoretical price as anchor,
    basis and VWAP as directional context, straddle for intraday range.

    FIX: carry now uses OPTIONS expiry (weekly), not futures expiry (monthly),
    which was overstating carry by up to 5× on expiry week.
    """
    spot         = market_data.spot
    futures_ltp  = market_data.futures
    futures_vwap = market_data.futures_vwap
    oi_chg_pct   = market_data.futures_oi_chg_pct

    # Use OPTIONS expiry for carry — this is the contract being priced
    opt_dte = max(
        (datetime.strptime(market_data.expiry, "%Y-%m-%d").date()
         - datetime.now().date()).days,
        1
    )

    basis        = round(futures_ltp - theoretical_price, 2)
    carry_approx = round(spot * r * (opt_dte / 365), 1)
    excess_basis = round(basis - carry_approx, 2)
    vwap_dev     = round(theoretical_price - futures_vwap, 2) if futures_vwap > 0 else 0.0
    spot_dev     = round(spot - theoretical_price, 2)

    NOISE = 15
    if abs(spot_dev) <= NOISE:
        spot_signal = "AT FAIR — no mispricing edge"
    elif spot_dev > 0:
        spot_signal = f"SPOT RICH by {spot_dev:.0f} pts — short candidate"
    else:
        spot_signal = f"SPOT CHEAP by {abs(spot_dev):.0f} pts — long candidate"

    if excess_basis > 15 and oi_chg_pct > 0:
        bias = "BULLISH — premium + OI build"
    elif excess_basis < -15 and oi_chg_pct > 0:
        bias = "BEARISH — discount + OI build"
    elif excess_basis > 15 and oi_chg_pct < 0:
        bias = "WEAK BULL — premium but OI unwinding"
    elif excess_basis < -15 and oi_chg_pct < 0:
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
        'vwap_deviation'   : vwap_dev,
        'spot_deviation'   : spot_dev,
        'straddle_value'   : sr['straddle_value'],
        'intraday_upper'   : sr['expected_upper'],
        'intraday_lower'   : sr['expected_lower'],
        'spot_signal'      : spot_signal,
        'directional_bias' : bias,
        'oi_change_pct'    : oi_chg_pct,
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
    Adaptive average of Futures, Synthetic, VWAP. Excludes missing/zero values.
    Kept for backward-compat with signals.py.
    """
    values = [v for v in [futures, synthetic, vwap] if v is not None and v >= 10]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def expiry_fair(call_oi_level, put_oi_level, max_pain):
    """
    Expiry Fair Value scalar used for LS Factor computation.

    FIX: ATM strike removed. It is approximately equal to spot by definition and
    systematically dampened LS by ~33% (pulled expiry_fair toward spot).

    Max Pain is the settlement anchor (60% weight) — this is where option writer
    losses are minimised and where price gravitates on expiry day.
    OI midpoint (40%) adds the structural support/resistance layer.

    If either OI level is missing, falls back to max_pain alone.
    """
    if not max_pain:
        return 0.0
    if not call_oi_level or not put_oi_level:
        return round(max_pain, 2)
    oi_mid = (call_oi_level + put_oi_level) / 2
    return round(max_pain * 0.6 + oi_mid * 0.4, 2)


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
                  spot_in_oi_corridor, pcr, data_reliable=True):
    """
    Confidence score (0–5) for the LS direction signal.

    Checks:
      1. Futures directional bias agrees (basis + OI change)
      2. Pain well depth > 1.5  (strong expiry magnet)
      3. Intraday gap agrees    (spot cheap/rich vs theoretical)
      4. Spot inside OI corridor (dealer zone = cleaner gravity)
      5. PCR aligned            (> 1.1 long / < 0.9 short)

    FIX: `in_oi_corridor` defaults False (not True) when data is unavailable —
    fail-safe, not fail-open. Missing data should never inflate confidence.

    FIX: `data_reliable` gate — if theoretical price validation is UNRELIABLE
    or chain has ATM data issues, the score is capped at 1 regardless of other checks.
    """
    direction = ls_direction(ls)
    is_long   = direction in ("LONG", "WEAK LONG")
    is_short  = direction in ("SHORT", "WEAK SHORT")

    bias_upper = (directional_bias or "").upper()

    checks = {
        'futures_bias': (
            (is_long  and "BULL" in bias_upper) or
            (is_short and "BEAR" in bias_upper)
        ),
        'strong_magnet': bool(pain_depth and pain_depth > 1.5),
        'intraday_aligned': (
            (is_long  and intraday_gap is not None and intraday_gap < -15) or
            (is_short and intraday_gap is not None and intraday_gap >  15)
        ),
        # FIX: default False — missing data must not boost confidence
        'in_oi_corridor': bool(spot_in_oi_corridor) if spot_in_oi_corridor is not None else False,
        'pcr_aligned': (
            (is_long  and pcr is not None and pcr > 1.1) or
            (is_short and pcr is not None and pcr < 0.9)
        ),
    }

    raw_score = sum(1 for v in checks.values() if v)

    # FIX: unreliable theoretical price or bad chain data caps score at 1
    score = min(raw_score, 1) if not data_reliable else raw_score
    level = "HIGH" if score >= 4 else "MEDIUM" if score >= 2 else "LOW"

    return {
        'direction'    : direction,
        'score'        : score,
        'max_score'    : 5,
        'level'        : level,
        'checks'       : checks,
        'data_reliable': data_reliable,
    }


def decision_point(ls, confidence):
    """
    Converts LS Factor + confidence into a single actionable decision.

    LS sets the bias. Confidence validates the structural setup.
    Intraday alignment (IG) is the entry trigger — without it, WAIT not SKIP.

    FIX: If data_reliable is False, the system can never produce ENTER —
    the best outcome is WAIT, because the structural signal may be correct
    but the theoretical price anchor is untrustworthy.
    """
    if ls is None:
        return {'action': 'NO TRADE', 'detail': 'No data', 'style': 'neutral'}

    direction     = confidence.get('direction', 'FLAT')
    score         = confidence.get('score', 0)
    checks        = confidence.get('checks', {})
    data_reliable = confidence.get('data_reliable', True)
    intraday_ok   = checks.get('intraday_aligned', False)

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

    # Unreliable data gate: can warn but never ENTER
    if not data_reliable:
        if abs_ls > 0.35 and score >= 3:
            return {
                'action': f'WAIT — {side} BIAS (DATA UNRELIABLE)',
                'detail': 'Structural setup suggests {side} but theoretical price validation failed — do not enter until data is confirmed'.format(side=side),
                'style' : 'wait',
            }
        return {
            'action': 'SKIP',
            'detail': 'Theoretical price is unreliable — no trade until methods converge',
            'style' : 'skip',
        }

    # Strong gravity  |LS| > 0.35
    if abs_ls > 0.35:
        if score >= 4 and intraday_ok:
            return {
                'action': f'ENTER {side}',
                'detail': f'{score}/5 signals confirm — expiry pull and session both aligned',
                'style' : 'strong',
            }
        if score >= 4 and not intraday_ok:
            return {
                'action': f'WAIT — STRONG {side} BIAS',
                'detail': f'{score}/5 structural signals confirmed — waiting for intraday gap (IG) to turn',
                'style' : 'wait',
            }
        if score == 3 and intraday_ok:
            return {
                'action': f'ENTER {side} — REDUCED SIZE',
                'detail': '3/5 signals — enter smaller, stop at theoretical price',
                'style' : 'moderate',
            }
        if score == 3 and not intraday_ok:
            return {
                'action': f'WAIT — {side} BIAS',
                'detail': '3/5 structural confirms but session gap not yet aligned — enter when IG turns',
                'style' : 'wait',
            }
        return {
            'action': 'SKIP',
            'detail': f'Gravity shows {side} but only {score}/5 signals confirm — setup not mature',
            'style' : 'skip',
        }

    # Weak gravity  0.15 < |LS| ≤ 0.35
    if score >= 4 and intraday_ok:
        return {
            'action': f'WATCH — {side}',
            'detail': f'All {score} signals agree but gravity thin (LS {ls:+.3f}) — wait for LS > 0.35 to size',
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
