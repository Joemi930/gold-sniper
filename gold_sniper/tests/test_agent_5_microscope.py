from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from agents.base_agent import AgentResult
from agents.agent_5_microscope import _calculate_level_payload, analyze_amd_sequence, build_replay_agent_5_diagnostic
from config import BE_PLUS_RR, TP1_RR, TP2_RR
from core.blackboard import BlackBoard


def candles(count: int) -> list[dict]:
    start = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return [
        {
            "time": start + timedelta(minutes=index),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "tick_volume": 10,
        }
        for index in range(count)
    ]


class TestAgent5ExecutionLevels(unittest.TestCase):
    def test_long_uses_sweep_low_for_sl_and_one_two_r_targets(self) -> None:
        levels = _calculate_level_payload("LONG", 101.0, 1.0, sweep_price=99.5)

        self.assertIsNotNone(levels)
        assert levels is not None
        self.assertLess(levels["sl"], 99.5)
        self.assertEqual(levels["tp1"], levels["entry"] + levels["risk_points"] * TP1_RR)
        self.assertEqual(levels["tp2"], levels["entry"] + levels["risk_points"] * TP2_RR)
        self.assertEqual(levels["be_plus_rr"], BE_PLUS_RR)
        self.assertEqual(levels["sl_basis"], "SWEEP_STRUCTURE")

    def test_short_uses_sweep_high_for_sl_and_one_two_r_targets(self) -> None:
        levels = _calculate_level_payload("SHORT", 99.0, 1.0, sweep_price=101.5)

        self.assertIsNotNone(levels)
        assert levels is not None
        self.assertGreater(levels["sl"], 101.5)
        self.assertEqual(levels["tp1"], levels["entry"] - levels["risk_points"] * TP1_RR)
        self.assertEqual(levels["tp2"], levels["entry"] - levels["risk_points"] * TP2_RR)
        self.assertEqual(levels["sl_basis"], "SWEEP_STRUCTURE")

    def test_insufficient_data_rejects_without_fake_trade(self) -> None:
        result = analyze_amd_sequence([], "LONG", {"bottom": 99.0, "top": 102.0}, 1.0, in_ote=True)

        self.assertFalse(result.hard_filter_pass)
        self.assertNotIn("entry", result.payload)
        self.assertEqual(result.reason, "NOT_ENOUGH_1M_CANDLES")

    def test_atr_fallback_is_marked_when_sweep_missing(self) -> None:
        levels = _calculate_level_payload("LONG", 101.0, 1.25, sweep_price=None)

        self.assertIsNotNone(levels)
        assert levels is not None
        self.assertEqual(levels["sl_basis"], "SL_FALLBACK_ATR")


class TestAgent5ReplayDiagnostic(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostic_payload_is_json_safe(self) -> None:
        board = BlackBoard()
        await board.update_agent("agent_1", {"direction": "SHORT"})
        await board.update_agent(
            "agent_2",
            {
                "hard_filter_pass": True,
                "reason": "OB_5_FACTORS",
                "poi_zone": {
                    "type": "BEARISH",
                    "top": 105.0,
                    "bottom": 100.0,
                    "entry_zone_top": 104.0,
                    "entry_zone_bottom": 101.0,
                    "fresh": True,
                    "ob_score": 76.0,
                },
                "ob_score": 76.0,
            },
        )
        await board.update_agent(
            "agent_4",
            {
                "hard_filter_pass": True,
                "reason": "IN_CORRECT_ZONE_BUT_NOT_YET_IN_OTE - Attendre",
                "ote_low": 98.0,
                "ote_high": 99.5,
                "in_ote": False,
                "in_discount": False,
                "in_premium": True,
            },
        )
        await board.update_dict("market_data", {"current_tick": {"bid": 102.0, "ask": 102.0}, "candles": {"15m": candles(2)}})
        result = AgentResult(
            agent_id="agent_5",
            score=0,
            hard_filter_pass=False,
            direction="SHORT",
            reason="PRICE_OUTSIDE_POI_OTE - CHoCH ignore",
        )

        diagnostic = build_replay_agent_5_diagnostic(
            candle={"time": datetime(2026, 4, 22, tzinfo=timezone.utc)},
            blackboard=board,
            candles_1m=candles(20),
            result=result,
        )

        json.dumps(diagnostic)
        self.assertTrue(diagnostic["price_in_agent2_poi"])
        self.assertFalse(diagnostic["price_in_agent4_ote"])
        self.assertEqual(diagnostic["final_reason"], "PRICE_OUTSIDE_POI_OTE - CHoCH ignore")
        self.assertEqual(diagnostic["distance_to_poi_points"], 0.0)
        self.assertEqual(diagnostic["distance_to_ote_points"], 2.5)


if __name__ == "__main__":
    unittest.main()
