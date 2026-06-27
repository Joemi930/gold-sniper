"""P4.2 — CLI parser integration tests.

Verifies that --engine, --parity, --fast are accepted and that
legacy flags remain backward-compatible.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure gold_sniper is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class TestCLIParserP42(unittest.TestCase):
    """Tests for the ReplayApp CLI parser (Gold_Sniper_Replay.py)."""

    @classmethod
    def setUpClass(cls):
        from gold_sniper.replay_app.Gold_Sniper_Replay import _build_cli_parser
        cls.parser = _build_cli_parser()

    def _parse(self, *args: str):
        return self.parser.parse_args(list(args))

    # ── P4.2 new flags ────────────────────────────────────────────────

    def test_cli_accepts_engine_v2(self):
        args = self._parse("--no-menu", "--engine", "v2",
                           "--start", "2025-12-08", "--end", "2025-12-09")
        self.assertEqual(args.engine, "v2")

    def test_cli_accepts_engine_legacy_default(self):
        args = self._parse("--no-menu",
                           "--start", "2025-12-08", "--end", "2025-12-09")
        self.assertEqual(args.engine, "legacy")

    def test_cli_accepts_parity(self):
        args = self._parse("--no-menu", "--engine", "v2", "--parity",
                           "--start", "2025-12-08", "--end", "2025-12-09")
        self.assertTrue(args.parity)

    def test_cli_accepts_engine_v2_fast(self):
        args = self._parse("--no-menu", "--engine", "v2", "--fast",
                           "--start", "2025-12-08", "--end", "2025-12-15")
        self.assertEqual(args.engine, "v2")
        self.assertTrue(args.fast)

    def test_cli_accepts_engine_v2_parity_fast(self):
        """Full user command: --engine v2 --parity --fast"""
        args = self._parse(
            "--no-menu", "--engine", "v2", "--parity", "--fast",
            "--start", "2025-12-08", "--end", "2025-12-09",
            "--warmup-start", "2025-12-01",
            "--run-id", "parity_1d",
            "--initial-equity", "100",
        )
        self.assertEqual(args.engine, "v2")
        self.assertTrue(args.parity)
        self.assertTrue(args.fast)
        self.assertEqual(args.start, "2025-12-08")
        self.assertEqual(args.end, "2025-12-09")
        self.assertEqual(args.warmup_start, "2025-12-01")
        self.assertEqual(args.run_id, "parity_1d")
        self.assertAlmostEqual(args.initial_equity, 100.0)

    # ── legacy flags still work ───────────────────────────────────────

    def test_legacy_flags_still_work(self):
        """--fast-replay, --profile-replay, --minimal-events, --no-tui must still parse."""
        args = self._parse(
            "--no-menu",
            "--start", "2025-12-08", "--end", "2025-12-09",
            "--fast-replay", "--profile-replay", "--minimal-events", "--no-tui",
        )
        self.assertTrue(args.fast_replay)
        self.assertTrue(args.profile_replay)
        self.assertTrue(args.minimal_events)
        self.assertTrue(args.no_tui)

    def test_fast_is_alias_for_fast_replay(self):
        """--fast should set the fast flag (maps to fast_replay in legacy)."""
        args = self._parse(
            "--no-menu", "--fast",
            "--start", "2025-12-08", "--end", "2025-12-09",
        )
        self.assertTrue(args.fast)

    def test_legacy_fast_replay_independent_of_fast(self):
        """--fast-replay and --fast are separate flags (both supported)."""
        args = self._parse(
            "--no-menu", "--fast-replay",
            "--start", "2025-12-08", "--end", "2025-12-09",
        )
        self.assertTrue(args.fast_replay)
        self.assertFalse(getattr(args, 'fast', False))

    # ── engine rejects invalid values ─────────────────────────────────

    def test_engine_rejects_invalid_value(self):
        with self.assertRaises(SystemExit):
            self._parse("--no-menu", "--engine", "v3",
                        "--start", "2025-12-08", "--end", "2025-12-09")

    # ── menu mode still default ───────────────────────────────────────

    def test_menu_is_default(self):
        """Without --no-menu, the parser should default to menu mode."""
        args = self._parse("--start", "2025-12-08", "--end", "2025-12-09")
        self.assertTrue(args.menu)
        self.assertFalse(args.no_menu)


class TestRunReplayParserP42(unittest.TestCase):
    """Tests for the legacy run_replay.py parser (must also accept P4.2 flags)."""

    @classmethod
    def setUpClass(cls):
        from gold_sniper.replay.run_replay import build_parser
        cls.parser = build_parser()

    def _parse(self, *args: str):
        return self.parser.parse_args(list(args))

    def test_accepts_engine_v2(self):
        args = self._parse("--engine", "v2", "--run-id", "test")
        self.assertEqual(args.engine, "v2")

    def test_accepts_parity(self):
        args = self._parse("--parity", "--run-id", "test")
        self.assertTrue(args.parity)

    def test_accepts_fast(self):
        args = self._parse("--fast", "--run-id", "test")
        self.assertTrue(args.fast)

    def test_legacy_flags_unchanged(self):
        args = self._parse(
            "--run-id", "test",
            "--start", "2025-12-08T00:00:00Z",
            "--end", "2025-12-09T00:00:00Z",
            "--fast-replay", "--profile-replay", "--minimal-events", "--no-tui",
        )
        self.assertTrue(args.fast_replay)
        self.assertTrue(args.profile_replay)
        self.assertTrue(args.minimal_events)
        self.assertTrue(args.no_tui)


if __name__ == "__main__":
    unittest.main()
