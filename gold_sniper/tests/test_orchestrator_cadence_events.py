"""Tests de cadence orchestrateur: dashboard pulse vs cloture bougie."""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard


class TestOrchestratorCadenceEvents(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_only_result_does_not_trigger_agent_update(self) -> None:
        bb = BlackBoard()
        result = AgentResult(
            agent_id="agent_5",
            score=0,
            hard_filter_pass=True,
            direction=None,
            reason="IDLE_SLEEPING",
        )

        await bb.write_agent_result("agent_5", result, trigger_orchestrator=False)

        self.assertTrue(bb.dashboard_update_event.is_set())
        self.assertFalse(bb._agent_update_event.is_set())
        self.assertIsNone(
            await bb.wait_for_agent_update(last_sequence=0, timeout=0.01)
        )

    async def test_real_result_triggers_agent_update_sequence(self) -> None:
        bb = BlackBoard()
        result = AgentResult(
            agent_id="agent_1",
            score=80,
            hard_filter_pass=True,
            direction="LONG",
            reason="CALCULATED",
        )

        await bb.write_agent_result("agent_1", result, trigger_orchestrator=True)
        update = await bb.wait_for_agent_update(last_sequence=0, timeout=0.01)

        self.assertIsNotNone(update)
        self.assertEqual(update["sequence"], 1)
        self.assertEqual(update["agent_id"], "agent_1")

    async def test_candle_close_event_is_1m_only(self) -> None:
        bb = BlackBoard()
        candle = {
            "time": datetime(2026, 5, 31, 8, 15, tzinfo=timezone.utc),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
        }

        await bb.notify_candle_close("15m", candle)
        self.assertFalse(bb.candle_close_event.is_set())

        await bb.notify_candle_close("1m", candle)
        update = await asyncio.wait_for(bb.wait_for_candle_close(), timeout=0.01)

        self.assertEqual(update["trigger"], "candle_close")
        self.assertEqual(update["sequence"], 1)
        self.assertEqual(update["timeframe"], "1m")

    async def test_risk_manager_veto_triggers_critical_path(self) -> None:
        bb = BlackBoard()

        await bb.update_agent("risk_manager", {"veto": True, "reason": "DD_LIMIT"})
        update = await asyncio.wait_for(
            bb.wait_for_critical_orchestrator_trigger(),
            timeout=0.01,
        )

        self.assertEqual(update["trigger"], "critical_veto")
        self.assertEqual(update["source"], "risk_manager")
        self.assertEqual(update["reason"], "DD_LIMIT")


if __name__ == "__main__":
    unittest.main()
