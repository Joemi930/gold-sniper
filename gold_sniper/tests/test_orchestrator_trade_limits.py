"""Tests limite quotidienne + override exceptionnel orchestrateur."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from core.orchestrator import run_orchestrator
from core.strategy_dictionary import Strategy


def _result(agent_id: str, score: float, direction: str | None = "LONG") -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        score=score,
        hard_filter_pass=True,
        direction=direction,
        reason="TEST",
    )


class TestOrchestratorTradeLimits(unittest.IsolatedAsyncioTestCase):
    strategy = Strategy(
        name="TEST_LIMIT_STRATEGY",
        description="test",
        sessions=["LONDON"],
        regimes=["TRENDING"],
        min_score=80.0,
        exceptional_score=92.0,
        risk_pct=1.0,
        sl_atr_multiplier=1.0,
        tp1_rr=1.5,
        tp2_rr=2.0,
        tp3_enabled=False,
        require_sweep=False,
        require_fvg_in_ob=False,
        require_idm_swept=False,
        min_ob_score=0.0,
        weight_overrides=None,
        priority=1,
    )

    def _board(self, trades_today: int) -> BlackBoard:
        board = BlackBoard()
        board._data["market"]["session"] = "LONDON"
        board._data["market"]["regime"] = "TRENDING"
        board._data["agents"]["risk_manager"]["trades_today"] = trades_today
        board._data["orchestrator"]["adaptive_weights"] = {
            "agent_1": 0,
            "agent_2": 0,
            "agent_3": 0,
            "agent_4": 0,
            "agent_5": 1,
        }
        return board

    def _agent_results(self, final_score: float) -> list[AgentResult]:
        return [
            _result("agent_1", 65, "LONG"),
            _result("agent_2", 70, "LONG"),
            _result("agent_3", 70, "LONG"),
            _result("agent_4", 70, "LONG"),
            _result("agent_5", final_score, "LONG"),
            _result("agent_6", 100, None),
            AgentResult(
                agent_id="agent_7",
                score=100,
                hard_filter_pass=True,
                direction=None,
                reason="SESSION_OK",
                payload={"session_name": "LONDON", "trading_allowed": True},
            ),
        ]

    @patch("core.orchestrator.log_missed_opportunity", new_callable=AsyncMock)
    @patch("core.orchestrator.send_discord_notification", new_callable=AsyncMock)
    @patch("core.orchestrator.select_active_strategy", return_value=strategy)
    async def test_three_trades_score_88_rejects_and_logs_missed(
        self,
        _strategy,
        notify: AsyncMock,
        missed: AsyncMock,
    ) -> None:
        decision = await run_orchestrator(self._agent_results(88), self._board(3))

        self.assertEqual(decision["decision"], "DAILY_LIMIT_REACHED")
        self.assertEqual(decision["score"], 88.0)
        notify.assert_awaited_once()
        missed.assert_awaited_once()

    @patch("core.orchestrator.send_discord_notification", new_callable=AsyncMock)
    @patch("core.orchestrator.select_active_strategy", return_value=strategy)
    async def test_three_trades_score_94_allows_exceptional_override(
        self,
        _strategy,
        notify: AsyncMock,
    ) -> None:
        decision = await run_orchestrator(self._agent_results(94), self._board(3))

        self.assertEqual(decision["decision"], "EXCEPTIONAL_OVERRIDE")
        self.assertEqual(decision["score"], 94.0)
        notify.assert_awaited_once()

    @patch("core.orchestrator.send_discord_notification", new_callable=AsyncMock)
    @patch("core.orchestrator.select_active_strategy", return_value=strategy)
    async def test_four_trades_score_100_rejects_absolute_limit(
        self,
        _strategy,
        notify: AsyncMock,
    ) -> None:
        decision = await run_orchestrator(self._agent_results(100), self._board(4))

        self.assertEqual(decision["decision"], "DAILY_LIMIT_ABSOLUTE")
        self.assertEqual(decision["score"], 100.0)
        notify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
