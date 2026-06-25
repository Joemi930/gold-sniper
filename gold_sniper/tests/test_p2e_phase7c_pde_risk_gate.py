"""P2-E Phase 7C — PDE risk gate integration tests."""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, SetupType
from gold_sniper.strategy.professional_decision_engine import evaluate_professional_decision
from gold_sniper.strategy.risk_allocator import allocate_risk


ENTER_DECISIONS = {"ENTER_FULL", "ENTER_REDUCED"}


def _bundle(setup_type="REVERSAL_STRICT", side="BUY", **overrides):
    data = {
        "setup_type": setup_type,
        "side": side,
        "context": {"direction": side, "in_ote": True},
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB" if side == "BUY" else "BEARISH_OB"},
            "price_bounds": {"low": 2400.0, "high": 2405.0},
            "execution_readiness": "READY",
            "poi_quality_score": 80.0,
        },
        "liquidity": {
            "liquidity_semantic_status": "LIQUIDITY_READY",
            "execution_readiness": "READY",
            "readiness_state": "READY",
        },
        "micro": {
            "micro_semantic_status": "MICRO_READY",
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "displacement_present": True,
            "retest_confirmed": True,
        },
        "session": {
            "trading_allowed": True,
            "session_label": "LONDON",
            "session": "LONDON",
            "session_grade": "HIGH",
        },
        "risk": {"passed": True},
        "raw": {
            "timing": {
                "readiness_state": "READY",
                "execution_readiness": "READY",
                "in_ote": True,
            },
            "setup_classification": {
                "setup_type": setup_type,
                "confidence": 0.85,
                "reason": "SYNTHETIC_READY",
                "family": "REVERSAL",
                "required_ready_sections": ["context", "poi", "liquidity", "timing", "micro"],
                "tags": [],
                "evidence": {},
            },
        },
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


class TestPdeRiskGate(unittest.TestCase):

    def test_final_risk_zero_when_enter_eligible_false(self):
        """PDE final risk_multiplier stays 0 when enter_eligible=False."""
        b = _bundle("POI_REACTION")
        result = evaluate_professional_decision(b)
        self.assertFalse(result.enter_eligible)
        self.assertEqual(result.risk_multiplier, 0.0)

    def test_risk_plan_reason_shows_enter_not_eligible_when_blocked(self):
        """When enter_eligible=False, risk_plan reason should be ENTER_NOT_ELIGIBLE."""
        b = _bundle("POI_REACTION")
        result = evaluate_professional_decision(b)
        risk_plan = result.risk_plan
        self.assertEqual(risk_plan.get("reason"), "ENTER_NOT_ELIGIBLE")

    def test_no_risk_positive_when_hard_veto(self):
        b = _bundle("REVERSAL_STRICT", session={
            "trading_allowed": False,
            "session_label": "TOKYO",
            "session": "TOKYO",
            "is_hard_block": True,
        })
        result = evaluate_professional_decision(b)
        self.assertEqual(result.risk_multiplier, 0.0)

    def test_no_risk_positive_when_setup_unknown(self):
        b = _bundle("UNKNOWN")
        result = evaluate_professional_decision(b)
        self.assertEqual(result.risk_multiplier, 0.0)
        self.assertFalse(result.enter_eligible)

    def test_no_risk_positive_when_setup_no_setup(self):
        b = _bundle("NO_SETUP")
        result = evaluate_professional_decision(b)
        self.assertEqual(result.risk_multiplier, 0.0)
        self.assertFalse(result.enter_eligible)

    def test_no_risk_positive_when_grade_d(self):
        # Build a bundle that scores D
        b = _bundle(
            "POI_REACTION",
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "execution_readiness": "UNAVAILABLE",
            },
            liquidity={},
            micro={},
            session={},
            raw={"timing": {}},
        )
        result = evaluate_professional_decision(b)
        self.assertEqual(result.risk_multiplier, 0.0)

    def test_score_breakdown_has_grade_risk_multiplier(self):
        b = _bundle("REVERSAL_STRICT")
        result = evaluate_professional_decision(b)
        self.assertIn("grade_risk_multiplier", result.score_breakdown)
        self.assertIn("effective_risk_pct", result.score_breakdown)

    def test_risk_preview_can_be_positive_but_final_risk_zero(self):
        """risk_preview (hypothetical) may be positive even if final risk is 0 due to blockers."""
        b = _bundle("POI_REACTION")
        result = evaluate_professional_decision(b)
        # With POI_REACTION, risk_preview should still be 0 because cap=0
        risk_preview = result.risk_preview
        self.assertFalse(risk_preview.get("allowed", True))
        self.assertEqual(risk_preview.get("risk_pct", 1.0), 0.0)

    def test_risk_allocator_passed_enter_eligible_from_pde(self):
        """Verify that when PDE calls allocate_risk, enter_eligible is transmitted."""
        b = _bundle("POI_REACTION")
        result = evaluate_professional_decision(b)
        risk_plan = result.risk_plan
        # The metadata should reflect enter_eligible was passed
        self.assertIn("enter_eligible", risk_plan.get("metadata", {}))
        self.assertFalse(risk_plan["metadata"]["enter_eligible"])

    def test_poi_reaction_never_enter_no_risk(self):
        """POI_REACTION: no ENTER, no risk."""
        b = _bundle("POI_REACTION")
        result = evaluate_professional_decision(b)
        self.assertNotIn(result.decision, ENTER_DECISIONS)
        self.assertFalse(result.enter_eligible)
        self.assertEqual(result.risk_multiplier, 0.0)


if __name__ == "__main__":
    unittest.main()
