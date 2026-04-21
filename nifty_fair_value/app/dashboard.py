import sys
import os
# Ensure the nifty_fair_value package root is on sys.path so that
# `from config import settings`, `from engine import ...` etc. resolve
# regardless of the working directory from which the app is launched.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading
import time
import pyotp
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template, request
from growwapi import GrowwAPI
from config import settings
from data.groww_client import GrowwClient, validate_chain
from engine.atm_selector import find_true_atm
from engine.synthetic import theoretical_price_pcp, futures_microprice, validate_theoretical_prices, breeden_litzenberger
from engine.oi_weighted import oi_weighted_levels
from engine.max_pain import max_pain
from engine.fair_value import (
    today_fair, expiry_fair, ls_factor, ls_direction, ls_confidence, decision_point,
    calculate_straddle_range, basis_analysis,
    todays_fair_value, expiry_fair_value
)
from engine.signals import generate_execution_setup
from engine.regime import classify_regime
from engine.oi_metrics import pcr_oi, pcr_near_atm, pcr_at_strike
from engine.options_strategy import options_strategy
from data import persistence

app = Flask(__name__, template_folder="../templates")

# Shared state
metrics_cache = {}
lock = threading.Lock()
error_state = None
poll_status = "Initializing"
last_success_ts = 0

# Wake event — set by /api/expiry when the user changes an override, so the
# poller breaks out of its idle sleep immediately and runs a fresh tick.
_wake_event = threading.Event()

# User-selected expiry overrides (None = auto-discover nearest non-expired).
# Set via POST /api/expiry; cleared by sending {"opt_expiry": null, "fut_expiry": null}.
selected_opt_expiry = None
selected_fut_expiry = None

# Populated by the poller once instruments are refreshed — served to the UI dropdown.
available_opt_expiries = []
available_fut_expiries = []

# The client handle, published once the poller has authenticated — used by the
# /api/expiries endpoint to refresh the dropdown list on demand without waiting
# for the next 6-hour instrument refresh.
_client_handle = None

_IST = timezone(timedelta(hours=5, minutes=30))

def _market_open() -> bool:
    """True during NSE F&O session: Mon–Fri 09:15–15:30 IST."""
    now = datetime.now(_IST)
    if now.weekday() >= 5:          # Saturday / Sunday
        return False
    open_  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_ = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_ <= now <= close_


def _refresh_expiry_lists(client):
    """Refresh the UI-facing expiry dropdown lists."""
    global available_opt_expiries, available_fut_expiries
    try:
        with lock:
            available_opt_expiries = client.list_option_expiries()
            available_fut_expiries = client.list_future_expiries()
    except Exception as e:
        print(f"[WARN] Failed to refresh expiry lists: {e}")


def poller():
    global metrics_cache, error_state, poll_status, last_success_ts, _client_handle

    try:
        poll_status = "Authenticating"
        totp = pyotp.TOTP(settings.TOTP_SECRET).now()
        api_auth_token = GrowwAPI.get_access_token(api_key=settings.TOTP_TOKEN, totp=totp)
        groww_api = GrowwAPI(api_auth_token)
        client = GrowwClient(groww_api)
        _client_handle = client

        poll_status = "Refreshing Instruments"
        client.refresh_instruments()
        _refresh_expiry_lists(client)

        last_instr_refresh = time.time()
        session_baselines  = {}   # OI levels at session open — set once, never updated
        prev_pcr           = None  # previous tick PCR for velocity computation

        print("[POLLER] Initialization complete. Entering main loop.")
        poll_status = "Connected"

        while True:
            try:
                # Refresh instruments every 6 hours
                if time.time() - last_instr_refresh > 21600:
                    client.refresh_instruments()
                    _refresh_expiry_lists(client)
                    last_instr_refresh = time.time()

                # User-selected expiry takes precedence; otherwise auto-discover.
                auto_opt, auto_fut = client.get_active_expiries()
                opt_expiry = selected_opt_expiry or auto_opt
                fut_expiry = selected_fut_expiry or auto_fut
                if not opt_expiry or not fut_expiry:
                    raise Exception("Could not discover active Nifty expiries.")

                today = datetime.now().date()
                opt_dte = (datetime.strptime(opt_expiry, "%Y-%m-%d").date() - today).days
                fut_dte = (datetime.strptime(fut_expiry, "%Y-%m-%d").date() - today).days

                market_data = client.get_market_data(settings.UNDERLYING, opt_expiry, fut_expiry)

                # ── Data Validation (PRD §3.3) ──
                if not market_data or market_data.spot <= 0 or not market_data.options:
                    # If the user has explicitly pinned a settled/expired contract,
                    # don't silently loop — surface it so the UI banner explains
                    # why nothing is showing. Covers: post-15:30 on expiry day,
                    # or manually selecting a date that's already expired.
                    expiry_override_in_use = (selected_opt_expiry is not None)
                    msg = (f"Selected expiry {opt_expiry} returned no data "
                           "(contract likely settled / expired)."
                           if expiry_override_in_use
                           else "Invalid market data received (Spot=0 or No Options).")

                    if not _market_open():
                        with lock:
                            poll_status = "Market Closed"
                            # Explicit empty-state marker so the UI resets stale
                            # fields instead of reusing the previous expiry's numbers.
                            metrics_cache = {
                                "poll_status": "Market Closed",
                                "error_msg"  : msg if expiry_override_in_use else None,
                                "expiry"     : opt_expiry,
                                "futures_expiry": fut_expiry,
                                "is_stale"   : True,
                                "ts"         : time.time(),
                            }
                        print(f"[POLLER] {msg} — sleeping 60s")
                        _wake_event.wait(timeout=60)
                        _wake_event.clear()
                        continue
                    raise Exception(msg)

                chain_warnings = validate_chain(market_data.options, market_data.spot)
                for w in chain_warnings:
                    print(f"[CHAIN WARN] {w}")

                # ── Module 1: True ATM Detection ──
                true_atm_opt, iv_spread, listed_atm, skew_shift = find_true_atm(
                    market_data.options, market_data.spot
                )
                if not true_atm_opt:
                    raise Exception("ATM Detection failed: No valid strikes found.")

                # ── Module 2: Theoretical Price (OI-weighted PCP — primary) ──
                theo_price, n_strikes, atm_iv = theoretical_price_pcp(
                    market_data.options, true_atm_opt.strike
                )
                if theo_price is None:
                    # Fallback to single-strike PCP if OI-weighted fails
                    theo_price = true_atm_opt.strike + (true_atm_opt.call_ltp - true_atm_opt.put_ltp)
                    n_strikes  = 1
                    atm_iv     = true_atm_opt.call_iv

                # Microprice (real-time cross-check)
                microprice, book_pressure = futures_microprice(
                    market_data.bid_price, market_data.offer_price,
                    market_data.bid_quantity, market_data.offer_quantity
                )

                # Breeden-Litzenberger (DTE=2 only — not DTE=1/0).
                # On expiry eve/day the chain is nearly expired: premiums collapse,
                # the call surface second-derivative is near-zero everywhere, and BL
                # returns an expected value far below spot (~1900pts off). This
                # contaminates the cross-validation spread and triggers UNRELIABLE,
                # blocking all signals on what is often the most active day.
                bl_price = None
                if opt_dte == 2:
                    bl_price, _, _, _, _ = breeden_litzenberger(
                        market_data.options, market_data.spot, T_days=2
                    )

                theo_validation = validate_theoretical_prices(theo_price, microprice, bl_price)

                # ── Module 4: OI Levels & Max Pain ──
                oi_levels = oi_weighted_levels(market_data.options, market_data.spot)
                call_resistance = oi_levels['call_resistance']
                put_support     = oi_levels['put_support']

                mp_strike, pain_surface, pain_depth = max_pain(market_data.options)

                if not session_baselines and call_resistance and put_support:
                    session_baselines = {'call_level': call_resistance, 'put_level': put_support}

                # ── Straddle + PCR (needed before LS and regime) ──
                sr  = calculate_straddle_range(true_atm_opt.call_ltp, true_atm_opt.put_ltp)
                pcr = pcr_oi(market_data.total_ce_oi, market_data.total_pe_oi)

                # Near-ATM PCR: ±3 strikes around true ATM — filters structural
                # far-OTM hedging that permanently inflates total-chain PCR.
                near_atm_pcr_val = pcr_near_atm(
                    market_data.options, true_atm_opt.strike, window=3
                )
                # Max pain PCR: put/call OI ratio at the settlement anchor.
                # > 1.3 = put writers defending as floor; < 0.7 = call ceiling.
                max_pain_pcr_val = pcr_at_strike(market_data.options, mp_strike)

                # PCR velocity — direction of change since last tick
                pcr_velocity  = round(pcr - prev_pcr, 4) if prev_pcr is not None else 0.0
                pcr_direction = (
                    "RISING"  if pcr_velocity >  0.02 else
                    "FALLING" if pcr_velocity < -0.02 else
                    "STABLE"
                )
                prev_pcr = pcr

                # OI level shift vs session open (None until first baselines set)
                oi_shift_call = (
                    round(call_resistance - session_baselines['call_level'], 1)
                    if session_baselines and call_resistance else None
                )
                oi_shift_put = (
                    round(put_support - session_baselines['put_level'], 1)
                    if session_baselines and put_support else None
                )
                straddle_upper = round(true_atm_opt.strike + sr, 1)
                straddle_lower = round(true_atm_opt.strike - sr, 1)

                # ── Module 3: Today's Fair Value (PRD-structured) ──
                today_fv_dict = todays_fair_value(market_data, true_atm_opt, theo_price)

                # ── Module 4: Expiry Fair Value scalar (LS anchor) ──
                # ATM strike removed from average — it is ~spot by definition and
                # dampens the expiry signal. Max Pain (settlement anchor) 60%,
                # OI midpoint (structural) 40%.
                expiry_fv_scalar = expiry_fair(call_resistance, put_support, mp_strike)
                ls    = ls_factor(expiry_fv_scalar, market_data.spot, sr)
                ls_dir = ls_direction(ls)

                # ── Module 4: Expiry Fair Value (PRD-structured) ──
                expiry_fv_dict = expiry_fair_value(
                    market_data.options, true_atm_opt, market_data.spot,
                    mp_strike, pain_depth, oi_levels
                )

                # ── Module 5: Regime Classification ──
                regime = classify_regime(
                    spot=market_data.spot,
                    theoretical_price=theo_price,
                    max_pain_strike=mp_strike,
                    directional_bias=today_fv_dict['directional_bias'],
                    pain_depth=pain_depth
                )

                # ── LS Confidence + Decision (requires regime and pcr) ──
                theo_reliable = not theo_validation['status'].startswith('UNRELIABLE')
                chain_ok      = len([w for w in chain_warnings if 'ATM' in w or 'zero' in w.lower()]) == 0
                ls_conf     = ls_confidence(
                    ls,
                    today_fv_dict.get('directional_bias', ''),
                    regime['intraday_gap'],
                    pain_depth,
                    regime['expiry_gap'],
                    pcr,
                    theo_reliable and chain_ok,
                    regime_bias=regime.get('bias'),
                    call_oi_hhi=oi_levels.get('call_oi_concentration'),
                    put_oi_hhi=oi_levels.get('put_oi_concentration'),
                    near_atm_pcr=near_atm_pcr_val,
                    max_pain_pcr=max_pain_pcr_val,
                )
                ls_decision = decision_point(ls, ls_conf)

                # ── Options Strategy ──
                opt_strategy = options_strategy(
                    ls, ls_conf, atm_iv, opt_dte,
                    market_data.options, true_atm_opt.strike, market_data.spot
                )

                # Legacy scalar for signals.py backward-compat
                today_fv_scalar = today_fair(market_data.futures, theo_price, market_data.futures_vwap)
                setup = generate_execution_setup(
                    market_data, today_fv_scalar, expiry_fv_scalar, theo_price,
                    call_resistance, put_support, sr, session_baselines, ls=ls
                )

                # Chart data (±3 strikes around true ATM)
                sorted_opts = sorted(market_data.options, key=lambda x: x.strike)
                atm_idx     = next((i for i, o in enumerate(sorted_opts) if o.strike == true_atm_opt.strike), -1)
                chart_strikes = []
                if atm_idx != -1:
                    for o in sorted_opts[max(0, atm_idx - 3): min(len(sorted_opts), atm_idx + 4)]:
                        chart_strikes.append({
                            "strike": o.strike, "call_oi": o.call_oi, "put_oi": o.put_oi,
                            "call_ltp": o.call_ltp, "put_ltp": o.put_ltp,
                            "call_iv": o.call_iv or 0, "put_iv": o.put_iv or 0
                        })

                with lock:
                    last_success_ts = time.time()
                    poll_status     = "Connected"
                    error_state     = None
                    metrics_cache   = {
                        "poll_status"        : poll_status,
                        "last_success_ts"    : last_success_ts,
                        "is_stale"           : False,
                        "spot"               : market_data.spot,
                        "futures"            : market_data.futures,
                        "futures_vwap"       : market_data.futures_vwap,
                        # Module 2
                        "theoretical_price"  : theo_price,
                        "theo_n_strikes"     : n_strikes,
                        "atm_iv"             : atm_iv,
                        "microprice"         : microprice,
                        "book_pressure"      : book_pressure,
                        "bl_price"           : bl_price,
                        "theo_status"        : theo_validation['status'],
                        "theo_spread"        : theo_validation['spread'],
                        "arbitrage"          : market_data.futures - (theo_price or 0),
                        # Module 3 (structured)
                        "today_fv"           : today_fv_scalar,
                        "today_fv_detail"    : today_fv_dict,
                        # Module 4 (structured)
                        "expiry_fv"          : expiry_fv_scalar,
                        "expiry_fv_detail"   : expiry_fv_dict,
                        "ls_factor"          : ls,
                        "ls_direction"       : ls_dir,
                        "ls_confidence"      : ls_conf,
                        "ls_decision"        : ls_decision,
                        "max_pain"           : mp_strike,
                        "pain_depth"         : pain_depth,
                        # Module 1 ATM info
                        "atm_strike"         : true_atm_opt.strike,
                        "listed_atm"         : listed_atm,
                        "skew_shift"         : skew_shift,
                        "atm_iv_spread"      : iv_spread,
                        "atm_call_ltp"       : true_atm_opt.call_ltp,
                        "atm_put_ltp"        : true_atm_opt.put_ltp,
                        "atm_ce_iv"          : true_atm_opt.call_iv,
                        "atm_pe_iv"          : true_atm_opt.put_iv,
                        # Straddle / range
                        "straddle_value"     : sr,
                        "straddle_upper"     : straddle_upper,
                        "straddle_lower"     : straddle_lower,
                        # OI levels
                        "call_resistance"    : call_resistance,
                        "put_support"        : put_support,
                        "oi_corridor_width"  : oi_levels.get('oi_corridor_width'),
                        "call_oi_concentration": oi_levels.get('call_oi_concentration'),
                        "put_oi_concentration" : oi_levels.get('put_oi_concentration'),
                        # Basis / VWAP (from today_fv_dict)
                        "basis"              : today_fv_dict['basis'],
                        "excess_basis"       : today_fv_dict['excess_basis'],
                        "vwap_deviation"     : today_fv_dict['vwap_deviation'],
                        "spot_deviation"     : today_fv_dict['spot_deviation'],
                        "spot_signal"        : today_fv_dict['spot_signal'],
                        "directional_bias"   : today_fv_dict['directional_bias'],
                        # Expiry detail (from expiry_fv_dict)
                        "gravity_signal"     : expiry_fv_dict['gravity_signal'],
                        "gap_to_max_pain"    : expiry_fv_dict['gap_to_max_pain'],
                        "skew_pull"          : expiry_fv_dict['skew_pull'],
                        "spot_in_oi_corridor": expiry_fv_dict['spot_in_oi_corridor'],
                        "spot_in_straddle"   : expiry_fv_dict['spot_in_straddle'],
                        # Module 5
                        "regime"             : regime["regime"],
                        "regime_bias"        : regime["bias"],
                        "regime_confidence"  : regime["confidence"],
                        "regime_rationale"   : regime["rationale"],
                        "intraday_gap"       : regime["intraday_gap"],
                        "expiry_gap"         : regime["expiry_gap"],
                        "alignment_gap"      : regime["alignment_gap"],
                        # Misc
                        "pcr_oi"             : pcr,
                        "pcr_near_atm"       : near_atm_pcr_val,
                        "pcr_max_pain"       : max_pain_pcr_val,
                        "pcr_velocity"       : pcr_velocity,
                        "pcr_direction"      : pcr_direction,
                        "oi_shift_call"      : oi_shift_call,
                        "oi_shift_put"       : oi_shift_put,
                        "signal"             : setup["signal"],
                        "setup"              : setup,
                        "expiry"             : opt_expiry,
                        "futures_expiry"     : fut_expiry,
                        "opt_dte"            : opt_dte,
                        "fut_dte"            : fut_dte,
                        "chart_data"         : chart_strikes,
                        "options_strategy"   : opt_strategy,
                        "ts"                 : time.time()
                    }

                persistence.save_market_tick(metrics_cache)

            except Exception as e:
                with lock:
                    error_state  = str(e)
                    poll_status  = "Reconnecting (Error)"
                    if metrics_cache:
                        metrics_cache["is_stale"]    = (time.time() - last_success_ts > 60)
                        metrics_cache["poll_status"] = poll_status
                        metrics_cache["error_msg"]   = error_state
                print(f"[POLLER ERROR] {e}")

            # Interruptible sleep — /api/expiry sets _wake_event to trigger an
            # immediate refresh when the user picks a new expiry.
            _wake_event.wait(timeout=5)
            _wake_event.clear()

    except Exception as e:
        with lock:
            error_state = f"Critical Failure: {e}"
            poll_status = "Disconnected"
        print(f"[CRITICAL ERROR] Poller failed to start: {e}")


@app.route("/")
def index():
    return render_template("fairvalue.html")

@app.route("/api/data")
def api_data():
    with lock:
        if error_state and not metrics_cache:
            return jsonify({"error": error_state, "poll_status": poll_status}), 500
        response = metrics_cache.copy()
        response["poll_status"] = poll_status
        return jsonify(response)

@app.route("/api/history")
def api_history():
    history = persistence.get_history()
    return jsonify(history)


@app.route("/api/expiries", methods=["GET"])
def api_expiries():
    """Returns the list of available option / futures expiries plus the
    currently-selected ones (null means auto-discover)."""
    # Refresh lazily if the poller hasn't populated them yet.
    if (not available_opt_expiries or not available_fut_expiries) and _client_handle:
        _refresh_expiry_lists(_client_handle)
    with lock:
        return jsonify({
            "opt_expiries"        : list(available_opt_expiries),
            "fut_expiries"        : list(available_fut_expiries),
            "selected_opt_expiry" : selected_opt_expiry,
            "selected_fut_expiry" : selected_fut_expiry,
        })


@app.route("/api/expiry", methods=["POST"])
def api_set_expiry():
    """Overrides the expiry used by the poller. Send {"opt_expiry": "YYYY-MM-DD"}
    and/or {"fut_expiry": "YYYY-MM-DD"}. Pass null to clear an override and
    return to auto-discovery.

    On any actual change we clear metrics_cache (so the UI shows a transient
    "refreshing" state rather than stale numbers from the previous expiry) and
    set _wake_event to interrupt the poller's idle sleep immediately.
    """
    global selected_opt_expiry, selected_fut_expiry, poll_status, metrics_cache
    body = request.get_json(silent=True) or {}
    changed = False

    if "opt_expiry" in body:
        val = body["opt_expiry"]
        if val is not None and val not in available_opt_expiries:
            return jsonify({"error": f"Unknown option expiry: {val}"}), 400
        if val != selected_opt_expiry:
            selected_opt_expiry = val
            changed = True

    if "fut_expiry" in body:
        val = body["fut_expiry"]
        if val is not None and val not in available_fut_expiries:
            return jsonify({"error": f"Unknown futures expiry: {val}"}), 400
        if val != selected_fut_expiry:
            selected_fut_expiry = val
            changed = True

    if changed:
        with lock:
            # Drop stale tick so /api/data doesn't keep serving the previous
            # expiry's numbers until the next poll completes.
            metrics_cache = {}
            poll_status   = "Refreshing"
        _wake_event.set()

    return jsonify({
        "selected_opt_expiry": selected_opt_expiry,
        "selected_fut_expiry": selected_fut_expiry,
        "refreshing"         : changed,
    })


def main():
    persistence.init_db()
    threading.Thread(target=poller, daemon=True).start()
    app.run(host="0.0.0.0", port=5002, debug=False)

if __name__ == "__main__":
    main()
