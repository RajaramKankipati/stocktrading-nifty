"""
Tests for engine/signals.py — generate_execution_setup

Decision tree tested:
  Setup C SHORT — spot breaks above call wall, synthetic BELOW wall  → SHORT trap
  Setup C LONG  — spot breaks below put wall, synthetic ABOVE wall   → LONG trap
  Setup C priority — C fires before B even when B conditions also met
  Setup B LONG  — ls > 0.35 + put wall shifting up
  Setup B SHORT — ls < -0.35 + call wall shifting down
  Setup B no-fire — ls strong but no baselines (shift undetectable)
  Setup A LONG  — ls > 0.35 + positive basis (futures > spot)
  Setup A SHORT — ls < -0.35 + negative basis (futures < spot)
  Setup A no-fire — ls strong but basis wrong sign
  Fallback BULLISH — ls > 0.15, no setup conditions met
  Fallback BEARISH — ls < -0.15
  Fallback NEUTRAL — ls ≈ 0

  Risk sizing: SL = straddle * 0.5 distance from entry
  Risk/reward: returned as "1:X" string
"""
import pytest
from engine.signals import generate_execution_setup
from tests.fixtures import MarketData


class MD:
    """Minimal market data stub."""
    def __init__(self, spot=24_500, futures=24_520):
        self.spot    = spot
        self.futures = futures


# ═══════════════════════════════════════════════════════════════════════
# Setup C — Breakout Trap
# ═══════════════════════════════════════════════════════════════════════
class TestSetupC:

    def test_short_trap_fires_when_spot_above_call_wall_and_synthetic_low(self):
        # spot >= call_level + 10 AND theo < call_level → SHORT trap
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_690,
            call_level=24_700, put_level=24_300, straddle_range=200, ls=-0.4
        )
        assert s["signal"] == "SHORT"
        assert s["type"] == "Breakout Trap"

    def test_long_trap_fires_when_spot_below_put_wall_and_synthetic_high(self):
        # spot <= put_level - 10 AND theo > put_level → LONG trap
        s = generate_execution_setup(
            MD(spot=24_285, futures=24_270),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_320,
            call_level=24_700, put_level=24_300, straddle_range=200, ls=0.4
        )
        assert s["signal"] == "LONG"
        assert s["type"] == "Breakout Trap"

    def test_short_trap_not_fire_when_synthetic_agrees(self):
        # Spot breaks above call wall but synthetic ALSO above → not a trap
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_720,
            call_level=24_700, put_level=24_300, straddle_range=200, ls=-0.4
        )
        assert s["type"] != "Breakout Trap"

    def test_long_trap_not_fire_when_synthetic_below_put(self):
        # Spot breaks below put wall but synthetic ALSO below → not a trap
        s = generate_execution_setup(
            MD(spot=24_285, futures=24_270),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_280,
            call_level=24_700, put_level=24_300, straddle_range=200, ls=0.4
        )
        assert s["type"] != "Breakout Trap"

    def test_short_trap_sl_above_entry(self):
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_690,
            call_level=24_700, put_level=24_300, straddle_range=200, ls=-0.4
        )
        assert s["sl"] > s["entry"]

    def test_long_trap_sl_below_entry(self):
        s = generate_execution_setup(
            MD(spot=24_285, futures=24_270),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_320,
            call_level=24_700, put_level=24_300, straddle_range=200, ls=0.4
        )
        assert s["sl"] < s["entry"]

    def test_sl_sized_as_straddle_half(self):
        straddle = 200
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_690,
            call_level=24_700, put_level=24_300, straddle_range=straddle, ls=-0.4
        )
        assert abs(s["sl"] - s["entry"]) == pytest.approx(straddle * 0.5, abs=1)

    def test_risk_reward_returned(self):
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_690,
            call_level=24_700, put_level=24_300, straddle_range=200, ls=-0.4
        )
        assert "1:" in s["risk_reward"]

    def test_setup_c_fires_before_setup_b(self):
        """Priority: C overrides B even when B conditions are also met."""
        baselines = {"put_level": 24_180, "call_level": 24_750}
        # B LONG conditions: ls > 0.35 AND put_shifting_up (24300 > 24180+10)
        # C SHORT conditions: spot above call wall, theo below
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_690,
            call_level=24_700, put_level=24_300,
            straddle_range=200, baselines=baselines, ls=0.5
        )
        # C SHORT should win because spot > call_level + 10 and theo < call_level
        assert s["type"] == "Breakout Trap"

    def test_target_uses_expiry_fv_when_available(self):
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_450, theoretical_price=24_690,
            call_level=24_700, put_level=24_300, straddle_range=200, ls=-0.4
        )
        assert s["target"] == pytest.approx(24_450, abs=1)

    def test_minimum_sl_distance_20pts_when_straddle_small(self):
        # straddle=10 → risk_unit = max(5, 20) = 20
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_690,
            call_level=24_700, put_level=24_300, straddle_range=10, ls=-0.4
        )
        assert abs(s["sl"] - s["entry"]) >= 20


# ═══════════════════════════════════════════════════════════════════════
# Setup B — Trend Momentum
# ═══════════════════════════════════════════════════════════════════════
class TestSetupB:

    def test_long_momentum_fires_ls_strong_put_shifting(self):
        baselines = {"put_level": 24_150, "call_level": 24_750}
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_390),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_398,
            call_level=24_700, put_level=24_200,  # put wall shifted up 50pts
            straddle_range=200, baselines=baselines, ls=0.45
        )
        assert s["signal"] == "LONG"
        assert s["type"] == "Trend Momentum"

    def test_short_momentum_fires_ls_negative_call_shifting(self):
        baselines = {"put_level": 24_200, "call_level": 24_800}
        s = generate_execution_setup(
            MD(spot=24_600, futures=24_610),
            today_fv=24_610, expiry_fv=24_500, theoretical_price=24_605,
            call_level=24_750,  # call wall shifted DOWN from 24800 → 24750, diff=50 > 10
            put_level=24_200,
            straddle_range=200, baselines=baselines, ls=-0.45
        )
        assert s["signal"] == "SHORT"
        assert s["type"] == "Trend Momentum"

    def test_b_not_fire_without_baselines(self):
        # No baselines → shift detection returns False → B cannot fire
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_390),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_398,
            call_level=24_700, put_level=24_200,
            straddle_range=200, baselines=None, ls=0.45
        )
        assert s["type"] != "Trend Momentum"

    def test_b_not_fire_when_ls_below_threshold(self):
        baselines = {"put_level": 24_150, "call_level": 24_750}
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_390),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_398,
            call_level=24_700, put_level=24_200,
            straddle_range=200, baselines=baselines, ls=0.3  # < 0.35
        )
        assert s["type"] != "Trend Momentum"

    def test_b_not_fire_when_wall_shift_below_10pts(self):
        baselines = {"put_level": 24_195, "call_level": 24_750}  # shift only 5 pts
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_390),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_398,
            call_level=24_700, put_level=24_200,
            straddle_range=200, baselines=baselines, ls=0.45
        )
        assert s["type"] != "Trend Momentum"

    def test_b_long_sl_below_entry(self):
        baselines = {"put_level": 24_150, "call_level": 24_750}
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_390),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_398,
            call_level=24_700, put_level=24_200,
            straddle_range=200, baselines=baselines, ls=0.45
        )
        if s["type"] == "Trend Momentum":
            assert s["sl"] < s["entry"]

    def test_b_short_sl_above_entry(self):
        baselines = {"put_level": 24_200, "call_level": 24_800}
        s = generate_execution_setup(
            MD(spot=24_600, futures=24_610),
            today_fv=24_610, expiry_fv=24_500, theoretical_price=24_605,
            call_level=24_750, put_level=24_200,
            straddle_range=200, baselines=baselines, ls=-0.45
        )
        if s["type"] == "Trend Momentum":
            assert s["sl"] > s["entry"]

    def test_b_target_is_entry_plus_straddle(self):
        baselines = {"put_level": 24_150, "call_level": 24_750}
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_390),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_398,
            call_level=24_700, put_level=24_200,
            straddle_range=200, baselines=baselines, ls=0.45
        )
        if s["type"] == "Trend Momentum":
            assert s["target"] == pytest.approx(s["entry"] + 200, abs=1)


# ═══════════════════════════════════════════════════════════════════════
# Setup A — Expiry Gravity
# ═══════════════════════════════════════════════════════════════════════
class TestSetupA:

    def test_long_gravity_fires_ls_positive_positive_basis(self):
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_425),   # basis = +25 (positive)
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=0.45
        )
        assert s["signal"] == "LONG"
        assert s["type"] == "Expiry Gravity"

    def test_short_gravity_fires_ls_negative_negative_basis(self):
        s = generate_execution_setup(
            MD(spot=24_600, futures=24_575),   # basis = -25 (negative)
            today_fv=24_610, expiry_fv=24_500, theoretical_price=24_605,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=-0.45
        )
        assert s["signal"] == "SHORT"
        assert s["type"] == "Expiry Gravity"

    def test_a_long_not_fire_when_basis_negative(self):
        # ls > 0.35 but basis < 0 → institutional flow not supporting upside
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_375),   # basis = -25
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=0.45
        )
        assert s["type"] != "Expiry Gravity"

    def test_a_short_not_fire_when_basis_positive(self):
        s = generate_execution_setup(
            MD(spot=24_600, futures=24_625),   # basis = +25
            today_fv=24_610, expiry_fv=24_500, theoretical_price=24_605,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=-0.45
        )
        assert s["type"] != "Expiry Gravity"

    def test_a_not_fire_when_ls_below_threshold(self):
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_425),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=0.3  # < 0.35
        )
        assert s["type"] != "Expiry Gravity"

    def test_a_long_sl_below_entry(self):
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_425),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=0.45
        )
        if s["type"] == "Expiry Gravity":
            assert s["sl"] < s["entry"]

    def test_a_short_sl_above_entry(self):
        s = generate_execution_setup(
            MD(spot=24_600, futures=24_575),
            today_fv=24_610, expiry_fv=24_500, theoretical_price=24_605,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=-0.45
        )
        if s["type"] == "Expiry Gravity":
            assert s["sl"] > s["entry"]

    def test_a_target_uses_expiry_fv(self):
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_425),
            today_fv=24_395, expiry_fv=24_520, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=0.45
        )
        if s["type"] == "Expiry Gravity":
            assert s["target"] == pytest.approx(24_520, abs=1)

    def test_a_target_uses_straddle_fallback_when_no_expiry_fv(self):
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_425),
            today_fv=24_395, expiry_fv=None, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=0.45
        )
        if s["type"] == "Expiry Gravity":
            # fallback target = spot + straddle * 0.7 = 24400 + 140 = 24540
            assert s["target"] == pytest.approx(24_400 + 200 * 0.7, abs=2)

    def test_a_sl_sized_correctly(self):
        straddle = 200
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_425),
            today_fv=24_395, expiry_fv=24_520, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=straddle, ls=0.45
        )
        if s["type"] == "Expiry Gravity":
            assert abs(s["sl"] - s["entry"]) == pytest.approx(straddle * 0.5, abs=1)


# ═══════════════════════════════════════════════════════════════════════
# Fallback — directional bias only
# ═══════════════════════════════════════════════════════════════════════
class TestFallback:

    def test_bullish_when_ls_above_0_15(self):
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_390),   # basis = -10
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=0.2
        )
        assert s["signal"] == "BULLISH"
        assert s["type"] == "No Active Setup"

    def test_bearish_when_ls_below_minus_0_15(self):
        s = generate_execution_setup(
            MD(spot=24_600, futures=24_615),   # basis = +15 → wouldn't trigger A SHORT
            today_fv=24_610, expiry_fv=24_500, theoretical_price=24_605,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=-0.2
        )
        assert s["signal"] == "BEARISH"
        assert s["type"] == "No Active Setup"

    def test_neutral_when_ls_flat(self):
        s = generate_execution_setup(
            MD(spot=24_500, futures=24_505),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_502,
            call_level=24_700, put_level=24_300,
            straddle_range=200, ls=0.05
        )
        assert s["signal"] == "NEUTRAL"

    def test_neutral_when_ls_zero(self):
        s = generate_execution_setup(
            MD(spot=24_500, futures=24_500),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_500,
            call_level=24_700, put_level=24_300,
            straddle_range=200, ls=0.0
        )
        assert s["signal"] == "NEUTRAL"

    def test_default_setup_fields_present(self):
        s = generate_execution_setup(
            MD(spot=24_500, futures=24_500),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_500,
            call_level=24_700, put_level=24_300,
            straddle_range=200, ls=0.0
        )
        for key in ("signal", "type", "entry", "sl", "target", "trailing", "risk_reward"):
            assert key in s


# ═══════════════════════════════════════════════════════════════════════
# Risk sizing and risk/reward
# ═══════════════════════════════════════════════════════════════════════
class TestRiskSizing:

    def test_risk_reward_format(self):
        s = generate_execution_setup(
            MD(spot=24_400, futures=24_425),
            today_fv=24_395, expiry_fv=24_500, theoretical_price=24_402,
            call_level=24_700, put_level=24_200,
            straddle_range=200, ls=0.45
        )
        if s["type"] != "No Active Setup":
            assert s["risk_reward"].startswith("1:")

    def test_minimum_risk_unit_is_20(self):
        """straddle_range=None → risk_unit defaults to 30."""
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_690,
            call_level=24_700, put_level=24_300,
            straddle_range=None, ls=-0.4
        )
        if s["type"] == "Breakout Trap":
            assert abs(s["sl"] - s["entry"]) >= 20

    def test_straddle_0_uses_30_as_risk_unit(self):
        s = generate_execution_setup(
            MD(spot=24_715, futures=24_730),
            today_fv=24_500, expiry_fv=24_500, theoretical_price=24_690,
            call_level=24_700, put_level=24_300,
            straddle_range=0, ls=-0.4
        )
        if s["type"] == "Breakout Trap":
            assert abs(s["sl"] - s["entry"]) == pytest.approx(30, abs=1)
