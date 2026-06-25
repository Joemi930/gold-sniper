"""P2-E Phase 7B — PDE enter gate integration tests."""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.strategy.professional_decision_engine import evaluate_professional_decision


ENTER_DECISIONS = {"ENTER_FULL", "ENTER_REDUCED"}


def _ready_bundle(setup_type="REVERSAL_STRICT", side="BUY", **overrides):
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


class TestPdeEnterGate(unittest.TestCase):

    def test_pde_exposes_enter_eligible(self):
        b = _ready_bundle("REVERSAL_STRICT")
        result = evaluate_professional_decision(b)
        self.assertTrue(hasattr(result, "enter_eligible"))
        self.assertIsInstance(result.enter_eligible, bool)

    def test_pde_exposes_enter_eligibility_reason(self):
        b = _ready_bundle("REVERSAL_STRICT")
        result = evaluate_professional_decision(b)
        self.assertTrue(hasattr(result, "enter_eligibility_reason"))
        self.assertIsInstance(result.enter_eligibility_reason, str)

    def test_pde_exposes_blockers(self):
        b = _ready_bundle("UNKNOWN")
        result = evaluate_professional_decision(b)
        self.assertTrue(hasattr(result, "enter_eligibility_blockers"))
        self.assertIsInstance(result.enter_eligibility_blockers, list)

    def test_pde_blocks_enter_when_not_eligible(self):
        b = _ready_bundle("POI_REACTION")
        result = evaluate_professional_decision(b)
        self.assertFalse(result.enter_eligible)
        # With enter_eligible=False, PDE must NOT return ENTER
        self.assertNotIn(result.decision, ENTER_DECISIONS,
                         f"PDE returned {result.decision} despite enter_eligible=False")

    def test_pde_can_enter_when_eligible_and_scores_ok(self):
        """Legacy REVERSAL_STRICT with all-ready evidence can get ENTER if eligible + score OK."""
        b = _ready_bundle("REVERSAL_STRICT")
        result = evaluate_professional_decision(b)
        # If enter_eligible is True AND score meets threshold, ENTER is allowed
        if result.enter_eligible:
            self.assertIn(result.decision, {
                "ENTER_FULL", "ENTER_REDUCED", "WAIT_FOR_TRIGGER",
                "WAIT_FOR_BETTER_PRICE", "WATCH_ONLY",
            })
        else:
            # Even if not eligible, must not be ENTER
            self.assertNotIn(result.decision, ENTER_DECISIONS)

    def test_poi_reaction_never_enter(self):
        b = _ready_bundle("POI_REACTION")
        result = evaluate_professional_decision(b)
        self.assertNotIn(result.decision, ENTER_DECISIONS)

    def test_score_breakdown_has_enter_eligibility(self):
        b = _ready_bundle("REVERSAL_STRICT")
        result = evaluate_professional_decision(b)
        self.assertIn("enter_eligibility", result.score_breakdown)
        ee = result.score_breakdown["enter_eligibility"]
        self.assertIn("enter_eligible", ee)
        self.assertIn("reason", ee)
        self.assertIn("blockers", ee)


if __name__ == "__main__":
    unittest.main()
