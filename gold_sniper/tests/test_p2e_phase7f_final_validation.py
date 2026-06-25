"""P2-E Phase 7F — Final validation test.

Tests the phase7_final_validation module against synthetic
and smoke-like summary dicts.
"""

import unittest

from gold_sniper.validation.phase7_final_validation import (
    validate_phase7_final_summary,
)


class TestFinalValidationAllPass(unittest.TestCase):
    """All checks pass with a valid summary."""

    def _valid_summary(self):
        return {
            "total_decisions": 11023,
            "ENTER_count": 0,
            "decision_counts": {"WATCH_ONLY": 7303, "REJECT": 3227},
            "setup_type_distribution": {"UNKNOWN": 6846, "POI_REACTION": 4177},
            "setup_family_distribution": {"UNKNOWN": 6846, "REACTION": 4177},
            "enter_eligible_count": 0,
            "enter_eligibility_reason_distribution": {"NO_SETUP": 4177},
            "enter_eligibility_blocker_distribution": {"NO_SETUP_OR_UNKNOWN": 6846},
            "risk_allowed_count": 0,
            "risk_multiplier_positive": 0,
            "risk_reason_distribution": {"ENTER_NOT_ELIGIBLE": 11023},
            "risk_positive_but_not_enter_eligible_count": 0,
            "readiness_state_distribution": {"UNAVAILABLE": 6746, "REJECT": 3227},
            "readiness_coherence_violation_count": 0,
            "READY_with_missing_ready_blockers_count": 0,
            "READY_with_non_ready_sections_count": 0,
            "agent_poi_handoff_source_distribution": {
                "agent3:NONE": 6846, "agent3:P2A_SELECTED_POI": 4177,
            },
            "legacy_fallback_usage_count": 0,
            "p2a_selected_poi_consumed_count": 12531,
            "p2a_candidate_fallback_count": 0,
            "p2a_missing_or_bounds_missing_count": 20538,
            "filled_trades": 0,
            "missed_entries": 0,
            "findings": [],
        }

    def test_phase7_final_valid(self):
        result = validate_phase7_final_summary(self._valid_summary())
        self.assertTrue(result["phase7_final_valid"])
        self.assertEqual(result["blocking_findings"], [])
        # Non-blocking warnings are expected (ENTER=0, UNKNOWN>0)
        self.assertGreaterEqual(len(result["warnings"]), 0)

    def test_all_checks_true(self):
        result = validate_phase7_final_summary(self._valid_summary())
        for check_name, check_value in result["checks"].items():
            self.assertTrue(check_value, f"Check '{check_name}' should be True")


class TestFinalValidationCriticalFailures(unittest.TestCase):
    """Critical invariants must fail validation when violated."""

    def test_risk_without_eligibility_fails(self):
        summary = {
            "setup_type_distribution": {"UNKNOWN": 100},
            "enter_eligible_count": 0,
            "risk_allowed_count": 5,
            "readiness_coherence_violation_count": 0,
            "agent_poi_handoff_source_distribution": {"agent3:NONE": 100},
            "risk_positive_but_not_enter_eligible_count": 5,
            "ENTER_count": 0,
        }
        result = validate_phase7_final_summary(summary)
        self.assertFalse(result["phase7_final_valid"])
        self.assertTrue(any("RISK_WITHOUT" in f for f in result["blocking_findings"]))

    def test_readiness_coherence_violation_fails(self):
        summary = {
            "setup_type_distribution": {"UNKNOWN": 100},
            "enter_eligible_count": 0,
            "risk_allowed_count": 0,
            "readiness_coherence_violation_count": 3,
            "agent_poi_handoff_source_distribution": {"agent3:NONE": 100},
            "risk_positive_but_not_enter_eligible_count": 0,
            "ENTER_count": 0,
        }
        result = validate_phase7_final_summary(summary)
        self.assertFalse(result["phase7_final_valid"])
        self.assertTrue(any("COHERENCE_VIOLATION" in f for f in result["blocking_findings"]))

    def test_enter_without_eligibility_fails(self):
        summary = {
            "setup_type_distribution": {"UNKNOWN": 100},
            "enter_eligible_count": 0,
            "risk_allowed_count": 5,
            "readiness_coherence_violation_count": 0,
            "agent_poi_handoff_source_distribution": {"agent3:NONE": 100},
            "risk_positive_but_not_enter_eligible_count": 0,
            "ENTER_count": 5,
        }
        result = validate_phase7_final_summary(summary)
        self.assertFalse(result["phase7_final_valid"])
        self.assertTrue(any("ENTER_WITHOUT" in f for f in result["blocking_findings"]))

    def test_missing_metrics_blocking(self):
        summary = {
            "ENTER_count": 0,
            "risk_positive_but_not_enter_eligible_count": 0,
        }
        result = validate_phase7_final_summary(summary)
        self.assertFalse(result["phase7_final_valid"])
        self.assertTrue(any("METRICS_MISSING" in f for f in result["blocking_findings"]))


class TestFinalValidationWarnings(unittest.TestCase):
    """Non-blocking conditions produce warnings only."""

    def test_unknown_setup_type_warns(self):
        summary = {
            "setup_type_distribution": {"UNKNOWN": 500, "POI_REACTION": 100},
            "enter_eligible_count": 0,
            "risk_allowed_count": 0,
            "readiness_coherence_violation_count": 0,
            "agent_poi_handoff_source_distribution": {"agent3:NONE": 600},
            "risk_positive_but_not_enter_eligible_count": 0,
            "ENTER_count": 0,
            "UNKNOWN_setup_type_count": 500,
        }
        result = validate_phase7_final_summary(summary)
        self.assertTrue(result["phase7_final_valid"])  # UNKNOWN > 0 is NOT blocking
        self.assertTrue(any("UNKNOWN_SETUP_TYPE_COUNT" in w for w in result["warnings"]))

    def test_enter_zero_warns(self):
        summary = {
            "setup_type_distribution": {"POI_REACTION": 100},
            "enter_eligible_count": 0,
            "risk_allowed_count": 0,
            "readiness_coherence_violation_count": 0,
            "agent_poi_handoff_source_distribution": {"agent3:P2A_SELECTED_POI": 100},
            "risk_positive_but_not_enter_eligible_count": 0,
            "ENTER_count": 0,
        }
        result = validate_phase7_final_summary(summary)
        self.assertTrue(result["phase7_final_valid"])
        self.assertTrue(any("ENTER_COUNT=0" in w for w in result["warnings"]))

    def test_coverage_missing_warns(self):
        summary = {
            "setup_type_distribution": {"UNKNOWN": 100},
            "enter_eligible_count": 0,
            "risk_allowed_count": 0,
            "readiness_coherence_violation_count": 0,
            "agent_poi_handoff_source_distribution": {"agent3:NONE": 100},
            "risk_positive_but_not_enter_eligible_count": 0,
            "ENTER_count": 0,
            "findings": ["30m_COVERAGE_MISSING"],
        }
        result = validate_phase7_final_summary(summary)
        self.assertTrue(result["phase7_final_valid"])
        self.assertTrue(any("30m_COVERAGE_MISSING" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
