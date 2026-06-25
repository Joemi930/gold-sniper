from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from replay.run_replay import main


CSV = (
    "time,open,high,low,close,tick_volume\n"
    "2026-04-01T01:00:00Z,100,101,99,100.5,10\n"
    "2026-04-01T01:01:00Z,100.5,102,100,101.5,12\n"
    "2026-04-01T01:02:00Z,101.5,103,101,102.5,11\n"
)


class TestReplayRunner(unittest.TestCase):
    def test_runner_smoke_creates_events_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "historical" / "XAUUSD"
            output = Path(tmp) / "runs"
            _write_replay_csvs(root)

            status = main([
                "--start",
                "2026-04-01",
                "--end",
                "2026-04-01",
                "--run-id",
                "smoke",
                "--data-root",
                str(root),
                "--output-root",
                str(output),
            ])

            summary_path = output / "smoke" / "summary.json"
            events_path = output / "smoke" / "events.jsonl"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            events = events_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(status, 0)
        self.assertEqual(summary["candles"], 3)
        self.assertTrue(events)
        self.assertIn("decision", {json.loads(line)["event"] for line in events})

    def test_runner_accepts_warmup_eval_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "historical" / "XAUUSD"
            output = Path(tmp) / "runs"
            _write_replay_csvs(root)

            status = main([
                "--warmup-start",
                "2026-04-01T01:00:00Z",
                "--eval-start",
                "2026-04-01T01:02:00Z",
                "--eval-end",
                "2026-04-01T01:02:00Z",
                "--run-id",
                "warmup",
                "--data-root",
                str(root),
                "--output-root",
                str(output),
            ])

            summary = json.loads((output / "warmup" / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertEqual(summary["total_candles_processed"], 3)
        self.assertEqual(summary["warmup_candles"], 2)
        self.assertEqual(summary["eval_candles"], 1)
        self.assertIn("global_summary", summary)
        self.assertIn("evaluation_summary", summary)

    def test_runner_accepts_agent_diagnostic_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "historical" / "XAUUSD"
            output = Path(tmp) / "runs"
            _write_replay_csvs(root)

            status = main([
                "--start",
                "2026-04-01",
                "--end",
                "2026-04-01",
                "--run-id",
                "diagnostic",
                "--data-root",
                str(root),
                "--output-root",
                str(output),
                "--diagnose-agent",
                "agent_2",
                "--diagnose-agent",
                "agent_5",
                "--diagnose-alignment",
                "poi_ote",
            ])

            summary = json.loads((output / "diagnostic" / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertEqual(summary["metadata"]["diagnose_agents"], ["agent_2", "agent_5"])
        self.assertEqual(summary["metadata"]["diagnose_alignment"], ["poi_ote"])

    def test_runner_accepts_tier_simulation_and_initial_equity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "historical" / "XAUUSD"
            output = Path(tmp) / "runs"
            _write_replay_csvs(root)

            status = main([
                "--start",
                "2026-04-01",
                "--end",
                "2026-04-01",
                "--run-id",
                "tier",
                "--data-root",
                str(root),
                "--output-root",
                str(output),
                "--tier-simulation",
                "--initial-equity",
                "100",
            ])

            summary = json.loads((output / "tier" / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertTrue(summary["tier_simulation"])
        self.assertEqual(summary["initial_equity"], 100.0)
        self.assertEqual(summary["metadata"]["initial_equity"], 100.0)
        self.assertIn("CANDIDATE_MICRO", summary["tier_summary"])

    def test_runner_without_agent6_does_not_read_invalid_news_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "historical" / "XAUUSD"
            output = Path(tmp) / "runs"
            news = Path(tmp) / "invalid_news.jsonl"
            _write_replay_csvs(root)
            news.write_text("{not-json", encoding="utf-8")

            status = main([
                "--start",
                "2026-04-01",
                "--end",
                "2026-04-01",
                "--run-id",
                "no_agent6",
                "--data-root",
                str(root),
                "--output-root",
                str(output),
                "--news-calendar",
                str(news),
                "--replay-agent", "agent_2",
                "--replay-agent", "agent_5",
            ])

            summary = json.loads((output / "no_agent6" / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertFalse(summary["metadata"]["news_calendar_requested"])
        self.assertFalse(summary["metadata"]["news_calendar_exists"])
        self.assertEqual(summary["metadata"]["loaded_news_events"], 0)

    def test_runner_with_agent6_loads_news_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "historical" / "XAUUSD"
            output = Path(tmp) / "runs"
            news = Path(tmp) / "news.jsonl"
            _write_replay_csvs(root)
            news.write_text(
                '{"time":"2026-04-01T01:01:00Z","name":"FOMC","impact":"HIGH","currency":"USD"}\n',
                encoding="utf-8",
            )

            status = main([
                "--start",
                "2026-04-01",
                "--end",
                "2026-04-01",
                "--run-id",
                "with_agent6",
                "--data-root",
                str(root),
                "--output-root",
                str(output),
                "--replay-agent",
                "agent_6",
                "--news-calendar",
                str(news),
            ])

            summary = json.loads((output / "with_agent6" / "summary.json").read_text(encoding="utf-8"))
            events = [
                json.loads(line)
                for line in (output / "with_agent6" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(status, 0)
        self.assertTrue(summary["metadata"]["news_calendar_requested"])
        self.assertTrue(summary["metadata"]["news_calendar_exists"])
        self.assertEqual(summary["metadata"]["loaded_news_events"], 1)
        self.assertTrue(any(event.get("agents", {}).get("agent_6") for event in events if event["event"] == "decision"))

    def test_runner_with_agent6_missing_news_calendar_fallback_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "historical" / "XAUUSD"
            output = Path(tmp) / "runs"
            _write_replay_csvs(root)

            status = main([
                "--start",
                "2026-04-01",
                "--end",
                "2026-04-01",
                "--run-id",
                "missing_agent6_news",
                "--data-root",
                str(root),
                "--output-root",
                str(output),
                "--replay-agent",
                "agent_6",
                "--news-calendar",
                str(Path(tmp) / "missing.jsonl"),
            ])

            summary = json.loads((output / "missing_agent6_news" / "summary.json").read_text(encoding="utf-8"))
            events = [
                json.loads(line)
                for line in (output / "missing_agent6_news" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(status, 0)
        self.assertTrue(summary["metadata"]["news_calendar_requested"])
        self.assertFalse(summary["metadata"]["news_calendar_exists"])
        self.assertEqual(summary["metadata"]["loaded_news_events"], 0)
        agent6_events = [
            event["agents"]["agent_6"]
            for event in events
            if event["event"] == "decision" and event.get("agents", {}).get("agent_6")
        ]
        self.assertTrue(agent6_events)
        self.assertTrue(all(not event["veto"] for event in agent6_events))
        self.assertTrue(all(event["reason"] == "NEWS_FEED_FALLBACK_CLEAR" for event in agent6_events))


def _write_replay_csvs(root: Path) -> None:
    for timeframe in ("1m", "15m", "4H"):
        folder = root / timeframe
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"XAUUSD_{timeframe}_2026-04-01_2026-06-05.csv"
        (folder / filename).write_text(CSV, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
