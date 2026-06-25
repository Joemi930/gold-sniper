"""P2-B Validation News Required tests — NEWS_MISSING, EMPTY, DATA_COVERAGE checks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gold_sniper.validation.p1_validation_report import (
    P1SmokeValidationReport,
    ValidationFinding,
    build_validation_report,
)


def _write_fake_run(run_dir: Path, summary_override: dict | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    base_summary = {
        "run_id": "TEST_RUN",
        "period_start": "2026-06-01T00:00:00+00:00",
        "period_end": "2026-06-03T23:59:59+00:00",
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
        },
        "p1_replay": {
            "total_decisions": 5000,
            "decision_counts": {"REJECT": 3000, "WAIT": 2000},
            "readiness_state_distribution": {"REJECT": 3000, "WATCH_ONLY": 2000},
            "setup_grade_distribution": {"D": 4000, "C": 1000},
            "veto_breakdown": {"NONE": 5000},
            "blocked_stage_breakdown": {"NONE": 5000},
            "score_before_veto_avg": 25.0,
            "score_after_veto_avg": 20.0,
            "hard_veto_count": 0,
            "replay_invalid_count": 0,
            "evidence_validation_error_count": 0,
            "determinism_hash": "abc123def456",
            "data_quality": {
                "1m": {"candles": 1000, "monotonic": True, "start_utc": "2026-06-01T00:00:00Z", "end_utc": "2026-06-03T23:59:00Z", "checksum": "xyz", "gaps": 0},
                "5m": {"candles": 500, "monotonic": True, "start_utc": "2026-06-01T00:00:00Z", "end_utc": "2026-06-03T23:55:00Z", "checksum": "xyz5", "gaps": 0},
                "15m": {"candles": 200, "monotonic": True, "start_utc": "2026-06-01T00:00:00Z", "end_utc": "2026-06-03T23:45:00Z", "checksum": "xyz2", "gaps": 0},
                "1H": {"candles": 75, "monotonic": True, "start_utc": "2026-06-01T00:00:00Z", "end_utc": "2026-06-03T23:00:00Z", "checksum": "xyzh1", "gaps": 0},
                "4H": {"candles": 50, "monotonic": True, "start_utc": "2026-06-01T00:00:00Z", "end_utc": "2026-06-03T20:00:00Z", "checksum": "xyz3", "gaps": 0},
            },
        },
    }
    if summary_override:
        _deep_update(base_summary, summary_override)
    (run_dir / "summary.json").write_text(json.dumps(base_summary))
    # Write a minimal decisions.jsonl
    decisions = []
    for i in range(100):
        decisions.append({"decision": "REJECT", "timestamp": f"2026-06-01T00:{i:02d}:00Z"})
    (run_dir / "decisions.jsonl").write_text("\n".join(json.dumps(d) for d in decisions))


def _deep_update(base: dict, update: dict) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


class TestP2bValidationNewsRequired(unittest.TestCase):
    def test_news_calendar_missing_blocks_pass(self):
        """NEWS_CALENDAR_MISSING → FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_fake_run(run_dir, {"metadata": {"news_calendar_missing": True}})
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-01T00:00:00Z",
                window_end_utc="2026-06-03T23:59:59Z",
            )
            self.assertFalse(report.success)
            codes = [f.code for f in report.findings]
            self.assertIn("NEWS_CALENDAR_MISSING", codes)

    def test_news_calendar_empty_blocks_pass(self):
        """NEWS_CALENDAR_EMPTY → FAIL (file exists but no events)."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_fake_run(run_dir, {
                "metadata": {
                    "news_calendar_missing": False,
                    "news_calendar_empty": True,
                }
            })
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-01T00:00:00Z",
                window_end_utc="2026-06-03T23:59:59Z",
            )
            self.assertFalse(report.success)
            codes = [f.code for f in report.findings]
            self.assertIn("NEWS_CALENDAR_EMPTY", codes)

    def test_data_manifest_missing_blocks_pass(self):
        """DATA_MANIFEST_MISSING → FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_fake_run(run_dir, {
                "metadata": {
                    "news_calendar_missing": False,
                    "news_calendar_empty": False,
                    "data_manifest": None,
                    "data_manifest_status": None,
                }
            })
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-01T00:00:00Z",
                window_end_utc="2026-06-03T23:59:59Z",
            )
            self.assertFalse(report.success)
            codes = [f.code for f in report.findings]
            self.assertIn("DATA_MANIFEST_MISSING", codes)

    def test_data_coverage_partial_blocks_pass(self):
        """DATA_COVERAGE_PARTIAL → FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_fake_run(run_dir, {
                "metadata": {
                    "news_calendar_missing": False,
                    "news_calendar_empty": False,
                    "data_manifest": {"overall_status": "PARTIAL"},
                    "data_manifest_status": "PARTIAL",
                }
            })
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-01T00:00:00Z",
                window_end_utc="2026-06-03T23:59:59Z",
            )
            self.assertFalse(report.success)
            codes = [f.code for f in report.findings]
            self.assertIn("DATA_COVERAGE_PARTIAL", codes)

    def test_data_coverage_missing_blocks_pass(self):
        """DATA_COVERAGE_MISSING → FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_fake_run(run_dir, {
                "metadata": {
                    "news_calendar_missing": False,
                    "news_calendar_empty": False,
                    "data_manifest": {"overall_status": "MISSING"},
                    "data_manifest_status": "MISSING",
                }
            })
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-01T00:00:00Z",
                window_end_utc="2026-06-03T23:59:59Z",
            )
            self.assertFalse(report.success)
            codes = [f.code for f in report.findings]
            self.assertIn("DATA_COVERAGE_MISSING", codes)

    def test_healthy_with_news_and_data_passes(self):
        """Full healthy scenario with news OK and data coverage OK → PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_fake_run(run_dir)
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-01T00:00:00Z",
                window_end_utc="2026-06-03T23:59:59Z",
            )
            self.assertTrue(report.success)


if __name__ == "__main__":
    unittest.main()
