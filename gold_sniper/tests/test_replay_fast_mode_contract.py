"""P4: Fast mode contract tests — verify fast_replay doesn't change strategy behaviour."""
from __future__ import annotations

import unittest

from replay.replay_runtime_config import ReplayRuntimeConfig
from replay.buffered_jsonl_writer import BufferedJsonlWriter


class TestReplayFastModeContract(unittest.TestCase):
    """Fast mode must reduce I/O without changing trading decisions."""

    def test_fast_config_warmup_decision_pipeline_disabled(self):
        """Fast mode MUST NOT run decision pipeline during warmup."""
        cfg = ReplayRuntimeConfig.fast()
        self.assertFalse(cfg.warmup_decision_pipeline,
                         "fast mode must disable warmup decision pipeline")

    def test_fast_config_writes_are_minimal(self):
        cfg = ReplayRuntimeConfig.fast()
        self.assertTrue(cfg.minimal_events)
        self.assertFalse(cfg.write_decisions_jsonl)
        self.assertFalse(cfg.write_decision_snapshots)

    def test_normal_config_preserves_full_output(self):
        cfg = ReplayRuntimeConfig()
        self.assertTrue(cfg.write_decisions_jsonl)
        self.assertTrue(cfg.write_decision_snapshots)

    def test_buffered_writer_flushes_on_close(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"
            w = BufferedJsonlWriter(path, flush_every=100)
            for i in range(50):
                w.write({"n": i, "event": "test"})
            self.assertEqual(w.buffered_lines, 50)
            w.close()
            lines = path.read_text().strip().split("\n")
            self.assertEqual(len(lines), 50)

    def test_buffered_writer_auto_flushes(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"
            w = BufferedJsonlWriter(path, flush_every=10)
            for i in range(25):
                w.write({"n": i, "event": "test"})
            # Should have auto-flushed at 10 and 20
            lines_before = len(path.read_text().strip().split("\n")) if path.exists() else 0
            self.assertGreaterEqual(lines_before, 20)
            w.close()
            lines = path.read_text().strip().split("\n")
            self.assertEqual(len(lines), 25)

    def test_is_fast_keep_event_filters(self):
        """P4: fast mode must keep trade lifecycle events, drop decision snapshots."""
        from replay.replay_engine import ReplayEngine
        engine = ReplayEngine.__new__(ReplayEngine)
        engine.runtime_config = ReplayRuntimeConfig.fast()
        # Lifecycle events MUST be kept
        self.assertTrue(engine._is_fast_keep_event({"event": "open"}))
        self.assertTrue(engine._is_fast_keep_event({"event": "close"}))
        self.assertTrue(engine._is_fast_keep_event({"event": "leg_close"}))
        self.assertTrue(engine._is_fast_keep_event({"event": "rejected"}))
        self.assertTrue(engine._is_fast_keep_event({"event": "missed_entry"}))
        # Decision snapshots must be dropped
        self.assertFalse(engine._is_fast_keep_event({"event": "decision", "eval_active": True}))
        self.assertFalse(engine._is_fast_keep_event({"event": "p1_decision", "eval_active": True}))
        # Warmup markers always kept
        self.assertTrue(engine._is_fast_keep_event({"event": "warmup_start"}))
        self.assertTrue(engine._is_fast_keep_event({"event": "warmup_end"}))


if __name__ == "__main__":
    unittest.main()
