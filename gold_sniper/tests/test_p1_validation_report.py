from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from gold_sniper.validation.p1_validation_report import build_validation_report


def _write_run(run_dir: Path, summary: dict, decisions: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run_dir / "decisions.jsonl").write_text(
        "\n".join(json.dumps(item) for item in decisions),
        encoding="utf-8",
    )


def _healthy_summary():
    return {
        "run_id": "UNIT",
        "metadata": {
            "news_calendar_missing": False,
            "news_calendar_empty": False,
            "news_calendar_errors": [],
            "data_manifest": {"overall_status": "COVERAGE_OK"},
            "data_manifest_status": "COVERAGE_OK",
            "execution_model": {
                "profile": {"avg_spread_points": 20.0},
                "slippage_points": 5.0,
                "fill_model": "conservative_intrabar",
                "validation_errors": [],
            },
            "execution_model_required": True,
            "p2c_faithful_simulation": True,
            "data_quality": {
                "1m": {"candles": 1000, "monotonic": True, "start_utc": "2026-06-07T00:00:00+00:00", "end_utc": "2026-06-17T00:00:00+00:00", "checksum": "abc"},
                "5m": {"candles": 500, "monotonic": True, "start_utc": "2026-06-07T00:00:00+00:00", "end_utc": "2026-06-17T00:00:00+00:00", "checksum": "abc5"},
                "15m": {"candles": 100, "monotonic": True, "start_utc": "2026-06-07T00:00:00+00:00", "end_utc": "2026-06-17T00:00:00+00:00", "checksum": "def"},
                "1H": {"candles": 50, "monotonic": True, "start_utc": "2026-06-07T00:00:00+00:00", "end_utc": "2026-06-17T00:00:00+00:00", "checksum": "def1h"},
                "4H": {"candles": 20, "monotonic": True, "start_utc": "2026-06-07T00:00:00+00:00", "end_utc": "2026-06-17T00:00:00+00:00", "checksum": "ghi"},
            },
        },
        "p1_replay": {
            "total_decisions": 200,
            "decision_counts": {"REJECT": 120, "WATCH_ONLY": 50, "WAIT_FOR_TRIGGER": 30},
            "readiness_state_distribution": {"REJECT": 120, "WATCH_ONLY": 50, "WAITING_TRIGGER": 30},
            "setup_grade_distribution": {"D": 120, "C": 50, "B": 30},
            "veto_breakdown": {"NONE": 80, "NEWS_HIGH_IMPACT_WINDOW": 20, "POI_MISSING": 100},
            "blocked_stage_breakdown": {"NONE": 80, "NEWS": 20, "POI": 100},
            "hard_veto_count": 20,
            "replay_invalid_count": 0,
            "evidence_validation_error_count": 0,
            "score_before_veto_avg": 42.5,
            "score_after_veto_avg": 35.2,
            "determinism_hash": "hash",
            "data_quality": {},
        },
    }


class TestP1ValidationReport(unittest.TestCase):
    def test_healthy_report_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "UNIT"
            _write_run(run_dir, _healthy_summary(), [{"decision": "REJECT"}] * 200)
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-07T00:00:00Z",
                window_end_utc="2026-06-17T00:00:00Z",
            )
            self.assertTrue(report.success)
            self.assertEqual(report.status, "PASS")

    def test_degenerate_distribution_fails(self):
        summary = _healthy_summary()
        summary["p1_replay"]["decision_counts"] = {"REJECT": 200}
        summary["p1_replay"]["readiness_state_distribution"] = {"REJECT": 200}
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "UNIT"
            _write_run(run_dir, summary, [{"decision": "REJECT"}] * 200)
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-07T00:00:00Z",
                window_end_utc="2026-06-17T00:00:00Z",
            )
            self.assertFalse(report.success)
            self.assertIn("SINGLE_DECISION_TYPE_ONLY", [f.code for f in report.findings])

    def test_missing_veto_breakdown_fails(self):
        summary = _healthy_summary()
        summary["p1_replay"]["veto_breakdown"] = {}
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "UNIT"
            _write_run(run_dir, summary, [{"decision": "REJECT"}] * 200)
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-07T00:00:00Z",
                window_end_utc="2026-06-17T00:00:00Z",
            )
            self.assertFalse(report.success)
            self.assertIn("VETO_BREAKDOWN_MISSING", [f.code for f in report.findings])

    def test_missing_news_calendar_fails(self):
        summary = _healthy_summary()
        summary["metadata"]["news_calendar_missing"] = True
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "UNIT"
            _write_run(run_dir, summary, [{"decision": "REJECT"}] * 200)
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-07T00:00:00Z",
                window_end_utc="2026-06-17T00:00:00Z",
            )
            self.assertFalse(report.success)
            self.assertIn("NEWS_CALENDAR_MISSING", [f.code for f in report.findings])


if __name__ == "__main__":
    unittest.main()
