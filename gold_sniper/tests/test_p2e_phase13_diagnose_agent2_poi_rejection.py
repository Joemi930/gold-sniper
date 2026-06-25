import json
import tempfile
import unittest
from pathlib import Path

from gold_sniper.tools.diagnose_agent2_poi_rejection import (
    diagnose_agent2_poi_rejection,
    main,
)


def _rows():
    return [
        {
            "timestamp": "2026-06-04T10:41:00+00:00",
            "setup_type": "POI_REACTION",
            "setup_grade": "C",
            "decision": "WATCH_ONLY",
            "poi_rejection_code": "POI_UNCLASSIFIED_LEGACY_REJECTED",
            "poi_rejection_severity": "RECOVERABLE",
            "poi_rejection_recoverable": True,
            "poi_rejection_fatal": False,
            "micro_contract_status": "MICRO_CONFIRMED",
            "micro_confirmed": True,
            "micro_inside_poi": True,
            "poi_micro_synergy": True,
        },
        {
            "timestamp": "2026-06-04T10:42:00+00:00",
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "D",
            "decision": "REJECT",
            "poi_rejection_code": "POI_DIRECTION_MISMATCH",
            "poi_rejection_severity": "FATAL",
            "poi_rejection_recoverable": False,
            "poi_rejection_fatal": True,
            "micro_contract_status": "MICRO_OUTSIDE_POI",
            "poi_micro_synergy": False,
        },
    ]


class TestP2EPhase13DiagnoseAgent2POIRejection(unittest.TestCase):
    def test_produces_code_distribution(self):
        report = diagnose_agent2_poi_rejection(_rows())
        self.assertEqual(report["poi_rejection_code_distribution"]["POI_UNCLASSIFIED_LEGACY_REJECTED"], 1)

    def test_produces_severity_distribution(self):
        report = diagnose_agent2_poi_rejection(_rows())
        self.assertEqual(report["poi_rejection_severity_distribution"]["RECOVERABLE"], 1)
        self.assertEqual(report["poi_rejection_severity_distribution"]["FATAL"], 1)

    def test_lists_micro_confirmed_rejected_cases(self):
        report = diagnose_agent2_poi_rejection(_rows())
        self.assertEqual(report["micro_confirmed_rejected_count"], 1)
        self.assertEqual(len(report["micro_confirmed_rejected_cases"]), 1)

    def test_lists_sweep_rejected_cases(self):
        report = diagnose_agent2_poi_rejection(_rows())
        self.assertEqual(report["sweep_rejected_count"], 1)

    def test_does_not_mutate_decisions(self):
        rows = _rows()
        original = [dict(item) for item in rows]
        diagnose_agent2_poi_rejection(rows)
        self.assertEqual(rows, original)

    def test_cli_reads_decisions_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "decisions.jsonl"
            output = root / "audit.json"
            decisions.write_text("\n".join(json.dumps(row) for row in _rows()), encoding="utf-8")
            code = main(["--decisions", str(decisions), "--output", str(output), "--top", "5"])
            self.assertEqual(code, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["total_decisions_scanned"], 2)


if __name__ == "__main__":
    unittest.main()
