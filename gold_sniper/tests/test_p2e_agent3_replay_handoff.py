"""Phase 4 replay tests for Agent3 P2-A liquidity handoff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from agents.base_agent import AgentResult
from gold_sniper.replay.decision_pipeline import run_replay_agent_3


class _FakeReplayBlackboard:
    def __init__(self, *, agent1_result: AgentResult, agent2_result: AgentResult) -> None:
        self.agent1_result = agent1_result
        self.agent2_result = agent2_result
        self.updated_agents: dict[str, dict] = {}
        self.updated_dicts: dict[str, dict] = {}
        self.candles_15m = _candles(40, minutes=15)
        self.candles_1m = _candles(180, minutes=1)

    def read_sync(self, path: str):
        if path == "agent_results.agent_1":
            return self.agent1_result
        if path == "agent_results.agent_2":
            return self.agent2_result
        if path == "market_data.candles.15m":
            return self.candles_15m
        if path == "market_data.candles.1m":
            return self.candles_1m
        if path == "market_data.atr_14":
            return 2.0
        raise KeyError(path)

    def get_agent(self, key: str) -> dict:
        return {}

    async def update_dict(self, path: str, payload: dict) -> None:
        self.updated_dicts[path] = payload

    async def update_agent(self, agent_id: str, payload: dict) -> None:
        self.updated_agents[agent_id] = payload


def _candles(count: int, *, minutes: int) -> list[dict]:
    start = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
    candles = []
    for idx in range(count):
        base = 2400.0 + (idx % 6) * 0.25
        candles.append({
            "time": start + timedelta(minutes=idx * minutes),
            "open": base,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + 0.15,
        })
    return candles


def _p2a_selected_poi() -> dict:
    return {
        "id": "poi-agent3",
        "poi_type_normalized": "OB",
        "price_bounds": {"low": 2398.0, "high": 2405.0},
        "execution_readiness": "READY",
    }


class TestP2eAgent3ReplayHandoff(unittest.IsolatedAsyncioTestCase):
    async def test_agent3_does_not_block_on_agent2_score_zero_when_p2a_poi_exists(self) -> None:
        agent1 = AgentResult(
            agent_id="agent_1",
            score=80.0,
            hard_filter_pass=True,
            direction="BUY",
            reason="CONTEXT_READY",
        )
        selected = _p2a_selected_poi()
        agent2 = AgentResult(
            agent_id="agent_2",
            score=0.0,
            hard_filter_pass=False,
            direction="BUY",
            reason="LEGACY_REJECT_WITH_POI",
            payload={
                "p2a_poi_connectivity": {
                    "selected_poi": selected,
                    "poi_candidates": [selected],
                    "audit": {"agent2_has_any_zone": True},
                }
            },
        )
        blackboard = _FakeReplayBlackboard(agent1_result=agent1, agent2_result=agent2)

        result = await run_replay_agent_3({"time": "2026-05-27T00:00:00Z"}, blackboard)

        self.assertNotEqual(result.reason, "WAITING_ON_AGENT2_FAIL")
        self.assertIn(result.payload.get("liquidity_handoff_status"), {"P2A_POI_CONSUMED"})
        self.assertEqual(result.payload["agent3_poi_handoff"]["source"], "P2A_SELECTED_POI")
        self.assertTrue(result.payload["agent3_consumed_poi"]["present"])
        self.assertIn(result.payload.get("readiness_state"), {"READY", "WAIT_FOR_TRIGGER", "REJECT", "WATCH_ONLY"})


if __name__ == "__main__":
    unittest.main()
