from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.blackboard import BlackBoard
from replay.decision_pipeline import ReplayDecisionPipeline
from replay.replay_engine import ReplayEngine
from replay.replay_metrics import _decision_hash


def _candle(i):
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    price = 2400.0 + i * 0.1
    return {
        "time": ts,
        "open": price,
        "high": price + 0.2,
        "low": price - 0.2,
        "close": price + 0.1,
        "volume": 1,
        "tick_volume": 1,
        "symbol": "XAUUSD",
    }


class TestP1ReplayDeterminism(unittest.TestCase):
    def test_same_decisions_same_hash(self):
        decisions = [
            {"decision": "ENTER_FULL", "setup_grade": "A_PLUS", "score_before_veto": 88.0},
            {"decision": "REJECT", "veto_code": "NEWS_HIGH_IMPACT_WINDOW"},
        ]
        self.assertEqual(_decision_hash(decisions), _decision_hash(decisions))

    def test_different_decision_different_hash(self):
        a = [{"decision": "ENTER_FULL"}]
        b = [{"decision": "ENTER_REDUCED"}]
        self.assertNotEqual(_decision_hash(a), _decision_hash(b))


class TestP1ReplayDeterminismMiniReplay(unittest.IsolatedAsyncioTestCase):
    async def test_same_mini_replay_same_decision_hash(self):
        candles = [_candle(i) for i in range(20)]

        async def run_once():
            with tempfile.TemporaryDirectory() as tmp:
                board = BlackBoard()
                pipeline = ReplayDecisionPipeline.from_agent_ids([])
                engine = ReplayEngine(
                    board,
                    candles,
                    output_root=Path(tmp),
                    run_id="DET",
                    on_decision_hook=pipeline,
                )
                await engine.run()
                return _decision_hash(engine._p1_decisions)

        first = await run_once()
        second = await run_once()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
