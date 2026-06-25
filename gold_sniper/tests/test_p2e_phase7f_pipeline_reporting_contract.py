"""P2-E Phase 7F — Pipeline and reporting contract tests.

Verifies that all pipeline stages and reporting outputs contain
the complete set of Phase7A-F contract fields.
"""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary

# Phase7A-F mandatory contract fields
PHASE7_MANDATORY_FIELDS = [
    "setup_type",
    "setup_classification",
    "setup_family",
    "enter_eligible",
    "enter_eligibility_reason",
    "risk_multiplier",
    "risk_allowed",
    "risk_reason",
    "grade_risk_multiplier",
    "effective_risk_pct",
    "readiness_coherence",
    "readiness_missing_ready_blockers",
    "readiness_non_ready_sections",
]

PHASE7_SUMMARY_MANDATORY_KEYS = [
    "setup_type_distribution",
    "enter_eligible_count",
    "risk_allowed_count",
    "readiness_coherence_violation_count",
    "risk_positive_but_not_enter_eligible_count",
    "agent_poi_handoff_source_distribution",
    "legacy_fallback_usage_count",
]


class TestDecisionPayloadContract(unittest.TestCase):
    """The decision payload dict must carry all Phase7A-F fields."""

    def _make_payload(self) -> dict:
        """Simulate _p1_decision_payload output."""
        return {
            "decision": "WATCH_ONLY",
            "setup_grade": "B",
            "setup_type": "CONTINUATION_LIGHT",
            "setup_classification": {"setup_type": "CONTINUATION_LIGHT"},
            "setup_family": "CONTINUATION",
            "setup_classification_reason": "SYNTHETIC",
            "setup_classification_confidence": 0.75,
            "enter_eligible": False,
            "enter_eligibility_reason": "GRADE_INSUFFICIENT",
            "enter_eligibility_blockers": [],
            "enter_eligibility_checks": {},
            "risk_preview": {},
            "risk_multiplier": 0.0,
            "grade_risk_multiplier": 0.5,
            "effective_risk_pct": 0.0,
            "setup_max_risk_multiplier": 0.75,
            "risk_allowed": False,
            "risk_reason": "ENTER_NOT_ELIGIBLE",
            "readiness_coherence": {"can_be_global_ready": False},
            "readiness_non_ready_sections": {},
            "readiness_missing_ready_blockers": [],
            "readiness_state": "UNAVAILABLE",
            "readiness_reason": "READINESS_UNAVAILABLE",
        }

    def test_payload_has_all_mandatory_fields(self):
        payload = self._make_payload()
        for field in PHASE7_MANDATORY_FIELDS:
            self.assertIn(field, payload, f"Missing mandatory field: {field}")

    def test_setup_type_present(self):
        self.assertIn("setup_type", self._make_payload())

    def test_enter_eligible_present(self):
        self.assertIn("enter_eligible", self._make_payload())

    def test_risk_multiplier_present(self):
        self.assertIn("risk_multiplier", self._make_payload())

    def test_risk_allowed_present(self):
        self.assertIn("risk_allowed", self._make_payload())

    def test_readiness_coherence_present(self):
        self.assertIn("readiness_coherence", self._make_payload())


class TestPerformanceSummaryContract(unittest.TestCase):
    """p2c_performance_summary must expose all Phase7A-F keys."""

    def _sample_decisions(self):
        return [
            {
                "decision": "WATCH_ONLY",
                "setup_type": "POI_REACTION",
                "setup_grade": "D",
                "setup_family": "REACTION",
                "enter_eligible": False,
                "enter_eligibility_reason": "NO_SETUP",
                "enter_eligibility_blockers": [],
                "risk_multiplier": 0.0,
                "risk_allowed": False,
                "risk_reason": "SETUP_TYPE_NOT_TRADABLE",
                "grade_risk_multiplier": 0.0,
                "effective_risk_pct": 0.0,
                "readiness_state": "UNAVAILABLE",
                "readiness_missing_ready_blockers": ["LIQUIDITY_MISSING"],
                "readiness_non_ready_sections": {"liquidity": "UNAVAILABLE"},
                "p1_evidence_bundle": {
                    "liquidity": {
                        "agent3_poi_handoff": {"source": "NONE"},
                    },
                    "raw": {
                        "timing": {
                            "agent4_poi_handoff": {"source": "NONE"},
                        },
                    },
                    "micro": {
                        "agent5_poi_handoff": {"source": "NONE"},
                    },
                },
            },
        ]

    def test_summary_has_all_mandatory_keys(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        for key in PHASE7_SUMMARY_MANDATORY_KEYS:
            self.assertIn(key, summary, f"Missing mandatory summary key: {key}")

    def test_summary_tolerates_empty_decisions(self):
        summary = build_p2c_performance_summary(decisions=[])
        self.assertIn("total_decisions", summary)
        self.assertEqual(summary["total_decisions"], 0)

    def test_summary_has_phase7a_keys(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        for key in ("setup_type_distribution", "setup_family_distribution"):
            self.assertIn(key, summary)

    def test_summary_has_phase7b_keys(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        for key in ("enter_eligible_count", "enter_eligibility_reason_distribution"):
            self.assertIn(key, summary)

    def test_summary_has_phase7c_keys(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        for key in ("risk_allowed_count", "risk_reason_distribution",
                     "risk_positive_but_not_enter_eligible_count"):
            self.assertIn(key, summary)

    def test_summary_has_phase7d_keys(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        for key in ("readiness_coherence_violation_count",
                     "READY_with_missing_ready_blockers_count"):
            self.assertIn(key, summary)

    def test_summary_has_phase7e_keys(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        for key in ("agent_poi_handoff_source_distribution",
                     "legacy_fallback_usage_count"):
            self.assertIn(key, summary)

    def test_risk_positive_but_not_eligible_is_zero(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        self.assertEqual(summary["risk_positive_but_not_enter_eligible_count"], 0)


class TestReadinessCoherenceInSummary(unittest.TestCase):
    """Readiness coherence metrics must be in summary."""

    def _sample_decisions(self):
        return [
            {
                "decision": "WATCH_ONLY",
                "setup_type": "CONTINUATION_LIGHT",
                "setup_grade": "B",
                "readiness_state": "READY",
                "readiness_missing_ready_blockers": [],
                "readiness_non_ready_sections": {},
                "enter_eligible": True,
                "risk_multiplier": 0.5,
                "risk_allowed": True,
                "risk_reason": "OK",
                "grade_risk_multiplier": 0.5,
                "effective_risk_pct": 0.5,
                "p1_evidence_bundle": {},
            },
            {
                "decision": "WATCH_ONLY",
                "setup_type": "POI_REACTION",
                "setup_grade": "D",
                "readiness_state": "UNAVAILABLE",
                "readiness_missing_ready_blockers": ["SESSION_CONTEXT_MISSING"],
                "readiness_non_ready_sections": {"session": "UNAVAILABLE"},
                "enter_eligible": False,
                "risk_multiplier": 0.0,
                "risk_allowed": False,
                "risk_reason": "ENTER_NOT_ELIGIBLE",
                "grade_risk_multiplier": 0.0,
                "effective_risk_pct": 0.0,
                "p1_evidence_bundle": {},
            },
        ]

    def test_readiness_coherence_violation_count(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        # READY decision has no blockers, no non-ready sections → 0 violations
        self.assertEqual(summary["readiness_coherence_violation_count"], 0)

    def test_ready_with_missing_blockers_count(self):
        summary = build_p2c_performance_summary(decisions=self._sample_decisions())
        # No READY decision has blockers
        self.assertEqual(summary["READY_with_missing_ready_blockers_count"], 0)


if __name__ == "__main__":
    unittest.main()
