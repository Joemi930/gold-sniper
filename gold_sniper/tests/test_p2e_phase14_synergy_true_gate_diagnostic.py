import unittest

from gold_sniper.tools.diagnose_readiness_risk_gate import diagnose_readiness_risk_gate


class TestP2EPhase14SynergyTrueGateDiagnostic(unittest.TestCase):
    def test_diagnostic_isolates_synergy_true_cases(self):
        report = diagnose_readiness_risk_gate([
            {"decision": "REJECT", "setup_type": "UNKNOWN", "poi_micro_synergy": False},
            {
                "decision": "WATCH_ONLY",
                "setup_type": "POI_REACTION",
                "setup_grade": "C",
                "poi_micro_synergy": True,
                "enter_eligible": False,
                "risk_allowed": False,
                "risk_multiplier": 0.0,
                "setup_candidates": [{"candidate_type": "POI_REACTION"}],
                "best_setup_candidate": {"candidate_type": "POI_REACTION"},
                "risk_preview": {"metadata": {"setup_max_risk_multiplier": 0.0}},
            },
        ])
        self.assertEqual(report["synergy_true_count"], 1)

    def test_diagnostic_lists_watch_only(self):
        report = diagnose_readiness_risk_gate([
            {"decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "poi_micro_synergy": True}
        ])
        self.assertEqual(report["synergy_true_watch_only_count"], 1)
        self.assertEqual(len(report["synergy_true_watch_only"]), 1)

    def test_diagnostic_lists_no_enter(self):
        report = diagnose_readiness_risk_gate([
            {"decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "poi_micro_synergy": True, "enter_eligible": False}
        ])
        self.assertEqual(report["synergy_true_not_enter_eligible_count"], 1)

    def test_diagnostic_does_not_modify_decisions(self):
        decisions = [{"decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "poi_micro_synergy": True}]
        original = [dict(item) for item in decisions]
        diagnose_readiness_risk_gate(decisions)
        self.assertEqual(decisions, original)


if __name__ == "__main__":
    unittest.main()
