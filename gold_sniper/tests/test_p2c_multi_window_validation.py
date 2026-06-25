from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from gold_sniper.validation.p2c_multi_window_validation import (
    build_coverage_status,
    generate_rolling_windows,
    run_multi_window_validation,
)


class TestP2cMultiWindowValidation(unittest.TestCase):
    def test_generate_rolling_windows(self) -> None:
        windows = generate_rolling_windows(
            start="2026-01-01T00:00:00Z",
            end="2026-01-10T23:59:59Z",
            window_days=5,
            step_days=5,
        )

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].start, "2026-01-01T00:00:00Z")
        self.assertEqual(windows[0].end, "2026-01-05T23:59:59Z")
        self.assertEqual(windows[1].start, "2026-01-06T00:00:00Z")

    def test_coverage_status_reports_partial_for_missing_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            news = root / "missing.csv"

            status = build_coverage_status(
                data_root=root,
                news_calendar=news,
                start="2026-01-01T00:00:00Z",
                end="2026-01-02T00:00:00Z",
            )

        self.assertEqual(status["status"], "PARTIAL_DATA_COVERAGE")
        self.assertEqual(status["data_manifest_status"], "MISSING")
        self.assertTrue(status["news_calendar_missing"])

    def test_multi_window_stops_on_partial_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "summary.json"
            args = argparse.Namespace(
                data_root=root / "historical",
                news_calendar=root / "news.csv",
                start="2026-01-01T00:00:00Z",
                end="2026-01-31T23:59:59Z",
                window_days=30,
                step_days=30,
                custom_windows=None,
                coverage_check_only=False,
                allow_partial_window=False,
                output=output,
                output_root=root / "runs",
            )

            payload = run_multi_window_validation(args)

            self.assertEqual(payload["status"], "PARTIAL_DATA_COVERAGE")
            self.assertEqual(payload["windows"], [])
            self.assertTrue(output.exists())
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["diagnostic"], "DATA_COVERAGE_PARTIAL")

    def test_coverage_check_only_writes_report_without_running_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "summary.json"
            args = argparse.Namespace(
                data_root=root / "historical",
                news_calendar=root / "news.csv",
                start="2026-01-01T00:00:00Z",
                end="2026-01-31T23:59:59Z",
                window_days=30,
                step_days=30,
                custom_windows=None,
                coverage_check_only=True,
                allow_partial_window=False,
                output=output,
                output_root=root / "runs",
            )

            payload = run_multi_window_validation(args)

            self.assertTrue(payload["coverage_check_only"])
            self.assertEqual(payload["windows"], [])
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
