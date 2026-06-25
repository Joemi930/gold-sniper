import copy
import unittest

from gold_sniper.tools.diagnose_micro_readiness import diagnose_micro_readiness


def _decision(*, grade="B", micro=None, blockers=None):
    return {
        "timestamp": "2026-05-27T00:00:00Z",
        "decision": "WATCH_ONLY",
        "setup_type": "SWEEP_REVERSAL",
        "setup_grade": grade,
        "hard_veto": False,
        "replay_invalid": False,
        "enter_eligibility_blockers": blockers or [
            "GLOBAL_READINESS_NOT_READY",
            "SECTION_NOT_READY:poi",
            "SECTION_NOT_READY:micro",
        ],
        "p1_evidence_bundle": {
            "micro": micro or {
                "readiness_state": "WAITING_TRIGGER",
                "readiness_reason": "MICRO_NO_TRIGGER_YET",
            }
        },
    }


class TestPhase10MicroReadinessAudit(unittest.TestCase):
    def test_filters_sweep_reversal_grade_b_c(self):
        report = diagnose_micro_readiness([
            _decision(grade="B"),
            _decision(grade="C"),
        ])
        self.assertEqual(report["true_near_miss_count"], 2)

    def test_excludes_grade_d(self):
        report = diagnose_micro_readiness([_decision(grade="D")])
        self.assertEqual(report["true_near_miss_count"], 0)

    def test_classifies_sweep_without_choch(self):
        report = diagnose_micro_readiness([
            _decision(micro={
                "readiness_state": "WAITING_TRIGGER",
                "readiness_reason": "MICRO_SWEEP_WAITING_CHOCH",
                "sweep_1m_confirmed": True,
                "choch_detected": False,
                "trigger_inside_poi": True,
                "candles_1m_count": 25,
            })
        ])
        self.assertEqual(report["top_cases"][0]["why_not_ready"], "SWEEP_PRESENT_CHOCH_MISSING")

    def test_classifies_choch_outside_poi(self):
        report = diagnose_micro_readiness([
            _decision(micro={
                "readiness_state": "REJECT",
                "readiness_reason": "MICRO_PRICE_OUTSIDE_POI",
                "sweep_1m_confirmed": True,
                "choch_detected": True,
                "trigger_inside_poi": False,
                "trigger_outside_poi": True,
                "candles_1m_count": 25,
            })
        ])
        self.assertEqual(report["top_cases"][0]["why_not_ready"], "TRIGGER_OUTSIDE_POI")

    def test_classifies_no_candles(self):
        report = diagnose_micro_readiness([
            _decision(micro={
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "MICRO_INSUFFICIENT_1M_CANDLES",
                "candles_1m_count": 0,
            })
        ])
        self.assertEqual(report["top_cases"][0]["why_not_ready"], "INSUFFICIENT_1M_CANDLES")

    def test_produces_what_if_poi_and_micro_ready(self):
        report = diagnose_micro_readiness([_decision()])
        what_if = report["top_cases"][0]["DIAGNOSTIC_ONLY_POI_MICRO_COUNTERFACTUAL"]
        self.assertIn("if_poi_and_micro_ready_remaining_blockers", what_if)

    def test_does_not_modify_decision(self):
        decisions = [_decision()]
        before = copy.deepcopy(decisions)
        diagnose_micro_readiness(decisions)
        self.assertEqual(decisions, before)


if __name__ == "__main__":
    unittest.main()
