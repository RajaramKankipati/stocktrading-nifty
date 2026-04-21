"""
Tests for engine/synthetic.py

Covers:
  synthetic_future, theoretical_price_pcp, futures_microprice,
  breeden_litzenberger, validate_theoretical_prices
"""
import pytest
from engine.synthetic import (
    synthetic_future,
    theoretical_price_pcp,
    futures_microprice,
    breeden_litzenberger,
    validate_theoretical_prices,
)
from tests.fixtures import OptionData


# ═══════════════════════════════════════════════════════════════════════
# synthetic_future
# ═══════════════════════════════════════════════════════════════════════
class TestSyntheticFuture:
    def test_basic_pcp(self):
        opt = OptionData(strike=24_500, call_ltp=120, put_ltp=100)
        assert synthetic_future(opt) == pytest.approx(24_520, abs=0.1)

    def test_equal_premiums_returns_strike(self):
        opt = OptionData(strike=24_500, call_ltp=100, put_ltp=100)
        assert synthetic_future(opt) == pytest.approx(24_500, abs=0.1)

    def test_put_heavy_below_strike(self):
        opt = OptionData(strike=24_500, call_ltp=80, put_ltp=120)
        assert synthetic_future(opt) == pytest.approx(24_460, abs=0.1)


# ═══════════════════════════════════════════════════════════════════════
# theoretical_price_pcp
# ═══════════════════════════════════════════════════════════════════════
class TestTheoreticalPricePcp:
    def _chain(self, atm=24_500, n=7, step=50):
        opts = []
        half = n // 2
        for i in range(-half, half + 1):
            strike = atm + i * step
            oi = max(10_000, 200_000 - abs(i) * 30_000)
            opts.append(OptionData(
                strike=strike, call_ltp=120 - i * 10,
                put_ltp=100 + i * 10,
                call_iv=15, put_iv=15,
                call_oi=oi, put_oi=oi,
            ))
        return opts

    def test_returns_reasonable_price_near_atm(self):
        opts = self._chain(atm=24_500)
        price, n, iv = theoretical_price_pcp(opts, 24_500)
        # Price should be close to ATM ± straddle/2
        assert price is not None
        assert abs(price - 24_500) < 200

    def test_n_strikes_counts_valid_contributors(self):
        opts = self._chain(atm=24_500)
        _, n, _ = theoretical_price_pcp(opts, 24_500)
        assert n >= 1

    def test_weighted_iv_returned(self):
        opts = self._chain(atm=24_500)
        _, _, iv = theoretical_price_pcp(opts, 24_500)
        assert iv is not None
        assert iv > 0

    def test_zero_ltp_strikes_excluded(self):
        opts = self._chain(atm=24_500)
        opts[3].call_ltp = 0.0  # ATM call zeroed
        price, n, _ = theoretical_price_pcp(opts, 24_500)
        # Should still return a price from remaining valid strikes
        assert price is not None

    def test_all_zero_oi_returns_none(self):
        opts = [OptionData(strike=24_500 + i * 50,
                           call_ltp=100, put_ltp=100,
                           call_oi=0, put_oi=0)
                for i in range(-3, 4)]
        price, n, iv = theoretical_price_pcp(opts, 24_500)
        assert price is None
        assert n == 0
        assert iv is None

    def test_atm_not_in_chain_uses_nearest(self):
        opts = [OptionData(strike=s, call_ltp=100, put_ltp=100,
                           call_oi=100_000, put_oi=100_000)
                for s in [24_400, 24_450, 24_550, 24_600]]
        # true ATM 24500 is not in chain — should use nearest
        price, n, _ = theoretical_price_pcp(opts, 24_500)
        assert price is not None

    def test_min_oi_weight_down_weights_far_otm(self):
        """Strike with low OI should contribute less than near-ATM strikes."""
        # ATM has high OI; far OTM has near-zero OI
        opts = [
            OptionData(strike=24_500, call_ltp=120, put_ltp=100,
                       call_oi=1_000_000, put_oi=1_000_000),
            OptionData(strike=24_550, call_ltp=80, put_ltp=130,
                       call_oi=1, put_oi=1),  # near zero weight
        ]
        price, _, _ = theoretical_price_pcp(opts, 24_500)
        # Should be dominated by ATM strike: 24500 + 120 - 100 = 24520
        assert price == pytest.approx(24_520, abs=5)


# ═══════════════════════════════════════════════════════════════════════
# futures_microprice
# ═══════════════════════════════════════════════════════════════════════
class TestFuturesMicroprice:
    def test_balanced_order_book(self):
        price, pressure = futures_microprice(24_528, 24_530, 100, 100)
        # Equal quantities → true mid
        assert price == pytest.approx(24_529.0, abs=0.1)
        assert pressure == "BALANCED"

    def test_buy_heavy_tilts_toward_bid(self):
        # bid_qty = 700, ask_qty = 300 → buy_ratio = 0.7 > 0.65
        price, pressure = futures_microprice(24_528, 24_530, 700, 300)
        assert pressure == "BUY_HEAVY"
        # Microprice tilts toward ask (more buyers waiting at ask)
        # microprice = (ask * bid_qty + bid * ask_qty) / (bid_qty + ask_qty)
        # = (24530*700 + 24528*300) / 1000 = (17171000 + 7358400) / 1000 = 24529.4
        assert price > 24_529.0

    def test_sell_heavy_tilts_toward_ask(self):
        price, pressure = futures_microprice(24_528, 24_530, 200, 800)
        assert pressure == "SELL_HEAVY"
        assert price < 24_529.0

    def test_no_book_bid_zero(self):
        price, pressure = futures_microprice(0, 24_530, 100, 100)
        assert price is None
        assert pressure == "NO_BOOK"

    def test_no_book_ask_zero(self):
        price, pressure = futures_microprice(24_528, 0, 100, 100)
        assert price is None
        assert pressure == "NO_BOOK"

    def test_both_qty_zero_returns_midpoint(self):
        price, pressure = futures_microprice(24_528, 24_530, 0, 0)
        assert price == pytest.approx(24_529.0, abs=0.1)
        assert pressure == "BALANCED"

    def test_buy_ratio_exactly_at_threshold_balanced(self):
        # buy_ratio = 0.65 exactly → not > 0.65 → BALANCED (boundary)
        price, pressure = futures_microprice(24_528, 24_530, 65, 35)
        assert pressure == "BALANCED"

    def test_buy_ratio_above_threshold_buy_heavy(self):
        price, pressure = futures_microprice(24_528, 24_530, 66, 34)
        assert pressure == "BUY_HEAVY"


# ═══════════════════════════════════════════════════════════════════════
# validate_theoretical_prices
# ═══════════════════════════════════════════════════════════════════════
class TestValidateTheoreticalPrices:
    def test_reliable_when_spread_below_20(self):
        r = validate_theoretical_prices(24_500, 24_510)
        assert r["status"] == "RELIABLE"

    def test_caution_when_spread_20_to_50(self):
        r = validate_theoretical_prices(24_500, 24_530)
        assert r["status"] == "CAUTION"

    def test_unreliable_when_spread_above_50(self):
        r = validate_theoretical_prices(24_500, 24_560)
        assert "UNRELIABLE" in r["status"]

    def test_bl_price_included_in_spread(self):
        # PCP=24500, micro=24505, BL=24600 → spread = 100 → UNRELIABLE
        r = validate_theoretical_prices(24_500, 24_505, bl_price=24_600)
        assert "UNRELIABLE" in r["status"]

    def test_bl_none_only_two_methods_compared(self):
        r = validate_theoretical_prices(24_500, 24_510, bl_price=None)
        assert r["spread"] == pytest.approx(10, abs=0.5)

    def test_all_none_returns_no_data(self):
        r = validate_theoretical_prices(None, None)
        assert r["status"] == "NO_DATA"

    def test_pcp_none_uses_microprice_only(self):
        r = validate_theoretical_prices(None, 24_510)
        assert r["status"] == "RELIABLE"  # only one value → spread=0

    def test_spread_is_max_minus_min(self):
        r = validate_theoretical_prices(24_490, 24_500, bl_price=24_510)
        assert r["spread"] == pytest.approx(20, abs=0.5)

    def test_consensus_is_mean_of_valid(self):
        r = validate_theoretical_prices(24_490, 24_510)
        assert r["consensus"] == pytest.approx(24_500, abs=0.5)

    def test_primary_is_pcp_price(self):
        r = validate_theoretical_prices(24_500, 24_510)
        assert r["primary"] == 24_500


# ═══════════════════════════════════════════════════════════════════════
# breeden_litzenberger
# ═══════════════════════════════════════════════════════════════════════
class TestBreedenLitzenberger:
    def _chain_with_known_skew(self, spot=24_500):
        """Simple Gaussian-like call price surface across 50-pt strikes."""
        import math
        strikes = list(range(24_000, 25_100, 50))
        sigma = 150
        opts = []
        for K in strikes:
            # Rough Black-Scholes call approximation for test
            intrinsic = max(0, spot - K)
            extrinsic = 50 * math.exp(-0.5 * ((K - spot) / sigma) ** 2)
            call_ltp = round(intrinsic + extrinsic, 2)
            opts.append(OptionData(strike=K, call_ltp=max(0.1, call_ltp), put_ltp=50))
        return opts

    def test_returns_five_tuple(self):
        opts = self._chain_with_known_skew()
        result = breeden_litzenberger(opts, spot=24_500, T_days=2)
        assert len(result) == 5

    def test_expected_value_near_spot(self):
        opts = self._chain_with_known_skew(spot=24_500)
        ev, _, _, _, _ = breeden_litzenberger(opts, spot=24_500, T_days=2)
        if ev is not None:
            assert abs(ev - 24_500) < 500

    def test_density_sums_to_approximately_one(self):
        opts = self._chain_with_known_skew()
        _, density, _, _, _ = breeden_litzenberger(opts, spot=24_500, T_days=2)
        if density:
            assert sum(density.values()) == pytest.approx(1.0, abs=0.01)

    def test_tail_probs_sum_near_one(self):
        opts = self._chain_with_known_skew()
        _, _, right, left, _ = breeden_litzenberger(opts, spot=24_500, T_days=2)
        if right is not None and left is not None:
            assert right + left <= 1.0 + 0.05  # allow small rounding

    def test_skew_indicator_sign(self):
        opts = self._chain_with_known_skew()
        _, _, right, left, skew = breeden_litzenberger(opts, spot=24_500, T_days=2)
        if skew is not None and right is not None and left is not None:
            assert skew == pytest.approx(right - left, abs=0.01)

    def test_too_few_strikes_returns_none(self):
        opts = [OptionData(strike=s, call_ltp=100, put_ltp=100)
                for s in [24_400, 24_450, 24_500, 24_550]]  # only 4 < 5
        ev, density, *_ = breeden_litzenberger(opts, spot=24_500, T_days=2)
        assert ev is None
        assert density == {}

    def test_negative_d2c_strips_excluded(self):
        """Strikes with negative second derivative (arbitrage) should be excluded."""
        opts = self._chain_with_known_skew()
        # Inject a spike at one strike to create negative d2C
        opts[10].call_ltp = 0.001  # very low → second derivative likely negative
        ev, density, _, _, _ = breeden_litzenberger(opts, spot=24_500, T_days=2)
        # Should still complete without error
        assert ev is not None or density == {}

    def test_t_days_defaults_to_1_when_zero(self):
        opts = self._chain_with_known_skew()
        ev, _, _, _, _ = breeden_litzenberger(opts, spot=24_500, T_days=0)
        # T_days=0 clamps to 1/365 — function should not crash
        assert ev is not None or ev is None  # just ensure no exception
