"""Tests publish_agent_dashboard throttle."""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard


class TestAgentDashboardPulse(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bb = BlackBoard()
        self.bb.write_agent_result = AsyncMock()  # type: ignore[method-assign]

    async def test_immediate_publish_when_no_throttle(self) -> None:
        result = AgentResult(
            agent_id="agent_1",
            score=0,
            hard_filter_pass=True,
            direction=None,
            reason="IDLE",
        )
        ok1 = await self.bb.publish_agent_dashboard("agent_1", result, min_interval_sec=0)
        ok2 = await self.bb.publish_agent_dashboard("agent_1", result, min_interval_sec=0)
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertEqual(self.bb.write_agent_result.await_count, 2)

    async def test_throttle_blocks_rapid_republish(self) -> None:
        result = AgentResult(
            agent_id="agent_5",
            score=0,
            hard_filter_pass=True,
            direction=None,
            reason="IDLE_SLEEPING",
        )
        ok1 = await self.bb.publish_agent_dashboard("agent_5", result, min_interval_sec=10.0)
        ok2 = await self.bb.publish_agent_dashboard("agent_5", result, min_interval_sec=10.0)
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(self.bb.write_agent_result.await_count, 1)

    async def test_throttle_allows_after_interval(self) -> None:
        result = AgentResult(
            agent_id="agent_5",
            score=0,
            hard_filter_pass=True,
            direction=None,
            reason="IDLE_SLEEPING",
        )
        self.bb._agent_dashboard_last_publish["agent_5"] = time.monotonic() - 11.0
        ok = await self.bb.publish_agent_dashboard("agent_5", result, min_interval_sec=10.0)
        self.assertTrue(ok)
        self.assertEqual(self.bb.write_agent_result.await_count, 1)


if __name__ == "__main__":
    unittest.main()
