from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from replay.execution_model import ReplayExecutionModel
from gold_sniper.validation.p1_validation_report import build_validation_report


def _valid_execution_model() -> dict:
    return ReplayExecutionModel().to_dict()


def _base_summary() -> dict:
    return {
        "run_id": "P2C_UNIT",
        "metadata": {
            "news_calendar_missing": False,
            "news_calendar_empty": False,
            "news_calendar_errors": [],
            "data_manifest": {"overall_status": "COVERAGE_OK"},
            "data_manifest_status": "COVERAGE_OK",
            "execution_model": _valid_execution_model(),
            "execution_model_required": True,
            "p2c_faithful_simulation": True,
        },
        "p1_replay": {
            "total_decisions": 500,
            "decision_counts": {"REJECT": 250, "WAIT_FOR_TRIGGER": 150, "WATCH_ONLY": 100},
            "readiness_state_distribution": {"REJECT": 250, "WAITING_TRIGGER": 150, "WATCH_ONLY": 100},
            "setup_grade_distribution": {"D": 250, "C": 150, "B": 100},
            "veto_breakdown": {"NONE": 300, "POI": 200},
            "blocked_stage_breakdown": {"NONE": 300, "POI": 200},
            "score_before_veto_avg": 42.0,
            "score_after_veto_avg": 37.0,
            "hard_veto_count": 0,
            "replay_invalid_count": 0,
            "evidence_validation_error_count": 0,
            "determinism_hash": "hash",
            "data_quality": {
                "1m": {"candles": 1000, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "a"},
                "5m": {"candles": 500, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "a5"},
                "15m": {"candles": 100, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "b"},
                "1H": {"candles": 50, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "b1h"},
                "4H": {"candles": 20, "monotonic": True, "start_utc": "s", "end_utc": "e", "checksum": "c"},
            },
        },
    }


def _write_run(run_dir: Path, summary: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run_dir / "decisions.jsonl").write_text("\n".join(json.dumps({"decision": "REJECT"}) for _ in range(500)), encoding="utf-8")


class TestP2cValidationExecutionRequired(unittest.TestCase):
    def _report_codes(self, summary: dict) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_run(run_dir, summary)
            report = build_validation_report(run_dir=run_dir, window_start_utc="s", window_end_utc="e")
            return [finding.code for finding in report.findings]

    def test_execution_model_absent_blocks_pass(self):
        summary = _base_summary()
        summary["metadata"].pop("execution_model")

        self.assertIn("EXECUTION_MODEL_MISSING", self._report_codes(summary))

    def test_zero_spread_blocks_pass(self):
        summary = _base_summary()
        summary["metadata"]["execution_model"]["profile"]["avg_spread_points"] = 0.0

        self.assertIn("ZERO_COST_EXECUTION_MODEL", self._report_codes(summary))

    def test_missing_fill_model_blocks_pass(self):
        summary = _base_summary()
        summary["metadata"]["execution_model"].pop("fill_model")

        self.assertIn("EXECUTION_MODEL_INVALID", self._report_codes(summary))

    def test_missing_faithful_flag_blocks_pass(self):
        summary = _base_summary()
        summary["metadata"].pop("p2c_faithful_simulation")

        self.assertIn("FAITHFUL_SIMULATION_MISSING", self._report_codes(summary))

    def test_valid_execution_model_can_pass(self):
        self.assertEqual([], self._report_codes(_base_summary()))


if __name__ == "__main__":
    unittest.main()
