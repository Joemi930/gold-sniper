"""P2-E Fix 1 tests for Agent2 replay payload normalization."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.agent_2_cartographe import build_agent_2_observation
from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from gold_sniper.replay.replay_engine import ReplayEngine


class TestP2eReplayAgent2Payload(unittest.TestCase):
    def test_build_agent_2_observation_preserves_p2a_connectivity(self) -> None:
        selected = {
            "poi_type_normalized": "OB",
            "lifecycle_normalized": "FRESH",
            "price_bounds": {"low": 2400.0, "high": 2405.0},
            "score": 81.0,
            "mitigation_pct": 8.0,
            "aligned_with_context": True,
            "execution_readiness": "READY",
        }
        result = AgentResult(
            agent_id="agent_2",
            score=81.0,
            hard_filter_pass=True,
            direction="LONG",
            reason="OB_READY",
            payload={
                "p2a_poi_connectivity": {
                    "poi_candidates": [selected],
                    "selected_poi": selected,
                    "audit": {
                        "agent2_has_any_zone": True,
                        "agent2_has_selected_ob": True,
                        "selected_poi_present": True,
                        "poi_bounds_present": True,
                    },
                }
            },
        )

        p2a = result.payload["p2a_poi_connectivity"]
        self.assertIsInstance(p2a["selected_poi"], dict)
        self.assertIsInstance(p2a["poi_candidates"], list)
        self.assertIsInstance(p2a["selected_poi"]["price_bounds"], dict)
        self.assertEqual(p2a["selected_poi"]["execution_readiness"], "READY")

        obs = build_agent_2_observation(result)
        payload = obs.payload

        self.assertTrue(payload["poi_available"])
        self.assertTrue(payload["selected_poi_present"])
        self.assertIsInstance(payload["price_bounds"], dict)
        self.assertEqual(payload["execution_readiness"], "READY")
        self.assertTrue(payload["poi_semantic_available"])
        self.assertEqual(payload["poi_semantic_status"], "POI_PRESENT_EXECUTABLE")
        self.assertTrue(payload["p2a_connectivity_audit"]["agent2_has_any_zone"])

    def test_candidates_without_selected_are_partial_but_available(self) -> None:
        candidate = {
            "poi_type_normalized": "FVG",
            "lifecycle_normalized": "PARTIAL",
            "price_bounds": {"low": 2390.0, "high": 2394.0},
            "score": 61.0,
            "execution_readiness": "WAITING_TRIGGER",
        }
        result = AgentResult(
            agent_id="agent_2",
            score=61.0,
            hard_filter_pass=True,
            direction="SHORT",
            reason="FVG_CANDIDATE",
            payload={
                "p2a_poi_connectivity": {
                    "poi_candidates": [candidate],
                    "selected_poi": None,
                    "audit": {"agent2_has_any_zone": True},
                }
            },
        )

        payload = build_agent_2_observation(result).payload

        self.assertEqual(payload["status"], "PARTIAL")
        self.assertTrue(payload["poi_available"])
        self.assertFalse(payload["selected_poi_present"])
        self.assertEqual(payload["poi_type_normalized"], "FVG")
        self.assertEqual(payload["execution_readiness"], "WAITING_TRIGGER")
        self.assertTrue(payload["poi_semantic_available"])
        self.assertEqual(payload["poi_semantic_status"], "POI_PRESENT_WAITING_TRIGGER")

    def test_replay_engine_does_not_mix_builder_bars_with_external_timeframes(self) -> None:
        import asyncio
        from datetime import datetime, timedelta, timezone

        async def run_case() -> list[str]:
            start = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
            candles_1m = []
            for i in range(16):
                ts = start + timedelta(minutes=i)
                candles_1m.append({
                    "time": ts,
                    "open": 2400.0 + i,
                    "high": 2401.0 + i,
                    "low": 2399.0 + i,
                    "close": 2400.5 + i,
                    "tick_volume": 1,
                })
            external_15m = [
                {
                    "time": start,
                    "open": 2400.0,
                    "high": 2405.0,
                    "low": 2395.0,
                    "close": 2402.0,
                    "tick_volume": 10,
                },
                {
                    "time": start + timedelta(minutes=15),
                    "open": 2402.0,
                    "high": 2406.0,
                    "low": 2398.0,
                    "close": 2404.0,
                    "tick_volume": 10,
                },
            ]
            with TemporaryDirectory() as tmp:
                engine = ReplayEngine(
                    BlackBoard(),
                    candles_1m,
                    output_root=Path(tmp),
                    run_id="p2e_mtf_external_contract",
                    candles_by_timeframe={"15m": external_15m, "4H": []},
                )
                await engine._prepare_blackboard()
                for index, candle in enumerate(candles_1m):
                    await engine._inject_candle(candle, index)
                return [
                    item["time"].isoformat()
                    for item in engine.blackboard.read_sync("market_data.candles.15m")
                ]

        times = asyncio.run(run_case())

        self.assertEqual(times, ["2026-06-01T12:00:00+00:00", "2026-06-01T12:15:00+00:00"])
        self.assertNotIn("2026-06-01T12:14:00+00:00", times)


if __name__ == "__main__":
    unittest.main()
