"""
Tests for engine/oi_weighted.py

Covers:
  oi_weighted_levels — OTM filtering, active-OI threshold, HHI concentration,
                       edge cases (all-zero OI, spot=None, single strike)
"""
import pytest
from engine.oi_weighted import oi_weighted_levels
from tests.fixtures import OptionData


def make_chain(strikes, call_ois, put_ois, step=50):
    return [
        OptionData(strike=s, call_oi=c, put_oi=p)
        for s, c, p in zip(strikes, call_ois, put_ois)
    ]


class TestOiWeightedLevels:

    # ── basic correctness ─────────────────────────────────────────────────
    def test_call_resistance_above_spot(self):
        opts = make_chain(
            strikes=[24_300, 24_400, 24_500, 24_600, 24_700],
            call_ois=[50_000, 80_000, 100_000, 90_000, 60_000],
            put_ois= [60_000, 90_000, 100_000, 70_000, 40_000],
        )
        r = oi_weighted_levels(opts, spot=24_500)
        # call_resistance should be OTM calls only → strikes 24600, 24700
        assert r["call_resistance"] > 24_500

    def test_put_support_below_spot(self):
        opts = make_chain(
            strikes=[24_300, 24_400, 24_500, 24_600, 24_700],
            call_ois=[50_000, 80_000, 100_000, 90_000, 60_000],
            put_ois= [60_000, 90_000, 100_000, 70_000, 40_000],
        )
        r = oi_weighted_levels(opts, spot=24_500)
        # put_support should be OTM puts only → strikes 24300, 24400
        assert r["put_support"] < 24_500

    def test_spot_none_uses_all_strikes(self):
        """Without spot, no OTM filter — all strikes contribute."""
        opts = make_chain(
            strikes=[24_300, 24_400, 24_500, 24_600, 24_700],
            call_ois=[50_000, 50_000, 50_000, 50_000, 50_000],
            put_ois= [50_000, 50_000, 50_000, 50_000, 50_000],
        )
        r_no_spot = oi_weighted_levels(opts, spot=None)
        r_with_spot = oi_weighted_levels(opts, spot=24_500)
        # Without spot filter, call centroid includes all strikes → lower than OTM-only
        assert r_no_spot["call_resistance"] != r_with_spot["call_resistance"]

    def test_corridor_width_is_call_minus_put(self):
        opts = make_chain(
            strikes=[24_200, 24_400, 24_500, 24_600, 24_800],
            call_ois=[10_000, 10_000, 10_000, 200_000, 200_000],
            put_ois= [200_000, 200_000, 10_000, 10_000, 10_000],
        )
        r = oi_weighted_levels(opts, spot=24_500)
        if r["call_resistance"] and r["put_support"]:
            expected = round(r["call_resistance"] - r["put_support"], 1)
            assert r["oi_corridor_width"] == pytest.approx(expected, abs=0.2)

    # ── OTM filter correctness ────────────────────────────────────────────
    def test_itm_calls_excluded_from_resistance(self):
        """A call strike BELOW spot is ITM — should not influence resistance."""
        opts = [
            OptionData(strike=24_000, call_oi=999_999, put_oi=10_000),  # deep ITM call
            OptionData(strike=24_600, call_oi=100_000, put_oi=10_000),  # OTM call
            OptionData(strike=24_300, call_oi=10_000,  put_oi=100_000), # OTM put
        ]
        r = oi_weighted_levels(opts, spot=24_500)
        # ITM call at 24000 should not drag resistance below spot
        assert r["call_resistance"] > 24_500

    def test_itm_puts_excluded_from_support(self):
        opts = [
            OptionData(strike=24_800, call_oi=10_000, put_oi=999_999),  # ITM put
            OptionData(strike=24_200, call_oi=10_000, put_oi=100_000),  # OTM put
            OptionData(strike=24_700, call_oi=100_000, put_oi=10_000),  # OTM call
        ]
        r = oi_weighted_levels(opts, spot=24_500)
        assert r["put_support"] < 24_500

    # ── active OI threshold filtering ────────────────────────────────────
    def test_negligible_oi_strike_excluded(self):
        """A strike with OI far below 0.2% of side total should be excluded."""
        opts = [
            OptionData(strike=24_600, call_oi=1_000_000, put_oi=0),   # dominant OTM call
            OptionData(strike=24_700, call_oi=1, put_oi=0),           # negligible
            OptionData(strike=24_300, call_oi=0, put_oi=1_000_000),   # dominant OTM put
        ]
        r = oi_weighted_levels(opts, spot=24_500)
        # Only the dominant strikes should matter — centroid should be at them
        assert r["call_resistance"] == pytest.approx(24_600, abs=1)
        assert r["put_support"] == pytest.approx(24_300, abs=1)

    # ── HHI concentration ────────────────────────────────────────────────
    def test_hhi_high_when_single_dominant_strike(self):
        """When all OI is at one strike, HHI = 1.0."""
        opts = [
            OptionData(strike=24_600, call_oi=1_000_000, put_oi=0),
            OptionData(strike=24_700, call_oi=0, put_oi=0),
            OptionData(strike=24_300, call_oi=0, put_oi=1_000_000),
        ]
        r = oi_weighted_levels(opts, spot=24_500)
        assert r["call_oi_concentration"] == pytest.approx(1.0, abs=0.01)
        assert r["put_oi_concentration"]  == pytest.approx(1.0, abs=0.01)

    def test_hhi_low_when_evenly_distributed(self):
        """Equal OI at many strikes → HHI approaches 0."""
        strikes = list(range(24_550, 24_950, 50))   # 8 OTM call strikes
        put_strikes = list(range(24_050, 24_450, 50))  # 8 OTM put strikes
        opts = (
            [OptionData(strike=s, call_oi=100_000, put_oi=0) for s in strikes] +
            [OptionData(strike=s, call_oi=0, put_oi=100_000) for s in put_strikes]
        )
        r = oi_weighted_levels(opts, spot=24_500)
        # With 8 equal-weight strikes, HHI = 1/8 = 0.125 for uniform
        # (actually 1/n for n equal weights)
        assert r["call_oi_concentration"] < 0.2
        assert r["put_oi_concentration"]  < 0.2

    # ── edge cases ────────────────────────────────────────────────────────
    def test_all_zero_oi_returns_none(self):
        opts = [OptionData(strike=s, call_oi=0, put_oi=0)
                for s in [24_300, 24_500, 24_700]]
        r = oi_weighted_levels(opts, spot=24_500)
        assert r["call_resistance"] is None
        assert r["put_support"] is None
        assert r["oi_corridor_width"] is None

    def test_no_otm_calls_returns_none_resistance(self):
        """All strikes are below spot — no OTM calls → resistance is None."""
        opts = [OptionData(strike=s, call_oi=100_000, put_oi=100_000)
                for s in [24_000, 24_200, 24_400]]  # all below spot=24500
        r = oi_weighted_levels(opts, spot=24_500)
        assert r["call_resistance"] is None

    def test_single_otm_call_strike(self):
        opts = [OptionData(strike=24_600, call_oi=100_000, put_oi=0)]
        r = oi_weighted_levels(opts, spot=24_500)
        assert r["call_resistance"] == pytest.approx(24_600, abs=1)

    def test_weighted_centroid_math(self):
        """Manual verification of the OI-weighted centroid formula."""
        # Two OTM call strikes with known OI
        opts = [
            OptionData(strike=24_600, call_oi=300_000, put_oi=0),
            OptionData(strike=24_700, call_oi=100_000, put_oi=0),
            OptionData(strike=24_300, call_oi=0, put_oi=200_000),
        ]
        r = oi_weighted_levels(opts, spot=24_500)
        # Expected centroid: (24600*300000 + 24700*100000) / 400000 = 24625
        assert r["call_resistance"] == pytest.approx(24_625, abs=1)
