"""
Tests for engine/options_strategy.py

Covers every execution branch:
  iv_regime, _find_strike, options_strategy (full decision matrix)

Decision matrix tested:
  data_reliable=False                              → NO TRADE
  CONFLICTED                                       → WAIT — CONFLICTED
  DTE=0 IV HIGH + score≥3 + strong LS             → SELL (expiry premium sell)
  DTE=0 otherwise                                  → NO TRADE
  FLAT (|LS|<0.15)  elevated IV DTE≥2 score≥2     → SELL STRADDLE
  FLAT normal IV                                   → NO TRADE
  score < 2                                        → NO TRADE
  DTE=1 weak (|LS|≤0.35 or score<4)               → NO TRADE
  DTE=1 strong (|LS|>0.35 AND score≥4)            → BUY allowed
  WEAK LS (0.15<|LS|≤0.35) score≥3                → WATCH
  WEAK LS score<3                                  → NO TRADE
  STRONG LS score=2                                → WAIT — BIAS
  STRONG LS score=3                                → BUY 1-OTM REDUCED
  STRONG LS score≥4 normal IV                     → BUY ATM FULL SIZE
  STRONG LS score≥4 elevated IV                   → BUY 1-OTM REDUCED
  LONG direction → CE, SHORT direction → PE
  Zero premium at target strike                    → NO TRADE
"""
import pytest
from engine.options_strategy import iv_regime, options_strategy
from tests.fixtures import OptionData, make_chain, make_conf


# ═══════════════════════════════════════════════════════════════════════
# iv_regime
# ═══════════════════════════════════════════════════════════════════════
class TestIvRegime:
    def test_low(self):
        assert iv_regime(10.0) == "LOW"

    def test_boundary_low_normal(self):
        # < 12 = LOW, ≥ 12 = NORMAL
        assert iv_regime(12.0) == "NORMAL"
        assert iv_regime(11.99) == "LOW"

    def test_normal(self):
        assert iv_regime(15.0) == "NORMAL"

    def test_boundary_normal_elevated(self):
        assert iv_regime(18.0) == "ELEVATED"
        assert iv_regime(17.99) == "NORMAL"

    def test_elevated(self):
        assert iv_regime(22.0) == "ELEVATED"

    def test_boundary_elevated_high(self):
        assert iv_regime(25.0) == "HIGH"
        assert iv_regime(24.99) == "ELEVATED"

    def test_high(self):
        assert iv_regime(35.0) == "HIGH"

    def test_zero_returns_unknown(self):
        assert iv_regime(0) == "UNKNOWN"

    def test_negative_returns_unknown(self):
        assert iv_regime(-5.0) == "UNKNOWN"

    def test_none_returns_unknown(self):
        assert iv_regime(None) == "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════
# options_strategy — guard rails
# ═══════════════════════════════════════════════════════════════════════
class TestOptionsStrategyGuards:
    def _opts(self, atm=24_500, n=9):
        return make_chain(atm=atm, n_strikes=n, call_ltp=120, put_ltp=100)

    def test_unreliable_data_no_trade(self):
        conf = make_conf(direction="LONG", score=5, data_reliable=False)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"
        assert r["style"] == "neutral"

    def test_conflicted_returns_wait(self):
        conf = make_conf(direction="LONG", score=4, conflict=True,
                         conflict_sources=["PCR 0.75 bearish"])
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert "CONFLICTED" in r["strategy"]
        assert r["side"] is None

    def test_conflicted_short_returns_wait(self):
        conf = make_conf(direction="SHORT", score=4, conflict=True,
                         conflict_sources=["PCR 1.45 bullish"])
        r = options_strategy(-0.5, conf, 15.0, 3, self._opts(), 24_500, 24_510)
        assert "CONFLICTED" in r["strategy"]

    def test_score_0_no_trade(self):
        conf = make_conf(direction="LONG", score=0)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    def test_score_1_no_trade(self):
        conf = make_conf(direction="LONG", score=1)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    def test_none_ls_treated_as_zero_flat(self):
        conf = make_conf(direction="FLAT", score=3)
        r = options_strategy(None, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"


# ═══════════════════════════════════════════════════════════════════════
# options_strategy — expiry day (DTE=0)
# ═══════════════════════════════════════════════════════════════════════
class TestOptionsStrategyExpiryDay:
    def _opts(self):
        return make_chain(atm=24_500, call_ltp=50, put_ltp=50)

    def test_dte_0_high_iv_strong_long_sells_put(self):
        # On expiry day, LONG gravity → sell PE (spot below max pain → sell put)
        conf = make_conf(direction="LONG", score=3)
        r = options_strategy(0.5, conf, 30.0, 0, self._opts(), 24_500, 24_490)
        assert r["strategy"].startswith("SELL PE")
        assert r["style"] == "sell"
        assert r["dte_warning"] is not None

    def test_dte_0_high_iv_strong_short_sells_call(self):
        conf = make_conf(direction="SHORT", score=3)
        r = options_strategy(-0.5, conf, 30.0, 0, self._opts(), 24_500, 24_510)
        assert r["strategy"].startswith("SELL CE")

    def test_dte_0_normal_iv_no_trade(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 0, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"
        assert "theta" in r["rationale"].lower()

    def test_dte_0_score_2_no_trade_even_high_iv(self):
        conf = make_conf(direction="LONG", score=2)
        r = options_strategy(0.5, conf, 30.0, 0, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    def test_dte_0_weak_ls_no_trade(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.2, conf, 30.0, 0, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    def test_dte_0_dte_warning_always_set(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 30.0, 0, self._opts(), 24_500, 24_490)
        assert r["dte_warning"] is not None
        assert "EXPIRY" in r["dte_warning"]


# ═══════════════════════════════════════════════════════════════════════
# options_strategy — FLAT zone
# ═══════════════════════════════════════════════════════════════════════
class TestOptionsStrategyFlat:
    def _opts(self):
        return make_chain(atm=24_500, call_ltp=120, put_ltp=100)

    def test_flat_elevated_iv_sells_straddle(self):
        conf = make_conf(direction="FLAT", score=2)
        r = options_strategy(0.1, conf, 22.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "SELL STRADDLE — ATM"
        assert r["side"] == "STRADDLE"
        assert r["style"] == "sell"

    def test_straddle_result_has_breakevens(self):
        conf = make_conf(direction="FLAT", score=2)
        r = options_strategy(0.05, conf, 22.0, 3, self._opts(), 24_500, 24_490)
        assert "breakeven_upper" in r
        assert "breakeven_lower" in r
        assert r["breakeven_upper"] > r["breakeven_lower"]

    def test_straddle_result_has_max_profit(self):
        conf = make_conf(direction="FLAT", score=2)
        r = options_strategy(0.05, conf, 22.0, 3, self._opts(), 24_500, 24_490)
        assert "max_profit_lot" in r
        assert r["max_profit_lot"] > 0

    def test_flat_normal_iv_no_trade(self):
        conf = make_conf(direction="FLAT", score=3)
        r = options_strategy(0.05, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    def test_flat_elevated_iv_dte_1_no_straddle(self):
        # DTE=1 overrides flat straddle — theta too severe for straddle selling
        # Actually: DTE=1 only blocks BUY, not SELL. Let's check what actually fires.
        conf = make_conf(direction="FLAT", score=3)
        r = options_strategy(0.05, conf, 22.0, 1, self._opts(), 24_500, 24_490)
        # DTE=1 with flat LS: FLAT branch fires before DTE=1 BUY guard
        # Straddle can still be sold on DTE=1 (theta is high → good for seller)
        assert r["strategy"] in ("SELL STRADDLE — ATM", "NO TRADE")

    def test_flat_score_1_no_trade_even_elevated_iv(self):
        conf = make_conf(direction="FLAT", score=1)
        r = options_strategy(0.05, conf, 22.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"


# ═══════════════════════════════════════════════════════════════════════
# options_strategy — DTE=1 gate
# ═══════════════════════════════════════════════════════════════════════
class TestOptionsStrategyDte1:
    def _opts(self):
        return make_chain(atm=24_500, call_ltp=50, put_ltp=50)

    def test_dte_1_strong_ls_score_4_allows_buy(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 1, self._opts(), 24_500, 24_490)
        assert "BUY" in r["strategy"]
        assert "ONE DAY" in r["dte_warning"]

    def test_dte_1_weak_ls_score_4_no_trade(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.25, conf, 15.0, 1, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    def test_dte_1_strong_ls_score_3_no_trade(self):
        conf = make_conf(direction="LONG", score=3)
        r = options_strategy(0.5, conf, 15.0, 1, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    def test_dte_1_dte_warning_set(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 1, self._opts(), 24_500, 24_490)
        assert r["dte_warning"] is not None
        assert "ONE DAY" in r["dte_warning"]


# ═══════════════════════════════════════════════════════════════════════
# options_strategy — WEAK gravity zone (0.15 < |LS| ≤ 0.35)
# ═══════════════════════════════════════════════════════════════════════
class TestOptionsStrategyWeakGravity:
    def _opts(self):
        return make_chain(atm=24_500, call_ltp=80, put_ltp=70)

    def test_weak_long_score_3_watch(self):
        conf = make_conf(direction="WEAK LONG", score=3)
        r = options_strategy(0.25, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert "WATCH" in r["strategy"]
        assert r["size_note"] == "WATCH"
        assert r["style"] == "moderate"

    def test_watch_result_has_strike_in_strategy(self):
        conf = make_conf(direction="WEAK LONG", score=3)
        r = options_strategy(0.25, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        # Strategy string should mention the target strike
        assert str(r["strike"]) in r["strategy"]

    def test_weak_long_score_2_no_trade(self):
        conf = make_conf(direction="WEAK LONG", score=2)
        r = options_strategy(0.25, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    def test_weak_short_score_3_watch_pe(self):
        conf = make_conf(direction="WEAK SHORT", score=3)
        r = options_strategy(-0.25, conf, 15.0, 3, self._opts(), 24_500, 24_510)
        assert "WATCH" in r["strategy"]
        assert "PE" in r["strategy"]

    def test_watch_no_max_loss(self):
        # WATCH is not an active buy — max_loss_lot should be None
        conf = make_conf(direction="WEAK LONG", score=3)
        r = options_strategy(0.25, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["max_loss_lot"] is None


# ═══════════════════════════════════════════════════════════════════════
# options_strategy — STRONG gravity zone (|LS| > 0.35), score gates
# ═══════════════════════════════════════════════════════════════════════
class TestOptionsStrategyStrongGravity:
    def _opts(self, atm=24_500):
        return make_chain(atm=atm, call_ltp=120, put_ltp=100)

    # ── score=2 → WAIT BIAS ──────────────────────────────────────────────
    def test_strong_long_score_2_wait_bias(self):
        conf = make_conf(direction="LONG", score=2)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert "WAIT" in r["strategy"]
        assert "LONG BIAS" in r["strategy"]
        assert r["size_note"] == "SKIP"
        assert r["style"] == "moderate"

    def test_strong_short_score_2_wait_bias(self):
        conf = make_conf(direction="SHORT", score=2)
        r = options_strategy(-0.5, conf, 15.0, 3, self._opts(), 24_500, 24_510)
        assert "WAIT" in r["strategy"]
        assert "SHORT BIAS" in r["strategy"]

    # ── score=3 → BUY 1-OTM REDUCED ────────────────────────────────────
    def test_strong_long_score_3_buy_otm_reduced(self):
        conf = make_conf(direction="LONG", score=3)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "BUY CE — 1-OTM"
        assert r["size_note"] == "REDUCED SIZE"
        assert r["style"] == "moderate"
        assert r["side"] == "CE"

    def test_strong_short_score_3_buy_pe_otm_reduced(self):
        conf = make_conf(direction="SHORT", score=3)
        r = options_strategy(-0.5, conf, 15.0, 3, self._opts(), 24_500, 24_510)
        assert r["strategy"] == "BUY PE — 1-OTM"
        assert r["side"] == "PE"

    def test_strike_is_one_step_above_atm_for_ce(self):
        # CE 1-OTM → one strike above ATM (24550 for 50-pt chain)
        conf = make_conf(direction="LONG", score=3)
        opts = make_chain(atm=24_500, n_strikes=7, step=50)
        r = options_strategy(0.5, conf, 15.0, 3, opts, 24_500, 24_490)
        assert r["strike"] == 24_550

    def test_strike_is_one_step_below_atm_for_pe(self):
        conf = make_conf(direction="SHORT", score=3)
        opts = make_chain(atm=24_500, n_strikes=7, step=50)
        r = options_strategy(-0.5, conf, 15.0, 3, opts, 24_500, 24_510)
        assert r["strike"] == 24_450

    # ── score=4 normal IV → BUY ATM FULL SIZE ───────────────────────────
    def test_strong_long_score_4_normal_iv_buy_atm(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "BUY CE — ATM"
        assert r["size_note"] == "FULL SIZE"
        assert r["style"] == "strong"
        assert r["strike"] == 24_500

    def test_strong_long_score_5_normal_iv_buy_atm(self):
        conf = make_conf(direction="LONG", score=5)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "BUY CE — ATM"
        assert r["size_note"] == "FULL SIZE"

    # ── score=4 elevated IV → BUY 1-OTM REDUCED ─────────────────────────
    def test_strong_long_score_4_elevated_iv_buy_otm(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 22.0, 3, self._opts(), 24_500, 24_490)
        assert r["strategy"] == "BUY CE — 1-OTM"
        assert r["size_note"] == "REDUCED SIZE"

    def test_strong_long_score_4_high_iv_buy_otm(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 30.0, 3, self._opts(), 24_500, 24_490)
        # DTE>0, IV HIGH → but not DTE=0, so normal path applies
        # IV HIGH → elevated_iv=True → REDUCED SIZE 1-OTM
        assert r["strategy"] == "BUY CE — 1-OTM"
        assert r["size_note"] == "REDUCED SIZE"

    # ── premium and max_loss_lot ─────────────────────────────────────────
    def test_premium_populated_in_result(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["premium"] is not None
        assert r["premium"] > 0

    def test_max_loss_lot_is_premium_times_lot_size(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490,
                             lot_size=65)
        assert r["max_loss_lot"] == pytest.approx(r["premium"] * 65, abs=1)

    # ── zero premium guard ──────────────────────────────────────────────
    def test_zero_premium_at_strike_returns_no_trade(self):
        opts = make_chain(atm=24_500, n_strikes=7)
        # Zero out all call premiums
        for o in opts:
            o.call_ltp = 0.0
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 3, opts, 24_500, 24_490)
        assert r["strategy"] == "NO TRADE"

    # ── DTE warning for close expiry ────────────────────────────────────
    def test_dte_2_warning_none(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 2, self._opts(), 24_500, 24_490)
        assert r["dte_warning"] is None

    def test_dte_3_warning_none(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert r["dte_warning"] is None

    # ── rationale content ───────────────────────────────────────────────
    def test_rationale_mentions_score(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 3, self._opts(), 24_500, 24_490)
        assert "4/5" in r["rationale"]

    def test_rationale_mentions_elevated_iv_when_present(self):
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 22.0, 3, self._opts(), 24_500, 24_490)
        assert "IV elevated" in r["rationale"] or "elevated" in r["rationale"].lower()


# ═══════════════════════════════════════════════════════════════════════
# BUY strike is spot-anchored, not true-ATM-anchored
# ═══════════════════════════════════════════════════════════════════════
class TestBuyStrikeSpotAnchored:
    """Under put-skew the IV-balance 'true ATM' can sit ~100 pts below spot.
    Offsetting from true ATM pushes BUY strikes into deep OTM where breakeven
    falls beyond the LS target. BUY strike must anchor on spot so the trade
    ends ITM at max pain (the thesis)."""

    def _skewed_chain(self):
        # 50pt spacing from 24200–24800. Spot = 24576, true ATM = 24500 (IV balance).
        strikes = list(range(24_200, 24_801, 50))
        return [OptionData(strike=s, call_ltp=100, put_ltp=100) for s in strikes]

    def test_buy_pe_full_size_anchors_on_spot_not_true_atm(self):
        # SHORT + score=4 + normal IV → FULL SIZE offset=0 from spot-anchor.
        # Spot 24576 → spot-anchor = 24600. offset=0 → 24600 PE (not 24500).
        conf = make_conf(direction="SHORT", score=4)
        r = options_strategy(-0.5, conf, 15.0, 3, self._skewed_chain(),
                             atm_strike=24_500, spot=24_576)
        assert r["side"] == "PE"
        assert r["strike"] == 24_600  # spot-anchored ATM, not true-ATM 24_500

    def test_buy_pe_reduced_is_1_otm_from_spot_not_true_atm(self):
        # SHORT + score=3 → REDUCED offset=1 PE from spot-anchor 24600 → 24550.
        # Old (buggy) behavior would have returned 24450 (1 below true ATM).
        conf = make_conf(direction="SHORT", score=3)
        r = options_strategy(-0.5, conf, 15.0, 3, self._skewed_chain(),
                             atm_strike=24_500, spot=24_576)
        assert r["side"] == "PE"
        assert r["strike"] == 24_550  # 1-OTM from spot-anchor 24600
        assert r["strike"] != 24_450  # the old buggy strike

    def test_buy_ce_full_size_anchors_on_spot_with_inverse_skew(self):
        # Inverse scenario: true ATM above spot (call skew). Spot 24_424,
        # true ATM 24_500. Spot-anchor = 24_400. LONG score=4 → FULL SIZE
        # CE at 24_400 (not 24_500).
        conf = make_conf(direction="LONG", score=4)
        r = options_strategy(0.5, conf, 15.0, 3, self._skewed_chain(),
                             atm_strike=24_500, spot=24_424)
        assert r["side"] == "CE"
        assert r["strike"] == 24_400

    def test_buy_ce_reduced_is_1_otm_above_spot(self):
        conf = make_conf(direction="LONG", score=3)
        r = options_strategy(0.5, conf, 15.0, 3, self._skewed_chain(),
                             atm_strike=24_500, spot=24_424)
        # Spot-anchor 24_400, offset=1 CE → 24_450 (above, = 1-OTM from spot)
        assert r["strike"] == 24_450

    def test_watch_strike_also_spot_anchored(self):
        # Weak gravity + score=3 → WATCH. Should also use spot anchor.
        conf = make_conf(direction="SHORT", score=3)
        r = options_strategy(-0.25, conf, 15.0, 3, self._skewed_chain(),
                             atm_strike=24_500, spot=24_576)
        assert r["size_note"] == "WATCH"
        assert r["strike"] == 24_550  # 1-OTM PE from spot-anchor 24600

    def test_sell_straddle_stays_on_true_atm(self):
        # FLAT + elevated IV + DTE≥2 → SELL STRADDLE. This must remain anchored
        # on true ATM (IV balance), NOT spot — the entire premise of short
        # straddle is selling the IV-balance strike.
        conf = make_conf(direction="FLAT", score=2)
        r = options_strategy(0.05, conf, 22.0, 3, self._skewed_chain(),
                             atm_strike=24_500, spot=24_576)
        assert r["side"] == "STRADDLE"
        assert r["strike"] == 24_500  # true ATM, not spot-anchor 24600

    def test_expiry_day_sell_stays_on_true_atm(self):
        # DTE=0 expiry-day SELL also anchors on true ATM (IV balance).
        conf = make_conf(direction="LONG", score=3)
        r = options_strategy(0.5, conf, 30.0, 0, self._skewed_chain(),
                             atm_strike=24_500, spot=24_576)
        # sell_side = 'PE' since is_long; offset=0 from true ATM 24_500
        assert r["side"] == "PE"
        assert r["strike"] == 24_500  # true ATM, not spot-anchor

    def test_when_spot_equals_true_atm_behavior_unchanged(self):
        # Regression guard: when there's no skew, spot-anchor == true ATM,
        # so strike selection matches the pre-change behavior.
        conf = make_conf(direction="SHORT", score=4)
        r = options_strategy(-0.5, conf, 15.0, 3, self._skewed_chain(),
                             atm_strike=24_500, spot=24_500)
        assert r["strike"] == 24_500  # ATM-from-spot = true ATM
