"""
Tests for engine/oi_metrics.py

Covers: pcr_oi, pcr_notional
"""
import pytest
from engine.oi_metrics import pcr_oi, pcr_notional
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
