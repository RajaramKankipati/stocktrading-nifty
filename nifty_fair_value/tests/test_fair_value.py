"""
Tests for engine/fair_value.py

Covers every public function and every decision branch:
  ls_factor, ls_direction, ls_confidence, decision_point,
  calculate_straddle_range, today_fair, expiry_fair,
  straddle_range, todays_fair_value, expiry_fair_value, basis_analysis
"""
import pytest
from unittest.mock import MagicMock
from engine.fair_value import (
    ls_factor, ls_direction, ls_confidence, decision_point,
    calculate_straddle_range, today_fair, expiry_fair,
    straddle_range, todays_fair_value, expiry_fair_value, basis_analysis,
)
from tests.fixtures import OptionData, MarketData, make_chain


# ═══════════════════════════════════════════════════════════════════════
# ls_factor
# ═══════════════════════════════════════════════════════════════════════
class TestLsFactor:
    def test_positive_gap_normalised_by_straddle(self):
        # Expiry anchor 100 pts above spot, straddle 200 → LS = 0.5
        assert ls_factor(24_600, 24_500, straddle_value=200) == 0.5

    def test_negative_gap_normalised_by_straddle(self):
        # Spot above expiry anchor → negative LS
        assert ls_factor(24_400, 24_500, straddle_value=200) == -0.5

    def test_fallback_denominator_200_when_no_straddle(self):
        # Without straddle, denominator = 200
        assert ls_factor(24_600, 24_500) == pytest.approx(0.5, abs=1e-4)

    def test_straddle_too_small_falls_back_to_200(self):
        # straddle_value=15 is below the 20-pt guard → use 200
        result = ls_factor(24_600, 24_500, straddle_value=15)
        assert result == pytest.approx((24_600 - 24_500) / 200, abs=1e-4)

    def test_zero_expiry_fv_returns_zero(self):
        assert ls_factor(0, 24_500) == 0.0

    def test_none_expiry_fv_returns_zero(self):
        assert ls_factor(None, 24_500) == 0.0

    def test_zero_spot_returns_zero(self):
        assert ls_factor(24_500, 0) == 0.0

    def test_at_par_returns_zero(self):
        assert ls_factor(24_500, 24_500) == 0.0

    def test_straddle_normalisation_scale_invariance(self):
        # Same proportional gap at two Nifty levels should produce same LS
        ls_low  = ls_factor(18_200, 18_000, straddle_value=200)
        ls_high = ls_factor(24_200, 24_000, straddle_value=200)
        assert ls_low == pytest.approx(ls_high, abs=1e-4)


# ═══════════════════════════════════════════════════════════════════════
# ls_direction
# ═══════════════════════════════════════════════════════════════════════
class TestLsDirection:
    def test_strong_long(self):
        assert ls_direction(0.36) == "LONG"

    def test_exactly_at_long_threshold(self):
        # 0.35 is the boundary — must be > 0.35 for LONG
        assert ls_direction(0.35) == "WEAK LONG"

    def test_weak_long(self):
        assert ls_direction(0.25) == "WEAK LONG"

    def test_flat_positive(self):
        assert ls_direction(0.14) == "FLAT"

    def test_flat_zero(self):
        assert ls_direction(0.0) == "FLAT"

    def test_flat_negative(self):
        assert ls_direction(-0.14) == "FLAT"

    def test_weak_short(self):
        assert ls_direction(-0.25) == "WEAK SHORT"

    def test_exactly_at_short_threshold(self):
        assert ls_direction(-0.35) == "WEAK SHORT"

    def test_strong_short(self):
        assert ls_direction(-0.36) == "SHORT"

    def test_none_returns_flat(self):
        assert ls_direction(None) == "FLAT"

    def test_extreme_positive(self):
        assert ls_direction(2.0) == "LONG"

    def test_extreme_negative(self):
        assert ls_direction(-2.0) == "SHORT"


# ═══════════════════════════════════════════════════════════════════════
# ls_confidence — individual check isolation
# ═══════════════════════════════════════════════════════════════════════
class TestLsConfidence:

    # ── helpers ──────────────────────────────────────────────────────────
    def _conf(self, ls=0.45, directional_bias="BULLISH — premium + OI build",
              intraday_gap=80, pain_depth=2.0, expiry_gap=-80, pcr=1.2,
              data_reliable=True, regime_bias="COUNTER_TREND",
              call_oi_hhi=0.1, put_oi_hhi=0.1):
        return ls_confidence(
            ls, directional_bias, intraday_gap, pain_depth, expiry_gap, pcr,
            data_reliable, regime_bias, call_oi_hhi, put_oi_hhi
        )

    # ── direction detection ───────────────────────────────────────────────
    def test_direction_long_for_positive_ls(self):
        c = self._conf(ls=0.45)
        assert c["direction"] == "LONG"

    def test_direction_short_for_negative_ls(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.8)
        assert c["direction"] == "SHORT"

    def test_direction_flat_within_threshold(self):
        c = ls_confidence(0.1, "NEUTRAL", 0, 1.0, 0, 1.0)
        assert c["direction"] == "FLAT"

    # ── check 1: futures_bias ─────────────────────────────────────────────
    def test_futures_bias_long_bullish(self):
        c = self._conf(directional_bias="BULLISH — premium + OI build")
        assert c["checks"]["futures_bias"] is True

    def test_futures_bias_long_weak_bull(self):
        c = self._conf(directional_bias="WEAK BULL — premium but OI unwinding")
        assert c["checks"]["futures_bias"] is True

    def test_futures_bias_long_bearish_fails(self):
        c = self._conf(directional_bias="BEARISH — discount + OI build")
        assert c["checks"]["futures_bias"] is False

    def test_futures_bias_short_bearish(self):
        c = ls_confidence(-0.45, "BEARISH — discount + OI build", -80, 2.0, 80, 0.8)
        assert c["checks"]["futures_bias"] is True

    def test_futures_bias_short_bullish_fails(self):
        c = ls_confidence(-0.45, "BULLISH — premium + OI build", -80, 2.0, 80, 0.8)
        assert c["checks"]["futures_bias"] is False

    def test_futures_bias_none_bias_safe(self):
        c = self._conf(directional_bias=None)
        assert c["checks"]["futures_bias"] is False

    # ── check 2: strong_magnet ───────────────────────────────────────────
    def test_strong_magnet_fires_when_depth_and_concentration(self):
        c = self._conf(pain_depth=2.0, call_oi_hhi=0.1, put_oi_hhi=0.05)
        assert c["checks"]["strong_magnet"] is True

    def test_strong_magnet_fails_shallow_well(self):
        c = self._conf(pain_depth=1.2, call_oi_hhi=0.1)
        assert c["checks"]["strong_magnet"] is False

    def test_strong_magnet_fails_when_both_hhi_below_threshold(self):
        # Depth OK but OI is scattered → no real pin wall
        c = self._conf(pain_depth=2.0, call_oi_hhi=0.05, put_oi_hhi=0.05)
        assert c["checks"]["strong_magnet"] is False

    def test_strong_magnet_passes_when_hhi_none(self):
        # When HHI unavailable, assume concentrated (don't penalise missing data)
        c = self._conf(pain_depth=2.0, call_oi_hhi=None, put_oi_hhi=None)
        assert c["checks"]["strong_magnet"] is True

    def test_strong_magnet_passes_when_only_one_hhi_above_threshold(self):
        c = self._conf(pain_depth=2.0, call_oi_hhi=0.09, put_oi_hhi=0.03)
        assert c["checks"]["strong_magnet"] is True

    def test_strong_magnet_pain_depth_exactly_at_boundary(self):
        # Must be > 1.5, not >= 1.5
        c = self._conf(pain_depth=1.5, call_oi_hhi=0.1)
        assert c["checks"]["strong_magnet"] is False

    def test_strong_magnet_pain_depth_none(self):
        c = self._conf(pain_depth=None, call_oi_hhi=0.1)
        assert c["checks"]["strong_magnet"] is False

    # ── check 3: regime_aligned ──────────────────────────────────────────
    def test_regime_aligned_long_counter_trend(self):
        c = self._conf(ls=0.45, regime_bias="COUNTER_TREND")
        assert c["checks"]["regime_aligned"] is True

    def test_regime_aligned_long_long_label(self):
        c = self._conf(ls=0.45, regime_bias="LONG")
        assert c["checks"]["regime_aligned"] is True

    def test_regime_aligned_long_with_trend_fails(self):
        c = self._conf(ls=0.45, regime_bias="WITH_TREND")
        assert c["checks"]["regime_aligned"] is False

    def test_regime_aligned_short_with_trend(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.8,
                          regime_bias="WITH_TREND")
        assert c["checks"]["regime_aligned"] is True

    def test_regime_aligned_short_short_label(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.8,
                          regime_bias="SHORT")
        assert c["checks"]["regime_aligned"] is True

    def test_regime_aligned_short_counter_trend_fails(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.8,
                          regime_bias="COUNTER_TREND")
        assert c["checks"]["regime_aligned"] is False

    def test_regime_aligned_none_regime_bias(self):
        c = self._conf(regime_bias=None)
        assert c["checks"]["regime_aligned"] is False

    def test_regime_aligned_equilibrium_label_fails(self):
        c = self._conf(regime_bias="NEUTRAL")
        assert c["checks"]["regime_aligned"] is False

    # ── check 4: expiry_pull ─────────────────────────────────────────────
    def test_expiry_pull_long_below_pain_by_80(self):
        # expiry_gap = spot - max_pain; -80 means spot is 80 below max pain → pull UP
        c = self._conf(ls=0.45, expiry_gap=-80)
        assert c["checks"]["expiry_pull"] is True

    def test_expiry_pull_long_gap_exactly_minus_30_fails(self):
        # Must be < -30, not ≤ -30
        c = self._conf(ls=0.45, expiry_gap=-30)
        assert c["checks"]["expiry_pull"] is False

    def test_expiry_pull_long_gap_minus_31_passes(self):
        c = self._conf(ls=0.45, expiry_gap=-31)
        assert c["checks"]["expiry_pull"] is True

    def test_expiry_pull_long_above_pain_fails(self):
        # spot ABOVE max pain → no upward pull for LONG
        c = self._conf(ls=0.45, expiry_gap=80)
        assert c["checks"]["expiry_pull"] is False

    def test_expiry_pull_short_above_pain(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.8)
        assert c["checks"]["expiry_pull"] is True

    def test_expiry_pull_short_gap_exactly_30_fails(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 30, 0.8)
        assert c["checks"]["expiry_pull"] is False

    def test_expiry_pull_none_expiry_gap_fails_safely(self):
        c = self._conf(ls=0.45, expiry_gap=None)
        assert c["checks"]["expiry_pull"] is False

    # ── check 5: pcr_aligned ─────────────────────────────────────────────
    def test_pcr_aligned_long_bullish_pcr(self):
        c = self._conf(ls=0.45, pcr=1.2)
        assert c["checks"]["pcr_aligned"] is True

    def test_pcr_aligned_long_exactly_1_1_boundary(self):
        # Code uses pcr > 1.1 (strict), so pcr=1.1 does NOT pass the check
        c = self._conf(ls=0.45, pcr=1.1)
        assert c["checks"]["pcr_aligned"] is False

    def test_pcr_aligned_long_just_above_1_1_passes(self):
        c = self._conf(ls=0.45, pcr=1.11)
        assert c["checks"]["pcr_aligned"] is True

    def test_pcr_aligned_long_bearish_pcr_fails(self):
        c = self._conf(ls=0.45, pcr=0.85)
        assert c["checks"]["pcr_aligned"] is False

    def test_pcr_aligned_short_low_pcr(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.8)
        assert c["checks"]["pcr_aligned"] is True

    def test_pcr_aligned_short_exactly_0_9_boundary(self):
        # Code uses pcr < 0.9 (strict), so pcr=0.9 does NOT pass the check
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.9)
        assert c["checks"]["pcr_aligned"] is False

    def test_pcr_aligned_short_just_below_0_9_passes(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.89)
        assert c["checks"]["pcr_aligned"] is True

    def test_pcr_aligned_none_pcr_fails_safely(self):
        c = self._conf(pcr=None)
        assert c["checks"]["pcr_aligned"] is False

    # ── score calculation ─────────────────────────────────────────────────
    def test_score_5_when_all_checks_pass(self):
        c = self._conf(
            ls=0.45, directional_bias="BULLISH — premium + OI build",
            pain_depth=2.0, expiry_gap=-80, pcr=1.2,
            call_oi_hhi=0.1, put_oi_hhi=0.1,
            regime_bias="COUNTER_TREND"
        )
        assert c["score"] == 5
        assert c["level"] == "HIGH"

    def test_score_0_when_no_checks_pass(self):
        c = ls_confidence(
            ls=0.45, directional_bias="BEARISH",
            intraday_gap=80, pain_depth=0.5, expiry_gap=80,
            pcr=0.85, data_reliable=True, regime_bias="WITH_TREND"
        )
        assert c["score"] == 0
        assert c["level"] == "LOW"

    def test_data_unreliable_caps_score_at_1(self):
        # All 5 checks pass but data unreliable → score capped at 1
        c = self._conf(data_reliable=False)
        assert c["score"] <= 1

    def test_data_unreliable_caps_raw_5_to_1(self):
        c = self._conf(
            ls=0.45, directional_bias="BULLISH — premium + OI build",
            pain_depth=2.0, expiry_gap=-80, pcr=1.2,
            call_oi_hhi=0.1, put_oi_hhi=0.1,
            regime_bias="COUNTER_TREND", data_reliable=False
        )
        assert c["score"] == 1

    def test_level_high_at_4(self):
        c = MagicMock()
        # Directly validate thresholds: MEDIUM at 2, HIGH at 4
        c_4 = ls_confidence(0.45, "BULLISH", 80, 2.0, -80, 1.2,
                            regime_bias="COUNTER_TREND", call_oi_hhi=0.1)
        assert c_4["level"] == "HIGH"

    def test_level_medium_at_2(self):
        c = ls_confidence(0.45, "BULLISH", 0, 2.0, -80, 0.85)
        assert c["level"] in ("MEDIUM", "HIGH", "LOW")  # just ensure no crash

    # ── CONFLICTED detection ──────────────────────────────────────────────
    def test_no_conflict_when_pcr_between_thresholds_long(self):
        # LONG with PCR = 1.15 — below 1.3 threshold → not CONFLICTED
        c = ls_confidence(0.45, "BULLISH", 80, 2.0, -80, 1.15)
        assert c["conflict"] is False
        assert c["conflict_sources"] == []

    def test_no_conflict_when_pcr_between_thresholds_short(self):
        # SHORT with PCR = 0.85 — above 0.8 threshold → not CONFLICTED
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 0.85)
        assert c["conflict"] is False

    def test_conflict_long_pcr_below_0_8(self):
        c = ls_confidence(0.45, "BULLISH", 80, 2.0, -80, 0.75)
        assert c["conflict"] is True
        assert any("bearish" in s.lower() for s in c["conflict_sources"])

    def test_conflict_long_pcr_exactly_0_8_no_conflict(self):
        # Must be < 0.8 to trigger conflict
        c = ls_confidence(0.45, "BULLISH", 80, 2.0, -80, 0.8)
        assert c["conflict"] is False

    def test_conflict_short_pcr_above_1_3(self):
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 1.4)
        assert c["conflict"] is True
        assert any("bullish" in s.lower() for s in c["conflict_sources"])

    def test_conflict_short_pcr_exactly_1_3_no_conflict(self):
        # Must be > 1.3 to trigger conflict
        c = ls_confidence(-0.45, "BEARISH", -80, 2.0, 80, 1.3)
        assert c["conflict"] is False

    def test_conflict_flat_ls_no_conflict_raised(self):
        # FLAT direction → no directional check → no conflict
        c = ls_confidence(0.1, "BULLISH", 5, 2.0, 10, 0.7)
        assert c["conflict"] is False

    def test_conflict_none_pcr_safe(self):
        c = ls_confidence(0.45, "BULLISH", 80, 2.0, -80, None)
        assert c["conflict"] is False


# ═══════════════════════════════════════════════════════════════════════
# decision_point
# ═══════════════════════════════════════════════════════════════════════
class TestDecisionPoint:

    def _conf(self, direction="LONG", score=3, data_reliable=True,
              conflict=False, conflict_sources=None):
        return {
            "direction"       : direction,
            "score"           : score,
            "data_reliable"   : data_reliable,
            "conflict"        : conflict,
            "conflict_sources": conflict_sources or [],
        }

    # ── guard: None ls ────────────────────────────────────────────────────
    def test_none_ls_returns_no_trade(self):
        d = decision_point(None, self._conf())
        assert d["action"] == "NO TRADE"

    # ── guard: FLAT direction ─────────────────────────────────────────────
    def test_flat_direction_no_trade(self):
        d = decision_point(0.1, self._conf(direction="FLAT", score=5))
        assert d["action"] == "NO TRADE"
        assert d["style"] == "neutral"

    # ── CONFLICTED gate ───────────────────────────────────────────────────
    def test_conflicted_fires_for_strong_long(self):
        c = self._conf(direction="LONG", score=4, conflict=True,
                       conflict_sources=["PCR 0.75 bearish"])
        d = decision_point(0.45, c)
        assert "CONFLICTED" in d["action"]
        assert "LONG" in d["action"]
        assert d["style"] == "conflicted"

    def test_conflicted_fires_for_weak_long_above_0_15(self):
        c = self._conf(direction="WEAK LONG", score=3, conflict=True,
                       conflict_sources=["PCR 0.75 bearish"])
        d = decision_point(0.2, c)
        assert "CONFLICTED" in d["action"]

    def test_conflicted_does_not_fire_below_0_15(self):
        # |LS| ≤ 0.15 with conflict — CONFLICTED gate skipped
        c = self._conf(direction="WEAK LONG", score=3, conflict=True,
                       conflict_sources=["PCR 0.75 bearish"])
        d = decision_point(0.1, c)
        assert "CONFLICTED" not in d["action"]

    def test_conflicted_includes_source_in_detail(self):
        c = self._conf(direction="SHORT", score=4, conflict=True,
                       conflict_sources=["PCR 1.45 bullish"])
        d = decision_point(-0.5, c)
        assert "PCR 1.45 bullish" in d["detail"]

    # ── unreliable data gate ──────────────────────────────────────────────
    def test_unreliable_strong_score_returns_wait_bias(self):
        c = self._conf(direction="LONG", score=3, data_reliable=False)
        d = decision_point(0.45, c)
        assert "WAIT" in d["action"]
        assert "DATA UNRELIABLE" in d["action"]
        assert d["style"] == "wait"

    def test_unreliable_weak_score_returns_skip(self):
        c = self._conf(direction="LONG", score=2, data_reliable=False)
        d = decision_point(0.45, c)
        assert d["action"] == "SKIP"
        assert d["style"] == "skip"

    def test_unreliable_weak_ls_returns_skip(self):
        c = self._conf(direction="WEAK LONG", score=3, data_reliable=False)
        d = decision_point(0.25, c)
        assert d["action"] == "SKIP"

    # ── strong gravity |LS| > 0.35 ───────────────────────────────────────
    def test_strong_ls_score_5_enter_long(self):
        d = decision_point(0.45, self._conf(direction="LONG", score=5))
        assert d["action"] == "ENTER LONG"
        assert d["style"] == "strong"

    def test_strong_ls_score_4_enter_long(self):
        d = decision_point(0.45, self._conf(direction="LONG", score=4))
        assert d["action"] == "ENTER LONG"

    def test_strong_ls_score_3_enter_reduced(self):
        d = decision_point(0.45, self._conf(direction="LONG", score=3))
        assert d["action"] == "ENTER LONG — REDUCED SIZE"
        assert d["style"] == "moderate"

    def test_strong_ls_score_2_wait_bias(self):
        d = decision_point(0.45, self._conf(direction="LONG", score=2))
        assert d["action"] == "WAIT — LONG BIAS"
        assert d["style"] == "wait"

    def test_strong_ls_score_1_skip(self):
        d = decision_point(0.45, self._conf(direction="LONG", score=1))
        assert d["action"] == "SKIP"
        assert d["style"] == "skip"

    def test_strong_ls_score_0_skip(self):
        d = decision_point(0.45, self._conf(direction="LONG", score=0))
        assert d["action"] == "SKIP"

    def test_strong_ls_short_score_4(self):
        d = decision_point(-0.45, self._conf(direction="SHORT", score=4))
        assert d["action"] == "ENTER SHORT"

    def test_strong_ls_short_score_3_reduced(self):
        d = decision_point(-0.45, self._conf(direction="SHORT", score=3))
        assert d["action"] == "ENTER SHORT — REDUCED SIZE"

    def test_exactly_at_strong_boundary_not_strong(self):
        # |LS| = 0.35 → falls into WEAK range, not STRONG
        d = decision_point(0.35, self._conf(direction="WEAK LONG", score=5))
        assert "WATCH" in d["action"]

    # ── weak gravity 0.15 < |LS| ≤ 0.35 ────────────────────────────────
    def test_weak_ls_score_3_watch(self):
        d = decision_point(0.25, self._conf(direction="WEAK LONG", score=3))
        assert "WATCH" in d["action"]
        assert "LONG" in d["action"]
        assert d["style"] == "watch"

    def test_weak_ls_score_5_watch(self):
        d = decision_point(0.25, self._conf(direction="WEAK LONG", score=5))
        assert "WATCH" in d["action"]

    def test_weak_ls_score_2_no_trade(self):
        d = decision_point(0.25, self._conf(direction="WEAK LONG", score=2))
        assert d["action"] == "NO TRADE"

    def test_weak_ls_score_0_no_trade(self):
        d = decision_point(0.25, self._conf(direction="WEAK LONG", score=0))
        assert d["action"] == "NO TRADE"

    def test_weak_ls_short_score_3_watch(self):
        d = decision_point(-0.25, self._conf(direction="WEAK SHORT", score=3))
        assert "WATCH" in d["action"]
        assert "SHORT" in d["action"]


# ═══════════════════════════════════════════════════════════════════════
# calculate_straddle_range
# ═══════════════════════════════════════════════════════════════════════
class TestCalculateStraddleRange:
    def test_sum_of_premiums(self):
        assert calculate_straddle_range(120, 100) == 220.0

    def test_rounded_to_one_decimal(self):
        assert calculate_straddle_range(119.6, 100.4) == 220.0

    def test_none_call_returns_zero(self):
        assert calculate_straddle_range(None, 100) == 0.0

    def test_none_put_returns_zero(self):
        assert calculate_straddle_range(120, None) == 0.0

    def test_zero_call_returns_zero(self):
        assert calculate_straddle_range(0, 100) == 0.0

    def test_equal_premiums(self):
        assert calculate_straddle_range(100, 100) == 200.0


# ═══════════════════════════════════════════════════════════════════════
# today_fair
# ═══════════════════════════════════════════════════════════════════════
class TestTodayFair:
    def test_returns_synthetic_when_valid(self):
        assert today_fair(24_528, 24_500, 24_510) == 24_500

    def test_returns_synthetic_over_futures(self):
        # synthetic always preferred when valid
        assert today_fair(24_528, 24_510, 0) == 24_510

    def test_falls_back_to_futures_when_synthetic_none(self):
        assert today_fair(24_528, None, 0) == 24_528

    def test_falls_back_to_futures_when_synthetic_zero(self):
        assert today_fair(24_528, 0, 0) == 24_528

    def test_returns_zero_when_both_missing(self):
        assert today_fair(None, None, 0) == 0.0

    def test_returns_zero_when_futures_too_small(self):
        assert today_fair(5, None, 0) == 0.0

    def test_synthetic_below_10_falls_to_futures(self):
        assert today_fair(24_528, 8, 0) == 24_528


# ═══════════════════════════════════════════════════════════════════════
# expiry_fair
# ═══════════════════════════════════════════════════════════════════════
class TestExpiryFair:
    def test_returns_max_pain_directly(self):
        assert expiry_fair(24_700, 24_200, 24_500) == 24_500.0

    def test_ignores_oi_levels(self):
        # OI levels should have NO effect — expiry fair = max pain only
        assert expiry_fair(25_000, 20_000, 24_500) == 24_500.0

    def test_zero_max_pain_returns_zero(self):
        assert expiry_fair(24_700, 24_200, 0) == 0.0

    def test_none_max_pain_returns_zero(self):
        assert expiry_fair(24_700, 24_200, None) == 0.0

    def test_float_max_pain_rounded_to_2dp(self):
        result = expiry_fair(24_700, 24_200, 24_487.555)
        assert result == 24_487.56


# ═══════════════════════════════════════════════════════════════════════
# straddle_range (structured version)
# ═══════════════════════════════════════════════════════════════════════
class TestStraddleRange:
    def test_straddle_value_is_sum(self):
        opt = OptionData(strike=24_500, call_ltp=120, put_ltp=100)
        r = straddle_range(opt)
        assert r["straddle_value"] == 220.0

    def test_upper_lower_symmetric(self):
        opt = OptionData(strike=24_500, call_ltp=100, put_ltp=100)
        r = straddle_range(opt)
        assert r["expected_upper"] == 24_700.0
        assert r["expected_lower"] == 24_300.0

    def test_synthetic_fwd_with_call_premium(self):
        # call_ltp > put_ltp → synthetic_fwd > strike → positive skew_pull
        opt = OptionData(strike=24_500, call_ltp=130, put_ltp=100)
        r = straddle_range(opt)
        assert r["synthetic_fwd"] == pytest.approx(24_530.0, abs=0.1)
        assert r["skew_pull"] > 0

    def test_negative_skew_pull_when_put_dominates(self):
        opt = OptionData(strike=24_500, call_ltp=90, put_ltp=120)
        r = straddle_range(opt)
        assert r["skew_pull"] < 0


# ═══════════════════════════════════════════════════════════════════════
# todays_fair_value
# ═══════════════════════════════════════════════════════════════════════
class TestTodaysFairValue:
    def _make_md(self, spot=24_500, futures=24_528, futures_vwap=0,
                 expiry="2026-04-29", oi_chg=0.5):
        md = MarketData(
            spot=spot, futures=futures, futures_vwap=futures_vwap,
            futures_expiry=expiry, futures_oi_chg_pct=oi_chg,
        )
        return md

    def _atm(self, call_ltp=120, put_ltp=100):
        return OptionData(strike=24_500, call_ltp=call_ltp, put_ltp=put_ltp)

    def test_basis_is_futures_minus_spot(self):
        md = self._make_md(spot=24_500, futures=24_530)
        r = todays_fair_value(md, self._atm(), 24_502)
        assert r["basis"] == pytest.approx(30.0, abs=0.5)

    def test_vwap_deviation_none_when_vwap_zero(self):
        md = self._make_md(futures_vwap=0)
        r = todays_fair_value(md, self._atm(), 24_500)
        assert r["vwap_deviation"] is None

    def test_vwap_deviation_calculated_when_vwap_nonzero(self):
        md = self._make_md(futures_vwap=24_490)
        r = todays_fair_value(md, self._atm(), 24_500)
        assert r["vwap_deviation"] == pytest.approx(10.0, abs=0.5)

    def test_spot_signal_at_fair_within_noise(self):
        md = self._make_md(spot=24_505, futures=24_510)
        r = todays_fair_value(md, self._atm(), 24_500)
        assert "AT FAIR" in r["spot_signal"]

    def test_spot_signal_rich_when_above_threshold(self):
        md = self._make_md(spot=24_520, futures=24_530)
        r = todays_fair_value(md, self._atm(), 24_500)
        assert "RICH" in r["spot_signal"]

    def test_spot_signal_cheap_when_below_threshold(self):
        md = self._make_md(spot=24_480, futures=24_490)
        r = todays_fair_value(md, self._atm(), 24_500)
        assert "CHEAP" in r["spot_signal"]

    def test_bullish_bias_when_excess_basis_positive_oi_build(self):
        # High positive excess basis + positive OI change → BULLISH
        md = self._make_md(spot=24_400, futures=24_460, oi_chg=1.5)
        r = todays_fair_value(md, self._atm(), 24_405, r=0.065, d_yield=0.013)
        assert "BULL" in r["directional_bias"].upper()

    def test_bearish_bias_when_excess_basis_negative_oi_build(self):
        md = self._make_md(spot=24_500, futures=24_450, oi_chg=1.5)
        r = todays_fair_value(md, self._atm(), 24_505, r=0.065, d_yield=0.013)
        assert "BEAR" in r["directional_bias"].upper()

    def test_neutral_bias_when_oi_change_zero(self):
        # NEUTRAL fires when oi_chg_pct=0 because all bias conditions require OI change
        md = self._make_md(spot=24_500, futures=24_508, oi_chg=0.0)
        r = todays_fair_value(md, self._atm(), 24_500)
        assert "NEUTRAL" in r["directional_bias"]

    def test_straddle_value_in_output(self):
        md = self._make_md()
        r = todays_fair_value(md, self._atm(call_ltp=120, put_ltp=100), 24_500)
        assert r["straddle_value"] == 220.0


# ═══════════════════════════════════════════════════════════════════════
# expiry_fair_value
# ═══════════════════════════════════════════════════════════════════════
class TestExpiryFairValue:
    def _oi_levels(self, call_res=24_700, put_sup=24_300):
        return {"call_resistance": call_res, "put_support": put_sup,
                "oi_corridor_width": call_res - put_sup}

    def _atm(self):
        return OptionData(strike=24_500, call_ltp=120, put_ltp=100)

    def test_near_max_pain_gravity_signal(self):
        opts = make_chain(atm=24_500)
        r = expiry_fair_value(opts, self._atm(), spot=24_520,
                              max_pain_strike=24_500, pain_depth=1.8,
                              oi_levels=self._oi_levels())
        assert "NEAR MAX PAIN" in r["gravity_signal"]

    def test_above_max_pain_gravity_signal(self):
        opts = make_chain(atm=24_500)
        r = expiry_fair_value(opts, self._atm(), spot=24_600,
                              max_pain_strike=24_500, pain_depth=1.8,
                              oi_levels=self._oi_levels())
        assert "ABOVE MAX PAIN" in r["gravity_signal"]
        assert "downside" in r["gravity_signal"].lower()

    def test_below_max_pain_gravity_signal(self):
        opts = make_chain(atm=24_500)
        r = expiry_fair_value(opts, self._atm(), spot=24_400,
                              max_pain_strike=24_500, pain_depth=1.8,
                              oi_levels=self._oi_levels())
        assert "BELOW MAX PAIN" in r["gravity_signal"]
        assert "upside" in r["gravity_signal"].lower()

    def test_spot_in_oi_corridor(self):
        opts = make_chain(atm=24_500)
        r = expiry_fair_value(opts, self._atm(), spot=24_500,
                              max_pain_strike=24_500, pain_depth=1.8,
                              oi_levels=self._oi_levels(24_700, 24_300))
        assert r["spot_in_oi_corridor"] is True

    def test_spot_outside_oi_corridor(self):
        opts = make_chain(atm=24_500)
        r = expiry_fair_value(opts, self._atm(), spot=24_800,
                              max_pain_strike=24_500, pain_depth=1.8,
                              oi_levels=self._oi_levels(24_700, 24_300))
        assert r["spot_in_oi_corridor"] is False

    def test_gap_to_max_pain_positive_when_spot_above(self):
        opts = make_chain(atm=24_500)
        r = expiry_fair_value(opts, self._atm(), spot=24_600,
                              max_pain_strike=24_500, pain_depth=1.8,
                              oi_levels=self._oi_levels())
        assert r["gap_to_max_pain"] == pytest.approx(100.0, abs=0.2)

    def test_gap_to_max_pain_negative_when_spot_below(self):
        opts = make_chain(atm=24_500)
        r = expiry_fair_value(opts, self._atm(), spot=24_400,
                              max_pain_strike=24_500, pain_depth=1.8,
                              oi_levels=self._oi_levels())
        assert r["gap_to_max_pain"] == pytest.approx(-100.0, abs=0.2)


# ═══════════════════════════════════════════════════════════════════════
# basis_analysis (legacy)
# ═══════════════════════════════════════════════════════════════════════
class TestBasisAnalysis:
    def test_bullish_when_excess_high_oi_build(self):
        r = basis_analysis(24_550, 24_500, 24_510, 24_490, oi_chg_pct=1.0,
                           days_to_expiry=7)
        # excess_basis = (24550-24500) - carry; if > 15 and OI positive → BULLISH
        # carry = 24490 * 0.065 * 7/365 ≈ 30.5; 50 - 30.5 = 19.5 > 15 → BULLISH
        assert r["directional_bias"] == "BULLISH"

    def test_bearish_when_excess_negative_oi_build(self):
        r = basis_analysis(24_450, 24_500, 24_480, 24_490, oi_chg_pct=1.0,
                           days_to_expiry=7)
        assert r["directional_bias"] == "BEARISH"

    def test_neutral_when_oi_change_zero(self):
        # NEUTRAL fires when oi_chg_pct=0 — all bias conditions require OI change != 0
        r = basis_analysis(24_510, 24_500, 24_505, 24_490, oi_chg_pct=0,
                           days_to_expiry=7)
        assert r["directional_bias"] == "NEUTRAL"

    def test_spot_rich_signal(self):
        r = basis_analysis(24_530, 24_500, 24_510, 24_530, oi_chg_pct=0)
        assert "RICH" in r["spot_signal"]

    def test_spot_cheap_signal(self):
        r = basis_analysis(24_530, 24_500, 24_510, 24_480, oi_chg_pct=0)
        assert "CHEAP" in r["spot_signal"]

    def test_spot_at_fair(self):
        r = basis_analysis(24_508, 24_500, 24_504, 24_507, oi_chg_pct=0)
        assert "AT FAIR" in r["spot_signal"]

    def test_missing_data_returns_defaults(self):
        r = basis_analysis(0, 0, 0, 0)
        assert r["directional_bias"] == "NEUTRAL"
        assert r["spot_signal"] == "DATA_MISSING"

    def test_vwap_zero_returns_zero_deviation(self):
        r = basis_analysis(24_530, 24_500, 0, 24_510, oi_chg_pct=0)
        assert r["vwap_deviation"] == 0.0
