"""P2-E Phase 7C — Risk multiplier mapping unit tests."""

import unittest

from gold_sniper.strategy.contracts import (
    DecisionAction,
    EvidenceBundle,
    SetupGrade,
    SetupType,
    TradeSide,
)
from gold_sniper.strategy.risk_allocator import (
    BASE_RISK_PCT,
    GRADE_RISK_MULTIPLIER,
    allocate_risk,
    grade_risk_multiplier,
)
from gold_sniper.strategy.setup_taxonomy import get_setup_requirement


def _bundle(setup_type="REVERSAL_STRICT", side="BUY", **overrides):
    """Build a minimal synthetic bundle."""
    data = {
        "setup_type": setup_type,
        "side": side,
        "context": {"direction": side, "in_ote": True},
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB" if side == "BUY" else "BEARISH_OB"},
        },
        "session": {"trading_allowed": True, "session_label": "LONDON"},
        "risk": {"passed": True},
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


class TestGradeRiskMultiplierMapping(unittest.TestCase):
    """Contract: grade → risk multiplier."""

    def test_a_plus_is_1_0(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.A_PLUS), 1.00)

    def test_a_is_0_75(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.A), 0.75)

    def test_b_is_0_5(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.B), 0.50)

    def test_c_is_0_25(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.C), 0.25)

    def test_d_is_0_0(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.D), 0.00)

    def test_unknown_grade_returns_0(self):
        self.assertEqual(grade_risk_multiplier(None), 0.0)
        self.assertEqual(grade_risk_multiplier("INVALID"), 0.0)

    def test_grade_from_string(self):
        self.assertEqual(grade_risk_multiplier("C"), 0.25)
        self.assertEqual(grade_risk_multiplier("B"), 0.50)

    def test_base_risk_pct_has_c_at_0_25(self):
        self.assertEqual(BASE_RISK_PCT[SetupGrade.C], 0.25)
        self.assertIs(GRADE_RISK_MULTIPLIER, BASE_RISK_PCT)

    def test_all_grades_in_mapping(self):
        for grade in SetupGrade:
            self.assertIn(grade, BASE_RISK_PCT, f"Grade {grade} missing from BASE_RISK_PCT")
            multiplier = grade_risk_multiplier(grade)
            self.assertIsInstance(multiplier, float)


class TestEnterEligibleGuard(unittest.TestCase):
    """Contract: allocate_risk with enter_eligible=False blocks all risk."""

    def test_enter_eligible_false_blocks_risk(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A_PLUS,
            evidence=_bundle("REVERSAL_STRICT"),
            capital=100.0,
            enter_eligible=False,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.reason, "ENTER_NOT_ELIGIBLE")
        self.assertEqual(plan.risk_pct, 0.0)
        self.assertEqual(plan.risk_multiplier, 0.0)

    def test_enter_eligible_false_meta(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.B,
            evidence=_bundle("CONTINUATION_LIGHT"),
            capital=100.0,
            enter_eligible=False,
        )
        self.assertEqual(plan.metadata.get("enter_eligible"), False)
        self.assertEqual(plan.metadata.get("action"), "ENTER_FULL")

    def test_enter_eligible_none_legacy_compat(self):
        """When enter_eligible=None, legacy behavior is preserved (no gate)."""
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A_PLUS,
            evidence=_bundle("REVERSAL_STRICT"),
            capital=100.0,
            enter_eligible=None,
        )
        # A+ with REVERSAL_STRICT (cap 1.0) should produce positive risk
        self.assertTrue(plan.allowed)
        self.assertGreater(plan.risk_pct, 0.0)

    def test_watch_only_action_always_no_risk(self):
        plan = allocate_risk(
            action=DecisionAction.WATCH_ONLY,
            grade=SetupGrade.A_PLUS,
            evidence=_bundle("REVERSAL_STRICT"),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.reason, "NO_EXECUTABLE_SHADOW_ENTRY")


class TestGradeCRiskAllocation(unittest.TestCase):
    """Contract: grade C with enter_eligible=True and tradable setup → positive risk."""

    def test_grade_c_enter_full_positive_risk(self):
        """Grade C (0.25) × combined multiplier (1.0) → risk should be positive
        with a tradable setup type that has cap > 0."""
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.C,
            evidence=_bundle("CONTINUATION_LIGHT"),  # max_risk_multiplier=0.75
            capital=100.0,
            enter_eligible=True,
        )
        # C = 0.25, CONTINUATION_LIGHT cap = 0.75 → risk_pct = 0.25 × 1.0 = 0.25
        self.assertTrue(plan.allowed, f"Expected allowed=True, got reason={plan.reason}")
        self.assertGreater(plan.risk_pct, 0.0)
        self.assertEqual(plan.risk_pct, 0.25)
        self.assertGreater(plan.risk_amount, 0.0)
        self.assertEqual(plan.metadata.get("grade_risk_multiplier"), 0.25)

    def test_grade_c_enter_reduced_capped(self):
        """ENTER_REDUCED caps base_pct at 0.50. Grade C is 0.25, so no effect."""
        plan = allocate_risk(
            action=DecisionAction.ENTER_REDUCED,
            grade=SetupGrade.C,
            evidence=_bundle("CONTINUATION_LIGHT"),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.risk_pct, 0.25)

    def test_grade_b_enter_full_positive_risk(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.B,
            evidence=_bundle("REVERSAL_STRICT"),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.risk_pct, 0.50)  # B=0.50, cap=1.0

    def test_grade_a_enter_full_positive_risk(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A,
            evidence=_bundle("REVERSAL_STRICT"),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.risk_pct, 0.75)  # A=0.75, cap=1.0

    def test_effective_risk_pct_in_metadata(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A_PLUS,
            evidence=_bundle("REVERSAL_STRICT"),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertIn("effective_risk_pct", plan.metadata)
        self.assertEqual(plan.metadata["effective_risk_pct"], plan.risk_pct)
        self.assertIn("combined_modulator", plan.metadata)
        self.assertIn("setup_max_risk_multiplier", plan.metadata)


class TestSetupCapEnforcement(unittest.TestCase):
    """Contract: max_risk_multiplier caps are enforced."""

    def test_poi_reaction_cap_zero(self):
        """POI_REACTION has max_risk_multiplier=0.0 — risk always 0."""
        req = get_setup_requirement(SetupType.POI_REACTION)
        self.assertEqual(req.max_risk_multiplier, 0.0)
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A_PLUS,
            evidence=_bundle("POI_REACTION"),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.risk_pct, 0.0)
        self.assertEqual(plan.reason, "ZERO_RISK_AFTER_MULTIPLIERS")

    def test_reversal_light_cap_0_25(self):
        req = get_setup_requirement(SetupType.REVERSAL_LIGHT)
        self.assertEqual(req.max_risk_multiplier, 0.25)
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A_PLUS,
            evidence=_bundle("REVERSAL_LIGHT"),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertTrue(plan.allowed)
        self.assertLessEqual(plan.risk_pct, 0.25)

    def test_continuation_strict_cap_0_75(self):
        req = get_setup_requirement(SetupType.CONTINUATION_STRICT)
        self.assertEqual(req.max_risk_multiplier, 0.75)

    def test_sweep_reversal_cap_0_75(self):
        req = get_setup_requirement(SetupType.SWEEP_REVERSAL)
        self.assertEqual(req.max_risk_multiplier, 0.75)

    def test_ote_pullback_cap_0_50(self):
        req = get_setup_requirement(SetupType.OTE_PULLBACK)
        self.assertEqual(req.max_risk_multiplier, 0.50)

    def test_no_setup_and_unknown_cap_zero(self):
        self.assertEqual(get_setup_requirement(SetupType.NO_SETUP).max_risk_multiplier, 0.0)
        self.assertEqual(get_setup_requirement(SetupType.UNKNOWN).max_risk_multiplier, 0.0)

    def test_risk_guard_hit_blocks_risk(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A_PLUS,
            evidence=_bundle("REVERSAL_STRICT", risk={"passed": True, "kill_switch": True}),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.reason, "RISK_GUARD_HIT")

    def test_d_grade_always_zero(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.D,
            evidence=_bundle("REVERSAL_STRICT"),
            capital=100.0,
            enter_eligible=True,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.risk_pct, 0.0)

    def test_metadata_contains_all_phase7c_fields(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.B,
            evidence=_bundle("CONTINUATION_LIGHT"),
            capital=100.0,
            enter_eligible=True,
        )
        for key in ("grade_risk_multiplier", "combined_modulator", "setup_max_risk_multiplier",
                     "effective_risk_pct", "enter_eligible"):
            self.assertIn(key, plan.metadata, f"Missing metadata key: {key}")


if __name__ == "__main__":
    unittest.main()
