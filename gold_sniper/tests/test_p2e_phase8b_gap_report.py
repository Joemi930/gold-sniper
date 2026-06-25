import unittest

from gold_sniper.validation.phase8_gap_report import build_phase8_gap_report


def _summary(**overrides):
    summary = {
        "run_id": "RUN",
        "total_decisions": 11023,
        "decision_distribution": {
            "WATCH_ONLY": 7303,
            "REJECT": 3227,
            "WAIT_FOR_TRIGGER": 493,
        },
        "setup_type_distribution": {
            "UNKNOWN": 6846,
            "POI_REACTION": 4177,
        },
        "enter_eligible_count": 0,
        "risk_multiplier_positive": 0,
        "risk_positive_but_not_enter_eligible_count": 0,
        "readiness_coherence_violation_count": 0,
        "legacy_fallback_usage_count": 0,
        "filled_trades": 0,
        "profit_factor": None,
    }
    summary.update(overrides)
    return summary


BASELINE = _summary(run_id="P2E_PHASE7F_FINAL_SMOKE_2026_05_27_2026_06_05")
CURRENT = _summary(
    run_id="P2E_PHASE8B_FINAL_SMOKE_2026_05_27_2026_06_05",
    decision_distribution={
        "WATCH_ONLY": 6784,
        "REJECT": 3746,
        "WAIT_FOR_TRIGGER": 493,
    },
    setup_type_distribution={
        "UNKNOWN": 6846,
        "POI_REACTION": 4122,
        "SWEEP_REVERSAL": 55,
    },
    near_miss_scanned_count=7277,
    enter_eligibility_blockers_by_setup_type={
        "SWEEP_REVERSAL": {
            "GLOBAL_READINESS_NOT_READY": 55,
            "SECTION_NOT_READY:poi": 55,
        }
    },
    readiness_reason_by_setup_type={
        "SWEEP_REVERSAL": {
            "POI_MEDIUM_CONTEXT_INTERESTING": 22,
        }
    },
)


class TestPhase8BGapReport(unittest.TestCase):
    def test_calculates_unknown_delta(self):
        report = build_phase8_gap_report(BASELINE, CURRENT)
        self.assertEqual(report["delta"]["UNKNOWN"], 0)

    def test_calculates_poi_reaction_delta(self):
        report = build_phase8_gap_report(BASELINE, CURRENT)
        self.assertEqual(report["delta"]["POI_REACTION"], -55)

    def test_calculates_sweep_reversal_delta(self):
        report = build_phase8_gap_report(BASELINE, CURRENT)
        self.assertEqual(report["delta"]["SWEEP_REVERSAL"], 55)

    def test_business_status_not_validated_when_enter_zero(self):
        report = build_phase8_gap_report(BASELINE, CURRENT)
        self.assertEqual(report["business_status"], "NOT_VALIDATED")

    def test_p2f_not_authorized_when_enter_or_trades_missing(self):
        report = build_phase8_gap_report(BASELINE, CURRENT)
        self.assertFalse(report["p2f_authorized"])

    def test_adds_improvement_when_sweep_reversal_increases(self):
        report = build_phase8_gap_report(BASELINE, CURRENT)
        self.assertTrue(any("SWEEP_REVERSAL" in item for item in report["improvements"]))

    def test_adds_residual_blocker_when_enter_eligible_stays_zero(self):
        report = build_phase8_gap_report(BASELINE, CURRENT)
        self.assertIn("enter_eligible_count remains 0.", report["residual_blockers"])

    def test_adds_next_priority_when_poi_section_blocker_is_present(self):
        report = build_phase8_gap_report(BASELINE, CURRENT)
        self.assertIn("Audit why SECTION_NOT_READY:poi blocks SWEEP_REVERSAL.", report["next_priorities"])

    def test_never_authorizes_p2f_without_trades(self):
        current = _summary(
            decision_distribution={"ENTER_REDUCED": 1},
            setup_type_distribution={"SWEEP_REVERSAL": 1},
            enter_eligible_count=1,
            risk_multiplier_positive=1,
            filled_trades=0,
            profit_factor=None,
        )
        report = build_phase8_gap_report(BASELINE, current)
        self.assertFalse(report["p2f_authorized"])


if __name__ == "__main__":
    unittest.main()
