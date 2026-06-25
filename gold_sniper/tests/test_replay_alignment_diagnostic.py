from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from replay.alignment_diagnostic import (
    build_poi_ote_alignment_diagnostic,
    distance_to_zone,
    zone_overlap,
)


def candles(count: int) -> list[dict]:
    start = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return [
        {
            "time": start + timedelta(minutes=index),
            "open": 100.0,
            "high": 106.0,
            "low": 99.0,
            "close": 103.5,
            "tick_volume": 10,
        }
        for index in range(count)
    ]


class TestReplayAlignmentDiagnostic(unittest.IsolatedAsyncioTestCase):
    def test_distance_and_overlap_helpers(self) -> None:
        self.assertEqual(distance_to_zone(103.0, 100.0, 105.0), 0.0)
        self.assertEqual(distance_to_zone(108.0, 100.0, 105.0), 3.0)
        self.assertEqual(distance_to_zone(98.0, 100.0, 105.0), 2.0)
        self.assertEqual(zone_overlap(100.0, 105.0, 103.0, 108.0), (103.0, 105.0, 2.0))
        self.assertEqual(zone_overlap(100.0, 105.0, 110.0, 112.0), (None, None, 0.0))

    async def test_alignment_payload_is_json_safe(self) -> None:
        board = BlackBoard()
        poi_zone = {
            "type": "BEARISH",
            "top": 105.0,
            "bottom": 100.0,
            "entry_zone_top": 104.0,
            "entry_zone_bottom": 101.0,
            "fresh": True,
            "ob_score": 76.0,
            "age": 4,
        }
        levels = {
            "direction": "SHORT",
            "swing_low": 99.0,
            "swing_high": 110.0,
            "equilibrium": 104.5,
            "ote_low": 103.0,
            "ote_high": 106.0,
        }
        await board.update_dict(
            "market_data",
            {
                "current_tick": {"bid": 103.5, "ask": 103.5},
                "candles": {"1m": candles(20)},
                "atr_14": 4.0,
            },
        )
        await board.update_agent("agent_1", {"direction": "SHORT"})
        await board.update_agent("agent_2", {"poi_zone": poi_zone, "ob_score": 76.0})
        await board.update_agent(
            "agent_4",
            {
                "swing_used": {"low_price": 99.0, "high_price": 110.0},
                "ote_anchor_mode": "AGENT2_POI_ANCHORED",
                "agent4_swing_contains_agent2_zone": True,
                "ote_low": 103.0,
                "ote_high": 106.0,
                "equilibrium": 104.5,
                "in_ote": True,
            },
        )
        for result in (
            AgentResult("agent_1", 65, True, "SHORT", "HTF_BIAS_SHORT_ESTABLISHED"),
            AgentResult("agent_2", 76, True, "SHORT", "OB_5_FACTORS", {"poi_zone": poi_zone, "ob_score": 76.0}),
            AgentResult(
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
                },
            ),
            AgentResult("agent_5", 0, False, "SHORT", "PRICE_OUTSIDE_POI_OTE - CHoCH ignore"),
        ):
            await board.write_agent_result(result.agent_id, result, trigger_orchestrator=False)

        diagnostic = build_poi_ote_alignment_diagnostic(
            candle={"time": datetime(2026, 4, 22, tzinfo=timezone.utc), "close": 103.5},
            blackboard=board,
        )

        json.dumps(diagnostic)
        self.assertTrue(diagnostic["agent2"]["price_in_poi"])
        self.assertTrue(diagnostic["agent4"]["price_in_ote"])
        self.assertTrue(diagnostic["alignment"]["poi_overlaps_ote"])
        self.assertTrue(diagnostic["alignment"]["price_in_both"])
        self.assertEqual(diagnostic["agent4"]["ote_anchor_mode"], "AGENT2_POI_ANCHORED")
        self.assertEqual(diagnostic["alignment"]["suspected_issue"], "POI_OTE_ALIGNED_NO_TRIGGER")
        self.assertEqual(diagnostic["alignment"]["overlap_size_points"], 1.0)


if __name__ == "__main__":
    unittest.main()
