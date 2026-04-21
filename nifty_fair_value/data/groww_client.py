import time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from growwapi import GrowwAPI
from .data_models import OptionData, MarketData

_IST = timezone(timedelta(hours=5, minutes=30))

# Retry delays (seconds) for transient errors and 429 rate-limits
_RETRY_DELAYS = (1, 2, 4)


class GrowwClient:
    def __init__(self, api: GrowwAPI):
        self.api = api
        self.instruments_df = None

    # ── Retry wrapper ────────────────────────────────────────────────────────

    def _call(self, fn, *args, **kwargs):
        """
        Calls fn(*args, **kwargs) with automatic retry on transient failures.
        Retries on: HTTP 429 (rate-limit), timeout, connection reset.
        Raises immediately on all other errors.
        """
        last_exc = None
        for i, delay in enumerate((*_RETRY_DELAYS, None)):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                msg = str(e).lower()
                is_transient = any(x in msg for x in ('429', 'rate limit', 'timeout', 'connection'))
                if not is_transient or delay is None:
                    raise
                last_exc = e
                print(f"[RETRY {i+1}/{len(_RETRY_DELAYS)}] {e} — backing off {delay}s")
                time.sleep(delay)
        raise last_exc  # unreachable but satisfies type checkers

    # ── Instrument management ─────────────────────────────────────────────────

    def refresh_instruments(self):
        """Downloads all instruments and filters for Nifty derivatives."""
        try:
            full_df = self.api.get_all_instruments()
            self.instruments_df = full_df[
                (full_df['underlying_symbol'] == 'NIFTY') &
                (full_df['segment'] == 'FNO')
            ].copy()
            print(f"[DEBUG] Refreshed Nifty instruments: {len(self.instruments_df)} contracts found.")
        except Exception as e:
            print(f"[ERROR] Failed to refresh instruments: {e}")

    def get_active_expiries(self):
        """Discovers the nearest weekly option expiry and nearest futures expiry.

        After 15:30 IST the current day's expiry is already settled — use strict
        greater-than so the just-expired contract is skipped and the next expiry
        is returned instead.
        """
        if self.instruments_df is None:
            self.refresh_instruments()

        if self.instruments_df is None or self.instruments_df.empty:
            return None, None

        now_ist     = datetime.now(_IST)
        today       = now_ist.strftime("%Y-%m-%d")
        after_close = now_ist >= now_ist.replace(hour=15, minute=30, second=0, microsecond=0)

        # After 15:30, today's contracts have settled — skip to the next expiry.
        expiry_dates = self.instruments_df['expiry_date']
        date_filter  = expiry_dates > today if after_close else expiry_dates >= today

        option_expiries = sorted(self.instruments_df[
            self.instruments_df['instrument_type'].isin(['CE', 'PE']) & date_filter
        ]['expiry_date'].unique())

        future_expiries = sorted(self.instruments_df[
            (self.instruments_df['instrument_type'] == 'FUT') & date_filter
        ]['expiry_date'].unique())

        return (option_expiries[0] if option_expiries else None,
                future_expiries[0] if future_expiries else None)

    @staticmethod
    def _month_end_cutoff(now_ist: datetime) -> str:
        """Cutoff = end of current month, extended to end of next month when
        we're within the last 7 days (so the dropdown always shows ≥4 weeklies
        and the next monthly, never degenerates to 1–2 entries near month-end).
        """
        # first day of next month
        y, m = now_ist.year, now_ist.month
        if m == 12:
            next_month_start = now_ist.replace(year=y+1, month=1, day=1)
        else:
            next_month_start = now_ist.replace(month=m+1, day=1)
        end_of_month = next_month_start - timedelta(days=1)
        # If we're within last 7 days of month, extend by another month
        if (end_of_month - now_ist).days < 7:
            y2, m2 = end_of_month.year, end_of_month.month
            if m2 == 12:
                after = end_of_month.replace(year=y2+1, month=1, day=1)
            else:
                after = end_of_month.replace(month=m2+1, day=1)
            # last day of the extended month
            y3, m3 = after.year, after.month
            if m3 == 12:
                end_of_month = after.replace(year=y3+1, month=1, day=1) - timedelta(days=1)
            else:
                end_of_month = after.replace(month=m3+1, day=1) - timedelta(days=1)
        return end_of_month.strftime("%Y-%m-%d")

    def list_option_expiries(self) -> list:
        """Returns option expiries from today through end of current month
        (extended to next month's end when we're within the last week of the
        current month). Covers ~4–8 weekly expiries plus the monthly anchor.

        Unlike get_active_expiries(), this INCLUDES today's expiry even after
        15:30 — the UI dropdown lets the user view/override even a settled chain.
        """
        if self.instruments_df is None:
            self.refresh_instruments()
        if self.instruments_df is None or self.instruments_df.empty:
            return []

        now_ist = datetime.now(_IST)
        today   = now_ist.strftime("%Y-%m-%d")
        cutoff  = self._month_end_cutoff(now_ist)

        exps = sorted(self.instruments_df[
            self.instruments_df['instrument_type'].isin(['CE', 'PE']) &
            (self.instruments_df['expiry_date'] >= today) &
            (self.instruments_df['expiry_date'] <= cutoff)
        ]['expiry_date'].unique())
        return list(exps)

    def list_future_expiries(self) -> list:
        """Returns futures expiries within the same month-end horizon as options."""
        if self.instruments_df is None:
            self.refresh_instruments()
        if self.instruments_df is None or self.instruments_df.empty:
            return []

        now_ist = datetime.now(_IST)
        today   = now_ist.strftime("%Y-%m-%d")
        cutoff  = self._month_end_cutoff(now_ist)

        exps = sorted(self.instruments_df[
            (self.instruments_df['instrument_type'] == 'FUT') &
            (self.instruments_df['expiry_date'] >= today) &
            (self.instruments_df['expiry_date'] <= cutoff)
        ]['expiry_date'].unique())
        return list(exps)

    def _get_futures_symbol(self, expiry: str) -> str:
        """Resolves the exact trading symbol for Nifty Futures for a given expiry."""
        if self.instruments_df is None:
            self.refresh_instruments()

        match = self.instruments_df[
            (self.instruments_df['instrument_type'] == 'FUT') &
            (self.instruments_df['expiry_date'] == expiry)
        ]
        if not match.empty:
            return match.iloc[0]['trading_symbol']

        dt = datetime.strptime(expiry, "%Y-%m-%d")
        return f"NIFTY{dt.strftime('%y%b').upper()}FUT"

    # ── Parallel data fetch ───────────────────────────────────────────────────

    def _fetch_option_chain_raw(self, underlying: str, opt_expiry: str) -> dict:
        return self._call(
            self.api.get_option_chain,
            exchange=self.api.EXCHANGE_NSE,
            underlying=underlying,
            expiry_date=opt_expiry,
        )

    def _fetch_futures_raw(self, fut_expiry: str) -> tuple:
        fsym = self._get_futures_symbol(fut_expiry)
        quote = self._call(
            self.api.get_quote,
            trading_symbol=fsym,
            exchange=self.api.EXCHANGE_NSE,
            segment=self.api.SEGMENT_FNO,
        )
        return fsym, quote

    def get_market_data(self, underlying: str, opt_expiry: str, fut_expiry: str) -> MarketData:
        """
        Fetches option chain and futures quote in parallel, then normalises into MarketData.

        Two independent Live Data API calls run concurrently via ThreadPoolExecutor —
        cuts per-cycle wall-clock time roughly in half (~500ms → ~250ms on Groww infra).
        Each call is wrapped with retry-on-429 backoff via _call().
        """
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_chain = ex.submit(self._fetch_option_chain_raw, underlying, opt_expiry)
            f_quote = ex.submit(self._fetch_futures_raw, fut_expiry)
            # .result() will re-raise any exception from the worker thread
            option_chain = f_chain.result()
            fsym, quote  = f_quote.result()

        # ── Parse option chain ────────────────────────────────────────────────
        spot    = option_chain.get("underlying_ltp", 0.0)
        strikes = option_chain.get("strikes", {})

        options_list = []
        total_ce_oi  = 0
        total_pe_oi  = 0

        atm_strike = float(min(strikes.keys(), key=lambda k: abs(float(k) - spot))) \
                     if strikes else 0.0

        atm_ce_ltp = atm_pe_ltp = atm_ce_iv = atm_pe_iv = 0.0

        for strike_str, data in strikes.items():
            strike_val = float(strike_str)
            ce = data.get("CE", {})
            pe = data.get("PE", {})

            c_oi  = ce.get("open_interest", 0) or 0
            p_oi  = pe.get("open_interest", 0) or 0
            c_iv  = ce.get("greeks", {}).get("iv", 0.0) or 0.0
            p_iv  = pe.get("greeks", {}).get("iv", 0.0) or 0.0
            c_ltp = ce.get("ltp", 0.0) or 0.0
            p_ltp = pe.get("ltp", 0.0) or 0.0

            if strike_val == atm_strike:
                atm_ce_ltp, atm_pe_ltp = c_ltp, p_ltp
                atm_ce_iv,  atm_pe_iv  = c_iv,  p_iv

            total_ce_oi += c_oi
            total_pe_oi += p_oi

            options_list.append(OptionData(
                strike=strike_val,
                call_ltp=c_ltp, put_ltp=p_ltp,
                call_oi=c_oi,   put_oi=p_oi,
                call_iv=c_iv,   put_iv=p_iv,
            ))

        # ── Parse futures quote ───────────────────────────────────────────────
        if not quote:
            print(f"[DEBUG] Futures data empty for {fsym}. Check symbol formatting.")
            quote = {}

        futures_price     = quote.get("last_price")           or 0.0
        futures_vwap      = quote.get("average_price")        or 0.0
        futures_oi        = quote.get("open_interest")        or 0
        futures_oi_chg    = quote.get("oi_day_change_percentage") or 0.0
        bid_price         = quote.get("bid_price")            or 0.0
        offer_price       = quote.get("offer_price")          or 0.0
        bid_quantity      = quote.get("bid_quantity")         or 0
        offer_quantity    = quote.get("offer_quantity")       or 0

        return MarketData(
            spot=spot or 0.0,
            futures=futures_price,
            futures_vwap=futures_vwap,
            futures_oi=futures_oi,
            options=options_list,
            atm_strike=atm_strike,
            atm_call_ltp=atm_ce_ltp or 0.0,
            atm_put_ltp=atm_pe_ltp  or 0.0,
            atm_ce_iv=atm_ce_iv     or 0.0,
            atm_pe_iv=atm_pe_iv     or 0.0,
            total_ce_oi=total_ce_oi or 0,
            total_pe_oi=total_pe_oi or 0,
            expiry=opt_expiry,
            futures_expiry=fut_expiry,
            futures_oi_chg_pct=futures_oi_chg,
            bid_price=bid_price,
            offer_price=offer_price,
            bid_quantity=bid_quantity,
            offer_quantity=offer_quantity,
        )


def validate_chain(options, spot):
    """
    PRD §3.3: Data quality checks applied before any computation cycle.
    Returns a list of warning strings (empty = all clear).
    Raises ValueError on fatal data issues.
    """
    if spot <= 0:
        raise ValueError("Invalid spot price in chain response")

    issues = []

    if len(options) < 20:
        issues.append(f"WARN: Only {len(options)} strikes available — chain may be incomplete")

    if options:
        atm_opt = min(options, key=lambda o: abs(o.strike - spot))
        if atm_opt.call_ltp <= 0 or atm_opt.put_ltp <= 0:
            issues.append("WARN: ATM CE or PE has zero LTP — stale or halted")

    return issues
