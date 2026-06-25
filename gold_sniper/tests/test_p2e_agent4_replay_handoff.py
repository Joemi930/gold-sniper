"""Pre-Phase 5 replay tests for Agent4 P2-A OTE handoff."""

from __future__ import annotations

import unittest

from agents.base_agent import AgentResult
import agents.agent_1_meteo as meteo
from core.blackboard import BlackBoard
from gold_sniper.replay import decision_pipeline


def _candles(count: int) -> list[dict]:
    return [
        {
            "time": f"2026-05-27T0{idx % 10}:00:00Z",
            "open": 100.0 + idx,
            "high": 105.0 + idx,
            "low": 99.0 + idx,
            "close": 102.0 + idx,
        }
        for idx in range(count)
    ]


class TestP2eAgent4ReplayHandoff(unittest.IsolatedAsyncioTestCase):
    async def test_agent4_does_not_block_on_agent2_score_zero_when_p2a_poi_exists(self) -> None:
        board = BlackBoard()
        candles_15m = _candles(12)
        selected = {
            "id": "p2a-ob",
            "poi_type_normalized": "OB",
            "price_bounds": {"low": 100.0, "high": 105.0},
            "execution_readiness": "READY",
        }
        await board.update_dict("market_data.candles", {"15m": candles_15m})
        await board.update_dict("market_data", {"atr_14": 1.0, "current_tick": {"bid": 102.0, "ask": 102.0}})
        await board.write_agent_result(
            "agent_1",
            AgentResult("agent_1", 70, True, "LONG", "HTF_BIAS_LONG_ESTABLISHED"),
            trigger_orchestrator=False,
        )
        await board.write_agent_result(
            "agent_2",
            AgentResult(
                "agent_2",
                0.0,
                False,
                "LONG",
                "LEGACY_REJECT_WITH_POI",
                {
                    "p2a_poi_connectivity": {
                        "selected_poi": selected,
                        "poi_candidates": [selected],
                        "audit": {"agent2_has_any_zone": True},
                    }
                },
            ),
            trigger_orchestrator=False,
        )

        original_detect_swings = meteo.detect_swings
        meteo.detect_swings = lambda high, low, close, n, atr_14: {
            "swing_highs": [{"index": 8, "price": 120.0}],
            "swing_lows": [{"index": 2, "price": 100.0}],
        }
        try:
            result = await decision_pipeline.run_replay_agent_4(candles_15m[-1], board)
        finally:
            meteo.detect_swings = original_detect_swings

        self.assertNotEqual(result.reason, "WAITING_ON_AGENT2_FAIL")
        self.assertEqual(result.payload["agent4_poi_handoff"]["source"], "P2A_SELECTED_POI")
        self.assertTrue(result.payload["agent4_consumed_poi"]["present"])
        self.assertEqual(result.payload["ote_handoff_status"], "P2A_POI_CONSUMED")


if __name__ == "__main__":
    unittest.main()
