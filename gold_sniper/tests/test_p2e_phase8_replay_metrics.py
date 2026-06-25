import unittest

from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary


def _decision(**overrides):
    row = {
        "decision": "WATCH_ONLY",
        "setup_type": "SWEEP_REVERSAL",
        "setup_grade": "C",
        "readiness_reason": "GLOBAL_READINESS_NOT_READY",
        "risk_reason": "ENTER_NOT_ELIGIBLE",
        "enter_eligible": False,
        "enter_eligibility_blockers": ["GLOBAL_READINESS_NOT_READY", "SECTION_NOT_READY:micro"],
        "risk_multiplier": 0.0,
        "setup_candidates": [
            {"candidate_type": "SWEEP_REVERSAL", "confidence": 0.65},
        ],
        "best_setup_candidate": {"candidate_type": "SWEEP_REVERSAL", "confidence": 0.65},
        "near_miss_missing_signals": ["MICRO_READY"],
    }
    row.update(overrides)
    return row


class TestPhase8ReplayMetrics(unittest.TestCase):
    def test_near_miss_counts_are_explicit_and_legacy_compatible(self):
        metrics = build_p2c_performance_summary(decisions=[
            _decision(setup_type="UNKNOWN", setup_candidates=[], best_setup_candidate={}),
            _decision(setup_type="POI_REACTION"),
            _decision(setup_type="SWEEP_REVERSAL"),
        ])
        self.assertEqual(metrics["near_miss_scanned_count"], 3)
        self.assertEqual(metrics["near_miss_with_candidates_count"], 2)
        self.assertEqual(metrics["near_miss_candidate_count"], 2)
        self.assertEqual(metrics["near_miss_by_current_setup_type"]["UNKNOWN"], 1)
        self.assertEqual(metrics["near_miss_by_current_setup_type"]["POI_REACTION"], 1)
        self.assertEqual(metrics["near_miss_by_current_setup_type"]["SWEEP_REVERSAL"], 1)

    def test_sweep_reversal_blockers_are_aggregated(self):
        metrics = build_p2c_performance_summary(decisions=[_decision()])
        blockers = metrics["enter_eligibility_blockers_by_setup_type"]["SWEEP_REVERSAL"]
        self.assertEqual(blockers["GLOBAL_READINESS_NOT_READY"], 1)
        self.assertEqual(blockers["SECTION_NOT_READY:micro"], 1)

    def test_sweep_reversal_readiness_reasons_are_aggregated(self):
        metrics = build_p2c_performance_summary(decisions=[_decision()])
        reasons = metrics["readiness_reason_by_setup_type"]["SWEEP_REVERSAL"]
        self.assertEqual(reasons["GLOBAL_READINESS_NOT_READY"], 1)

    def test_sweep_reversal_risk_reasons_are_aggregated(self):
        metrics = build_p2c_performance_summary(decisions=[_decision()])
        reasons = metrics["risk_reason_by_setup_type"]["SWEEP_REVERSAL"]
        self.assertEqual(reasons["ENTER_NOT_ELIGIBLE"], 1)

    def test_nested_counters_are_json_style_dicts(self):
        metrics = build_p2c_performance_summary(decisions=[_decision()])
        self.assertIsInstance(metrics["decision_counts_by_setup_type"], dict)
        self.assertIsInstance(metrics["decision_counts_by_setup_type"]["SWEEP_REVERSAL"], dict)
        self.assertEqual(metrics["decision_counts_by_setup_type"]["SWEEP_REVERSAL"]["WATCH_ONLY"], 1)
        self.assertEqual(metrics["grade_distribution_by_setup_type"]["SWEEP_REVERSAL"]["C"], 1)


if __name__ == "__main__":
    unittest.main()
