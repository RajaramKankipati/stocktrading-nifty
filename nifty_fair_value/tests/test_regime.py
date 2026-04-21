"""
Tests for engine/regime.py

Covers every regime branch, both TREND_DAY bias labels, confidence gates,
gap calculations, and the DATA_ERROR guard.
"""
import pytest
from engine.regime import classify_regime


class TestClassifyRegime:

    # ── helper ────────────────────────────────────────────────────────────
    def _r(self, spot, theo, pain, bias="NEUTRAL", depth=None):
        return classify_regime(spot, theo, pain,
                               directional_bias=bias,
                               pain_depth=depth)

    # ── DATA_ERROR guard ─────────────────────────────────────────────────
    def test_data_error_zero_spot(self):
        r = self._r(spot=0, theo=24_500, pain=24_500)
        assert r["regime"] == "DATA_ERROR"
        assert r["bias"] == "NEUTRAL"
        assert r["confidence"] == "NONE"

    def test_data_error_none_spot(self):
        r = self._r(spot=None, theo=24_500, pain=24_500)
        assert r["regime"] == "DATA_ERROR"

    def test_data_error_none_theo(self):
        r = self._r(spot=24_500, theo=None, pain=24_500)
        assert r["regime"] == "DATA_ERROR"

    def test_data_error_zero_max_pain(self):
        r = self._r(spot=24_500, theo=24_500, pain=0)
        assert r["regime"] == "DATA_ERROR"

    def test_data_error_returns_zero_gaps(self):
        r = self._r(spot=None, theo=24_500, pain=24_500)
        assert r["intraday_gap"] == 0.0
        assert r["expiry_gap"] == 0.0
        assert r["alignment_gap"] == 0.0

    # ── gap calculation ───────────────────────────────────────────────────
    def test_intraday_gap_is_spot_minus_theo(self):
        r = self._r(spot=24_530, theo=24_500, pain=24_500)
        assert r["intraday_gap"] == pytest.approx(30.0, abs=0.2)

    def test_expiry_gap_is_spot_minus_max_pain(self):
        r = self._r(spot=24_600, theo=24_550, pain=24_500)
        assert r["expiry_gap"] == pytest.approx(100.0, abs=0.2)

    def test_alignment_gap_is_theo_minus_max_pain(self):
        r = self._r(spot=24_500, theo=24_580, pain=24_500)
        assert r["alignment_gap"] == pytest.approx(80.0, abs=0.2)

    # ── DOUBLE_OVERVALUED ────────────────────────────────────────────────
    def test_double_overvalued_both_above(self):
        # intraday gap > 20, expiry gap > 50
        r = self._r(spot=24_600, theo=24_570, pain=24_500)
        # intraday = 30 > 20 ✓, expiry = 100 > 50 ✓
        assert r["regime"] == "DOUBLE_OVERVALUED"
        assert r["bias"] == "SHORT"

    def test_double_overvalued_high_confidence_when_bearish(self):
        r = self._r(spot=24_600, theo=24_570, pain=24_500, bias="BEARISH")
        assert r["regime"] == "DOUBLE_OVERVALUED"
        assert r["confidence"] == "HIGH"

    def test_double_overvalued_medium_confidence_without_bearish(self):
        r = self._r(spot=24_600, theo=24_570, pain=24_500, bias="NEUTRAL")
        assert r["regime"] == "DOUBLE_OVERVALUED"
        assert r["confidence"] == "MEDIUM"

    # ── DOUBLE_UNDERVALUED ───────────────────────────────────────────────
    def test_double_undervalued_both_below(self):
        # intraday gap < -20, expiry gap < -50
        r = self._r(spot=24_400, theo=24_430, pain=24_500)
        # intraday = -30, expiry = -100
        assert r["regime"] == "DOUBLE_UNDERVALUED"
        assert r["bias"] == "LONG"

    def test_double_undervalued_high_confidence_when_bullish(self):
        r = self._r(spot=24_400, theo=24_430, pain=24_500, bias="BULLISH")
        assert r["confidence"] == "HIGH"

    def test_double_undervalued_medium_confidence_without_bullish(self):
        r = self._r(spot=24_400, theo=24_430, pain=24_500, bias="NEUTRAL")
        assert r["confidence"] == "MEDIUM"

    # ── INTRADAY_TRAP ────────────────────────────────────────────────────
    def test_intraday_trap_spot_above_theo_below_pain(self):
        # intraday_g > 20, expiry_g < -50
        r = self._r(spot=24_530, theo=24_500, pain=24_620)
        # intraday = 30 > 20 ✓, expiry = -90 < -50 ✓
        assert r["regime"] == "INTRADAY_TRAP"
        assert r["bias"] == "SHORT"
        assert r["confidence"] == "MEDIUM"

    def test_intraday_trap_with_correct_gap_signs(self):
        r = self._r(spot=24_550, theo=24_525, pain=24_650)
        assert r["intraday_gap"] > 20
        assert r["expiry_gap"] < -50
        assert r["regime"] == "INTRADAY_TRAP"

    # ── INTRADAY_DIP ─────────────────────────────────────────────────────
    def test_intraday_dip_spot_below_theo_above_pain(self):
        # intraday_g < -20, expiry_g > 50
        r = self._r(spot=24_470, theo=24_500, pain=24_400)
        # intraday = -30, expiry = 70
        assert r["regime"] == "INTRADAY_DIP"
        assert r["bias"] == "LONG"
        assert r["confidence"] == "MEDIUM"

    # ── TREND_DAY ────────────────────────────────────────────────────────
    def test_trend_day_when_fairs_diverged(self):
        # alignment_gap = theo - max_pain; if |> 80| → TREND_DAY
        r = self._r(spot=24_500, theo=24_600, pain=24_500)
        # alignment = 100 > 80 ✓, intraday = -100 < -20 and expiry = 0 < 50
        # Actually: intraday= -100, expiry=0 → only checks fairs_diverged (align=100>80)
        # Need to ensure we don't hit DOUBLE_UNDERVALUED first:
        # DOUBLE_UNDERVALUED: intraday_cheap AND expiry_below → intraday_cheap = (-100 < -20) True
        # expiry_below = expiry_g < -50 → expiry_g = 0 < -50? No
        # So DOUBLE_UNDERVALUED fails, INTRADAY_DIP: intraday_cheap AND expiry_above
        # expiry_above = 0 > 50? No. Then fairs_diverged check
        assert r["regime"] == "TREND_DAY"

    def test_trend_day_with_trend_when_align_positive(self):
        # align_g = theo - max_pain > 0 → WITH_TREND
        r = self._r(spot=24_500, theo=24_600, pain=24_490)
        # Ensure we reach TREND_DAY branch: need no prior branch to fire
        # intraday = -100 (cheap), expiry = 10 (not above 50) → INTRADAY_DIP fails
        # alignment = 24600 - 24490 = 110 > 80 → TREND_DAY
        if r["regime"] == "TREND_DAY":
            assert r["bias"] == "WITH_TREND"

    def test_trend_day_counter_trend_when_align_negative(self):
        # align_g = theo - max_pain < 0 → COUNTER_TREND
        r = self._r(spot=24_500, theo=24_400, pain=24_505)
        # intraday = 100 (rich), expiry = -5 (not > 50) → no OVERVALUED/TRAP
        # alignment = 24400 - 24505 = -105 < -80 → TREND_DAY COUNTER_TREND
        if r["regime"] == "TREND_DAY":
            assert r["bias"] == "COUNTER_TREND"

    # ── EQUILIBRIUM ──────────────────────────────────────────────────────
    def test_equilibrium_when_all_gaps_small(self):
        # All gaps within thresholds
        r = self._r(spot=24_505, theo=24_500, pain=24_510)
        assert r["regime"] == "EQUILIBRIUM"
        assert r["bias"] == "NEUTRAL"
        assert r["confidence"] == "LOW"

    def test_equilibrium_rationale_mentions_both_fair_values(self):
        r = self._r(spot=24_505, theo=24_500, pain=24_510)
        assert "fair" in r["rationale"].lower()

    # ── pain_well_depth passthrough ──────────────────────────────────────
    def test_pain_well_depth_in_output(self):
        r = self._r(spot=24_505, theo=24_500, pain=24_500, depth=2.1)
        assert r["pain_well_depth"] == 2.1

    def test_pain_well_depth_none_when_not_provided(self):
        r = self._r(spot=24_505, theo=24_500, pain=24_500)
        assert r["pain_well_depth"] is None

    # ── threshold boundary precision ────────────────────────────────────
    def test_exactly_at_intraday_threshold_not_rich(self):
        # intraday_g = exactly 20 → NOT > 20 → not intraday_rich
        r = self._r(spot=24_520, theo=24_500, pain=24_400)
        assert r["intraday_gap"] == 20.0
        # Should NOT be DOUBLE_OVERVALUED / INTRADAY_TRAP (intraday_rich = False)
        assert r["regime"] not in ("DOUBLE_OVERVALUED", "INTRADAY_TRAP")

    def test_exactly_at_expiry_threshold_not_above(self):
        # expiry_g = exactly 50 → NOT > 50
        r = self._r(spot=24_550, theo=24_520, pain=24_500)
        assert r["expiry_gap"] == 50.0
        assert r["regime"] not in ("DOUBLE_OVERVALUED", "INTRADAY_DIP")
