from config import settings


# ── IV regime thresholds (Nifty historical norms) ──────────────────────────
_IV_LOW      = 12.0   # below = cheap to buy
_IV_NORMAL   = 18.0   # 12–18 = standard conditions
_IV_ELEVATED = 25.0   # 18–25 = expensive, prefer OTM or sell


def iv_regime(atm_iv):
    """Classifies ATM IV level relative to Nifty norms."""
    if atm_iv is None or atm_iv <= 0:
        return 'UNKNOWN'
    if atm_iv < _IV_LOW:
        return 'LOW'
    if atm_iv < _IV_NORMAL:
        return 'NORMAL'
    if atm_iv < _IV_ELEVATED:
        return 'ELEVATED'
    return 'HIGH'


def _find_strike(options, atm_strike, side, offset_strikes):
    """
    Finds the option `offset_strikes` steps away from ATM in the direction of `side`.
    CE: moves up the chain (higher strikes = OTM calls)
    PE: moves down the chain (lower strikes = OTM puts)
    Uses actual sorted chain so spacing differences (50pt vs 100pt) are handled correctly.
    """
    sorted_opts = sorted(options, key=lambda x: x.strike)
    atm_idx = next((i for i, o in enumerate(sorted_opts) if o.strike == atm_strike), None)

    if atm_idx is None:
        # fallback: find nearest
        atm_idx = min(range(len(sorted_opts)),
                      key=lambda i: abs(sorted_opts[i].strike - atm_strike))

    if side == 'CE':
        target_idx = min(atm_idx + offset_strikes, len(sorted_opts) - 1)
    else:
        target_idx = max(atm_idx - offset_strikes, 0)

    return sorted_opts[target_idx]


def options_strategy(ls, ls_conf, atm_iv, opt_dte, options, atm_strike, spot, lot_size=None):
    """
    Converts LS Factor + confidence into an actionable Nifty options strategy.

    Outputs:
      strategy      : human-readable action (BUY CE — ATM / BUY PE — 1-OTM / SELL STRADDLE / NO TRADE)
      side          : CE / PE / STRADDLE / None
      strike        : recommended strike (int)
      premium       : current LTP at that strike
      max_loss_lot  : premium × lot_size (max loss per 1 lot for BUY trades)
      size_note     : FULL / REDUCED / SMALL
      iv_regime     : LOW / NORMAL / ELEVATED / HIGH
      iv_value      : raw ATM IV %
      rationale     : explanation string
      dte_warning   : warning string or None
      style         : CSS hint (strong / moderate / sell / neutral / warn)

    Rules:
      1. DTE = 0  → block all BUY; only SELL if IV HIGH + score >= 3
      2. DTE = 1  → BUY only on strong signal (|LS| > 0.35, score >= 4)
      3. IV HIGH/ELEVATED → prefer OTM (offset=1) or sell straddle
      4. score < 2 → NO TRADE regardless of LS magnitude
      5. FLAT (|LS| < 0.15) → consider SELL STRADDLE if IV elevated + DTE >= 2
      6. data_reliable=False → NO TRADE (theoretical price untrustworthy)
    """
    ls_lot = lot_size or getattr(settings, 'LOT_SIZE', 25)

    if ls is None:
        ls = 0.0

    direction    = ls_conf.get('direction', 'FLAT')
    score        = ls_conf.get('score', 0)
    data_reliable = ls_conf.get('data_reliable', True)
    abs_ls       = abs(ls)
    iv_reg       = iv_regime(atm_iv)
    is_long      = direction in ('LONG', 'WEAK LONG')
    is_short     = direction in ('SHORT', 'WEAK SHORT')
    side         = 'CE' if is_long else ('PE' if is_short else None)

    # ── DTE context ────────────────────────────────────────────────────────
    if opt_dte == 0:
        dte_warning = 'EXPIRY DAY — theta destroys buyers. Only sell premium on strong setups.'
    elif opt_dte == 1:
        dte_warning = 'ONE DAY LEFT — theta accelerating. Buy only on strongest signal (score 4–5).'
    else:
        dte_warning = None

    # ── Guard: unreliable data ──────────────────────────────────────────────
    if not data_reliable:
        return _result(
            'NO TRADE', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            'Theoretical price unreliable — option pricing anchor unavailable', dte_warning, 'neutral'
        )

    # ── Guard: CONFLICTED — LS direction actively opposed by PCR ───────────
    if ls_conf.get('conflict'):
        sources = " + ".join(ls_conf.get('conflict_sources', []))
        return _result(
            'WAIT — CONFLICTED', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            f'LS points {direction} but {sources} — wait for opposing forces to resolve',
            dte_warning, 'neutral'
        )

    # ── Expiry day logic ────────────────────────────────────────────────────
    if opt_dte == 0:
        if side and abs_ls > 0.35 and score >= 3 and iv_reg in ('ELEVATED', 'HIGH'):
            # Selling against the direction on expiry: e.g. spot above max pain → sell CE
            sell_side  = 'PE' if is_long else 'CE'   # sell the side pointing wrong
            opt        = _find_strike(options, atm_strike, sell_side, 0)
            prem       = opt.call_ltp if sell_side == 'CE' else opt.put_ltp
            return _result(
                f'SELL {sell_side} — ATM (Expiry)', sell_side, opt.strike, prem,
                _max_loss(prem, ls_lot), 'REDUCED',
                iv_reg, atm_iv,
                f'Expiry day — selling premium at {opt.strike} (IV {atm_iv:.1f}%, gravity {direction})',
                dte_warning, 'sell'
            )
        return _result(
            'NO TRADE', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            'Expiry day — theta too severe for option buying', dte_warning, 'neutral'
        )

    # ── FLAT: no directional gravity ───────────────────────────────────────
    if not side or abs_ls < 0.15:
        if iv_reg in ('ELEVATED', 'HIGH') and opt_dte >= 2 and score >= 2:
            opt      = _find_strike(options, atm_strike, 'CE', 0)
            ce_prem  = opt.call_ltp
            pe_prem  = opt.put_ltp
            combined = round(ce_prem + pe_prem, 2)
            result   = _result(
                'SELL STRADDLE — ATM', 'STRADDLE', atm_strike, combined,
                None, 'REDUCED',
                iv_reg, atm_iv,
                f'No directional gravity (LS {ls:+.3f}) | IV elevated at {atm_iv:.1f}% — collect ₹{combined} credit',
                dte_warning, 'sell'
            )
            result['ce_premium']      = round(ce_prem, 2)
            result['pe_premium']      = round(pe_prem, 2)
            result['breakeven_upper'] = round(atm_strike + combined, 1)
            result['breakeven_lower'] = round(atm_strike - combined, 1)
            result['max_profit_lot']  = round(combined * ls_lot, 0)
            return result
        return _result(
            'NO TRADE', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            f'No directional gravity — LS {ls:+.3f} is within FLAT zone', dte_warning, 'neutral'
        )

    # ── Minimum conviction threshold ───────────────────────────────────────
    if score < 2:
        return _result(
            'NO TRADE', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            f'Signal insufficient ({score}/5 confirms) — too risky to buy options', dte_warning, 'neutral'
        )

    # ── DTE=1: only trade on strongest signal ──────────────────────────────
    if opt_dte == 1 and (abs_ls <= 0.35 or score < 4):
        return _result(
            'NO TRADE', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            f'One day to expiry — need |LS| > 0.35 and score 4–5 to buy. Got LS {ls:+.3f}, {score}/5',
            dte_warning, 'neutral'
        )

    # ── Weak gravity: WATCH only (align with decision_point which requires WATCH ≥3) ──
    # 0.15 < |LS| ≤ 0.35: gravity exists but insufficient to size into. Tell trader
    # what to watch for so they're ready when gravity strengthens.
    if abs_ls <= 0.35:
        if score >= 3:
            watch_opt = _find_strike(options, atm_strike, side, 1)
            watch_prem = watch_opt.call_ltp if side == 'CE' else watch_opt.put_ltp
            return _result(
                f'WATCH — prepare BUY {side} 1-OTM @ {watch_opt.strike}', side,
                watch_opt.strike, round(watch_prem, 2) if watch_prem else None,
                None, 'WATCH', iv_reg, atm_iv,
                f'{direction} bias weak (LS {ls:+.4f}) | {score}/5 confirms | Wait for |LS| > 0.35 before entering',
                dte_warning, 'moderate'
            )
        return _result(
            'NO TRADE', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            f'Weak gravity LS {ls:+.3f} with low conviction {score}/5 — no edge', dte_warning, 'neutral'
        )

    # ── Strong gravity |LS| > 0.35 — gate on score before sizing ──────────
    # score=2: bias confirmed but structure incomplete → WAIT (aligns with decision_point)
    if score == 2:
        return _result(
            f'WAIT — {direction} BIAS', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            f'Strong LS {ls:+.3f} but only {score}/5 confirms — bias noted, needs more structure',
            dte_warning, 'moderate'
        )

    # ── Determine strike offset and size (score ≥ 3, |LS| > 0.35) ─────────
    # ATM (offset=0): full conviction, normal/low IV
    # 1-OTM (offset=1): elevated IV or score=3 — cheaper entry, defined risk
    elevated_iv = iv_reg in ('ELEVATED', 'HIGH')
    strong      = abs_ls > 0.35 and score >= 4

    if strong and not elevated_iv:
        offset, size_note, style = 0, 'FULL SIZE', 'strong'
    else:
        # score=3 or elevated IV at score≥4 — prefer OTM to reduce premium risk
        offset, size_note, style = 1, 'REDUCED SIZE', 'moderate'

    opt    = _find_strike(options, atm_strike, side, offset)
    strike = opt.strike
    prem   = opt.call_ltp if side == 'CE' else opt.put_ltp

    if not prem or prem <= 0:
        return _result(
            'NO TRADE', None, None, None, None, 'SKIP', iv_reg, atm_iv,
            f'Target strike {strike} has zero premium — stale or illiquid', dte_warning, 'neutral'
        )

    # ── Build rationale ────────────────────────────────────────────────────
    strike_label = 'ATM' if offset == 0 else '1-OTM'
    strategy     = f'BUY {side} — {strike_label}'

    parts = []
    parts.append(f'{direction} gravity  (LS {ls:+.4f})')
    parts.append(f'{score}/5 signals confirm')
    if elevated_iv:
        parts.append(f'IV elevated at {atm_iv:.1f}% — using OTM to reduce premium risk')
    if opt_dte <= 2:
        parts.append(f'{opt_dte}d to expiry — theta risk elevated')

    return _result(
        strategy, side, strike, round(prem, 2),
        _max_loss(prem, ls_lot), size_note,
        iv_reg, atm_iv,
        ' | '.join(parts), dte_warning, style
    )


# ── Helpers ────────────────────────────────────────────────────────────────

def _max_loss(premium, lot_size):
    if not premium or premium <= 0:
        return None
    return round(premium * lot_size, 0)


def _result(strategy, side, strike, premium, max_loss_lot, size_note,
            iv_reg, iv_val, rationale, dte_warning, style):
    return {
        'strategy'     : strategy,
        'side'         : side,
        'strike'       : strike,
        'premium'      : premium,
        'max_loss_lot' : max_loss_lot,
        'size_note'    : size_note,
        'iv_regime'    : iv_reg,
        'iv_value'     : iv_val,
        'rationale'    : rationale,
        'dte_warning'  : dte_warning,
        'style'        : style,
    }
