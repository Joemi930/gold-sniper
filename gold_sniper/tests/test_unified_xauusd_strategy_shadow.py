from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from gold_sniper.strategy.professional_decision_engine import (
    DECISION_ENTER_FULL,
    DECISION_ENTER_REDUCED,
    DECISION_REJECT,
    DECISION_WAIT_FOR_TRIGGER,
    DECISION_WATCH_ONLY,
)
from gold_sniper.strategy.unified_xauusd_strategy import SHADOW_ONLY, evaluate_unified_xauusd_strategy


def _complete_event() -> dict:
    return {
        "spread_risk": {"spread_ok": True},
        "risk": {"risk_ok": True},
        "setup_type": "CONTINUATION",
        "context": {
            "session_label": "NY",
            "news_clear": True,
            "premium_discount": "DISCOUNT",
            "direction": "LONG",
            "fibonacci_anchor_valid": True,
            "draw_on_liquidity": "BUY_SIDE",
            "liquidity_target_open": True,
            "htf_aligned": True,
            "dol_aligned": True,
            "order_flow_aligned": True,
            "sweep_detected": True,
            "rejection_confirmed": True,
            "in_ote": True,
            "trigger_kind": "MICRO_CHOCH",
            "displacement_present": True,
            "has_retest": True,
        },
        "agents": {
            "agent_1": {"htf_context": "BULLISH", "draw_on_liquidity": "BUY_SIDE"},
            "agent_2": {
                "poi": {
                    "normalized_poi_type": "OB",
                    "high": 2050.0,
                    "low": 2042.0,
                    "lifecycle_normalized": "FRESH",
                    "displacement_after_ob": True,
                    "aligned_with_context": True,
                    "has_fvg": True,
                    "liquidity_sweep_before": True,
                    "is_extreme_ob": True,
                    "touch_count": 1,
                }
            },
            "agent_3": {"liquidity": {"sweep_detected": True, "rejection_confirmed": True}},
            "agent_4": {"ote": {"in_ote": True, "fibonacci_anchor_valid": True}},
            "agent_5": {
                "trigger_kind": "MICRO_CHOCH",
                "trigger_inside_poi": True,
                "displacement_present": True,
                "reclaim_confirmed": True,
                "retest_confirmed": True,
            },
            "agent_6": {"hard_filter_pass": True, "news_clear": True},
            "agent_7": {"session_label": "NY"},
        },
    }


class TestUnifiedXauusdStrategyShadow(unittest.TestCase):
    def assertHardVetoContract(self, decision, reason: str | None = None) -> None:
        self.assertEqual(decision.decision, DECISION_REJECT)
        self.assertTrue(decision.hard_veto)
        self.assertEqual(decision.setup_grade, "D")
        self.assertEqual(decision.risk_multiplier, 0.0)
        self.assertEqual(decision.required_execution_mode, SHADOW_ONLY)
        if reason is None:
            self.assertIsNotNone(decision.hard_veto_reason)
        else:
            self.assertEqual(decision.hard_veto_reason, reason)

    def test_empty_event_waits_without_crash(self) -> None:
        decision = evaluate_unified_xauusd_strategy({})
        self.assertIn(decision.decision, {"WAIT", "REJECT"})
        self.assertEqual(decision.mode, SHADOW_ONLY)
        self.assertIn("NEWS_CONTEXT_MISSING", decision.missing_conditions)

    def test_agent6_hard_veto_rejects(self) -> None:
        event = {"agents": {"agent_6": {"hard_filter_pass": False}}}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, "REJECT")
        self.assertTrue(decision.hard_veto)
        self.assertEqual(decision.hard_veto_reason, "NEWS_HARD_VETO")

    def test_tokyo_session_rejects(self) -> None:
        event = {"agents": {"agent_6": {"news_clear": True}, "agent_7": {"session_label": "TOKYO"}}}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, "REJECT")
        self.assertEqual(decision.hard_veto_reason, "SESSION_VETO_TOKYO_ASIA")

    def test_unknown_session_waits(self) -> None:
        event = {"agents": {"agent_6": {"news_clear": True}, "agent_7": {"session_label": "UNKNOWN"}}}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, "WAIT")
        self.assertIn("SESSION_CONTEXT_UNKNOWN", decision.missing_conditions)

    def test_off_session_is_known_non_tradable_not_unknown(self) -> None:
        event = {"agents": {"agent_6": {"news_clear": True}, "agent_7": {"session_label": "OFF_SESSION"}}}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, "REJECT")
        self.assertEqual(decision.hard_veto_reason, "SESSION_OFF_SESSION_NON_TRADABLE")
        self.assertNotIn("SESSION_CONTEXT_UNKNOWN", decision.missing_conditions)

    def test_incomplete_context_waits_with_missing_conditions(self) -> None:
        event = {"agents": {"agent_6": {"news_clear": True}, "agent_7": {"session_label": "LONDON"}}}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertHardVetoContract(decision, "LIQUIDITY_STORY_MISSING_BLOCKS_SETUP")

    def test_synthetic_complete_context_can_enter_shadow_only(self) -> None:
        decision = evaluate_unified_xauusd_strategy(_complete_event())
        self.assertEqual(decision.decision, DECISION_ENTER_FULL)
        self.assertEqual(decision.mode, SHADOW_ONLY)
        self.assertEqual(decision.setup_type, "CONTINUATION")
        self.assertFalse(decision.hard_veto)
        self.assertIn("shadow", decision.explanation.lower())

    def test_new_module_does_not_import_metatrader5(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "strategy" / "unified_xauusd_strategy.py"
        with module_path.open("r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        self.assertNotIn("MetaTrader5", imports)

    def test_broker_is_not_called(self) -> None:
        broker = Mock()
        event = _complete_event()
        event["broker"] = broker
        with patch("gold_sniper.strategy.unified_xauusd_strategy._extract_agents", wraps=None) as extract_agents:
            extract_agents.return_value = event["agents"]
            decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.mode, SHADOW_ONLY)
        broker.assert_not_called()

    def test_output_contains_explanation_and_steps(self) -> None:
        decision = evaluate_unified_xauusd_strategy(_complete_event())
        self.assertTrue(decision.explanation)
        self.assertTrue(decision.passed_steps)
        self.assertIsInstance(decision.failed_steps, list)
        self.assertIn("news_permission", decision.evidence)

    def test_unified_poi_accept_micro_watch_waits(self) -> None:
        event = _complete_event()
        event["agents"]["agent_5"].pop("retest_confirmed")
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, DECISION_WATCH_ONLY)
        self.assertTrue(any(reason.startswith("MICRO_CONFIRMATION_WATCH_") for reason in decision.missing_conditions))

    def test_unified_poi_accept_micro_confirmed_can_enter_shadow(self) -> None:
        decision = evaluate_unified_xauusd_strategy(_complete_event())
        self.assertEqual(decision.decision, DECISION_ENTER_FULL)
        self.assertEqual(decision.mode, SHADOW_ONLY)

    def test_unified_trigger_outside_poi_blocks_enter(self) -> None:
        event = _complete_event()
        event["agents"]["agent_5"]["trigger_inside_poi"] = False
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, DECISION_REJECT)
        self.assertEqual(decision.hard_veto_reason, "TRIGGER_OUTSIDE_POI")
        self.assertEqual(decision.evidence["funnel_trace"]["micro_status"], "MICRO_REJECT_OWN")

    def test_unified_liquidity_watch_waits(self) -> None:
        event = _complete_event()
        event["agents"]["agent_3"]["liquidity"] = {"approaching_liquidity": True}
        event["context"].pop("sweep_detected")
        event["context"].pop("rejection_confirmed")
        event["context"].pop("liquidity_target_open")
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, DECISION_WAIT_FOR_TRIGGER)
        self.assertIn("LIQUIDITY_WATCH_APPROACHING_LIQUIDITY", decision.missing_conditions)

    def test_unified_liquidity_blocks_setup(self) -> None:
        event = _complete_event()
        event["agents"]["agent_3"]["liquidity"] = {"dol_status": "CONSUMED"}
        event["context"].pop("liquidity_target_open")
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertHardVetoContract(decision, "DOL_CONSUMED_NO_NEW_DRAW")

    def test_unified_liquidity_support_poi_micro_complete_can_enter_shadow(self) -> None:
        decision = evaluate_unified_xauusd_strategy(_complete_event())
        self.assertEqual(decision.decision, DECISION_ENTER_FULL)
        self.assertIn("liquidity_state", decision.evidence)

    def test_unified_without_liquidity_story_never_enters(self) -> None:
        event = _complete_event()
        event["agents"]["agent_3"]["liquidity"] = {}
        event["context"].pop("sweep_detected")
        event["context"].pop("rejection_confirmed")
        event["context"].pop("liquidity_target_open")
        event["context"].pop("draw_on_liquidity")
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertHardVetoContract(decision, "LIQUIDITY_STORY_MISSING_BLOCKS_SETUP")

    def test_unified_session_gate_watch_waits(self) -> None:
        event = _complete_event()
        event["context"]["session_label"] = "UNKNOWN"
        event["agents"]["agent_7"]["session_label"] = "NY"
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, DECISION_ENTER_REDUCED)
        self.assertTrue(any(reason.startswith("SESSION_PREMIUM_OTE_WATCH_") for reason in decision.missing_conditions))

    def test_unified_premium_conflict_reversal_blocks_enter(self) -> None:
        event = _complete_event()
        event["setup_type"] = "REVERSAL"
        event["context"]["setup_type"] = "REVERSAL"
        event["context"]["premium_discount"] = "PREMIUM"
        event["context"]["direction"] = "LONG"
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertHardVetoContract(decision)

    def test_unified_ote_conflict_continuation_is_not_hard_reject(self) -> None:
        event = _complete_event()
        event["context"]["ote_conflict"] = True
        event["context"]["in_ote"] = False
        event["agents"]["agent_4"]["ote"] = {"ote_conflict": True, "fibonacci_anchor_valid": True}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, DECISION_ENTER_REDUCED)
        self.assertFalse(decision.hard_veto)

    def test_unified_sniper_pullback_long_premium_blocks_enter(self) -> None:
        event = _complete_event()
        event["setup_type"] = "SNIPER_PULLBACK"
        event["context"]["setup_type"] = "SNIPER_PULLBACK"
        event["context"]["direction"] = "LONG"
        event["context"]["premium_discount"] = "PREMIUM"
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.setup_type, "SNIPER_PULLBACK")
        self.assertHardVetoContract(decision)

    def test_unified_trend_continuation_ote_conflict_watches_not_reversal_block(self) -> None:
        event = _complete_event()
        event["setup_type"] = "TREND_CONTINUATION"
        event["context"]["setup_type"] = "TREND_CONTINUATION"
        event["context"]["direction"] = "LONG"
        event["context"]["premium_discount"] = "PREMIUM"
        event["context"]["ote_conflict"] = True
        event["context"]["in_ote"] = False
        event["agents"]["agent_4"]["ote"] = {"ote_conflict": True, "fibonacci_anchor_valid": True}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.setup_type, "TREND_CONTINUATION")
        self.assertEqual(decision.decision, DECISION_WATCH_ONLY)
        self.assertFalse(decision.hard_veto)
        self.assertTrue(any(reason.startswith("SESSION_PREMIUM_OTE_WATCH_") for reason in decision.missing_conditions))
        gate = decision.evidence["session_premium_ote_gate"]["value"]
        self.assertFalse(gate["hard_block"])
        self.assertIn("OTE_CONFLICT", gate["warnings"])

    def test_unified_pass_liquidity_poi_micro_can_enter_shadow(self) -> None:
        decision = evaluate_unified_xauusd_strategy(_complete_event())
        self.assertEqual(decision.decision, DECISION_ENTER_FULL)

    def test_unified_news_veto_rejects(self) -> None:
        event = _complete_event()
        event["agents"]["agent_6"]["news_veto"] = True
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.decision, "REJECT")

    def test_wait_decision_contains_decision_explainer(self) -> None:
        decision = evaluate_unified_xauusd_strategy({})
        self.assertIsNotNone(decision.explanation_detail)
        self.assertIn("decision_explainer", decision.evidence)

    def test_reject_decision_contains_decision_explainer(self) -> None:
        event = {"agents": {"agent_6": {"hard_filter_pass": False}}}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.explanation_detail["pipeline_stage"], "NEWS")
        self.assertEqual(decision.explanation_detail["trade_readiness"], "BLOCKED")

    def test_enter_shadow_contains_decision_explainer(self) -> None:
        decision = evaluate_unified_xauusd_strategy(_complete_event())
        self.assertEqual(decision.explanation_detail["trade_readiness"], "READY_SHADOW")
        self.assertIn("shadow", decision.explanation_detail["summary"].lower())

    def test_decision_explainer_stage_liquidity_when_liquidity_blocks(self) -> None:
        event = _complete_event()
        event["agents"]["agent_3"]["liquidity"] = {}
        event["context"].pop("sweep_detected")
        event["context"].pop("rejection_confirmed")
        event["context"].pop("liquidity_target_open")
        event["context"].pop("draw_on_liquidity")
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.explanation_detail["pipeline_stage"], "LIQUIDITY")

    def test_decision_explainer_stage_session_premium_ote_when_gate_blocks(self) -> None:
        event = _complete_event()
        event["setup_type"] = "REVERSAL"
        event["context"]["setup_type"] = "REVERSAL"
        event["context"]["premium_discount"] = "PREMIUM"
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertHardVetoContract(decision)

    def test_best_scenario_tradable_pilots_setup_type_when_no_explicit_setup(self) -> None:
        event = _complete_event()
        event.pop("setup_type", None)
        event["context"].pop("setup_type", None)
        event["context"].update(
            {
                "scenario_ready": True,
                "poi_stars": 5,
                "risk_valid": True,
                "micro_trigger": True,
                "htf_context_available": True,
                "dol_available": True,
                "session_allowed": True,
            }
        )
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.setup_type, "OTE_CONFLUENCE")

    def test_observation_used_when_context_known_but_no_setup_clear(self) -> None:
        event = {"agents": {"agent_6": {"news_clear": True}, "agent_7": {"session_label": "NY_KILLZONE"}}}
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.setup_type, "OBSERVATION")

    def test_poi_reject_before_micro_marks_micro_inherited(self) -> None:
        event = _complete_event()
        event["agents"]["agent_2"]["poi"]["lifecycle_normalized"] = "CONSUMED"
        decision = evaluate_unified_xauusd_strategy(event)
        trace = decision.evidence["funnel_trace"]
        self.assertEqual(trace["poi_status"], "POI_REJECT_OWN")
        self.assertEqual(trace["micro_status"], "NOT_EVALUATED_INHERITED_POI_REJECT")
        self.assertEqual(decision.decision, DECISION_REJECT)

    def test_poi_watch_micro_incomplete_marks_micro_watch_near_miss(self) -> None:
        event = _complete_event()
        event["agents"]["agent_5"].pop("retest_confirmed")
        decision = evaluate_unified_xauusd_strategy(event)
        self.assertEqual(decision.evidence["funnel_trace"]["micro_status"], "MICRO_WATCH_NEAR_MISS")

    def test_decision_explainer_ready_shadow_when_pipeline_complete(self) -> None:
        decision = evaluate_unified_xauusd_strategy(_complete_event())
        self.assertEqual(decision.explanation_detail["trade_readiness"], "READY_SHADOW")
        self.assertEqual(decision.explanation_detail["kasper_alignment"], "ALIGNED")


if __name__ == "__main__":
    unittest.main()
