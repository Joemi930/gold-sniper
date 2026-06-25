import json
import unittest
from datetime import datetime, timedelta, timezone

from agents.agent_2_cartographe import (
    build_shadow_agent2_poi_stack,
    build_replay_agent_2_diagnostic,
    rank_order_blocks_fresh_first,
    score_agent_2,
    select_best_order_block,
    select_best_order_block_with_ote_confluence,
)
from core.blackboard import BlackBoard


def make_ob(score: float, *, fresh: bool, index: int = 0, valid: bool = True) -> dict:
    return {
        "type": "BULLISH",
        "top": 101.0,
        "bottom": 99.0,
        "entry_zone_top": 101.0,
        "entry_zone_bottom": 99.0,
        "ob_score": score,
        "score": score,
        "score_factors": {"freshness": 20.0 if fresh else 0.0},
        "fresh": fresh,
        "valid": valid,
        "candle_index": index,
        "age": 10 - index,
        "grade": "A",
    }


def candles(count: int) -> list[dict]:
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    return [
        {
            "time": start + timedelta(minutes=15 * index),
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.5,
            "tick_volume": 10,
        }
        for index in range(count)
    ]


def swings() -> dict:
    return {
        "swing_lows": [{"index": 1, "price": 90.0}],
        "swing_highs": [{"index": 8, "price": 120.0}],
    }


class TestAgent2OrderBlockSelection(unittest.TestCase):
    def test_fresh_ob_is_selected_before_higher_scored_mitigated_ob(self) -> None:
        mitigated = make_ob(90.0, fresh=False, index=1)
        fresh = make_ob(70.0, fresh=True, index=2)

        self.assertIs(select_best_order_block([mitigated, fresh]), fresh)

    def test_highest_scored_fresh_ob_is_selected(self) -> None:
        lower_fresh = make_ob(65.0, fresh=True, index=1)
        higher_fresh = make_ob(80.0, fresh=True, index=2)

        self.assertIs(select_best_order_block([lower_fresh, higher_fresh]), higher_fresh)

    def test_fresh_ob_is_not_excluded_by_three_higher_scored_mitigated_obs(self) -> None:
        mitigated_95 = make_ob(95.0, fresh=False, index=1)
        mitigated_90 = make_ob(90.0, fresh=False, index=2)
        mitigated_85 = make_ob(85.0, fresh=False, index=3)
        fresh_70 = make_ob(70.0, fresh=True, index=4)

        ranked_top3 = rank_order_blocks_fresh_first(
            [mitigated_95, mitigated_90, mitigated_85, fresh_70]
        )[:3]

        self.assertIn(fresh_70, ranked_top3)
        self.assertEqual(ranked_top3, [fresh_70, mitigated_95, mitigated_90])

    def test_only_mitigated_obs_still_reject_with_zone_already_mitigated(self) -> None:
        lower_mitigated = make_ob(65.0, fresh=False, index=1)
        higher_mitigated = make_ob(85.0, fresh=False, index=2)
        selected = select_best_order_block([lower_mitigated, higher_mitigated])

        result = score_agent_2(selected, None, 100.0, 1.0, BlackBoard())

        self.assertIs(selected, higher_mitigated)
        self.assertFalse(result.hard_filter_pass)
        self.assertEqual(result.reason, "ZONE_ALREADY_MITIGATED")

    def test_no_ob_selection_returns_none(self) -> None:
        self.assertIsNone(select_best_order_block([]))

        result = score_agent_2(None, None, 100.0, 1.0, BlackBoard())

        self.assertFalse(result.hard_filter_pass)
        self.assertEqual(result.reason, "NO_VALID_OB_SCORE_GE_60")

    def test_diagnostic_reports_fresh_first_selection_policy(self) -> None:
        mitigated = make_ob(90.0, fresh=False, index=1)
        fresh = make_ob(70.0, fresh=True, index=2)
        selected = select_best_order_block([mitigated, fresh])
        board = BlackBoard()

        diagnostic = build_replay_agent_2_diagnostic(
            candle={"time": datetime(2026, 4, 1, 12, tzinfo=timezone.utc)},
            blackboard=board,
            candles_15m=candles(5),
            candles_4h=candles(2),
            obs=[mitigated, fresh],
            fvgs=[],
            selected_ob=selected,
            atr_14=1.0,
            direction="LONG",
            final_reason="OB_5_FACTORS",
            hard_filter_pass=True,
            score=70.0,
        )

        json.dumps(diagnostic)
        self.assertEqual(diagnostic["selection_policy"], "fresh_first_score_desc")
        self.assertTrue(diagnostic["selected_ob_was_fresh"])
        self.assertEqual(diagnostic["best_raw_score_ob"]["score"], 90.0)
        self.assertEqual(diagnostic["best_fresh_ob"]["score"], 70.0)
        self.assertEqual(diagnostic["selected_zone"]["score"], 70.0)

    def test_ote_overlap_fresh_ob_selected_before_higher_scored_no_overlap_ob(self) -> None:
        no_overlap = make_ob(90.0, fresh=True, index=2)
        no_overlap.update({"top": 107.0, "bottom": 105.0, "entry_zone_top": 107.0, "entry_zone_bottom": 105.0})
        overlap = make_ob(75.0, fresh=True, index=4)
        overlap.update({"top": 101.0, "bottom": 99.0, "entry_zone_top": 101.0, "entry_zone_bottom": 99.0})

        selected = select_best_order_block_with_ote_confluence([no_overlap, overlap], candles(12), swings(), "LONG")

        self.assertEqual(selected["ob_score"], 75.0)
        meta = selected["ote_confluence_selection"]
        self.assertEqual(meta["selection_policy"], "fresh_ote_confluence_score_desc")
        self.assertTrue(meta["selected_ob_has_ote_overlap"])

    def test_best_scored_ote_overlap_ob_is_selected(self) -> None:
        overlap_75 = make_ob(75.0, fresh=True, index=4)
        overlap_80 = make_ob(80.0, fresh=True, index=5)

        selected = select_best_order_block_with_ote_confluence([overlap_75, overlap_80], candles(12), swings(), "LONG")

        self.assertEqual(selected["ob_score"], 80.0)
        self.assertTrue(selected["ote_confluence_selection"]["selected_ob_has_ote_overlap"])

    def test_no_ote_overlap_keeps_fresh_first_score_desc(self) -> None:
        lower = make_ob(75.0, fresh=True, index=4)
        higher = make_ob(90.0, fresh=True, index=5)
        for item in (lower, higher):
            item.update({"top": 107.0, "bottom": 105.0, "entry_zone_top": 107.0, "entry_zone_bottom": 105.0})

        selected = select_best_order_block_with_ote_confluence([lower, higher], candles(12), swings(), "LONG")

        self.assertEqual(selected["ob_score"], 90.0)
        self.assertEqual(selected["ote_confluence_selection"]["selection_policy"], "fresh_first_score_desc")
        self.assertFalse(selected["ote_confluence_selection"]["selected_ob_has_ote_overlap"])

    def test_no_fresh_ob_keeps_mitigated_selection_behavior(self) -> None:
        lower_mitigated = make_ob(70.0, fresh=False, index=4)
        higher_mitigated = make_ob(90.0, fresh=False, index=5)

        selected = select_best_order_block_with_ote_confluence([lower_mitigated, higher_mitigated], candles(12), swings(), "LONG")
        result = score_agent_2(selected, None, 100.0, 1.0, BlackBoard())

        self.assertEqual(selected["ob_score"], 90.0)
        self.assertEqual(result.reason, "ZONE_ALREADY_MITIGATED")

    def test_diagnostic_reports_ote_confluence_selection_policy(self) -> None:
        no_overlap = make_ob(90.0, fresh=True, index=2)
        no_overlap.update({"top": 107.0, "bottom": 105.0, "entry_zone_top": 107.0, "entry_zone_bottom": 105.0})
        overlap = make_ob(75.0, fresh=True, index=4)
        selected = select_best_order_block_with_ote_confluence([no_overlap, overlap], candles(12), swings(), "LONG")

        diagnostic = build_replay_agent_2_diagnostic(
            candle={"time": datetime(2026, 4, 1, 12, tzinfo=timezone.utc)},
            blackboard=BlackBoard(),
            candles_15m=candles(12),
            candles_4h=candles(2),
            obs=[no_overlap, overlap],
            fvgs=[],
            selected_ob=selected,
            atr_14=1.0,
            direction="LONG",
            final_reason="OB_5_FACTORS",
            hard_filter_pass=True,
            score=75.0,
        )

        json.dumps(diagnostic)
        self.assertEqual(diagnostic["selection_policy"], "fresh_ote_confluence_score_desc")
        self.assertTrue(diagnostic["ote_confluence_available"])
        self.assertTrue(diagnostic["selected_ob_has_ote_overlap"])

    def test_shadow_poi_stack_prefers_wick_tagged_ob_before_fvg(self) -> None:
        ob = make_ob(68.0, fresh=False, index=2)
        lifecycle = {
            "zone_id": "2",
            "state": "WICK_TAGGED",
            "touch_count": 1,
            "deepest_penetration_pct": 0.14,
            "mean_threshold_reached": False,
            "close_inside_count": 0,
            "reaction_displacement_score": 0.4,
        }
        fvg = {
            "type": "FVG_CONTINUATION_POI",
            "direction": "LONG",
            "high": 101.0,
            "low": 99.0,
            "score_shadow": 90,
            "state_shadow": "FRESH",
            "filled_pct": 0.0,
            "age_minutes": 15,
        }

        stack = build_shadow_agent2_poi_stack(
            [ob],
            [fvg],
            [lifecycle],
            direction="LONG",
            current_price=100.0,
            atr_14=2.0,
            agent1_score=80.0,
        )

        self.assertEqual(stack["best_shadow_poi_type"], "OB_CONTINUATION_WICK_TAGGED")
        self.assertEqual(stack["best_shadow_poi"]["human_zone_state_shadow"], "WICK_TAGGED")
        self.assertTrue(stack["best_shadow_poi"]["zone_still_contextually_usable"])

    def test_shadow_poi_stack_uses_fvg_only_when_no_usable_ob(self) -> None:
        ob = make_ob(85.0, fresh=False, index=2)
        lifecycle = {
            "zone_id": "2",
            "state": "CONSUMED",
            "touch_count": 3,
            "deepest_penetration_pct": 1.0,
            "mean_threshold_reached": True,
            "close_inside_count": 2,
            "reaction_displacement_score": 0.0,
        }
        fvg = {
            "direction": "LONG",
            "high": 101.0,
            "low": 99.0,
            "score_shadow": 72,
            "filled_pct": 0.0,
            "age_minutes": 30,
        }

        stack = build_shadow_agent2_poi_stack(
            [ob],
            [fvg],
            [lifecycle],
            direction="LONG",
            current_price=100.0,
            atr_14=2.0,
            agent1_score=80.0,
        )

        self.assertEqual(stack["best_shadow_poi_type"], "FVG_CONTINUATION_ALIGNED")
        self.assertEqual(stack["best_shadow_poi"]["type"], "FVG_CONTINUATION")

    def test_shadow_poi_stack_waits_when_no_poi_available(self) -> None:
        stack = build_shadow_agent2_poi_stack(
            [],
            [],
            [],
            direction="SHORT",
            current_price=100.0,
            atr_14=2.0,
            agent1_score=82.0,
        )

        self.assertEqual(stack["best_shadow_poi_type"], "WAIT_FOR_POI_DEVELOPMENT")
        self.assertIsNone(stack["best_shadow_poi"])

    def test_diagnostic_reports_shadow_poi_stack_without_changing_decision(self) -> None:
        ob = make_ob(70.0, fresh=False, index=2)
        diagnostic = build_replay_agent_2_diagnostic(
            candle={"time": datetime(2026, 4, 1, 12, tzinfo=timezone.utc), "close": 100.0},
            blackboard=BlackBoard(),
            candles_15m=candles(12),
            candles_4h=candles(2),
            obs=[ob],
            fvgs=[],
            selected_ob=ob,
            atr_14=2.0,
            direction="LONG",
            final_reason="ZONE_ALREADY_MITIGATED",
            hard_filter_pass=False,
            score=0.0,
        )

        json.dumps(diagnostic)
        self.assertIn("shadow_agent2_poi_stack", diagnostic)
        self.assertIn("best_shadow_poi_type", diagnostic)
        self.assertFalse(diagnostic["hard_filter_pass"])
        self.assertEqual(diagnostic["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
