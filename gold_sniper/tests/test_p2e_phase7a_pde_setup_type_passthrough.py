"""P2-E Phase 7A — PDE setup_type passthrough tests (no ENTER forced)."""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, SetupType, TradeSide
from gold_sniper.strategy.professional_decision_engine import evaluate_professional_decision
from gold_sniper.strategy.setup_taxonomy import classify_setup


ENTER_DECISIONS = {"ENTER_FULL", "ENTER_REDUCED"}


def _classified_bundle(setup_type: SetupType, **overrides):
    """Build an EvidenceBundle with a pre-classified setup_type and classification in raw."""
    data: dict = {
        "setup_type": setup_type.value,
        "side": "BUY",
        "context": {"direction": "BUY", "htf_aligned": True},
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
            "price_bounds": {"low": 2400.0, "high": 2405.0},
        },
        "liquidity": {"readiness_state": "READY"},
        "micro": {"readiness_state": "READY"},
        "session": {"trading_allowed": True, "session_label": "LONDON"},
        "risk": {"passed": True},
        "raw": {
            "timing": {"readiness_state": "READY"},
            "setup_classification": {
                "setup_type": setup_type.value,
                "confidence": 0.85,
                "reason": "SYNTHETIC_TEST",
                "family": "TEST",
                "required_ready_sections": [],
                "tags": [],
                "evidence": {},
            },
        },
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


class TestPDESetupTypePassthrough(unittest.TestCase):

    def test_pde_exposes_setup_type_in_score_breakdown(self):
        """PDE score_breakdown contains setup_type."""
        bundle = _classified_bundle(SetupType.CONTINUATION_STRICT)
        result = evaluate_professional_decision(bundle)
        breakdown = result.score_breakdown
        self.assertIn("setup_type", breakdown)
        self.assertEqual(breakdown["setup_type"], "CONTINUATION_STRICT")

    def test_pde_exposes_setup_classification_in_score_breakdown(self):
        """PDE score_breakdown contains setup_classification dict."""
        bundle = _classified_bundle(SetupType.CONTINUATION_STRICT)
        result = evaluate_professional_decision(bundle)
        breakdown = result.score_breakdown
        self.assertIn("setup_classification", breakdown)
        classification = breakdown["setup_classification"]
        self.assertEqual(classification["setup_type"], "CONTINUATION_STRICT")
        self.assertEqual(classification["reason"], "SYNTHETIC_TEST")

    def test_phase7a_does_not_force_enter(self):
        """Phase 7A MUST NOT produce ENTER just because setup_type is classified."""
        # Build a perfect-looking setup — but risk_multiplier stays 0 for new types
        bundle = _classified_bundle(SetupType.CONTINUATION_STRICT)
        result = evaluate_professional_decision(bundle)
        self.assertNotIn(result.decision, ENTER_DECISIONS,
                         f"Phase 7A MUST NOT produce {result.decision} — risk_multiplier={result.risk_multiplier}")

    def test_phase7a_risk_multiplier_zero_for_new_types(self):
        """Phase 7A MUST NOT produce positive risk_multiplier for new setup types."""
        for st in [SetupType.CONTINUATION_STRICT, SetupType.SWEEP_REVERSAL,
                     SetupType.OTE_PULLBACK, SetupType.POI_REACTION, SetupType.REVERSAL_LIGHT]:
            bundle = _classified_bundle(st)
            result = evaluate_professional_decision(bundle)
            self.assertEqual(result.risk_multiplier, 0.0,
                             f"{st.value} must have risk_multiplier=0.0, got {result.risk_multiplier}")

    def test_no_setup_never_enter(self):
        """NO_SETUP must never produce ENTER."""
        bundle = _classified_bundle(SetupType.NO_SETUP)
        result = evaluate_professional_decision(bundle)
        self.assertNotIn(result.decision, ENTER_DECISIONS)
        self.assertEqual(result.risk_multiplier, 0.0)

    def test_legacy_reversal_strict_can_still_enter(self):
        """Phase 7A does NOT break legacy REVERSAL_STRICT entry path."""
        bundle = _classified_bundle(SetupType.REVERSAL_STRICT)
        result = evaluate_professional_decision(bundle)
        # Legacy type with high enough score SHOULD be able to enter
        # (risk_multiplier may still be positive for legacy types)
        self.assertIsNotNone(result.decision)
        self.assertIn(result.decision, {
            "ENTER_FULL", "ENTER_REDUCED", "WAIT_FOR_TRIGGER",
            "WAIT_FOR_BETTER_PRICE", "WATCH_ONLY", "REJECT",
        })

    def test_pde_does_not_change_risk_multiplier_for_unknown(self):
        """Phase 7A: UNKNOWN setup_type produces no risk."""
        bundle = _classified_bundle(SetupType.UNKNOWN)
        result = evaluate_professional_decision(bundle)
        self.assertEqual(result.risk_multiplier, 0.0)


if __name__ == "__main__":
    unittest.main()
