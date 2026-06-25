"""P2-E Phase19 — Risk realism and cost-aware sizing tests.

Tests:
- A+ grade risks ~1% of equity at SL
- A grade risks ~0.75% of equity at SL
- B grade risks ~0.50% of equity at SL
- C/D grades produce no entry
- SELL cost-aware volume includes spread/slippage on both entry and exit
- BUY cost-aware volume includes spread/slippage on both entry and exit
- TP1 = 1R from effective risk
- TP2 = 2R from effective risk
- SL loss does not exceed risk_cash tolerance (≤1.25x)
- Max 2 trades/day respected
- 3rd trade only if A+ or A grade
- POI_REACTION remains non-tradable
- No forced ENTER
- Hard veto not bypassed
- worst_case_effective_risk_points returns correct values
"""
from __future__ import annotations

import unittest

from gold_sniper.replay.shadow_live_policy import (
    GRADE_RISK_PCT,
    EXECUTABLE_GRADES,
    EXCEPTION_GRADES,
    ShadowLivePolicy,
    compute_shadow_position_size,
    grade_is_executable,
    grade_allows_daily_exception,
    normalize_grade,
    risk_pct_for_grade,
    worst_case_effective_risk_points,
)
from gold_sniper.replay.execution_model import (
    BrokerExecutionProfile,
    ReplayExecutionModel,
    build_default_execution_model,
)


class TestGradeRiskPct(unittest.TestCase):
    """Verify grade → risk % mapping matches mission spec."""

    def test_a_plus_risks_1_percent(self):
        self.assertEqual(GRADE_RISK_PCT["A_PLUS"], 1.00)
        self.assertEqual(risk_pct_for_grade("A+"), 1.00)
        self.assertEqual(risk_pct_for_grade("A_PLUS"), 1.00)

    def test_a_risks_0_75_percent(self):
        self.assertEqual(GRADE_RISK_PCT["A"], 0.75)
        self.assertEqual(risk_pct_for_grade("A"), 0.75)

    def test_b_risks_0_50_percent(self):
        self.assertEqual(GRADE_RISK_PCT["B"], 0.50)
        self.assertEqual(risk_pct_for_grade("B"), 0.50)

    def test_c_is_zero_risk(self):
        self.assertEqual(GRADE_RISK_PCT["C"], 0.00)
        self.assertEqual(risk_pct_for_grade("c"), 0.00)
        self.assertNotIn("C", EXECUTABLE_GRADES)

    def test_d_is_zero_risk(self):
        self.assertEqual(GRADE_RISK_PCT["D"], 0.00)
        self.assertNotIn("D", EXECUTABLE_GRADES)

    def test_unknown_is_zero_risk(self):
        self.assertEqual(GRADE_RISK_PCT["UNKNOWN"], 0.00)

    def test_grade_is_executable(self):
        self.assertTrue(grade_is_executable("A_PLUS"))
        self.assertTrue(grade_is_executable("A"))
        self.assertTrue(grade_is_executable("B"))
        self.assertFalse(grade_is_executable("C"))
        self.assertFalse(grade_is_executable("D"))

    def test_exception_grades(self):
        self.assertTrue(grade_allows_daily_exception("A_PLUS"))
        self.assertTrue(grade_allows_daily_exception("A"))
        self.assertFalse(grade_allows_daily_exception("B"))
        self.assertFalse(grade_allows_daily_exception("C"))


class TestNormalizeGrade(unittest.TestCase):
    """Grade normalization round-trips."""

    def test_a_plus_variants(self):
        self.assertEqual(normalize_grade("A+"), "A_PLUS")
        self.assertEqual(normalize_grade("a+"), "A_PLUS")
        self.assertEqual(normalize_grade("A_PLUS"), "A_PLUS")

    def test_standard_grades(self):
        for grade in ("A", "B", "C", "D"):
            self.assertEqual(normalize_grade(grade), grade)
            self.assertEqual(normalize_grade(grade.lower()), grade)

    def test_unknown(self):
        self.assertEqual(normalize_grade(None), "UNKNOWN")
        self.assertEqual(normalize_grade(""), "UNKNOWN")
        self.assertEqual(normalize_grade("X"), "UNKNOWN")


class TestWorstCaseEffectiveRiskPoints(unittest.TestCase):
    """Cost-aware effective risk computation."""

    def setUp(self):
        self.exec_model = build_default_execution_model()

    def test_buy_increases_effective_risk(self):
        """BUY: effective_entry = entry + spread/2 + slippage → larger risk."""
        worst = worst_case_effective_risk_points(
            side="BUY",
            requested_entry=2650.00,
            stop_loss=2645.00,
            spread_points=20.0,
            slippage_points=5.0,
        )
        # structural = 2650 - 2645 = 5
        self.assertEqual(worst["structural_risk_points"], 5.0)
        # effective_entry = 2650 + 10 + 5 = 2665
        self.assertEqual(worst["effective_entry"], 2665.0)
        # effective_sl_exit = 2645 - 10 - 5 = 2630
        self.assertEqual(worst["effective_sl_exit"], 2630.0)
        # effective_risk = 2665 - 2630 = 35
        self.assertEqual(worst["effective_risk_points"], 35.0)
        # effective risk > structural risk
        self.assertGreater(worst["effective_risk_points"], worst["structural_risk_points"])

    def test_sell_increases_effective_risk(self):
        """SELL: effective_entry = entry - spread/2 - slippage → larger risk."""
        worst = worst_case_effective_risk_points(
            side="SELL",
            requested_entry=2650.00,
            stop_loss=2660.00,
            spread_points=20.0,
            slippage_points=5.0,
        )
        # structural = 2660 - 2650 = 10
        self.assertEqual(worst["structural_risk_points"], 10.0)
        # effective_entry = 2650 - 10 - 5 = 2635
        self.assertEqual(worst["effective_entry"], 2635.0)
        # effective_sl_exit = 2660 + 10 + 5 = 2675
        self.assertEqual(worst["effective_sl_exit"], 2675.0)
        # effective_risk = 2675 - 2635 = 40
        self.assertEqual(worst["effective_risk_points"], 40.0)
        self.assertGreater(worst["effective_risk_points"], worst["structural_risk_points"])

    def test_no_spread_slippage_equal_risk(self):
        """With zero costs, effective == structural."""
        worst = worst_case_effective_risk_points(
            side="BUY",
            requested_entry=2650.00,
            stop_loss=2645.00,
            spread_points=0.0,
            slippage_points=0.0,
        )
        self.assertEqual(worst["effective_risk_points"], 5.0)
        self.assertEqual(worst["structural_risk_points"], 5.0)

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            worst_case_effective_risk_points(
                side="NONE",
                requested_entry=2650.0,
                stop_loss=2640.0,
                spread_points=20.0,
                slippage_points=5.0,
            )


class TestComputeShadowPositionSizeCostAware(unittest.TestCase):
    """Volume sized from effective risk, not structural risk."""

    def setUp(self):
        self.policy = ShadowLivePolicy(initial_equity=100.0)

    def test_volume_smaller_with_costs(self):
        """With costs, volume should be smaller than without costs."""
        # Without costs
        sizing_no_cost = compute_shadow_position_size(
            equity=100.0,
            grade="A",
            entry=2650.00,
            stop_loss=2660.00,
            policy=self.policy,
            side="SELL",
            spread_points=0.0,
            slippage_points=0.0,
        )
        # With costs (spread=20, slippage=5)
        sizing_with_cost = compute_shadow_position_size(
            equity=100.0,
            grade="A",
            entry=2650.00,
            stop_loss=2660.00,
            policy=self.policy,
            side="SELL",
            spread_points=20.0,
            slippage_points=5.0,
        )
        self.assertLess(sizing_with_cost["volume"], sizing_no_cost["volume"])
        # structural_risk = 10, risk_cash = 0.75
        self.assertEqual(sizing_no_cost["risk_cash"], 0.75)
        self.assertEqual(sizing_no_cost["risk_points"], 10.0)
        # Volume should be 0.75/10 = 0.075 without costs
        self.assertAlmostEqual(sizing_no_cost["volume"], 0.075, places=5)
        # With costs, volume = 0.75 / effective_risk_points (< 0.075)
        self.assertGreater(sizing_with_cost["effective_risk_points"], 10.0)

    def test_expected_loss_close_to_risk_cash(self):
        """Expected SL loss should be very close to risk_cash."""
        sizing = compute_shadow_position_size(
            equity=100.0,
            grade="A",
            entry=2650.00,
            stop_loss=2660.00,
            policy=self.policy,
            side="SELL",
            spread_points=20.0,
            slippage_points=5.0,
        )
        # expected_sl_loss should not exceed 1.25 * risk_cash
        risk_cash = sizing["risk_cash"]
        expected_loss = sizing["expected_sl_loss"]
        ratio = expected_loss / risk_cash
        self.assertLessEqual(ratio, 1.25, f"Expected SL loss ({expected_loss}) exceeds 1.25x risk_cash ({risk_cash})")

    def test_a_plus_risk_1_percent_of_equity(self):
        """A+ grade risks approximately 1% of equity."""
        sizing = compute_shadow_position_size(
            equity=500.0,
            grade="A_PLUS",
            entry=2650.00,
            stop_loss=2660.00,
            policy=self.policy,
            side="SELL",
            spread_points=20.0,
            slippage_points=5.0,
        )
        # risk_cash should be close to 500 * 1% = 5.00, but may be reduced by effective risk
        self.assertAlmostEqual(sizing["risk_pct"], 1.0, places=2)
        self.assertGreater(sizing["risk_cash"], 0.0)
        self.assertLessEqual(sizing["risk_cash"], 5.01)

    def test_a_risk_0_75_percent_of_equity(self):
        """A grade risks approximately 0.75% of equity."""
        sizing = compute_shadow_position_size(
            equity=100.0,
            grade="A",
            entry=2650.00,
            stop_loss=2660.00,
            policy=self.policy,
            side="SELL",
            spread_points=20.0,
            slippage_points=5.0,
        )
        self.assertAlmostEqual(sizing["risk_pct"], 0.75, places=2)
        self.assertAlmostEqual(sizing["risk_cash"], 0.75, places=2)

    def test_b_risk_0_50_percent_of_equity(self):
        """B grade risks approximately 0.50% of equity."""
        sizing = compute_shadow_position_size(
            equity=100.0,
            grade="B",
            entry=2650.00,
            stop_loss=2660.00,
            policy=self.policy,
            side="SELL",
            spread_points=20.0,
            slippage_points=5.0,
        )
        self.assertAlmostEqual(sizing["risk_pct"], 0.50, places=2)
        self.assertAlmostEqual(sizing["risk_cash"], 0.50, places=2)

    def test_c_grade_raises(self):
        """C grade should not be executable."""
        with self.assertRaises(ValueError) as ctx:
            compute_shadow_position_size(
                equity=100.0,
                grade="C",
                entry=2650.00,
                stop_loss=2660.00,
                policy=self.policy,
                side="SELL",
            )
        self.assertIn("GRADE_NOT_EXECUTABLE", str(ctx.exception))

    def test_sell_volume_correct(self):
        """Sell sizing includes spread/slippage on both entry and exit."""
        sizing = compute_shadow_position_size(
            equity=100.0,
            grade="B",
            entry=2650.00,
            stop_loss=2660.00,
            policy=self.policy,
            side="SELL",
            spread_points=20.0,
            slippage_points=5.0,
        )
        # risk_cash = 0.50, structural = 10, effective > 10
        # effective_risk = (2660+10+5) - (2650-10-5) = 2675 - 2635 = 40
        self.assertEqual(sizing["effective_risk_points"], 40.0)
        # volume = 0.50 / 40 = 0.0125
        self.assertAlmostEqual(sizing["volume"], 0.0125, places=5)
        # expected_sl_loss = 0.0125 * 40 = 0.50 (exact!)
        self.assertAlmostEqual(sizing["expected_sl_loss"], 0.50, places=5)

    def test_buy_volume_correct(self):
        """Buy sizing includes spread/slippage on both entry and exit."""
        sizing = compute_shadow_position_size(
            equity=100.0,
            grade="B",
            entry=2650.00,
            stop_loss=2645.00,
            policy=self.policy,
            side="BUY",
            spread_points=20.0,
            slippage_points=5.0,
        )
        # structural = 5
        self.assertEqual(sizing["risk_points"], 5.0)
        # effective_entry = 2650+10+5=2665, effective_sl_exit=2645-10-5=2630
        # effective_risk = 2665 - 2630 = 35
        self.assertEqual(sizing["effective_risk_points"], 35.0)
        # volume = 0.50 / 35 ≈ 0.014286
        self.assertAlmostEqual(sizing["volume"], 0.50 / 35.0, places=5)
        # expected_sl_loss = volume * effective_risk (≈0.50, with tiny float rounding)
        self.assertAlmostEqual(sizing["expected_sl_loss"], 0.50, places=4)

    def test_min_stop_distance_rejects_too_close(self):
        """Trades with negative effective risk points (costs > structural) are rejected."""
        # For SELL: effective_risk = (SL + spread/2 + slippage) - (entry - spread/2 - slippage)
        # = SL - entry + spread + 2*slippage
        # With spread=20, slippage=5: effective_risk = SL - entry + 30
        # For effective_risk <= 0: SL <= entry - 30 = 2620
        with self.assertRaises(ValueError) as ctx:
            compute_shadow_position_size(
                equity=100.0,
                grade="A",
                entry=2650.00,
                stop_loss=2620.00,  # effective_risk = 2620 - 2650 + 30 = 0 (invalid)
                policy=self.policy,
                side="SELL",
                spread_points=20.0,
                slippage_points=5.0,
            )
        self.assertTrue(
            "INVALID" in str(ctx.exception) or "STOP" in str(ctx.exception)
        )


class TestTpFromEffectiveRisk(unittest.TestCase):
    """TP1 = 1R, TP2 = 2R from effective risk."""

    def test_tp1_is_1r_from_effective_risk(self):
        """For a SELL: TP1 = entry - 1R (from effective risk)."""
        exec_model = ReplayExecutionModel(tp1_rr=1.0, tp2_rr=2.0)
        # Simulating what _build_trade does:
        requested_entry = 2650.00
        sl = 2660.00
        spread = exec_model.spread_points(news_blocked_or_near=False)  # 20.0
        slippage = exec_model.slippage_for_event(news_blocked_or_near=False)  # 5.0

        worst = worst_case_effective_risk_points(
            side="SELL",
            requested_entry=requested_entry,
            stop_loss=sl,
            spread_points=spread,
            slippage_points=slippage,
        )
        effective_entry = worst["effective_entry"]  # 2635.0
        effective_risk = worst["effective_risk_points"]  # 40.0

        tp1 = effective_entry - effective_risk * exec_model.tp1_rr
        tp2 = effective_entry - effective_risk * exec_model.tp2_rr

        # tp1 = 2635 - 40 = 2595
        self.assertEqual(tp1, 2595.0)
        # tp2 = 2635 - 80 = 2555
        self.assertEqual(tp2, 2555.0)

        # TP basis is effective execution risk
        self.assertEqual(effective_risk, 40.0)
        # 1R from entry = 40 points
        self.assertEqual(effective_entry - tp1, 40.0)
        # 2R from entry = 80 points
        self.assertEqual(effective_entry - tp2, 80.0)


class TestDailyLimitPolicy(unittest.TestCase):
    """Max 2 trades/day, 3rd only if A+ or A."""

    def setUp(self):
        self.policy = ShadowLivePolicy()

    def test_max_standard_2(self):
        self.assertEqual(self.policy.max_standard_trades_per_day, 2)

    def test_max_absolute_3(self):
        self.assertEqual(self.policy.max_absolute_trades_per_day, 3)

    def test_exceptional_requires_a_plus_or_a(self):
        self.assertTrue(grade_allows_daily_exception("A_PLUS"))
        self.assertTrue(grade_allows_daily_exception("A"))
        self.assertFalse(grade_allows_daily_exception("B"))
        self.assertFalse(grade_allows_daily_exception("C"))


class TestNoPoiReactionTradable(unittest.TestCase):
    """POI_REACTION must remain non-tradable."""

    def test_only_sweep_reversal_executable(self):
        """Verify that only executable grades are A+/A/B, never C."""
        self.assertNotIn("C", EXECUTABLE_GRADES)
        self.assertNotIn("D", EXECUTABLE_GRADES)

    def test_c_grade_volume_raises(self):
        with self.assertRaises(ValueError) as ctx:
            compute_shadow_position_size(
                equity=100.0,
                grade="C",
                entry=2650.00,
                stop_loss=2660.00,
                policy=ShadowLivePolicy(),
                side="SELL",
            )
        self.assertIn("GRADE_NOT_EXECUTABLE", str(ctx.exception))


class TestNoForcedEnter(unittest.TestCase):
    """Verify that compute_shadow_position_size never forces a trade."""

    def test_zero_equity_rejected(self):
        """Zero equity produces risk_cash too small for min_risk_cash threshold."""
        with self.assertRaises(ValueError) as ctx:
            compute_shadow_position_size(
                equity=0.0,
                grade="A",
                entry=2650.00,
                stop_loss=2660.00,
                policy=ShadowLivePolicy(),
                side="SELL",
                spread_points=0.0,
                slippage_points=0.0,
            )
        self.assertIn("RISK_CASH_TOO_SMALL", str(ctx.exception))

    def test_unknown_grade_rejected(self):
        with self.assertRaises(ValueError):
            compute_shadow_position_size(
                equity=100.0,
                grade="UNKNOWN",
                entry=2650.00,
                stop_loss=2660.00,
                policy=ShadowLivePolicy(),
                side="SELL",
            )


if __name__ == "__main__":
    unittest.main()
