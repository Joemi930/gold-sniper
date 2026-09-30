from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from core.orchestrator import _direction_to_signal, _entry_price_for_direction, run_orchestrator
from core.unified_live_decision import grade_scaled_risk_pct
from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager


class TestLiveDemoDeploymentWiring(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._risk_scale = os.environ.get("GS_RISK_SCALE")

    def tearDown(self) -> None:
        if self._risk_scale is None:
            os.environ.pop("GS_RISK_SCALE", None)
        else:
            os.environ["GS_RISK_SCALE"] = self._risk_scale

    def test_unified_direction_normalizes_buy_sell_and_legacy_long_short(self) -> None:
        tick = {"bid": 1999.5, "ask": 2000.0}

        self.assertEqual(_direction_to_signal("BUY"), "BUY")
        self.assertEqual(_direction_to_signal("LONG"), "BUY")
        self.assertEqual(_entry_price_for_direction("BUY", tick), 2000.0)

        self.assertEqual(_direction_to_signal("SELL"), "SELL")
        self.assertEqual(_direction_to_signal("SHORT"), "SELL")
        self.assertEqual(_entry_price_for_direction("SELL", tick), 1999.5)

        self.assertIsNone(_direction_to_signal("NONE"))
        self.assertIsNone(_entry_price_for_direction("NONE", tick))

    def test_grade_risk_uses_gs_risk_scale(self) -> None:
        os.environ["GS_RISK_SCALE"] = "3"

        self.assertEqual(grade_scaled_risk_pct("A_PLUS"), 3.0)
        self.assertEqual(grade_scaled_risk_pct("A"), 2.25)
        self.assertEqual(grade_scaled_risk_pct("B"), 1.5)
        self.assertEqual(grade_scaled_risk_pct("D"), 0.0)

    async def test_unified_pipeline_exception_fails_closed(self) -> None:
        board = BlackBoard()
        agents = [
            AgentResult(f"agent_{idx}", 100.0, True, "LONG", "ok")
            for idx in range(1, 6)
        ]

        with patch("core.orchestrator.UNIFIED_PIPELINE", True), patch(
            "core.unified_live_decision.unified_live_decision",
            side_effect=RuntimeError("boom"),
        ):
            result = await run_orchestrator(agents, board)

        self.assertEqual(result["decision"], "REJECT")
        self.assertIn("UNIFIED_PIPELINE_ERROR_FAIL_CLOSED", result["reason"])

    async def test_replay_entry_guard_uses_shared_live_guards(self) -> None:
        os.environ["GS_RISK_SCALE"] = "3"
        board = BlackBoard()
        manager = SimulatedTradeManager(
            board,
            SimulatedTradeConfig(require_execution_model=False),
        )
        trade = {"type": "BUY", "rr_estimate": 3.99}

        with patch("config.MIN_RR", 4.0):
            reason = manager._entry_block_reason(trade, "2026-06-01T10:00:00+00:00")

        self.assertEqual(reason, "MIN_RR")
        self.assertEqual(manager._lg_by_reason["MIN_RR"], 1)


if __name__ == "__main__":
    unittest.main()
