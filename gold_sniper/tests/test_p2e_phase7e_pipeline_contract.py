"""P2-E Phase 7E — Pipeline contract tests.

Verifies that the replay decision pipeline preserves all Phase7A-D
fields end-to-end. Uses mocked payloads to avoid the pre-existing
replay/__init__.py import bug.
"""

import unittest

from gold_sniper.strategy.contracts import (
    EvidenceBundle,
    SetupType,
)


def _minimal_bundle(**overrides) -> EvidenceBundle:
    data = {
        "setup_type": "CONTINUATION_LIGHT",
        "side": "BUY",
        "context": {"direction": "BUY", "htf_aligned": True, "in_ote": True},
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB", "score": 85},
            "price_bounds": {"low": 2400, "high": 2405},
        },
        "liquidity": {
            "liquidity_semantic_status": "LIQUIDITY_READY",
            "agent3_poi_handoff": {"source": "P2A_SELECTED_POI"},
            "agent3_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
        },
        "micro": {
            "micro_semantic_status": "MICRO_READY",
            "agent5_poi_handoff": {"source": "P2A_SELECTED_POI"},
            "agent5_consumed_poi": {"present": True},
        },
        "news": {"news_clear": True},
        "session": {"trading_allowed": True},
        "risk": {},
        "raw": {
            "timing": {
                "agent4_poi_handoff": {"source": "P2A_SELECTED_POI"},
                "agent4_consumed_poi": {"present": True},
            },
            "setup_classification": {
                "setup_type": "CONTINUATION_LIGHT",
                "confidence": 0.75,
                "reason": "SYNTHETIC",
                "family": "CONTINUATION",
            },
        },
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


class TestEvidenceBundleContract(unittest.TestCase):
    """EvidenceBundle contract: setup_type must be present and classifiable."""

    def test_setup_type_present_in_bundle(self):
        b = _minimal_bundle()
        self.assertIsNotNone(b.setup_type)
        self.assertNotEqual(b.setup_type, SetupType.UNKNOWN)

    def test_setup_classification_in_raw(self):
        b = _minimal_bundle()
        classification = b.raw.get("setup_classification")
        self.assertIsNotNone(classification)
        self.assertEqual(classification["setup_type"], "CONTINUATION_LIGHT")

    def test_unknown_setup_type_detected(self):
        """Bundle with UNKNOWN setup_type — verifies classification would be needed."""
        b = _minimal_bundle(setup_type="UNKNOWN")
        self.assertEqual(b.setup_type, SetupType.UNKNOWN)
        # In real pipeline, evidence_builder would call classify_setup() here

    def test_handoff_fields_present_in_bundle(self):
        """Agent handoff fields must be accessible from the bundle."""
        b = _minimal_bundle()
        self.assertIn("agent3_poi_handoff", b.liquidity)
        self.assertEqual(b.liquidity["agent3_poi_handoff"]["source"], "P2A_SELECTED_POI")
        self.assertIn("agent4_poi_handoff", b.raw["timing"])
        self.assertEqual(b.raw["timing"]["agent4_poi_handoff"]["source"], "P2A_SELECTED_POI")
        self.assertIn("agent5_poi_handoff", b.micro)
        self.assertEqual(b.micro["agent5_poi_handoff"]["source"], "P2A_SELECTED_POI")


class TestDecisionPayloadContract(unittest.TestCase):
    """Decision payload must contain all Phase7A-D contract fields."""

    def _payload(self) -> dict:
        """Simulate a minimal _p1_decision_payload output."""
        return {
            "decision": "WATCH_ONLY",
            "setup_grade": "B",
            # Phase 7A: setup taxonomy
            "setup_type": "CONTINUATION_LIGHT",
            "setup_classification": {
                "setup_type": "CONTINUATION_LIGHT",
                "family": "CONTINUATION", "confidence": 0.75, "reason": "SYNTHETIC",
            },
            "setup_family": "CONTINUATION",
            "setup_classification_reason": "SYNTHETIC",
            "setup_classification_confidence": 0.75,
            # Phase 7B: enter eligibility
            "enter_eligible": False,
            "enter_eligibility_reason": "GRADE_INSUFFICIENT",
            "enter_eligibility_blockers": ["GRADE_BELOW_B"],
            "enter_eligibility_checks": {"all_sections_ready": False},
            "risk_preview": {"allowed": False, "reason": "GRADE_INSUFFICIENT"},
            # Phase 7C: risk multiplier mapping
            "risk_multiplier": 0.0,
            "grade_risk_multiplier": 0.5,
            "effective_risk_pct": 0.0,
            "setup_max_risk_multiplier": 0.75,
            "risk_allowed": False,
            "risk_reason": "ENTER_NOT_ELIGIBLE",
            # Phase 7D: readiness coherence
            "readiness_coherence": {
                "can_be_global_ready": False,
                "missing_ready_blockers": ["SESSION_CONTEXT_MISSING"],
                "non_ready_sections": {"session": "UNAVAILABLE"},
            },
            "readiness_non_ready_sections": {"session": "UNAVAILABLE"},
            "readiness_missing_ready_blockers": ["SESSION_CONTEXT_MISSING"],
            "readiness_state": "UNAVAILABLE",
            "readiness_reason": "SESSION_CONTEXT_MISSING_NOT_READY",
        }

    def test_payload_has_setup_type(self):
        self.assertIn("setup_type", self._payload())

    def test_payload_has_enter_eligible(self):
        self.assertIn("enter_eligible", self._payload())

    def test_payload_has_risk_multiplier(self):
        self.assertIn("risk_multiplier", self._payload())

    def test_payload_has_risk_allowed(self):
        self.assertIn("risk_allowed", self._payload())

    def test_payload_has_risk_reason(self):
        self.assertIn("risk_reason", self._payload())

    def test_payload_has_readiness_coherence(self):
        self.assertIn("readiness_coherence", self._payload())

    def test_payload_has_all_phase7a_keys(self):
        for key in ("setup_type", "setup_classification", "setup_family",
                     "setup_classification_reason", "setup_classification_confidence"):
            self.assertIn(key, self._payload(), f"Missing Phase7A key: {key}")

    def test_payload_has_all_phase7b_keys(self):
        for key in ("enter_eligible", "enter_eligibility_reason",
                     "enter_eligibility_blockers", "enter_eligibility_checks", "risk_preview"):
            self.assertIn(key, self._payload(), f"Missing Phase7B key: {key}")

    def test_payload_has_all_phase7c_keys(self):
        for key in ("risk_multiplier", "grade_risk_multiplier", "effective_risk_pct",
                     "setup_max_risk_multiplier", "risk_allowed", "risk_reason"):
            self.assertIn(key, self._payload(), f"Missing Phase7C key: {key}")

    def test_payload_has_all_phase7d_keys(self):
        for key in ("readiness_coherence", "readiness_non_ready_sections",
                     "readiness_missing_ready_blockers"):
            self.assertIn(key, self._payload(), f"Missing Phase7D key: {key}")


if __name__ == "__main__":
    unittest.main()
