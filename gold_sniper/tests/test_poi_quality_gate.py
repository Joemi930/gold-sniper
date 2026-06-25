from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import unittest

from gold_sniper.strategy.poi_quality_gate import ACCEPT, REJECT, WATCH, evaluate_poi_quality
from gold_sniper.strategy.unified_xauusd_strategy import evaluate_unified_xauusd_strategy


def _context(**overrides):
    data = {
        "session_label": "NY",
        "draw_on_liquidity": "BUY_SIDE",
        "liquidity_target_open": True,
        "htf_aligned": True,
        "dol_aligned": True,
        "order_flow_aligned": True,
        "sweep_detected": True,
        "in_ote": True,
        "trigger_kind": "MICRO_CHOCH",
        "displacement_present": True,
        "has_retest": True,
    }
    data.update(overrides)
    return data


def _ob(**overrides):
    data = {
        "normalized_poi_type": "OB",
        "high": 2050.0,
        "low": 2042.0,
        "lifecycle_normalized": "FRESH",
        "displacement_after_ob": True,
        "aligned_with_context": True,
        "has_fvg": True,
        "liquidity_sweep_before": True,
        "is_extreme_ob": True,
    }
    data.update(overrides)
    return data


def _fvg(**overrides):
    data = {
        "normalized_poi_type": "FVG",
        "high": 2050.0,
        "low": 2048.0,
        "created_by_displacement": True,
        "distance_atr": 0.5,
        "age_sessions": 1,
        "fill_pct": 0.2,
        "close_inside_count": 0,
        "touch_count": 1,
        "aligned_with_context": True,
        "linked_to_ob": True,
        "clean_retest": True,
    }
    data.update(overrides)
    return data


class TestPoiQualityGate(unittest.TestCase):
    def test_quality_ob_accepts_grade_a_or_b(self) -> None:
        result = evaluate_poi_quality(_ob(), _context())
        self.assertEqual(result.decision, ACCEPT)
        self.assertIn(result.grade, {"A", "B"})

    def test_wick_tagged_light_ob_is_not_rejected(self) -> None:
        result = evaluate_poi_quality(_ob(lifecycle_normalized="WICK_TAGGED", penetration_pct=0.12), _context())
        self.assertIn(result.decision, {WATCH, ACCEPT})

    def test_partial_mitigation_controlled_is_watch(self) -> None:
        result = evaluate_poi_quality(_ob(lifecycle_normalized="PARTIALLY_MITIGATED", penetration_pct=0.35), _context())
        self.assertEqual(result.decision, WATCH)

    def test_consumed_or_deep_mitigated_ob_rejects(self) -> None:
        consumed = evaluate_poi_quality(_ob(lifecycle_normalized="CONSUMED"), _context())
        deep = evaluate_poi_quality(_ob(lifecycle_normalized="PARTIALLY_MITIGATED", penetration_pct=0.75), _context())
        self.assertEqual(consumed.decision, REJECT)
        self.assertEqual(deep.decision, REJECT)

    def test_recent_near_fvg_with_displacement_accepts_or_watches(self) -> None:
        result = evaluate_poi_quality(_fvg(), _context())
        self.assertIn(result.decision, {ACCEPT, WATCH})

    def test_fvg_missing_distance_never_accepts(self) -> None:
        poi = _fvg()
        poi.pop("distance_atr")
        result = evaluate_poi_quality(poi, _context())
        self.assertNotEqual(result.decision, ACCEPT)
        self.assertIn("FVG_DISTANCE_MISSING", result.missing_evidence)

    def test_fvg_missing_age_never_accepts(self) -> None:
        poi = _fvg()
        poi.pop("age_sessions")
        result = evaluate_poi_quality(poi, _context())
        self.assertNotEqual(result.decision, ACCEPT)
        self.assertIn("FVG_AGE_MISSING", result.missing_evidence)

    def test_fvg_missing_fill_never_accepts(self) -> None:
        poi = _fvg()
        poi.pop("fill_pct")
        result = evaluate_poi_quality(poi, _context())
        self.assertNotEqual(result.decision, ACCEPT)
        self.assertIn("FVG_FILL_MISSING", result.missing_evidence)

    def test_fvg_missing_distance_age_fill_reports_missing_evidence(self) -> None:
        poi = _fvg()
        for key in ("distance_atr", "age_sessions", "fill_pct"):
            poi.pop(key)
        result = evaluate_poi_quality(poi, _context())
        self.assertNotEqual(result.decision, ACCEPT)
        self.assertIn("FVG_DISTANCE_MISSING", result.missing_evidence)
        self.assertIn("FVG_AGE_MISSING", result.missing_evidence)
        self.assertIn("FVG_FILL_MISSING", result.missing_evidence)

    def test_fvg_too_many_closes_inside_rejects(self) -> None:
        result = evaluate_poi_quality(_fvg(close_inside_count=3), _context())
        self.assertEqual(result.decision, REJECT)
        self.assertIn("FVG_TOO_MANY_CLOSES_INSIDE", result.reasons)

    def test_fvg_too_many_touches_rejects(self) -> None:
        result = evaluate_poi_quality(_fvg(touch_count=4), _context())
        self.assertEqual(result.decision, REJECT)
        self.assertIn("FVG_TOO_MANY_TOUCHES", result.reasons)

    def test_ob_too_many_touches_rejects(self) -> None:
        result = evaluate_poi_quality(_ob(touch_count=4), _context())
        self.assertEqual(result.decision, REJECT)
        self.assertIn("OB_TOO_MANY_TOUCHES", result.reasons)

    def test_wick_tagged_light_ob_with_acceptable_touch_count_is_not_rejected(self) -> None:
        result = evaluate_poi_quality(_ob(lifecycle_normalized="WICK_TAGGED", penetration_pct=0.12, touch_count=1), _context())
        self.assertIn(result.decision, {WATCH, ACCEPT})

    def test_clean_fvg_with_required_evidence_keeps_expected_behavior(self) -> None:
        result = evaluate_poi_quality(_fvg(distance_atr=0.4, age_sessions=1, fill_pct=0.1, close_inside_count=0, touch_count=1), _context())
        self.assertIn(result.decision, {ACCEPT, WATCH})
        self.assertNotIn("FVG_DISTANCE_MISSING", result.missing_evidence)
        self.assertNotIn("FVG_AGE_MISSING", result.missing_evidence)
        self.assertNotIn("FVG_FILL_MISSING", result.missing_evidence)

    def test_old_far_filled_fvg_rejects(self) -> None:
        result = evaluate_poi_quality(_fvg(distance_atr=3.0, age_sessions=5, fill_pct=0.9), _context())
        self.assertEqual(result.decision, REJECT)

    def test_clean_fvg_without_ob_is_watch_not_auto_accept(self) -> None:
        result = evaluate_poi_quality(_fvg(linked_to_ob=False), _context())
        self.assertEqual(result.decision, WATCH)

    def test_fvg_without_displacement_rejects(self) -> None:
        result = evaluate_poi_quality(_fvg(created_by_displacement=False), _context(displacement_present=False))
        self.assertEqual(result.decision, REJECT)

    def test_choch_alone_adds_warning_and_no_decisive_bonus(self) -> None:
        result = evaluate_poi_quality(_ob(), _context(displacement_present=False, has_retest=False))
        self.assertIn("MICRO_CHOCH_ALONE_NOT_DECISIVE", result.warnings)
        self.assertNotEqual(result.decision, ACCEPT)

    def test_london_ny_overlap_bonus(self) -> None:
        ny = evaluate_poi_quality(_ob(), _context(session_label="NY"))
        london = evaluate_poi_quality(_ob(), _context(session_label="LONDON"))
        overlap = evaluate_poi_quality(_ob(), _context(session_label="OVERLAP"))
        self.assertGreaterEqual(ny.score, 70)
        self.assertGreaterEqual(london.score, 70)
        self.assertGreaterEqual(overlap.score, 70)

    def test_tokyo_asia_warning_and_malus(self) -> None:
        result = evaluate_poi_quality(_ob(), _context(session_label="TOKYO"))
        self.assertIn("ASIA_TOKYO_POI_OBSERVATION_ONLY", result.warnings)
        self.assertLess(result.score, evaluate_poi_quality(_ob(), _context(session_label="NY")).score)

    def test_missing_bounds_rejects(self) -> None:
        poi = _ob()
        poi.pop("high")
        result = evaluate_poi_quality(poi, _context())
        self.assertEqual(result.decision, REJECT)
        self.assertTrue(result.hard_reject)

    def test_function_does_not_mutate_input_poi(self) -> None:
        poi = _ob()
        before = deepcopy(poi)
        evaluate_poi_quality(poi, _context())
        self.assertEqual(poi, before)

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "strategy" / "poi_quality_gate.py"
        with module_path.open("r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        self.assertNotIn("MetaTrader5", imports)

    def test_unified_integration_watch_blocks_enter_accept_can_continue(self) -> None:
        base_event = {
            "spread_risk": {"spread_ok": True},
            "risk": {"risk_ok": True},
            "context": _context(),
            "agents": {
                "agent_1": {"htf_context": "BULLISH", "draw_on_liquidity": "BUY_SIDE"},
                "agent_2": {"poi": _ob(lifecycle_normalized="PARTIALLY_MITIGATED", penetration_pct=0.35)},
                "agent_3": {"liquidity": {"sweep": True}},
                "agent_4": {"ote": {"in_ote": True}},
                "agent_5": {
                    "trigger_kind": "MICRO_CHOCH",
                    "trigger_inside_poi": True,
                    "displacement_present": True,
                    "reclaim_confirmed": True,
                    "retest_confirmed": True,
                },
                "agent_6": {"news_clear": True},
                "agent_7": {"session_label": "NY"},
            },
        }
        watch_decision = evaluate_unified_xauusd_strategy(base_event)
        self.assertEqual(watch_decision.decision, "WATCH_ONLY")

        base_event["agents"]["agent_2"]["poi"] = _ob()
        accept_decision = evaluate_unified_xauusd_strategy(base_event)
        self.assertIn(accept_decision.decision, {"ENTER_FULL", "ENTER_REDUCED", "WAIT_FOR_BETTER_PRICE"})


if __name__ == "__main__":
    unittest.main()
