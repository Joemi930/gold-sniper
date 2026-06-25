from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core.blackboard import BlackBoard
from replay.decision_pipeline import ReplayDecisionPipeline


class TestP1DecisionPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_returns_p1_decision_without_trade_signal(self):
        board = BlackBoard()
        pipeline = ReplayDecisionPipeline.from_agent_ids([])
        candle = {
            "time": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
            "symbol": "XAUUSD",
        }
        result = await pipeline(candle, board)
        self.assertIn("p1_evidence_bundle", result)
        self.assertIn("decision", result)
        self.assertIn("score_before_veto", result)
        self.assertNotIn("signal", result)
        self.assertNotIn("trade_signal", result)
        self.assertEqual(result["orchestrator"]["enabled"], False)


if __name__ == "__main__":
    unittest.main()
