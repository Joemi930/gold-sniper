import json
import tempfile
import unittest
from pathlib import Path

from gold_sniper.tools.diagnose_readiness_risk_gate import (
    diagnose_readiness_risk_gate,
    main,
)


class TestP2EPhase14DiagnoseReadinessRiskGate(unittest.TestCase):
    def test_cli_reads_decisions_jsonl_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            decisions = Path(tmp) / "decisions.jsonl"
            output = Path(tmp) / "audit.json"
            decisions.write_text(
                json.dumps({
                    "decision": "WATCH_ONLY",
                    "setup_type": "POI_REACTION",
                    "setup_grade": "C",
                    "poi_micro_synergy": True,
                    "enter_eligible": False,
                }) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(main(["--decisions", str(decisions), "--output", str(output), "--top", "5"]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["synergy_true_count"], 1)

    def test_report_produces_synergy_true_watch_only(self):
        report = diagnose_readiness_risk_gate([
            {"decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "poi_micro_synergy": True}
        ])
        self.assertEqual(report["synergy_true_watch_only_count"], 1)

    def test_report_produces_gate_distributions(self):
        report = diagnose_readiness_risk_gate([
            {"decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "poi_micro_synergy": True}
        ])
        self.assertIn("gate_primary_blocker_distribution", report)
        self.assertIn("gate_blocker_distribution", report)

    def test_report_lists_no_enter_cases(self):
        report = diagnose_readiness_risk_gate([
            {"decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "poi_micro_synergy": True, "enter_eligible": False}
        ])
        self.assertEqual(len(report["synergy_true_no_enter"]), 1)

    def test_diagnostic_is_read_only(self):
        decisions = [{"decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "poi_micro_synergy": True}]
        original = [dict(item) for item in decisions]
        diagnose_readiness_risk_gate(decisions)
        self.assertEqual(decisions, original)


if __name__ == "__main__":
    unittest.main()
