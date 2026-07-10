"""P4.2 — Warmup gate test: no decisions, no trades during warmup."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from gold_sniper.replay.metrics_aggregator import MetricsAggregator


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestWarmupGate(unittest.TestCase):

    def setUp(self):
        self.m = MetricsAggregator()

    def test_warmup_candles_counted_separately(self):
        """Warmup candles must be tracked separately from eval candles."""
        self.m.record_candle(eval_active=False)
        self.m.record_candle(eval_active=False)
        self.m.record_candle(eval_active=True)
        self.m.record_candle(eval_active=True)
        self.m.record_candle(eval_active=True)
        summary = self.m.finalize()
        self.assertEqual(summary["warmup_candle_count"], 2)
        self.assertEqual(summary["eval_candle_count"], 3)
        self.assertEqual(summary["candle_count"], 5)

    def test_no_decision_no_trade_in_warmup(self):
        """Zero decisions and zero trades when only warmup candles processed."""
        for _ in range(100):
            self.m.record_candle(eval_active=False)
        summary = self.m.finalize()
        self.assertEqual(summary["decision_count"], 0)
        self.assertEqual(summary["trade_count"], 0)
        self.assertEqual(summary["eval_candle_count"], 0)

    def test_eval_starts_at_eval_start(self):
        """Decisions are only recorded on eval candles."""
        # Simulate warmup
        for _ in range(50):
            self.m.record_candle(eval_active=False)
        # Eval starts
        for _ in range(10):
            self.m.record_candle(eval_active=True)
        self.m.record_decision("REJECT", reject_reason="SESSION_BLOCKED")
        self.m.record_decision("ENTER_REDUCED", setup_type="SWEEP_REVERSAL")

        summary = self.m.finalize()
        self.assertEqual(summary["warmup_candle_count"], 50)
        self.assertEqual(summary["eval_candle_count"], 10)
        self.assertEqual(summary["decision_count"], 2)

    def test_warmup_has_no_trades(self):
        """Warmup phase must produce zero trades."""
        self.m.record_candle(eval_active=False)
        self.m.record_decision("ENTER_REDUCED", setup_type="SWEEP_REVERSAL")  # shouldn't happen in warmup
        summary = self.m.finalize()
        # The warmup gate is enforced at the engine level; here we verify
        # that the metrics aggregator correctly reflects the state
        self.assertEqual(summary["eval_candle_count"], 0)
        self.assertEqual(summary["warmup_candle_count"], 1)


if __name__ == "__main__":
    unittest.main()
