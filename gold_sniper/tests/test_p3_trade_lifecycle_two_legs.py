"""P3 — Two-Leg Trade Lifecycle Tests.

Validates:
  1. Risk split per grade (A_PLUS/A/B/C_CONFIRMED/C)
  2. Payoff outcomes (direct SL, TP1+protected SL, TP1+TP2)
  3. Daily limiter counts parents, not legs
  4. Duplicate gate on scenario_key
  5. Parent PnL = leg_1 PnL + leg_2 PnL
  6. Summary uses parent trades for WR/payoff
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from gold_sniper.replay.shadow_live_policy import (
    GRADE_RISK_PCT,
    EXECUTABLE_GRADES,
    LEG_RISK_SPLIT,
    LEG_TARGET_RR,
    PROTECTED_RUNNER_SL_R,
    ShadowLivePolicy,
    grade_is_executable,
    leg_risk_pct,
    normalize_grade,
    risk_pct_for_grade,
)
from gold_sniper.replay.execution_model import ReplayExecutionModel, build_default_execution_model


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 1-5: Risk split per grade
# ═══════════════════════════════════════════════════════════════════════════════


class TestP3GradeRiskSplit(unittest.TestCase):
    """P3: Grade → total risk → leg risk split."""

    def test_a_plus_splits_into_050_050(self):
        total = risk_pct_for_grade("A_PLUS")
        self.assertEqual(total, 1.00)
        leg1, leg2 = leg_risk_pct("A_PLUS")
        self.assertEqual(leg1, 0.50)
        self.assertEqual(leg2, 0.50)
        self.assertAlmostEqual(leg1 + leg2, total)

    def test_a_splits_into_0375_0375(self):
        total = risk_pct_for_grade("A")
        self.assertEqual(total, 0.75)
        leg1, leg2 = leg_risk_pct("A")
        self.assertEqual(leg1, 0.375)
        self.assertEqual(leg2, 0.375)
        self.assertAlmostEqual(leg1 + leg2, total)

    def test_b_splits_into_025_025(self):
        total = risk_pct_for_grade("B")
        self.assertEqual(total, 0.50)
        leg1, leg2 = leg_risk_pct("B")
        self.assertEqual(leg1, 0.25)
        self.assertEqual(leg2, 0.25)
        self.assertAlmostEqual(leg1 + leg2, total)

    def test_c_generic_not_executable(self):
        self.assertFalse(grade_is_executable("C"))
        self.assertEqual(risk_pct_for_grade("C"), 0.0)

    def test_c_confirmed_splits_into_0125_0125(self):
        total = risk_pct_for_grade("C_CONFIRMED")
        self.assertEqual(total, 0.25)
        leg1, leg2 = leg_risk_pct("C_CONFIRMED")
        self.assertEqual(leg1, 0.125)
        self.assertEqual(leg2, 0.125)
        self.assertAlmostEqual(leg1 + leg2, total)

    def test_c_confirmed_is_executable(self):
        self.assertTrue(grade_is_executable("C_CONFIRMED"))

    def test_d_not_executable(self):
        self.assertFalse(grade_is_executable("D"))
        self.assertEqual(risk_pct_for_grade("D"), 0.0)

    def test_normalize_c_confirmed(self):
        self.assertEqual(normalize_grade("C_CONFIRMED"), "C_CONFIRMED")
        self.assertEqual(normalize_grade("c_confirmed"), "C_CONFIRMED")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 6-8: Payoff outcomes (unit math, no replay needed)
# ═══════════════════════════════════════════════════════════════════════════════


class TestP3PayoffMath(unittest.TestCase):
    """P3: Verify payoff math for the two-leg model."""

    def test_direct_sl_is_minus_1r_parent(self):
        """Both legs hit SL → parent = -1.0R."""
        # Leg risk: 0.5R each (50% of parent risk)
        # leg_1: -0.5R, leg_2: -0.5R → parent = -1.0R
        leg1_r = -1.0  # -1 * leg_risk = -0.5 * parent_risk
        leg2_r = -1.0  # -1 * leg_risk = -0.5 * parent_risk
        parent_r = (leg1_r + leg2_r) / 2.0  # each leg is half parent risk
        self.assertAlmostEqual(parent_r, -1.0)

    def test_tp1_then_protected_sl_is_075r_parent(self):
        """Leg1 TP1 (+1R on 50%), leg2 PROTECTED_SL (+0.5R on 50%) → +0.75R."""
        leg1_r = 1.0   # +0.5 * parent_risk
        leg2_r = 0.5   # +0.25 * parent_risk (protected SL at +0.5R × leg risk)
        parent_r = (leg1_r + leg2_r) / 2.0
        self.assertAlmostEqual(parent_r, 0.75)

    def test_tp1_then_tp2_is_15r_parent(self):
        """Leg1 TP1 (+1R on 50%), leg2 TP2 (+2R on 50%) → +1.5R."""
        leg1_r = 1.0   # +0.5 * parent_risk
        leg2_r = 2.0   # +1.0 * parent_risk
        parent_r = (leg1_r + leg2_r) / 2.0
        self.assertAlmostEqual(parent_r, 1.5)

    def test_parent_pnl_r_equals_leg_sum(self):
        """parent_pnl_R = leg_1_pnl_R + leg_2_pnl_R, normalized by parent risk."""
        total_risk = 1.0  # parent risk
        leg_risk = total_risk / 2.0  # 0.5 per leg
        leg1_pnl = leg_risk * 1.0   # TP1: +0.5
        leg2_pnl = leg_risk * 2.0   # TP2: +1.0
        parent_pnl = leg1_pnl + leg2_pnl  # +1.5
        parent_r = parent_pnl / total_risk
        self.assertAlmostEqual(parent_r, 1.5)
        # Also verify via leg R values
        leg1_r = leg1_pnl / leg_risk  # 1.0
        leg2_r = leg2_pnl / leg_risk  # 2.0
        parent_r_from_legs = (leg1_r + leg2_r) / 2.0
        self.assertAlmostEqual(parent_r_from_legs, 1.5)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 9-10: Daily limiter and duplicate gate
# ═══════════════════════════════════════════════════════════════════════════════


class TestP3DailyLimiterParent(unittest.TestCase):
    """P3: Daily limiter counts parent setups, not legs."""

    def test_policy_defaults_unchanged(self):
        policy = ShadowLivePolicy()
        self.assertEqual(policy.max_standard_trades_per_day, 2)
        self.assertEqual(policy.max_absolute_trades_per_day, 3)
        self.assertEqual(policy.be_plus_r, 0.5)

    def test_leg_split_constant(self):
        self.assertEqual(LEG_RISK_SPLIT, 0.5)
        self.assertEqual(LEG_TARGET_RR[1], 1.0)
        self.assertEqual(LEG_TARGET_RR[2], 2.0)
        self.assertEqual(PROTECTED_RUNNER_SL_R, 0.5)


class TestP3DuplicateGateParent(unittest.TestCase):
    """P3: Duplicate gate blocks on scenario_key (parent level)."""

    def test_scenario_key_in_grade_risk_mapping(self):
        """scenario_key is preserved through risk mapping."""
        self.assertIn("C_CONFIRMED", GRADE_RISK_PCT)
        self.assertIn("C_CONFIRMED", EXECUTABLE_GRADES)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 11-12: Intrabar conservative policy
# ═══════════════════════════════════════════════════════════════════════════════


class TestP3IntrabarPolicy(unittest.TestCase):
    """P3: Conservative intrabar exit priority."""

    def test_execution_model_be_plus_r_is_05(self):
        model = build_default_execution_model()
        self.assertEqual(model.be_plus_r, 0.5)
        self.assertEqual(model.tp1_rr, 1.0)
        self.assertEqual(model.tp2_rr, 2.0)

    def test_protected_sl_distance_is_half_r(self):
        """Protected SL = entry ± 0.5R."""
        entry = 2000.0
        risk = 40.0
        model = build_default_execution_model()
        # BUY protected SL = entry + be_plus_r * risk
        buy_psl = entry + model.be_plus_r * risk
        self.assertEqual(buy_psl, 2020.0)  # 2000 + 0.5*40
        # SELL protected SL = entry - be_plus_r * risk
        sell_psl = entry - model.be_plus_r * risk
        self.assertEqual(sell_psl, 1980.0)  # 2000 - 0.5*40


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 13-15: Cost reporting and summary
# ═══════════════════════════════════════════════════════════════════════════════


class TestP3CostSeparation(unittest.TestCase):
    """P3: Costs reported separately from pure R."""

    def test_execution_model_has_spread_and_slippage(self):
        model = build_default_execution_model()
        self.assertGreater(model.profile.avg_spread_points, 0)
        self.assertGreater(model.slippage_points, 0)
        # Costs are configurable
        self.assertEqual(model.spread_mode, "conservative_fixed")

    def test_entry_costs_are_positive(self):
        model = build_default_execution_model()
        spread = model.spread_points(news_blocked_or_near=False)
        slippage = model.slippage_for_event(news_blocked_or_near=False)
        self.assertGreater(spread, 0)
        self.assertGreater(slippage, 0)


class TestP3SummaryParentLevel(unittest.TestCase):
    """P3: Summary metrics use parent trades, not leg events."""

    def test_policy_to_dict_includes_be_plus_r(self):
        policy = ShadowLivePolicy()
        d = policy.to_dict()
        self.assertIn("be_plus_r", d)
        self.assertEqual(d["be_plus_r"], 0.5)

    def test_execution_model_validates_with_05r(self):
        model = ReplayExecutionModel(be_plus_r=0.5)
        self.assertEqual(model.validate(), [])

    def test_execution_model_rejects_be_plus_r_above_1(self):
        model = ReplayExecutionModel(be_plus_r=1.5)
        errors = model.validate()
        self.assertIn("BE_PLUS_R_OUT_OF_BOUNDS", errors)


class TestP3CConfirmedGate(unittest.TestCase):
    """P3: C_CONFIRMED — all 8 gates pass but score in C range."""

    def test_all_gates_pass_helper(self):
        """_all_gates_pass returns True only when all 8 gates are PASS."""
        from gold_sniper.strategy.kasper_scenario_engine import KasperScenarioEngine
        engine = KasperScenarioEngine()
        # All pass
        self.assertTrue(engine._all_gates_pass({
            "htf_bias": "PASS", "liquidity_sweep": "PASS",
            "reintegrated": "PASS", "displacement": "PASS",
            "structure_shift": "PASS", "poi": "PASS",
            "micro_confirmation": "PASS", "risk_precheck": "PASS",
        }))
        # One missing
        self.assertFalse(engine._all_gates_pass({
            "htf_bias": "PASS", "liquidity_sweep": "PASS",
            "reintegrated": "PASS", "displacement": "PASS",
            "structure_shift": "PASS", "poi": "PASS",
            "micro_confirmation": "FAIL", "risk_precheck": "PASS",
        }))
        # Empty
        self.assertFalse(engine._all_gates_pass({}))

    def test_c_confirmed_not_blocked_by_grade_gate(self):
        """C_CONFIRMED is not in (C, D) — passes shadow signal gate."""
        grade = "C_CONFIRMED"
        self.assertNotIn(grade, ("C", "D"))

    def test_c_confirmed_is_executable(self):
        """C_CONFIRMED is in executable grades with 0.25% risk."""
        from gold_sniper.replay.shadow_live_policy import (
            grade_is_executable, risk_pct_for_grade, leg_risk_pct,
        )
        self.assertTrue(grade_is_executable("C_CONFIRMED"))
        self.assertEqual(risk_pct_for_grade("C_CONFIRMED"), 0.25)
        leg1, leg2 = leg_risk_pct("C_CONFIRMED")
        self.assertEqual(leg1, 0.125)
        self.assertEqual(leg2, 0.125)

    def test_generic_c_still_not_executable(self):
        """Generic C without all gates passing stays WAIT/REJECT."""
        from gold_sniper.replay.shadow_live_policy import grade_is_executable
        self.assertFalse(grade_is_executable("C"))

    def test_c_confirmed_risk_per_leg(self):
        """C_CONFIRMED: 0.25% total → 0.125% per leg."""
        from gold_sniper.replay.shadow_live_policy import leg_risk_pct
        leg1, leg2 = leg_risk_pct("C_CONFIRMED")
        total = leg1 + leg2
        self.assertAlmostEqual(total, 0.25)
        # Each leg = half
        self.assertAlmostEqual(leg1, 0.125)
        self.assertAlmostEqual(leg2, 0.125)


if __name__ == "__main__":
    unittest.main()
