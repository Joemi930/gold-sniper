"""Tests P1.27 — ZoneLifecycle shadow mode classification."""
import unittest

from context.zone_lifecycle import (
    ZoneState,
    classify_zone_lifecycle,
    classify_zone_pool_shadow,
    zone_lifecycle_pool_summary,
)
from typing import get_args


class TestZoneState(unittest.TestCase):
    def test_all_states_defined(self):
        """Les 8 états exigés par les specs doivent être présents."""
        expected = {
            "FRESH", "WICK_TAGGED", "PARTIALLY_MITIGATED", "MITIGATED",
            "CONSUMED", "INVALIDATED", "STALE", "FLIPPED_BREAKER",
        }
        self.assertEqual(set(get_args(ZoneState)), expected)


def _make_zone(top: float = 2000.0, bottom: float = 1990.0,
               direction: str = "BULLISH", candle_index: int = 2,
               fresh: bool = True, age: int = 5) -> dict:
    return {
        "type": direction,
        "top": top,
        "bottom": bottom,
        "candle_index": candle_index,
        "fresh": fresh,
        "age": age,
        "score": 70.0,
        "ob_score": 70.0,
        "valid": True,
    }


def _make_candle(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


class TestClassifyZoneLifecycle(unittest.TestCase):

    def test_fresh_zone_no_touch(self):
        """Zone sans touch postérieure → FRESH."""
        zone = _make_zone(top=2000.0, bottom=1990.0, candle_index=2)
        # Bougies ne rentrent pas dans la zone
        candles = [
            _make_candle(2010, 2020, 2008, 2015),   # idx 0
            _make_candle(2008, 2018, 2005, 2010),   # idx 1
            _make_candle(1988, 1989, 1985, 1987),   # idx 2 — création OB
            _make_candle(2010, 2025, 2008, 2022),   # idx 3
            _make_candle(2020, 2030, 2018, 2028),   # idx 4
        ]
        lc = classify_zone_lifecycle(zone, candles)
        self.assertEqual(lc["state"], "FRESH")
        self.assertEqual(lc["touch_count"], 0)
        self.assertFalse(lc["mean_threshold_reached"])

    def test_wick_tagged(self):
        """Zone avec wick touch mais pénétration < 50% → WICK_TAGGED ou FRESH."""
        zone = _make_zone(top=2000.0, bottom=1990.0, candle_index=2)
        candles = [
            _make_candle(2010, 2020, 2008, 2015),   # 0
            _make_candle(2008, 2018, 2005, 2010),   # 1
            _make_candle(1988, 1989, 1985, 1987),   # 2 création
            _make_candle(2010, 2025, 2008, 2022),   # 3 skip
            # idx 4 : wick low=1997 touche la zone (bottom=1990), pénétration (2000-1997)/10=30%
            # close=2005 hors zone → pas de close inside
            _make_candle(2005, 2010, 1997, 2005),
        ]
        lc = classify_zone_lifecycle(zone, candles)
        self.assertIn(lc["state"], ("WICK_TAGGED", "FRESH"))
        self.assertGreaterEqual(lc["touch_count"], 1)


    def test_consumed_multiple_close_inside(self):
        """Zone avec 2+ closes inside → CONSUMED."""
        zone = _make_zone(top=2000.0, bottom=1990.0, candle_index=2)
        candles = [
            _make_candle(2010, 2020, 2008, 2015),   # 0
            _make_candle(2008, 2018, 2005, 2010),   # 1
            _make_candle(1988, 1989, 1985, 1987),   # 2 création
            _make_candle(2010, 2025, 2008, 2022),   # 3 skip
            _make_candle(2000, 2005, 1989, 1995),   # 4 close inside
            _make_candle(1998, 2002, 1989, 1996),   # 5 close inside
        ]
        lc = classify_zone_lifecycle(zone, candles)
        self.assertEqual(lc["state"], "CONSUMED")
        self.assertGreaterEqual(lc["close_inside_count"], 2)

    def test_invalidated(self):
        """Close BULLISH sous le bottom → INVALIDATED."""
        zone = _make_zone(top=2000.0, bottom=1990.0, direction="BULLISH", candle_index=2)
        candles = [
            _make_candle(2010, 2020, 2008, 2015),
            _make_candle(2008, 2018, 2005, 2010),
            _make_candle(1988, 1989, 1985, 1987),   # création
            _make_candle(2010, 2025, 2008, 2022),
            _make_candle(1988, 1991, 1980, 1982),   # close < bottom
        ]
        lc = classify_zone_lifecycle(zone, candles)
        self.assertIn(lc["state"], ("INVALIDATED", "FLIPPED_BREAKER"))
        self.assertIsNotNone(lc["invalidation_reason"])

    def test_lifecycle_json_safe(self):
        """Le résultat doit être JSON-sérialisable."""
        import json
        zone = _make_zone()
        candles = [_make_candle(2010, 2020, 2008, 2015)] * 10
        lc = classify_zone_lifecycle(zone, candles)
        json_str = json.dumps(lc)
        self.assertIsInstance(json_str, str)


class TestZonePoolShadow(unittest.TestCase):

    def test_empty_pool(self):
        """Pool vide → summary avec total=0."""
        result = zone_lifecycle_pool_summary([])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["killed_by_legacy_but_viable"], 0)

    def test_classify_pool(self):
        """classify_zone_pool_shadow traite sans exception."""
        obs = [_make_zone(candle_index=i, age=i * 2) for i in range(2, 5)]
        candles = [_make_candle(2010, 2020, 2008, 2015)] * 20
        lifecycles = classify_zone_pool_shadow(obs, candles, atr_14=10.0)
        self.assertEqual(len(lifecycles), 3)

    def test_summary_fields(self):
        """Le résumé contient les champs attendus."""
        obs = [_make_zone(candle_index=2, age=5)]
        candles = [_make_candle(2010, 2020, 2008, 2015)] * 10
        lifecycles = classify_zone_pool_shadow(obs, candles)
        summary = zone_lifecycle_pool_summary(lifecycles)
        self.assertIn("total", summary)
        self.assertIn("by_state", summary)
        self.assertIn("killed_by_legacy_but_viable", summary)
        self.assertIn("mean_touch_count", summary)


class TestAgent2DiagnosticIntegration(unittest.TestCase):
    """Vérifie que build_replay_agent_2_diagnostic transmet les champs shadow."""

    def test_shadow_fields_present_in_diagnostic(self):
        from unittest.mock import MagicMock
        import asyncio
        from agents.agent_2_cartographe import build_replay_agent_2_diagnostic

        bb = MagicMock()
        bb.read_sync.return_value = None

        zone = _make_zone(candle_index=2, age=5)
        candles = [_make_candle(2010, 2020, 2008, 2015)] * 10

        diag = build_replay_agent_2_diagnostic(
            candle={"time": "2026-05-20T10:00:00Z"},
            blackboard=bb,
            candles_15m=candles,
            candles_4h=candles,
            obs=[zone],
            fvgs=[],
            selected_ob=None,
            atr_14=10.0,
            direction="LONG",
            final_reason="TEST",
            hard_filter_pass=False,
            score=0.0,
        )

        # Les champs shadow doivent être présents
        self.assertIn("shadow_zone_lifecycle_summary", diag)
        self.assertIn("shadow_selected_zone_lifecycle", diag)
        # Les décisions ne sont PAS modifiées
        self.assertFalse(diag["hard_filter_pass"])
        self.assertEqual(diag["score"], 0.0)
        self.assertEqual(diag["direction"], "LONG")


if __name__ == "__main__":
    unittest.main()
