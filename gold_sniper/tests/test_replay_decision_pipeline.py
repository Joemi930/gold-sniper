from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from unittest.mock import patch

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
import replay.decision_pipeline as decision_pipeline
from replay.decision_pipeline import ReplayDecisionPipeline
from replay.replay_engine import ReplayEngine


def candles(count: int) -> list[dict]:
    start = datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc)
    return [
        {
            "time": start + timedelta(minutes=index),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 10.0,
            "tick_volume": 10.0,
        }
        for index in range(count)
    ]


class TestReplayDecisionPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_replay_agent7_runs_one_shot_without_orchestrator_loop(self) -> None:
        board = BlackBoard()
        pipeline = ReplayDecisionPipeline.from_agent_ids(["agent_7"])
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(3), output_root=tmp, run_id="agent7", on_decision_hook=pipeline)
            await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        decisions = [event for event in events if event["event"] == "decision"]
        self.assertTrue(decisions)
        self.assertIn("agent_7", decisions[-1]["agents"])
        self.assertEqual(decisions[-1]["orchestrator"]["decision"], "DISABLED")

    async def test_replay_orchestrator_raises_value_error_on_p1_clean(self) -> None:
        with self.assertRaises(ValueError):
            ReplayDecisionPipeline(agent_runners=[], use_orchestrator=True)

    async def test_failed_agent_runner_is_journaled_and_pipeline_continues(self) -> None:
        board = BlackBoard()

        def failing_agent(candle, blackboard):
            del candle, blackboard
            raise RuntimeError("agent exploded")

        failing_agent.replay_agent_id = "agent_bad"

        async def passing_agent(candle, blackboard):
            del candle, blackboard
            return AgentResult(
                agent_id="agent_7",
                score=50,
                hard_filter_pass=True,
                direction=None,
                reason="REPLAY_OK",
            )

        pipeline = ReplayDecisionPipeline(
            agent_runners=[failing_agent, passing_agent],
            use_orchestrator=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(1), output_root=tmp, run_id="runner_error", on_decision_hook=pipeline)
            summary = await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        decision = [event for event in events if event["event"] == "decision"][-1]
        self.assertEqual(summary["errors"], [])
        self.assertIn("agent_7", decision["agents"])
        self.assertEqual(decision["agent_errors"]["agent_bad"], "agent exploded")
        self.assertEqual(decision["agents"]["agent_bad"]["error"], "agent exploded")

    async def test_agent2_diagnostic_flag_does_not_change_decision_fields(self) -> None:
        calls = []
        original = decision_pipeline.run_replay_agent_2

        async def fake_agent2(candle, blackboard, *, diagnose=False):
            del candle, blackboard
            calls.append(diagnose)
            payload = {"diagnostic": {"detected_ob_count": 1}} if diagnose else {}
            return AgentResult(
                agent_id="agent_2",
                score=0,
                hard_filter_pass=False,
                direction="SHORT",
                reason="ZONE_ALREADY_MITIGATED",
                payload=payload,
            )

        try:
            decision_pipeline.run_replay_agent_2 = fake_agent2
            plain = ReplayDecisionPipeline.from_agent_ids(["agent_2"], use_orchestrator=False)
            diagnosed = ReplayDecisionPipeline.from_agent_ids(
                ["agent_2"],
                use_orchestrator=False,
                diagnose_agents=["agent_2"],
            )

            plain_decision = await plain(candles(1)[0], BlackBoard())
            diagnosed_decision = await diagnosed(candles(1)[0], BlackBoard())
        finally:
            decision_pipeline.run_replay_agent_2 = original

        plain_agent = plain_decision["agents"]["agent_2"]
        diagnosed_agent = diagnosed_decision["agents"]["agent_2"]
        self.assertEqual(calls, [False, True])
        self.assertEqual(diagnosed_agent["score"], plain_agent["score"])
        self.assertEqual(diagnosed_agent["hard_filter_pass"], plain_agent["hard_filter_pass"])
        self.assertEqual(diagnosed_agent["reason"], plain_agent["reason"])
        self.assertIn("diagnostic", diagnosed_agent["payload"])

    async def test_agent5_diagnostic_flag_does_not_change_decision_fields(self) -> None:
        calls = []
        original = decision_pipeline.run_replay_agent_5

        async def fake_agent5(candle, blackboard, *, diagnose=False):
            del candle, blackboard
            calls.append(diagnose)
            payload = {"diagnostic": {"price_in_agent2_poi": False}} if diagnose else {}
            return AgentResult(
                agent_id="agent_5",
                score=0,
                hard_filter_pass=False,
                direction="SHORT",
                reason="PRICE_OUTSIDE_POI_OTE - CHoCH ignore",
                payload=payload,
            )

        try:
            decision_pipeline.run_replay_agent_5 = fake_agent5
            plain = ReplayDecisionPipeline.from_agent_ids(["agent_5"], use_orchestrator=False)
            diagnosed = ReplayDecisionPipeline.from_agent_ids(
                ["agent_5"],
                use_orchestrator=False,
                diagnose_agents=["agent_5"],
            )

            plain_decision = await plain(candles(1)[0], BlackBoard())
            diagnosed_decision = await diagnosed(candles(1)[0], BlackBoard())
        finally:
            decision_pipeline.run_replay_agent_5 = original

        plain_agent = plain_decision["agents"]["agent_5"]
        diagnosed_agent = diagnosed_decision["agents"]["agent_5"]
        self.assertEqual(calls, [False, True])
        self.assertEqual(diagnosed_agent["score"], plain_agent["score"])
        self.assertEqual(diagnosed_agent["hard_filter_pass"], plain_agent["hard_filter_pass"])
        self.assertEqual(diagnosed_agent["reason"], plain_agent["reason"])
        self.assertIn("diagnostic", diagnosed_agent["payload"])
        json.dumps(diagnosed_agent["payload"]["diagnostic"])

    async def test_alignment_diagnostic_flag_does_not_change_agent_decisions(self) -> None:
        poi_zone = {
            "type": "BEARISH",
            "top": 105.0,
            "bottom": 100.0,
            "entry_zone_top": 104.0,
            "entry_zone_bottom": 101.0,
            "fresh": True,
            "ob_score": 76.0,
        }
        levels = {
            "direction": "SHORT",
            "swing_low": 99.0,
            "swing_high": 110.0,
            "equilibrium": 104.5,
            "ote_low": 103.0,
            "ote_high": 106.0,
        }

        async def fake_agent1(candle, blackboard):
            del candle, blackboard
            return AgentResult("agent_1", 65, True, "SHORT", "HTF_BIAS_SHORT_ESTABLISHED")

        async def fake_agent2(candle, blackboard):
            del candle, blackboard
            return AgentResult("agent_2", 76, True, "SHORT", "OB_5_FACTORS", {"poi_zone": poi_zone, "ob_score": 76.0})

        async def fake_agent4(candle, blackboard):
            del candle, blackboard
            return AgentResult(
                "agent_4",
                80,
                True,
                "SHORT",
                "IN_OTE_PRECISION",
                {
                    "levels": levels,
                    "in_ote": True,
                    "ote_anchor_mode": "AGENT2_POI_ANCHORED",
                    "agent4_swing_contains_agent2_zone": True,
                    "swing_used": {"low_price": 99.0, "high_price": 110.0},
                },
            )

        async def fake_agent5(candle, blackboard):
            del candle, blackboard
            return AgentResult("agent_5", 0, False, "SHORT", "PRICE_OUTSIDE_POI_OTE - CHoCH ignore")

        runners = [fake_agent1, fake_agent2, fake_agent4, fake_agent5]
        for agent_id, runner in zip(("agent_1", "agent_2", "agent_4", "agent_5"), runners):
            runner.replay_agent_id = agent_id

        async def run_pipeline(alignment=False):
            board = BlackBoard()
            await board.update_dict(
                "market_data",
                {
                    "current_tick": {"bid": 103.5, "ask": 103.5},
                    "candles": {"1m": candles(20)},
                    "atr_14": 4.0,
                },
            )
            pipeline = ReplayDecisionPipeline(
                agent_runners=runners,
                use_orchestrator=False,
                alignment_diagnostics=["poi_ote"] if alignment else [],
            )
            return await pipeline({"time": candles(1)[0]["time"], "close": 103.5}, board)

        plain_decision = await run_pipeline(alignment=False)
        diagnosed_decision = await run_pipeline(alignment=True)

        self.assertIsNone(plain_decision["alignment_diagnostic"])
        self.assertIn("alignment", diagnosed_decision["alignment_diagnostic"])
        self.assertEqual(
            diagnosed_decision["alignment_diagnostic"]["agent4"]["ote_anchor_mode"],
            "AGENT2_POI_ANCHORED",
        )
        json.dumps(diagnosed_decision["alignment_diagnostic"])
        self.assertEqual(
            diagnosed_decision["agents"]["agent_5"]["reason"],
            plain_decision["agents"]["agent_5"]["reason"],
        )
        self.assertEqual(
            diagnosed_decision["agents"]["agent_5"]["hard_filter_pass"],
            plain_decision["agents"]["agent_5"]["hard_filter_pass"],
        )

    async def test_replay_agent4_publishes_agent2_anchor_mode(self) -> None:
        import agents.agent_1_meteo as meteo

        board = BlackBoard()
        candles_15m = candles(12)
        candles_15m[2].update({"low": 100.0, "high": 105.0})
        poi_zone = {
            "type": "BULLISH",
            "top": 105.0,
            "bottom": 100.0,
            "entry_zone_top": 104.0,
            "entry_zone_bottom": 100.0,
            "candle_index": 2,
            "fresh": True,
            "ob_score": 72.0,
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
            AgentResult("agent_2", 72, True, "LONG", "OB_5_FACTORS", {"poi_zone": poi_zone, "ob_score": 72.0}),
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

        self.assertEqual(result.payload["ote_anchor_mode"], "AGENT2_POI_ANCHORED")
        self.assertTrue(result.payload["agent4_swing_contains_agent2_zone"])
        agent4_state = board.get_agent("agent_4")
        self.assertEqual(agent4_state["ote_anchor_mode"], "AGENT2_POI_ANCHORED")
        self.assertEqual(agent4_state["swing_used"]["low_price"], 100.0)

    async def test_replay_agents_2_to_5_run_without_agent6(self) -> None:
        board = BlackBoard()
        pipeline = ReplayDecisionPipeline.from_agent_ids(["agent_2", "agent_3", "agent_4", "agent_5"])
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(3), output_root=tmp, run_id="agents_2345", on_decision_hook=pipeline)
            summary = await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        decision = [event for event in events if event["event"] == "decision"][-1]
        self.assertEqual(summary["errors"], [])
        self.assertIn("agent_2", decision["agents"])
        self.assertIn("agent_3", decision["agents"])
        self.assertIn("agent_4", decision["agents"])
        self.assertIn("agent_5", decision["agents"])
        self.assertNotIn("agent_6", decision["agents"])

    async def test_replay_agent6_uses_offline_calendar_and_vetoes_high_impact(self) -> None:
        board = BlackBoard()
        events = [
            {
                "time": "2026-04-01T08:00:00Z",
                "name": "FOMC Interest Rate Decision",
                "impact": "HIGH",
                "currency": "USD",
            }
        ]
        pipeline = ReplayDecisionPipeline.from_agent_ids(["agent_6"], news_events=events)
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(1), output_root=tmp, run_id="agent6", on_decision_hook=pipeline)
            summary = await engine.run()
            decision = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["event"] == "decision"
            ][-1]

        self.assertEqual(summary["errors"], [])
        self.assertTrue(decision["agents"]["agent_6"]["veto"])
        self.assertIn("NEWS_BLACKOUT_HIGH", decision["agents"]["agent_6"]["reason"])
        self.assertFalse(board.read_sync("risk_management.volatility_gate")["allow_trade"])

    async def test_replay_agent6_low_impact_or_outside_window_is_clear(self) -> None:
        board = BlackBoard()
        events = [
            {
                "time": "2026-04-01T01:01:00Z",
                "event": "Minor USD Auction",
                "impact": "LOW",
                "currency": "USD",
            },
            {
                "time": "2026-04-02T08:00:00Z",
                "event": "FOMC Interest Rate Decision",
                "impact": "HIGH",
                "currency": "USD",
            },
        ]
        pipeline = ReplayDecisionPipeline.from_agent_ids(["agent_6"], news_events=events)
        decision = await pipeline(candles(1)[0], board)

        agent6 = decision["agents"]["agent_6"]
        self.assertFalse(agent6["veto"])
        self.assertTrue(agent6["hard_filter_pass"])
        self.assertEqual(agent6["reason"], "NEWS_CLEAR")
        self.assertTrue(board.read_sync("risk_management.volatility_gate")["allow_trade"])

    async def test_replay_agent6_does_not_call_live_fetchers(self) -> None:
        board = BlackBoard()
        events = [
            {
                "time": "2026-04-01T08:00:00Z",
                "event": "FOMC Interest Rate Decision",
                "impact": "HIGH",
                "currency": "USD",
            }
        ]
        pipeline = ReplayDecisionPipeline.from_agent_ids(["agent_6"], news_events=events)
        with patch("agents.agent_6_sentinelle.AgentSentinelle.refresh_events", side_effect=AssertionError("network fetch called")):
            decision = await pipeline(candles(1)[0], board)

        self.assertTrue(decision["agents"]["agent_6"]["veto"])


if __name__ == "__main__":
    unittest.main()
