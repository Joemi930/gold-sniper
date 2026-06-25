import json
import tempfile
import unittest
from pathlib import Path

from gold_sniper.tools.diagnose_near_enter import diagnose_near_miss, main, near_miss_score


def _decision(**overrides):
    row = {
        "timestamp": "2026-06-01T00:00:00Z",
        "bar_index": 1,
        "decision": "WATCH_ONLY",
        "setup_type": "POI_REACTION",
        "setup_grade": "C",
        "readiness_state": "WAITING_TRIGGER",
        "enter_eligible": False,
        "enter_eligibility_reason": "GLOBAL_READINESS_NOT_READY",
        "enter_eligibility_blockers": ["GLOBAL_READINESS_NOT_READY"],
        "risk_reason": "ENTER_NOT_ELIGIBLE",
        "setup_signal_inventory": {
            "poi_present": True,
            "trend_aligned_poi": True,
            "counter_trend_poi": False,
            "micro_waiting": True,
            "micro_partial": False,
            "liquidity_waiting": False,
            "sweep_detected": False,
            "in_ote": False,
            "timing_ready": True,
            "present_signals": ["POI_PRESENT", "TREND_ALIGNED_POI", "MICRO_WAITING"],
            "missing_core": [],
        },
        "setup_candidates": [
            {
                "candidate_type": "CONTINUATION_LIGHT",
                "confidence": 0.6,
                "reason": "SYNTHETIC",
                "present": ["POI_PRESENT"],
                "missing": ["MICRO_READY"],
            }
        ],
        "best_setup_candidate": {
            "candidate_type": "CONTINUATION_LIGHT",
            "confidence": 0.6,
            "missing": ["MICRO_READY"],
        },
        "near_miss_present_signals": ["POI_PRESENT", "TREND_ALIGNED_POI", "MICRO_WAITING"],
        "near_miss_missing_signals": ["MICRO_READY"],
    }
    row.update(overrides)
    return row


class TestNearMissTool(unittest.TestCase):
    def test_reads_decisions_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            decisions = Path(tmp) / "decisions.jsonl"
            output = Path(tmp) / "near.json"
            decisions.write_text(json.dumps(_decision()) + "\n", encoding="utf-8")
            rc = main(["--decisions", str(decisions), "--top", "50", "--output", str(output)])
            self.assertEqual(rc, 0)
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["total_decisions_scanned"], 1)

    def test_selects_top_50(self):
        rows = [_decision(bar_index=index) for index in range(60)]
        payload = diagnose_near_miss(rows, top=50)
        self.assertEqual(payload["near_miss_count"], 50)
        self.assertEqual(payload["near_miss_selected_top_count"], 50)

    def test_score_prioritizes_wait_poi_reaction_grade_c(self):
        high = near_miss_score(_decision())
        low = near_miss_score(_decision(decision="REJECT", setup_type="NO_SETUP", setup_grade="D"))
        self.assertGreater(high, low)

    def test_produces_candidate_type_distribution(self):
        payload = diagnose_near_miss([_decision()], top=50)
        self.assertEqual(payload["candidate_type_distribution"]["CONTINUATION_LIGHT"], 1)

    def test_produces_main_missing_signal_distribution(self):
        payload = diagnose_near_miss([_decision()], top=50)
        self.assertEqual(payload["main_missing_signal_distribution"]["MICRO_READY"], 1)

    def test_does_not_write_full_decisions(self):
        payload = diagnose_near_miss([_decision(extra_large="x" * 1000)], top=50)
        self.assertNotIn("extra_large", json.dumps(payload))

    def test_near_miss_scanned_count_counts_all_filtered_near_misses(self):
        payload = diagnose_near_miss(
            [
                _decision(bar_index=1, setup_candidates=[]),
                _decision(bar_index=2, setup_type="SWEEP_REVERSAL"),
                _decision(bar_index=3, decision="REJECT"),
            ],
            top=1,
        )
        self.assertEqual(payload["near_miss_scanned_count"], 2)
        self.assertEqual(payload["near_miss_selected_top_count"], 1)

    def test_near_miss_with_candidates_count_only_counts_candidate_rows(self):
        payload = diagnose_near_miss(
            [
                _decision(bar_index=1, setup_candidates=[]),
                _decision(bar_index=2, setup_type="SWEEP_REVERSAL"),
            ],
            top=50,
        )
        self.assertEqual(payload["near_miss_scanned_count"], 2)
        self.assertEqual(payload["near_miss_with_candidates_count"], 1)

    def test_by_current_setup_type_can_exceed_candidate_count(self):
        payload = diagnose_near_miss(
            [
                _decision(bar_index=1, setup_candidates=[]),
                _decision(bar_index=2, setup_candidates=[]),
                _decision(bar_index=3, setup_type="SWEEP_REVERSAL"),
            ],
            top=50,
        )
        scanned_by_type = sum(payload["near_miss_by_current_setup_type"].values())
        self.assertGreater(scanned_by_type, payload["near_miss_with_candidates_count"])

    def test_legacy_names_remain_compatible(self):
        payload = diagnose_near_miss([_decision()], top=50)
        self.assertEqual(payload["near_miss_count"], payload["near_miss_selected_top_count"])
        self.assertEqual(payload["top"], payload["top_overall"])
        self.assertEqual(
            payload["candidate_type_distribution"],
            payload["near_miss_candidate_type_distribution"],
        )

    def test_top_by_setup_type_includes_sweep_reversal(self):
        payload = diagnose_near_miss(
            [
                _decision(bar_index=1),
                _decision(bar_index=2, setup_type="SWEEP_REVERSAL"),
            ],
            top=50,
        )
        self.assertIn("SWEEP_REVERSAL", payload["top_by_setup_type"])
        self.assertEqual(len(payload["top_by_setup_type"]["SWEEP_REVERSAL"]), 1)


if __name__ == "__main__":
    unittest.main()
