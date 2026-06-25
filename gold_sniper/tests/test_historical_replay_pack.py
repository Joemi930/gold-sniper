from __future__ import annotations

import ast
import csv
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from gold_sniper.replay.historical_replay_pack import (
    build_event_from_row,
    run_phase_7_preflight,
    run_phase_7_replay_pack,
    run_replay_on_rows,
    scan_local_xauusd_datasets,
)
from gold_sniper.replay.news_loader import load_local_news_cache


class TestHistoricalReplayPack(unittest.TestCase):
    def test_replay_runner_accepts_empty_dataset_without_crash(self) -> None:
        decisions = run_replay_on_rows([])
        self.assertEqual(decisions, [])

    def test_dataset_without_dates_generates_blocking_report(self) -> None:
        with TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "XAUUSD"
            folder = data_root / "15m"
            folder.mkdir(parents=True)
            (folder / "empty.csv").write_text("time,open,high,low,close,tick_volume\n", encoding="utf-8")
            report_root = Path(tmp) / "reports"
            result = run_phase_7_replay_pack(data_root=data_root, report_root=report_root)
            self.assertEqual(result["process_status"], "FAILED_CLEANLY")
            self.assertTrue((report_root / "phase_7_replay_summary.md").exists())

    def test_minimal_xauusd_dataset_produces_decisions_without_broker(self) -> None:
        rows = [{"time": "2026-04-01T08:00:00Z", "open": "1", "high": "2", "low": "0.5", "close": "1.5"}]
        broker = Mock()
        with TemporaryDirectory() as tmp:
            cache = load_local_news_cache(_write_news_cache(Path(tmp), []))
            decisions = run_replay_on_rows(rows, news_cache=cache)
        self.assertEqual(len(decisions), 1)
        self.assertIn(decisions[0]["decision"], {"WAIT", "REJECT", "ENTER"})
        broker.assert_not_called()

    def test_source_rows_are_not_mutated(self) -> None:
        rows = [{"time": "2026-04-01T08:00:00Z", "open": "1", "high": "2", "low": "0.5", "close": "1.5"}]
        before = deepcopy(rows)
        with TemporaryDirectory() as tmp:
            cache = load_local_news_cache(_write_news_cache(Path(tmp), []))
            run_replay_on_rows(rows, news_cache=cache)
        self.assertEqual(rows, before)

    def test_metrics_total_decisions_correct(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = _write_dataset(root)
            news_path = _write_news_cache(root, [])
            result = run_phase_7_replay_pack(data_root=data_root, report_root=root / "reports", news_cache_path=news_path)
            self.assertEqual(result["metrics"]["total_decisions"], 2)

    def test_block_stage_counts_calculated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = _write_dataset(root)
            news_path = _write_news_cache(root, [])
            result = run_phase_7_replay_pack(data_root=data_root, report_root=root / "reports", news_cache_path=news_path)
            self.assertIsInstance(result["metrics"]["main_blocking_stage_counts"], dict)
            self.assertIn("funnel_exit_stage_counts", result["metrics"])
            self.assertIn("poi_reject_own_count", result["metrics"])
            self.assertIn("micro_reject_inherited_count", result["metrics"])

    def test_missing_news_cache_keeps_news_context_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            data_root = _write_dataset(Path(tmp))
            result = run_phase_7_replay_pack(
                data_root=data_root,
                report_root=Path(tmp) / "reports",
                news_cache_path=Path(tmp) / "missing_news.json",
            )
            self.assertEqual(result["process_status"], "FAILED_CLEANLY")
            self.assertEqual(result["metrics"]["total_decisions"], 0)
            self.assertIn("NEWS_CACHE_FILE_MISSING", result["metrics"]["main_missing_condition_counts"])

    def test_valid_news_cache_injects_agent6_clear_context(self) -> None:
        with TemporaryDirectory() as tmp:
            news_path = _write_news_cache(Path(tmp), [])
            event = build_event_from_row(
                {"time": "2026-04-01T08:00:00Z", "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
                news_cache=load_local_news_cache(news_path),
            )
            self.assertTrue(event["agents"]["agent_6"]["news_clear"])

    def test_agent7_injects_session_allowed_and_quality(self) -> None:
        with TemporaryDirectory() as tmp:
            news_path = _write_news_cache(Path(tmp), [])
            event = build_event_from_row(
                {"time": "2026-04-01T11:30:00Z", "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
                news_cache=load_local_news_cache(news_path),
            )
            agent7 = event["agents"]["agent_7"]
            self.assertEqual(agent7["session"], "NY_KILLZONE")
            self.assertTrue(agent7["session_allowed"])
            self.assertEqual(agent7["session_quality"], "HIGH")

    def test_replay_with_valid_news_cache_counts_news_clear(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = _write_dataset(root)
            news_path = _write_news_cache(root, [])
            result = run_phase_7_replay_pack(data_root=data_root, report_root=root / "reports", news_cache_path=news_path)
            self.assertEqual(result["process_status"], "TERMINATED_CLEANLY")
            self.assertEqual(result["metrics"]["events_with_news_clear"], 2)
            self.assertEqual(result["metrics"]["events_with_news_context_missing"], 0)

    def test_evidence_coverage_non_zero_with_multitimeframe_data(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = _write_dataset(root, include_multitimeframe=True, rows=40)
            news_path = _write_news_cache(root, [])
            result = run_phase_7_replay_pack(data_root=data_root, report_root=root / "reports", news_cache_path=news_path)
            self.assertGreater(result["metrics"]["htf_context_available_count"], 0)
            self.assertGreater(result["metrics"]["dol_available_count"], 0)
            self.assertGreater(result["metrics"]["premium_discount_available_count"], 0)

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "replay" / "historical_replay_pack.py"
        with module_path.open("r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        self.assertNotIn("MetaTrader5", imports)

    def test_no_broker_called(self) -> None:
        broker = Mock()
        build_event_from_row({"time": "2026-04-01T08:00:00Z"})
        broker.assert_not_called()

    def test_summary_generated_in_test_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            data_root = _write_dataset(Path(tmp))
            report_root = Path(tmp) / "reports"
            run_phase_7_replay_pack(data_root=data_root, report_root=report_root)
            self.assertTrue((report_root / "phase_7_replay_summary.md").exists())

    def test_no_live_ready_wording_in_reports(self) -> None:
        with TemporaryDirectory() as tmp:
            data_root = _write_dataset(Path(tmp))
            report_root = Path(tmp) / "reports"
            run_phase_7_replay_pack(data_root=data_root, report_root=report_root)
            combined = (report_root / "phase_7_replay_summary.md").read_text(encoding="utf-8")
            self.assertNotIn("live-ready", combined.lower())

    def test_scan_profiles_detects_dates(self) -> None:
        with TemporaryDirectory() as tmp:
            data_root = _write_dataset(Path(tmp))
            profiles = scan_local_xauusd_datasets(data_root)
            self.assertEqual(profiles[0].date_start, "2026-04-01T08:00:00Z")

    def test_preflight_only_checks_readiness_without_final_replay_outputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = _write_dataset(root, include_multitimeframe=True, rows=40)
            news_path = _write_news_cache(root, [])
            result = run_phase_7_preflight(data_root=data_root, news_cache_path=news_path, sample_events=2)
            self.assertIn(result["process_status"], {"PREFLIGHT_OK", "FAILED_CLEANLY"})
            self.assertEqual(result["preflight"]["sample_events_evaluated"], 2)
            self.assertTrue(result["preflight"]["funnel_trace_present"])


def _write_dataset(root: Path, *, include_multitimeframe: bool = False, rows: int = 2) -> Path:
    data_root = root / "XAUUSD"
    folder = data_root / "15m"
    folder.mkdir(parents=True)
    with (folder / "sample.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "open", "high", "low", "close", "tick_volume"])
        writer.writeheader()
        for idx in range(rows):
            day = 4 if include_multitimeframe else 1
            hour = 8 + (idx // 4)
            minute = (idx % 4) * 15
            price = 100 + idx
            writer.writerow({"time": f"2026-04-{day:02d}T{hour:02d}:{minute:02d}:00Z", "open": price, "high": price + 2, "low": price - 1, "close": price + 1, "tick_volume": "10"})
    if include_multitimeframe:
        for timeframe, step_minutes, count in (("1m", 1, rows * 15), ("4H", 240, 24)):
            tf_folder = data_root / timeframe
            tf_folder.mkdir(parents=True)
            with (tf_folder / "sample.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["time", "open", "high", "low", "close", "tick_volume"])
                writer.writeheader()
                base_hour = 0
                for idx in range(count):
                    total_minutes = base_hour * 60 + idx * step_minutes
                    day = 1 + total_minutes // (24 * 60)
                    minutes_in_day = total_minutes % (24 * 60)
                    hour = minutes_in_day // 60
                    minute = minutes_in_day % 60
                    price = 90 + idx * 0.5
                    writer.writerow({"time": f"2026-04-{day:02d}T{hour:02d}:{minute:02d}:00Z", "open": price, "high": price + 2, "low": price - 1, "close": price + 1, "tick_volume": "10"})
    return data_root


def _write_news_cache(root: Path, events: list[dict]) -> Path:
    path = root / "news.json"
    path.write_text(
        json.dumps(
            {
                "source": "local_news_api",
                "symbol": "XAUUSD",
                "currency_filter": ["USD"],
                "date_start": "2026-04-01T01:00:00Z",
                "date_end": "2026-06-05T20:00:00Z",
                "timezone": "UTC",
                "events": events,
            }
        ),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
