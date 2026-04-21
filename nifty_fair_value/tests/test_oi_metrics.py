"""
Tests for engine/oi_metrics.py

Covers: pcr_oi, pcr_notional, pcr_near_atm, pcr_at_strike
"""
import pytest
from engine.oi_metrics import pcr_oi, pcr_notional, pcr_near_atm, pcr_at_strike
from tests.fixtures import OptionData


class TestPcrOi:
    def test_bullish_market_high_put_oi(self):
        r = pcr_oi(total_ce_oi=1_000_000, total_pe_oi=1_200_000)
        assert r == pytest.approx(1.2, abs=0.0001)

    def test_bearish_market_low_put_oi(self):
        r = pcr_oi(total_ce_oi=1_000_000, total_pe_oi=800_000)
        assert r == pytest.approx(0.8, abs=0.0001)

    def test_balanced_market_pcr_1(self):
        r = pcr_oi(total_ce_oi=1_000_000, total_pe_oi=1_000_000)
        assert r == pytest.approx(1.0, abs=0.0001)

    def test_zero_ce_oi_returns_none(self):
        r = pcr_oi(total_ce_oi=0, total_pe_oi=1_000_000)
        assert r is None

    def test_rounded_to_4dp(self):
        r = pcr_oi(total_ce_oi=3, total_pe_oi=2)
        assert r == pytest.approx(2 / 3, abs=0.00005)


class TestPcrNotional:
    def _chain(self, ce_oi=100, pe_oi=120, ce_ltp=100, pe_ltp=90):
        return [OptionData(strike=24_500,
                           call_oi=ce_oi, put_oi=pe_oi,
                           call_ltp=ce_ltp, put_ltp=pe_ltp)]

    def test_basic_ratio(self):
        opts = self._chain(ce_oi=100, pe_oi=100, ce_ltp=100, pe_ltp=100)
        r = pcr_notional(opts, lot_size=65)
        assert r == pytest.approx(1.0, abs=0.001)

    def test_higher_pe_notional_above_1(self):
        opts = self._chain(ce_oi=100, pe_oi=120, ce_ltp=100, pe_ltp=100)
        r = pcr_notional(opts, lot_size=65)
        assert r > 1.0

    def test_zero_ce_notional_returns_zero(self):
        opts = [OptionData(strike=24_500, call_oi=0, put_oi=100,
                           call_ltp=0, put_ltp=100)]
        r = pcr_notional(opts)
        assert r == 0.0


# ═══════════════════════════════════════════════════════════════════════
# pcr_near_atm
# ═══════════════════════════════════════════════════════════════════════
class TestPcrNearAtm:

    def _chain(self, strikes, call_oi=100_000, put_oi=120_000):
        """Build a uniform chain; all strikes share the same OI unless overridden."""
        return [OptionData(strike=s, call_oi=call_oi, put_oi=put_oi)
                for s in strikes]

    def test_empty_options_returns_none(self):
        assert pcr_near_atm([], atm_strike=24_500) is None

    def test_basic_ratio_uses_only_atm_window(self):
        # Window of ±3 around ATM=24500 → strikes 24350–24650 (7 strikes × 100k/120k)
        # Far OTM strikes outside window should be excluded
        strikes = list(range(24_200, 24_901, 50))  # 15 strikes
        # Set far OTM to inverted OI to confirm they're excluded
        opts = []
        for s in strikes:
            if abs(s - 24_500) <= 150:  # within ±3 × 50 = 150 pts
                opts.append(OptionData(strike=s, call_oi=100_000, put_oi=120_000))
            else:
                opts.append(OptionData(strike=s, call_oi=500_000, put_oi=10_000))
        r = pcr_near_atm(opts, atm_strike=24_500, window=3)
        # If window worked, ratio ≈ 120/100 = 1.2; far OTM would push it <1
        assert r == pytest.approx(1.2, abs=0.001)

    def test_zero_call_oi_in_window_returns_none(self):
        strikes = [24_400, 24_450, 24_500, 24_550, 24_600]
        opts = [OptionData(strike=s, call_oi=0, put_oi=100_000) for s in strikes]
        assert pcr_near_atm(opts, atm_strike=24_500) is None

    def test_window_clipped_at_chain_start(self):
        # ATM is the first strike — window clips to index 0
        strikes = [24_500, 24_550, 24_600, 24_650, 24_700]
        opts = [OptionData(strike=s, call_oi=100_000, put_oi=80_000) for s in strikes]
        r = pcr_near_atm(opts, atm_strike=24_500, window=3)
        # Should not crash; returns ratio for whatever strikes remain after clip
        assert r is not None
        assert r > 0

    def test_window_clipped_at_chain_end(self):
        # ATM is the last strike — window clips to chain end
        strikes = [24_300, 24_350, 24_400, 24_450, 24_500]
        opts = [OptionData(strike=s, call_oi=100_000, put_oi=80_000) for s in strikes]
        r = pcr_near_atm(opts, atm_strike=24_500, window=3)
        assert r is not None

    def test_nearest_match_used_when_exact_strike_missing(self):
        # Chain has no 24_500; nearest is 24_490
        strikes = [24_390, 24_440, 24_490, 24_540, 24_590]
        opts = [OptionData(strike=s, call_oi=100_000, put_oi=150_000) for s in strikes]
        r = pcr_near_atm(opts, atm_strike=24_500, window=1)
        # window=1 → 3 strikes around 24490 (idx=2): [24440, 24490, 24540]
        # 3×150k / 3×100k = 1.5
        assert r == pytest.approx(1.5, abs=0.001)

    def test_custom_window_size(self):
        strikes = list(range(24_300, 24_801, 50))  # 11 strikes
        opts = [OptionData(strike=s, call_oi=100_000, put_oi=100_000) for s in strikes]
        r1 = pcr_near_atm(opts, atm_strike=24_500, window=1)
        r2 = pcr_near_atm(opts, atm_strike=24_500, window=5)
        # Both return 1.0 for symmetric chain, but sizes differ
        assert r1 == pytest.approx(1.0, abs=0.001)
        assert r2 == pytest.approx(1.0, abs=0.001)

    def test_rounded_to_4dp(self):
        strikes = [24_400, 24_500, 24_600]
        opts = [OptionData(strike=s, call_oi=3, put_oi=2) for s in strikes]
        r = pcr_near_atm(opts, atm_strike=24_500, window=1)
        assert r == pytest.approx(2 / 3, abs=0.00005)

    def test_put_heavy_near_atm_returns_above_1(self):
        strikes = list(range(24_300, 24_801, 50))
        opts = [OptionData(strike=s, call_oi=100_000, put_oi=200_000) for s in strikes]
        r = pcr_near_atm(opts, atm_strike=24_500)
        assert r == pytest.approx(2.0, abs=0.001)

    def test_call_heavy_near_atm_returns_below_1(self):
        strikes = list(range(24_300, 24_801, 50))
        opts = [OptionData(strike=s, call_oi=200_000, put_oi=100_000) for s in strikes]
        r = pcr_near_atm(opts, atm_strike=24_500)
        assert r == pytest.approx(0.5, abs=0.001)

    def test_far_otm_structural_hedging_excluded(self):
        # Nifty structural pattern: dense 50-pt chain from 22500 to 26500 (81 strikes).
        # Far-OTM strikes have huge structural PE OI; near-ATM is balanced.
        # ATM=24500 sits at index ~40; window=3 covers only ±3 indices (6 near-ATM
        # strikes), leaving the far-OTM structural hedges outside the window.
        strikes = list(range(22_500, 26_550, 50))  # 81 strikes
        opts = []
        for s in strikes:
            if abs(s - 24_500) <= 200:          # near-ATM — balanced
                opts.append(OptionData(strike=s, call_oi=200_000, put_oi=200_000))
            elif s < 23_000:                    # deep OTM puts — structural hedge
                opts.append(OptionData(strike=s, call_oi=1_000, put_oi=5_000_000))
            elif s > 26_000:                    # deep OTM calls — structural hedge
                opts.append(OptionData(strike=s, call_oi=5_000_000, put_oi=1_000))
            else:
                opts.append(OptionData(strike=s, call_oi=50_000, put_oi=50_000))
        r = pcr_near_atm(opts, atm_strike=24_500, window=3)
        # Near-ATM window contains only balanced OI → ratio ≈ 1.0
        assert 0.9 <= r <= 1.1


# ═══════════════════════════════════════════════════════════════════════
# pcr_at_strike
# ═══════════════════════════════════════════════════════════════════════
class TestPcrAtStrike:

    def test_empty_options_returns_none(self):
        assert pcr_at_strike([], strike=24_500) is None

    def test_exact_strike_match(self):
        opts = [
            OptionData(strike=24_400, call_oi=100_000, put_oi=200_000),
            OptionData(strike=24_500, call_oi=100_000, put_oi=150_000),
            OptionData(strike=24_600, call_oi=100_000, put_oi=50_000),
        ]
        r = pcr_at_strike(opts, strike=24_500)
        assert r == pytest.approx(1.5, abs=0.0001)

    def test_nearest_match_when_exact_not_in_chain(self):
        opts = [
            OptionData(strike=24_450, call_oi=100_000, put_oi=80_000),
            OptionData(strike=24_550, call_oi=100_000, put_oi=120_000),
        ]
        # Nearest to 24_500 is equidistant; either could match but result is non-None
        r = pcr_at_strike(opts, strike=24_500)
        assert r is not None
        assert r > 0

    def test_zero_call_oi_returns_none(self):
        opts = [OptionData(strike=24_500, call_oi=0, put_oi=100_000)]
        assert pcr_at_strike(opts, strike=24_500) is None

    def test_put_floor_signal_above_1_3(self):
        # > 1.3 → put writers dominant → put-writer floor
        opts = [OptionData(strike=24_500, call_oi=100_000, put_oi=150_000)]
        r = pcr_at_strike(opts, strike=24_500)
        assert r > 1.3

    def test_call_ceiling_signal_below_0_7(self):
        # < 0.7 → call writers dominant → call-writer ceiling
        opts = [OptionData(strike=24_500, call_oi=150_000, put_oi=80_000)]
        r = pcr_at_strike(opts, strike=24_500)
        assert r < 0.7

    def test_balanced_ratio_between_0_7_and_1_3(self):
        opts = [OptionData(strike=24_500, call_oi=100_000, put_oi=100_000)]
        r = pcr_at_strike(opts, strike=24_500)
        assert 0.7 <= r <= 1.3

    def test_single_option_in_chain(self):
        opts = [OptionData(strike=24_700, call_oi=80_000, put_oi=100_000)]
        # Only option available — must be returned regardless of distance
        r = pcr_at_strike(opts, strike=24_500)
        assert r == pytest.approx(100_000 / 80_000, abs=0.0001)

    def test_rounded_to_4dp(self):
        opts = [OptionData(strike=24_500, call_oi=3, put_oi=2)]
        r = pcr_at_strike(opts, strike=24_500)
        assert r == pytest.approx(2 / 3, abs=0.00005)

    def test_correct_strike_selected_not_adjacent(self):
        opts = [
            OptionData(strike=24_000, call_oi=100_000, put_oi=500_000),  # far
            OptionData(strike=24_500, call_oi=100_000, put_oi=120_000),  # target
            OptionData(strike=25_000, call_oi=100_000, put_oi=50_000),   # far
        ]
        r = pcr_at_strike(opts, strike=24_500)
        # Should select exactly 24_500, not the far strikes
        assert r == pytest.approx(1.2, abs=0.0001)
