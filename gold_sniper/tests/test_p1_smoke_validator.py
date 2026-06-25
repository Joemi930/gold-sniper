from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from gold_sniper.validation.p1_smoke_validator import (
    SmokeInputStatus,
    build_smoke_command,
    validate_smoke_inputs,
    run_smoke_validation,
)


class TestP1SmokeValidator(unittest.TestCase):
    def test_validate_inputs_detects_missing_folder(self):
        status = validate_smoke_inputs(
            data_root="/nonexistent/path/XAUUSD",
            news_calendar="/nonexistent/path/news.jsonl",
        )
        self.assertFalse(status.ok)
        self.assertTrue(status.missing_files)

    def test_validate_inputs_detects_empty_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1m").mkdir()
            (root / "15m").mkdir()
            (root / "4H").mkdir()
            news = root / "news.jsonl"
            news.write_text("", encoding="utf-8")
            status = validate_smoke_inputs(data_root=root, news_calendar=news)
            self.assertFalse(status.ok)
            self.assertTrue(any(m.endswith("/*.csv") for m in status.missing_files))

    def test_validate_inputs_ok_when_data_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tf in ("1m", "15m", "4H"):
                tf_dir = root / tf
                tf_dir.mkdir(parents=True)
                (tf_dir / "XAUUSD_test.csv").write_text("dummy", encoding="utf-8")
            news = root / "news.jsonl"
            news.write_text("{}", encoding="utf-8")
            status = validate_smoke_inputs(data_root=root, news_calendar=news)
            self.assertTrue(status.ok, msg=f"Expected ok but missing: {status.missing_files}")

    def test_build_command_includes_initial_equity(self):
        cmd = build_smoke_command(
            run_id="UNIT",
            start_utc="2026-01-01T00:00:00Z",
            end_utc="2026-01-03T00:00:00Z",
            data_root="/data",
            output_root="/output",
            news_calendar="/news.jsonl",
        )
        self.assertIn("--initial-equity", cmd)
        idx = cmd.index("--initial-equity")
        self.assertEqual(cmd[idx + 1], "100")

    def test_dry_run_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tf in ("1m", "15m", "4H"):
                tf_dir = root / tf
                tf_dir.mkdir(parents=True)
                (tf_dir / "XAUUSD_test.csv").write_text("dummy", encoding="utf-8")
            news = root / "news.jsonl"
            news.write_text("{}", encoding="utf-8")
            report = run_smoke_validation(
                data_root=root,
                news_calendar=news,
                output_root=root / "runs",
                report_output=root / "report.json",
                run_id="DRY",
                execute=False,
            )
            self.assertFalse((root / "runs" / "DRY" / "summary.json").exists())
            self.assertTrue(report.to_dict())


if __name__ == "__main__":
    unittest.main()
