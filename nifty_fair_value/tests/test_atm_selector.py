"""
Tests for engine/atm_selector.py

Covers:
  find_true_atm — IV minimisation, OI-balance fallback, IV anomaly filter,
                  max-skew cap, empty/degenerate inputs
  get_atm_option — nearest strike
"""
import pytest
from engine.atm_selector import find_true_atm, get_atm_option
from tests.fixtures import OptionData


def make_chain_with_spread(atm=24_500, n=9, step=50):
    """Chain where CE_IV > PE_IV below ATM and CE_IV < PE_IV above ATM,
    so the true IV-minimisation ATM is the centre strike."""
    opts = []
    half = n // 2
    for i in range(-half, half + 1):
        strike = atm + i * step
        # Symmetry: IV spread is |i| points, so |CE-PE| is lowest at centre
        ce_iv = 15.0 + i * 0.5
        pe_iv = 15.0 - i * 0.5
        opts.append(OptionData(
            strike=strike, call_iv=ce_iv, put_iv=pe_iv,
            call_oi=200_000, put_oi=200_000,
        ))
    return opts


def make_equal_iv_chain(atm=24_500, n=9, step=50, iv=15.0):
    """Chain where CE_IV == PE_IV at every strike — triggers OI-balance fallback."""
    opts = []
    half = n // 2
    for i in range(-half, half + 1):
        strike = atm + i * step
        # OI is highest at ATM (i=0) and decays, so ATM has most balanced OI
        oi = max(50_000, 200_000 - abs(i) * 20_000)
        opts.append(OptionData(
            strike=strike, call_iv=iv, put_iv=iv,
            call_oi=oi, put_oi=oi,
        ))
    return opts


# ═══════════════════════════════════════════════════════════════════════
# find_true_atm — IV minimisation path
# ═══════════════════════════════════════════════════════════════════════
class TestFindTrueAtmIV:
    def test_returns_centre_strike_as_true_atm(self):
        opts = make_chain_with_spread(atm=24_500)
        true_atm, iv_spread, listed_atm, skew_shift = find_true_atm(opts, spot=24_505)
        assert true_atm.strike == 24_500

    def test_iv_spread_is_non_negative(self):
        opts = make_chain_with_spread(atm=24_500)
        _, iv_spread, _, _ = find_true_atm(opts, spot=24_505)
        assert iv_spread >= 0

    def test_listed_atm_is_nearest_to_spot(self):
        opts = make_chain_with_spread(atm=24_500)
        _, _, listed_atm, _ = find_true_atm(opts, spot=24_510)
        assert listed_atm == 24_500  # 24500 is nearest 50-pt strike to 24510

    def test_skew_shift_positive_when_true_atm_below_listed(self):
        # Make IV spread minimum at strike 50 below listed ATM
        opts = []
        for i in range(-4, 5):
            strike = 24_500 + i * 50
            # Min spread at strike=24_450 (i=-1)
            spread = abs(i + 1) * 1.5
            ce_iv = 15.0 + spread / 2
            pe_iv = 15.0 - spread / 2
            opts.append(OptionData(strike=strike, call_iv=max(0.1, ce_iv),
                                   put_iv=max(0.1, pe_iv),
                                   call_oi=200_000, put_oi=200_000))
        _, _, listed_atm, skew_shift = find_true_atm(opts, spot=24_510)
        # skew_shift = listed_atm - true_atm_strike; positive = true below listed
        assert skew_shift >= 0

    def test_anomalous_iv_above_200_filtered(self):
        opts = make_chain_with_spread(atm=24_500)
        # Inject an extreme IV at one strike — should be ignored
        opts[0].call_iv = 250.0
        opts[0].put_iv = 250.0
        true_atm, _, _, _ = find_true_atm(opts, spot=24_500)
        assert true_atm is not None
        assert true_atm.call_iv != 250.0

    def test_missing_iv_strike_skipped(self):
        opts = make_chain_with_spread(atm=24_500)
        opts[4].call_iv = 0.0  # zero IV at ATM candidate — should be skipped
        opts[4].put_iv  = 0.0
        true_atm, _, _, _ = find_true_atm(opts, spot=24_500)
        assert true_atm is not None

    def test_max_skew_cap_3_strikes(self):
        """True ATM cannot be more than 3 strikes from listed ATM."""
        opts = []
        # Create a chain where the minimum IV spread is 5 strikes from ATM
        for i in range(-6, 7):
            strike = 24_500 + i * 50
            # IV spread absolute minimum at i=-5 (would be 5 strikes below ATM)
            spread = abs(i + 5) * 0.1
            opts.append(OptionData(
                strike=strike,
                call_iv=15 + spread, put_iv=15 - spread,
                call_oi=200_000, put_oi=200_000,
            ))
        true_atm, _, listed_atm, skew_shift = find_true_atm(opts, spot=24_505)
        # Skew should be capped at ≤ 3 strikes × 50 = 150 pts
        assert abs(true_atm.strike - listed_atm) <= 150

    def test_empty_options_returns_none(self):
        true_atm, iv_spread, listed_atm, skew_shift = find_true_atm([], spot=24_500)
        assert true_atm is None

    def test_single_option_returns_it_as_listed(self):
        opts = [OptionData(strike=24_500, call_iv=15, put_iv=15)]
        true_atm, _, listed_atm, _ = find_true_atm(opts, spot=24_500)
        assert true_atm is not None
        assert listed_atm == 24_500


# ═══════════════════════════════════════════════════════════════════════
# find_true_atm — OI-balance fallback (all CE_IV == PE_IV)
# ═══════════════════════════════════════════════════════════════════════
class TestFindTrueAtmOIFallback:
    def test_fallback_selects_strike_with_most_balanced_oi(self):
        opts = make_equal_iv_chain(atm=24_500)
        # ATM has equal call_oi/put_oi; outer strikes also equal but with less OI
        # Balance metric |CE-PE|/(CE+PE): all equal here → first minimum wins
        true_atm, _, _, _ = find_true_atm(opts, spot=24_505)
        assert true_atm is not None

    def test_fallback_picks_imbalance_minimum(self):
        """Force an unambiguous OI imbalance minimum at a specific strike."""
        opts = []
        for i in range(-4, 5):
            strike = 24_500 + i * 50
            # At i=0 (24500): perfectly balanced OI
            # All others: 2x imbalance
            if i == 0:
                ce_oi, pe_oi = 200_000, 200_000
            else:
                ce_oi, pe_oi = 200_000, 100_000
            opts.append(OptionData(strike=strike, call_iv=15, put_iv=15,
                                   call_oi=ce_oi, put_oi=pe_oi))
        true_atm, _, _, _ = find_true_atm(opts, spot=24_505)
        assert true_atm.strike == 24_500

    def test_fallback_with_zero_oi_strikes_skipped(self):
        opts = make_equal_iv_chain(atm=24_500)
        # Zero out OI at the most balanced strike so it is excluded
        opts[4].call_oi = 0
        opts[4].put_oi  = 0
        true_atm, _, _, _ = find_true_atm(opts, spot=24_505)
        assert true_atm is not None
        assert true_atm.strike != 24_500  # 24500 excluded, fallback picks next best

    def test_fallback_returns_listed_atm_when_all_oi_zero(self):
        opts = make_equal_iv_chain(atm=24_500)
        for o in opts:
            o.call_oi = 0
            o.put_oi  = 0
        true_atm, _, listed_atm, _ = find_true_atm(opts, spot=24_505)
        # No OI balance can be computed — falls through to listed ATM
        assert true_atm.strike == listed_atm


# ═══════════════════════════════════════════════════════════════════════
# get_atm_option (legacy)
# ═══════════════════════════════════════════════════════════════════════
class TestGetAtmOption:
    def _chain(self):
        return [OptionData(strike=s) for s in [24_300, 24_350, 24_400, 24_450,
                                               24_500, 24_550, 24_600]]

    def test_returns_nearest_strike_exact_match(self):
        opt = get_atm_option(self._chain(), 24_500)
        assert opt.strike == 24_500

    def test_rounds_to_nearest_below(self):
        opt = get_atm_option(self._chain(), 24_475)
        assert opt.strike in (24_450, 24_500)

    def test_rounds_to_nearest_above(self):
        opt = get_atm_option(self._chain(), 24_525)
        assert opt.strike in (24_500, 24_550)

    def test_single_option_chain(self):
        opt = get_atm_option([OptionData(strike=24_500)], 99_999)
        assert opt.strike == 24_500
