from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gold_sniper.replay.execution_model import ReplayExecutionModel
from gold_sniper.validation.p1_validation_report import build_validation_report


def _summary() -> dict:
    return {
        "run_id": "P2D_UNIT",
        "metadata": {
            "news_calendar_missing": False,
            "news_calendar_empty": False,
            "news_calendar_errors": [],
            "data_manifest": {"overall_status": "COVERAGE_OK"},
            "data_manifest_status": "COVERAGE_OK",
            "execution_model": ReplayExecutionModel().to_dict(),
            "execution_model_required": True,
            "p2c_faithful_simulation": True,
        },
        "p1_replay": {
            "total_decisions": 400,
            "decision_counts": {"REJECT": 200, "WAIT_FOR_TRIGGER": 100, "WATCH_ONLY": 100},
            "setup_grade_distribution": {"D": 200, "C": 200},
            "veto_breakdown": {"NONE": 300, "NEWS": 100},
            "blocked_stage_breakdown": {"NONE": 300, "NEWS": 100},
            "score_before_veto_avg": 42.0,
            "score_after_veto_avg": 35.0,
            "hard_veto_count": 100,
            "replay_invalid_count": 0,
            "evidence_validation_error_count": 0,
            "determinism_hash": "hash",
            "readiness_state_distribution": {"REJECT": 200, "WAITING_TRIGGER": 100, "WATCH_ONLY": 100},
            "data_quality": {
                "1m": {"candles": 1000, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "a"},
                "5m": {"candles": 500, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "a5"},
                "15m": {"candles": 100, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "b"},
                "1H": {"candles": 50, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "b1h"},
                "4H": {"candles": 20, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "c"},
            },
        },
    }


def _codes(summary: dict) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (run_dir / "decisions.jsonl").write_text("", encoding="utf-8")
        report = build_validation_report(run_dir=run_dir, window_start_utc="s", window_end_utc="e")
        return [finding.code for finding in report.findings]


class TestP2dValidationReadinessRequired(unittest.TestCase):
    def test_missing_readiness_blocks(self):
        summary = _summary()
        summary["p1_replay"].pop("readiness_state_distribution")

        self.assertIn("READINESS_STATE_MISSING", _codes(summary))

    def test_degenerate_readiness_blocks(self):
        summary = _summary()
        summary["p1_replay"]["readiness_state_distribution"] = {"REJECT": 400}

        self.assertIn("READINESS_DISTRIBUTION_DEGENERATE", _codes(summary))

    def test_all_reject_blocks(self):
        summary = _summary()
        summary["p1_replay"]["decision_counts"] = {"REJECT": 400}

        self.assertIn("DECISION_STILL_ALL_REJECT", _codes(summary))

    def test_non_degenerate_has_no_p2d_findings(self):
        codes = _codes(_summary())

        self.assertNotIn("READINESS_STATE_MISSING", codes)
        self.assertNotIn("READINESS_DISTRIBUTION_DEGENERATE", codes)
        self.assertNotIn("DECISION_STILL_ALL_REJECT", codes)


if __name__ == "__main__":
    unittest.main()
