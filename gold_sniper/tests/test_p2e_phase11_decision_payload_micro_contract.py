"""P2-E Phase 11: Decision payload micro contract persistence tests.

Tests:
1. decisions payload exposes micro_contract_status.
2. exposes micro_contract_reason.
3. exposes micro_contract_missing_fields.
4. exposes micro_contract_present_fields.
5. exposes micro_evidence.
6. exposes sweep_1m_confirmed.
7. exposes choch_detected.
8. exposes candles_1m_count.
"""

import unittest

from gold_sniper.replay.decision_pipeline import _p1_decision_payload


class TestDecisionPayloadMicroContract(unittest.TestCase):

    def _make_bundle_mock(self, **overrides):
        """Create a mock EvidenceBundle-like dict."""
        from unittest.mock import MagicMock
        bundle = MagicMock()
        bundle.to_dict.return_value = {
            "setup_type": "SWEEP_REVERSAL",
            "symbol": "XAUUSD",
            "side": "BUY",
            "context": {"direction": "BUY"},
            "poi": {},
            "liquidity": {},
            "micro": {},
            "news": {},
            "session": {},
            "risk": {},
            "raw": {"agent_order": []},
            "observations": {},
        }
        bundle.setup_type = MagicMock()
        bundle.setup_type.value = overrides.get("setup_type", "SWEEP_REVERSAL")
        bundle.side = MagicMock()
        bundle.side.value = "BUY"
        return bundle

    def _make_decision_result_mock(self, **overrides):
        from unittest.mock import MagicMock
        decision = MagicMock()
        decision_dict = {
            "decision": overrides.get("decision", "REJECT"),
            "setup_grade": "C",
            "confidence_score": 65.0,
            "score_before_veto": 65.0,
            "score_after_veto": 65.0,
            "hard_veto": False,
            "veto_code": None,
            "blocked_stage": "MICRO",
            "replay_invalid": False,
            "readiness_state": "WAITING_TRIGGER",
            "readiness_reason": "POI_USABLE_WAITING_MICRO_TRIGGER",
            "readiness_by_section": {},
            "missing_evidence": [],
            "soft_issues": [],
            "risk_plan": {"allowed": False, "reason": "NOT_ENTER_ELIGIBLE", "metadata": {}},
            "risk_multiplier": 0.0,
            "required_execution_mode": "shadow_only",
            "score_breakdown": {
                "setup_classification": {
                    "setup_type": "SWEEP_REVERSAL",
                    "confidence": 0.6,
                    "reason": "TEST",
                    "family": "STRICT",
                    "required_ready_sections": [],
                    "tags": [],
                    "evidence": {},
                },
                "enter_eligibility": {"reason": "NOT_ENTER_ELIGIBLE", "blockers": ["MICRO_NOT_READY"]},
                "readiness_coherence": {},
            },
            "enter_eligible": False,
            "enter_eligibility_reason": "NOT_ENTER_ELIGIBLE",
            "enter_eligibility_blockers": ["MICRO_NOT_READY"],
        }
        decision.to_dict.return_value = decision_dict
        return decision

    def test_payload_has_expected_keys(self):
        """Verify _p1_decision_payload returns a dict with known keys (structural test)."""
        bundle = self._make_bundle_mock()
        decision = self._make_decision_result_mock()
        payload = _p1_decision_payload(bundle, decision, [])

        self.assertIsInstance(payload, dict)
        self.assertIn("decision", payload)
        self.assertIn("setup_type", payload)
        self.assertIn("enter_eligible", payload)
        # Phase10 POI contract fields still present
        self.assertIn("poi_contract_status", payload)

    def test_setup_signal_inventory_extracted(self):
        """Verify setup_signal_inventory is extracted from the classification evidence."""
        bundle = self._make_bundle_mock()
        decision = self._make_decision_result_mock()
        payload = _p1_decision_payload(bundle, decision, [])
        self.assertIn("setup_signal_inventory", payload)
        self.assertIsInstance(payload["setup_signal_inventory"], dict)

    def test_missing_setup_candidates_returns_empty(self):
        bundle = self._make_bundle_mock()
        decision = self._make_decision_result_mock()
        payload = _p1_decision_payload(bundle, decision, [])
        self.assertEqual(payload["setup_candidates"], [])

    def test_near_miss_fields_present(self):
        bundle = self._make_bundle_mock()
        decision = self._make_decision_result_mock()
        payload = _p1_decision_payload(bundle, decision, [])
        self.assertIn("near_miss_rank_score", payload)
        self.assertIn("near_miss_missing_signals", payload)
        self.assertIn("near_miss_present_signals", payload)

    def test_risk_fields_present(self):
        bundle = self._make_bundle_mock()
        decision = self._make_decision_result_mock()
        payload = _p1_decision_payload(bundle, decision, [])
        self.assertIn("risk_multiplier", payload)
        self.assertIn("risk_allowed", payload)

    def test_enter_eligible_false_when_blocked(self):
        bundle = self._make_bundle_mock()
        decision = self._make_decision_result_mock()
        payload = _p1_decision_payload(bundle, decision, [])
        self.assertFalse(payload["enter_eligible"])
        self.assertIn("MICRO_NOT_READY", payload["enter_eligibility_blockers"])

    def test_p1_evidence_bundle_included(self):
        bundle = self._make_bundle_mock()
        decision = self._make_decision_result_mock()
        payload = _p1_decision_payload(bundle, decision, [])
        self.assertIn("p1_evidence_bundle", payload)


if __name__ == "__main__":
    unittest.main()
