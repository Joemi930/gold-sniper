import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from gold_sniper.tools.diagnose_poi_micro_synergy import diagnose_poi_micro_synergy


def _decisions():
    return [
        {
            "timestamp": "2026-06-04T10:41:00Z",
            "decision": "WATCH_ONLY",
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "C",
            "poi_micro_synergy": True,
            "poi_micro_synergy_status": "SYNERGY_READY",
            "poi_micro_reason": "TEST",
            "micro_confirmed": True,
            "micro_inside_poi": True,
            "enter_eligibility_blockers": ["RISK_NOT_ALLOWED"],
        },
        {
            "timestamp": "2026-06-04T10:42:00Z",
            "decision": "WATCH_ONLY",
            "setup_type": "POI_REACTION",
            "setup_grade": "C",
            "poi_micro_synergy": False,
            "poi_micro_synergy_status": "POI_TOO_WEAK",
            "poi_micro_reason": "POI_REJECTED_FAILURE_CLASS",
            "micro_confirmed": True,
            "micro_inside_poi": False,
            "p1_evidence_bundle": {"poi": {}},
        },
    ]


class TestP2EPhase12DiagnosePOIMicroSynergy(unittest.TestCase):
    def test_reads_decisions_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in _decisions()), encoding="utf-8")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            report = diagnose_poi_micro_synergy(rows)
        self.assertEqual(report["total_decisions_scanned"], 2)

    def test_produces_poi_micro_synergy_count(self):
        report = diagnose_poi_micro_synergy(_decisions())
        self.assertEqual(report["poi_micro_synergy_count"], 1)

    def test_lists_micro_confirmed_but_no_synergy(self):
        report = diagnose_poi_micro_synergy(_decisions())
        self.assertEqual(len(report["micro_confirmed_but_no_synergy"]), 1)

    def test_lists_synergy_true_cases(self):
        report = diagnose_poi_micro_synergy(_decisions())
        self.assertEqual(len(report["synergy_true_cases"]), 1)

    def test_does_not_modify_decisions(self):
        rows = _decisions()
        before = deepcopy(rows)
        diagnose_poi_micro_synergy(rows)
        self.assertEqual(rows, before)


if __name__ == "__main__":
    unittest.main()
