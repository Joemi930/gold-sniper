from __future__ import annotations

from datetime import datetime, timedelta, timezone
from copy import deepcopy
import unittest

from gold_sniper.replay.offline_evidence_builder import (
    OfflineEvidenceBuilder,
    build_agent1,
    build_agent2,
    build_agent3,
    build_agent4,
    build_agent5,
)
from gold_sniper.replay.offline_market_structure import Candle


def candles(count: int, start: float = 100.0, step: float = 1.0, tf_minutes: int = 15) -> list[Candle]:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    out = []
    for i in range(count):
        price = start + i * step
        out.append(Candle(base + timedelta(minutes=i * tf_minutes), (base + timedelta(minutes=i * tf_minutes)).isoformat().replace("+00:00", "Z"), price, price + 2, price - 1, price + 1))
    return out


class TestOfflineEvidenceBuilder(unittest.TestCase):
    def test_h4_bullish_creates_dol(self) -> None:
        agent1 = build_agent1(candles(20, tf_minutes=240), candles(20))
        self.assertTrue(agent1["htf_context_available"])
        self.assertTrue(agent1["dol_available"])

    def test_liquidity_sweep_detected(self) -> None:
        base = candles(20)
        base[-1] = Candle(base[-1].time, base[-1].raw_time, 105, max(x.high for x in base[-13:-1]) + 3, 100, max(x.high for x in base[-13:-1]) - 1)
        agent3 = build_agent3(base, {"dol_available": True, "draw_on_liquidity": "BUY_SIDE"})
        self.assertTrue(agent3["sweep_detected"])
        self.assertTrue(agent3["rejection_after_sweep"])

    def test_liquidity_run_detected(self) -> None:
        agent3 = build_agent3(candles(20), {"dol_available": True, "draw_on_liquidity": "BUY_SIDE"})
        self.assertTrue(agent3["liquidity_story_available"])

    def test_ob_detected_after_displacement(self) -> None:
        data = candles(20)
        data[-2] = Candle(data[-2].time, data[-2].raw_time, 120, 121, 116, 117)
        data[-1] = Candle(data[-1].time, data[-1].raw_time, 117, 132, 116, 130)
        agent2 = build_agent2(data, {"close": "130"}, {"dol_available": True}, {"liquidity_story_available": True})
        self.assertTrue(agent2["poi_available"])

    def test_premium_discount_and_ote_calculated(self) -> None:
        agent4 = build_agent4(candles(40), candles(20, tf_minutes=240), {"close": "125"}, {"bias": "BULLISH"})
        self.assertTrue(agent4["range_available"])
        self.assertTrue(agent4["ote_available"])

    def test_micro_choch_alone_does_not_mark_trigger(self) -> None:
        m1 = candles(20, tf_minutes=1)
        agent5 = build_agent5(m1, {"close": "120"}, {"poi_available": True, "poi_low": 80, "poi_high": 90, "poi_direction": "LONG"}, "REVERSAL")
        self.assertFalse(agent5["micro_trigger"])

    def test_micro_displacement_reclaim_retest_valid(self) -> None:
        m1 = candles(20, start=80, step=0.2, tf_minutes=1)
        m1[-1] = Candle(m1[-1].time, m1[-1].raw_time, 84, 88, 82, 87)
        agent5 = build_agent5(m1, {"close": "87"}, {"poi_available": True, "poi_low": 82, "poi_high": 88, "poi_direction": "LONG"}, "TREND_CONTINUATION")
        self.assertTrue(agent5["inside_poi"])

    def test_builder_does_not_mutate_row(self) -> None:
        row = {"time": "2026-04-01T01:00:00Z", "close": "101"}
        before = deepcopy(row)
        OfflineEvidenceBuilder([], [], []).build(row)
        self.assertEqual(row, before)


if __name__ == "__main__":
    unittest.main()
