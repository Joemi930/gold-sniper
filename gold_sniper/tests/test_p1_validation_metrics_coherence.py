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
            "news_calendar_errors": [],
            "data_quality": {
                "1m": {"candles": 1000, "monotonic": True, "start_utc": "2026-06-07T00:00:00+00:00", "end_utc": "2026-06-17T00:00:00+00:00", "checksum": "abc"},
                "15m": {"candles": 100, "monotonic": True, "start_utc": "2026-06-07T00:00:00+00:00", "end_utc": "2026-06-17T00:00:00+00:00", "checksum": "def"},
                "4H": {"candles": 20, "monotonic": True, "start_utc": "2026-06-07T00:00:00+00:00", "end_utc": "2026-06-17T00:00:00+00:00", "checksum": "ghi"},
            },
        },
        "p1_replay": {
            "total_decisions": 200,
            "decision_counts": {"REJECT": 120, "WATCH_ONLY": 50, "WAIT_FOR_TRIGGER": 30},
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


class TestP1ValidationMetricsCoherence(unittest.TestCase):
    def test_decision_counts_sum_matches_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "UNIT"
            summary = _healthy_summary()
            _write_run(run_dir, summary, [{"decision": "REJECT"}] * 200)
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-07T00:00:00Z",
                window_end_utc="2026-06-17T00:00:00Z",
            )
            count_sum = sum(report.decision_counts.values())
            self.assertEqual(report.total_decisions, count_sum)

    def test_replay_invalid_rate_blocks_if_too_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "UNIT"
            summary = _healthy_summary()
            p1 = summary["p1_replay"]
            p1["replay_invalid_count"] = 100
            p1["total_decisions"] = 200
            p1["decision_counts"] = {"REJECT": 100, "REPLAY_INVALID": 100}
            _write_run(run_dir, summary, [{"decision": "REJECT"}] * 200)
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-07T00:00:00Z",
                window_end_utc="2026-06-17T00:00:00Z",
                max_replay_invalid_rate=0.02,
            )
            self.assertFalse(report.success)
            self.assertFalse(report.timezone_news_ok)
            self.assertIn("REPLAY_INVALID_RATE_TOO_HIGH", [f.code for f in report.findings])

    def test_score_opacity_blocked_when_both_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "UNIT"
            summary = _healthy_summary()
            p1 = summary["p1_replay"]
            p1["score_before_veto_avg"] = 0.0
            p1["score_after_veto_avg"] = 0.0
            _write_run(run_dir, summary, [{"decision": "REJECT"}] * 200)
            report = build_validation_report(
                run_dir=run_dir,
                window_start_utc="2026-06-07T00:00:00Z",
                window_end_utc="2026-06-17T00:00:00Z",
            )
            self.assertFalse(report.veto_score_transparency_ok)
            self.assertIn("SCORE_TRANSPARENCY_MISSING", [f.code for f in report.findings])


if __name__ == "__main__":
    unittest.main()
