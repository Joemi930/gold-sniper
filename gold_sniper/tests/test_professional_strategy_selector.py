from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from strategies.ob_five_star_evidence import enrich_active_ob_with_five_star_evidence
from strategies.ob_five_star_strict import score_ob_five_star
from strategies.professional_strategy_selector import evaluate_professional_strategies, normalize_ob_lifecycle


def event_for_strategy(
    *,
    session: str = "NY",
    distance: float = 4.0,
    trigger_kind: str = "MICRO_CHOCH",
    poi_type: str = "FVG_CONTINUATION_ALIGNED",
    poi_state: str = "FRESH",
    sweep: bool = False,
    displacement: float = 0.0,
    retest: bool = False,
    poi_stack: list[dict] | None = None,
    active_ob: dict | None = None,
) -> dict:
    event = {
        "event": "decision",
        "time": "2026-05-13T14:00:00+00:00",
        "eval_active": True,
        "agents": {
            "agent_1": {
                "score": 95,
                "direction": "LONG",
                "hard_filter_pass": True,
                "payload": {
                    "shadow_ict_contract": {
                        "contextual_notes": {
                            "primary_regime": "STRONG_UP",
                            "htf_draw_on_liquidity": "BUY_SIDE",
                            "institutional_order_flow": "BULLISH",
                        }
                    }
                },
            },
            "agent_2": {
                "score": 0,
                "direction": "LONG",
                "hard_filter_pass": False,
                "payload": {
                    "diagnostic": {
                            "best_shadow_poi_type": poi_type,
                            "best_shadow_poi": {
                                "type": "FVG_CONTINUATION" if poi_type.startswith("FVG") else "OB_CONTINUATION",
                                "zone_type": "OB_CONTINUATION" if poi_type.startswith("OB") else "",
                                "priority_label": poi_type,
                                "score_shadow": 90,
                                "human_zone_state_shadow": poi_state,
                                "state_shadow": poi_state,
                                "filled_pct": 0.0,
                                "distance_to_price": distance,
                                "aligned_with_order_flow": True,
                                "liquidity_target_still_open": True,
                                "deepest_penetration_pct": 20.0,
                                "close_inside_count": 0,
                            },
                    }
                },
            },
            "agent_5": {
                "score": 95,
                "direction": "LONG",
                "hard_filter_pass": True,
                "payload": {
                    "sweep_1m_confirmed": sweep,
                    "displacement_ratio": displacement,
                    "shadow_ict_contract": {
                        "contextual_notes": {
                            "trigger_kind": trigger_kind,
                            "delivery_phase": "SNIPER_PULLBACK",
                            "trigger_inside_poi": True,
                        }
                    },
                    "shadow_trigger_context": {
                        "setup_family_hint": "SNIPER_PULLBACK",
                        "retest_detected": retest,
                        "poi_context_valid": True,
                    },
                },
            },
            "agent_6": {"score": 100, "hard_filter_pass": True, "reason": "NEWS_CLEAR"},
            "agent_7": {
                "score": 80,
                "hard_filter_pass": True,
                "payload": {
                    "session_name": session,
                    "trading_allowed": session != "TOKYO",
                    "shadow_ict_contract": {"contextual_notes": {"session_label": session}},
                },
            },
        },
    }
    if poi_stack is not None:
        event["agents"]["agent_2"]["payload"]["diagnostic"]["shadow_agent2_poi_stack"] = poi_stack
    if active_ob is not None:
        event["agents"]["agent_2"]["payload"]["diagnostic"]["active_ob"] = active_ob
    return event


def five_star_active_ob(**overrides) -> dict:
    ob = {
        "type": "BULLISH",
        "fresh": True,
        "high": 2350.0,
        "low": 2340.0,
        "timeframe": "15m",
        "session_created": "NY",
        "imbalance_created": True,
        "liquidity_sweep_before": True,
        "is_extreme_ob": True,
        "golden_hour_return": False,
    }
    ob.update(overrides)
    return ob


def evidence_candles() -> list[dict]:
    start = datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc)
    values = [
        (100, 101, 98, 100),
        (100, 101, 98, 100),
        (100, 101, 98, 100),
        (100, 101, 98, 100),
        (99, 100, 96, 99),
        (97, 100, 96, 99),
        (101, 103, 100, 102),
        (104, 108, 104, 107),
        (107, 109, 106, 108),
    ]
    return [
        {"time": start + timedelta(minutes=index), "open": open_, "high": high, "low": low, "close": close}
        for index, (open_, high, low, close) in enumerate(values)
    ]


class TestProfessionalStrategySelector(unittest.TestCase):
    def test_near_fvg_gets_candidate_without_real_decision_change(self) -> None:
        result = evaluate_professional_strategies(event_for_strategy())
        shadow = result["human_orchestrator_strategy_shadow"]

        self.assertIn("FVG_NEAR_ONLY", shadow["candidate_strategies"])
        self.assertIn(shadow["decision"], {"CANDIDATE", "STANDARD_SHADOW", "WAIT"})
        self.assertGreater(len(result["strategy_results"]), 5)
        self.assertIn("blocking_layer", shadow)
        self.assertIn("strategy_signal_semantics", shadow)

    def test_tokyo_veto_downgrades_to_wait(self) -> None:
        result = evaluate_professional_strategies(event_for_strategy(session="TOKYO", displacement=1.0, retest=True))
        shadow = result["human_orchestrator_strategy_shadow"]

        self.assertIsNone(shadow["final_shadow_decision"]["selected_entry_strategy_id"])
        self.assertEqual(shadow["decision"], "WAIT")
        self.assertEqual(shadow["blocking_layer"], "TIME")
        self.assertFalse(shadow["final_shadow_decision"]["is_executable_entry"])

    def test_far_fvg_is_not_premium_by_near_strategy(self) -> None:
        result = evaluate_professional_strategies(event_for_strategy(distance=24.0))
        near = next(item for item in result["strategy_results"] if item["strategy_id"] == "FVG_NEAR_ONLY")

        self.assertEqual(near["permission"], "REJECT")
        self.assertEqual(near["blocking_layer"], "POI")

    def test_trigger_none_blocks_executable_entry(self) -> None:
        result = evaluate_professional_strategies(event_for_strategy(trigger_kind="NONE"))
        final = result["final_shadow_decision"]

        self.assertEqual(final["entry_block_reason"], "TRIGGER_NONE")
        self.assertFalse(final["is_executable_entry"])
        self.assertEqual(result["strategy_signal_semantics"]["executable_shadow_entry_count"], 0)
        self.assertGreater(result["strategy_signal_semantics"]["candidate_count"], 0)

    def test_premium_strict_cannot_be_selected_entry_strategy_alone(self) -> None:
        event = event_for_strategy(poi_type="UNKNOWN", trigger_kind="MICRO_CHOCH")
        event["agents"]["agent_2"]["payload"]["diagnostic"]["best_shadow_poi"] = {}

        result = evaluate_professional_strategies(event)
        final = result["final_shadow_decision"]

        self.assertIsNone(final["selected_entry_strategy_id"])
        self.assertIn("premium_strict", result["gates"])
        self.assertFalse(final["is_executable_entry"])

    def test_premium_strict_can_upgrade_real_entry_strategy(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(sweep=True, displacement=1.4, retest=True)
        )
        final = result["final_shadow_decision"]

        self.assertEqual(final["selected_entry_strategy_id"], "FVG_NEAR_ONLY")
        self.assertEqual(final["decision"], "PREMIUM_SHADOW")
        self.assertTrue(final["is_executable_entry"])
        self.assertIn("PREMIUM_STRICT", final["selected_gate_ids"])

    def test_strategy_evaluation_is_not_entry(self) -> None:
        result = evaluate_professional_strategies(event_for_strategy(trigger_kind="NONE"))
        semantics = result["strategy_signal_semantics"]

        self.assertGreater(semantics["evaluation_count"], semantics["executable_shadow_entry_count"])

    def test_ob_wick_tagged_strategy_can_be_selected(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                poi_type="OB_CONTINUATION_WICK_TAGGED",
                poi_state="WICK_TAGGED",
                trigger_kind="MICRO_CHOCH",
                displacement=1.2,
                retest=True,
            )
        )
        final = result["final_shadow_decision"]
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_WICK_TAGGED_RETEST")

        self.assertEqual(ob["permission"], "STANDARD_SHADOW")
        self.assertEqual(final["selected_entry_strategy_id"], "OB_WICK_TAGGED_RETEST")

    def test_ob_wick_tagged_visible_as_candidate(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                poi_type="OB_CONTINUATION_WICK_TAGGED",
                poi_state="wick_tagged",
                trigger_kind="MICRO_CHOCH",
                displacement=1.0,
                retest=False,
            )
        )
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_WICK_TAGGED_RETEST")

        self.assertEqual(ob["permission"], "CANDIDATE")
        self.assertEqual(ob["reason"], "OB_NO_RETEST")

    def test_ob_wick_tagged_without_trigger_is_not_executable(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                poi_type="OB_CONTINUATION_WICK_TAGGED",
                poi_state="WICK_TAGGED",
                trigger_kind="NONE",
            )
        )
        final = result["final_shadow_decision"]
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_WICK_TAGGED_RETEST")

        self.assertEqual(ob["permission"], "CANDIDATE")
        self.assertFalse(final["is_executable_entry"])
        self.assertEqual(final["entry_block_reason"], "TRIGGER_NONE")

    def test_ob_partial_mitigation_watch_is_candidate_not_entry_by_default(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                poi_type="OB_CONTINUATION_PARTIALLY_MITIGATED",
                poi_state="partial",
                trigger_kind="NONE",
            )
        )
        partial = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_PARTIAL_MITIGATION_WATCH")

        self.assertEqual(partial["permission"], "CANDIDATE")
        self.assertEqual(partial["reason"], "OB_PARTIAL_NEEDS_CONFIRMATION")
        self.assertFalse(result["final_shadow_decision"]["is_executable_entry"])

    def test_ob_lifecycle_mapping_is_stable(self) -> None:
        cases = {
            "wick_tagged": "WICK_TAGGED",
            "WICK_TAGGED": "WICK_TAGGED",
            "partial": "PARTIALLY_MITIGATED",
            "partially_mitigated": "PARTIALLY_MITIGATED",
            "consumed": "CONSUMED",
            "invalidated": "INVALIDATED",
            "unknown": "UNKNOWN",
        }

        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_ob_lifecycle(raw), expected)

    def test_ob_candidate_lost_to_fvg_is_diagnosed(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                poi_type="FVG_CONTINUATION_ALIGNED",
                poi_state="FRESH",
                sweep=True,
                displacement=1.4,
                retest=True,
                poi_stack=[
                    {
                        "type": "OB_CONTINUATION",
                        "zone_type": "OB_CONTINUATION",
                        "priority_label": "OB_CONTINUATION_WICK_TAGGED",
                        "score_shadow": 65,
                        "human_zone_state_shadow": "WICK_TAGGED",
                        "deepest_penetration_pct": 10.0,
                        "close_inside_count": 0,
                    }
                ],
            )
        )
        competition = result["human_orchestrator_strategy_shadow"]["selector_competition"]

        self.assertIn("OB_WICK_TAGGED_RETEST", competition["ob_candidate_strategy_ids"])
        self.assertTrue(competition["ob_lost_to_fvg"])

    def test_active_ob_is_exported_to_ob_strategy_input(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                poi_type="FVG_CONTINUATION_ALIGNED",
                active_ob={
                    "type": "BEARISH",
                    "fresh": True,
                    "high": 2350.0,
                    "low": 2340.0,
                    "timeframe": "15m",
                },
            )
        )
        diagnostics = result["human_orchestrator_strategy_shadow"]["ob_strategy_diagnostics"]

        active = [poi for poi in diagnostics["poi_candidates_seen"] if poi["source_field"] == "active_ob"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["normalized_lifecycle"], "FRESH")
        self.assertTrue(active[0]["is_strategy_input_candidate"])

    def test_payload_level_active_ob_is_exported_to_ob_strategy_input(self) -> None:
        event = event_for_strategy(poi_type="FVG_CONTINUATION_ALIGNED")
        event["agents"]["agent_2"]["payload"]["active_ob"] = {
            "type": "BEARISH",
            "fresh": True,
            "high": 2350.0,
            "low": 2340.0,
        }

        result = evaluate_professional_strategies(event)
        diagnostics = result["human_orchestrator_strategy_shadow"]["ob_strategy_diagnostics"]
        active = [poi for poi in diagnostics["poi_candidates_seen"] if poi["source_field"] == "active_ob"]

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["normalized_lifecycle"], "FRESH")

    def test_active_ob_without_lifecycle_stays_exported_as_unknown(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                poi_type="FVG_CONTINUATION_ALIGNED",
                active_ob={
                    "type": "BULLISH",
                    "high": 2350.0,
                    "low": 2340.0,
                },
            )
        )
        diagnostics = result["human_orchestrator_strategy_shadow"]["ob_strategy_diagnostics"]

        active = [poi for poi in diagnostics["poi_candidates_seen"] if poi["source_field"] == "active_ob"]
        reasons = [item["reason"] for item in diagnostics["results"] if item["strategy_id"].startswith("OB_")]
        self.assertEqual(active[0]["normalized_lifecycle"], "UNKNOWN")
        self.assertIn("OB_LIFECYCLE_UNKNOWN", reasons)

    def test_active_ob_export_does_not_force_entry_with_trigger_none(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                trigger_kind="NONE",
                poi_type="FVG_CONTINUATION_ALIGNED",
                active_ob={
                    "type": "BEARISH",
                    "human_zone_state_shadow": "WICK_TAGGED",
                    "high": 2350.0,
                    "low": 2340.0,
                },
            )
        )
        final = result["final_shadow_decision"]

        self.assertFalse(final["is_executable_entry"])
        self.assertEqual(result["strategy_signal_semantics"]["premium_strict_standalone_entries"], 0)

    def test_ob_five_star_strict_scores_five_out_of_five_without_forced_entry(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                poi_type="FVG_CONTINUATION_ALIGNED",
                trigger_kind="NONE",
                active_ob=five_star_active_ob(),
            )
        )
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_FIVE_STAR_STRICT" and item["is_applicable"])

        self.assertEqual(ob["ob_five_star"]["base_star_count"], 5)
        self.assertEqual(ob["ob_five_star"]["quality_tier"], "FIVE_STAR_STRICT")
        self.assertEqual(ob["permission"], "CANDIDATE")
        self.assertFalse(result["final_shadow_decision"]["is_executable_entry"])

    def test_ob_five_star_strict_scores_four_out_of_five_without_sweep(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(active_ob=five_star_active_ob(liquidity_sweep_before=False))
        )
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_FIVE_STAR_STRICT" and item["is_applicable"])

        self.assertEqual(ob["ob_five_star"]["base_star_count"], 4)
        self.assertEqual(ob["ob_five_star"]["quality_tier"], "FOUR_STAR_WATCH")
        self.assertEqual(ob["permission"], "CANDIDATE")

    def test_ob_five_star_strict_mitigated_ob_is_not_strict(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(
                active_ob=five_star_active_ob(fresh=False, human_zone_state_shadow="PARTIALLY_MITIGATED")
            )
        )
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_FIVE_STAR_STRICT" and item["is_applicable"])

        self.assertFalse(ob["ob_five_star"]["stars"]["unmitigated"]["passed"])
        self.assertNotEqual(ob["ob_five_star"]["quality_tier"], "FIVE_STAR_STRICT")

    def test_ob_five_star_strict_tokyo_creation_is_not_strict(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(active_ob=five_star_active_ob(session_created="TOKYO"))
        )
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_FIVE_STAR_STRICT" and item["is_applicable"])

        self.assertFalse(ob["ob_five_star"]["stars"]["london_or_ny_creation"]["passed"])
        self.assertNotEqual(ob["ob_five_star"]["quality_tier"], "FIVE_STAR_STRICT")

    def test_ob_five_star_strict_without_imbalance_is_not_strict(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(active_ob=five_star_active_ob(imbalance_created=False))
        )
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_FIVE_STAR_STRICT" and item["is_applicable"])

        self.assertFalse(ob["ob_five_star"]["stars"]["imbalance_created"]["passed"])
        self.assertNotEqual(ob["ob_five_star"]["quality_tier"], "FIVE_STAR_STRICT")

    def test_ob_five_star_strict_trigger_none_preserves_semantic_invariants(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(trigger_kind="NONE", active_ob=five_star_active_ob())
        )
        semantics = result["strategy_signal_semantics"]
        final = result["final_shadow_decision"]
        ob = next(item for item in result["strategy_results"] if item["strategy_id"] == "OB_FIVE_STAR_STRICT" and item["is_applicable"])

        self.assertEqual(ob["permission"], "CANDIDATE")
        self.assertEqual(ob["reason"], "OB_FIVE_STAR_TRIGGER_NONE")
        self.assertEqual(semantics["executable_shadow_entry_count"], 0)
        self.assertEqual(semantics["premium_strict_standalone_entries"], 0)
        self.assertFalse(final["is_executable_entry"])

    def test_ob_five_star_active_ob_routing_is_intact(self) -> None:
        result = evaluate_professional_strategies(
            event_for_strategy(active_ob=five_star_active_ob())
        )
        diagnostics = result["human_orchestrator_strategy_shadow"]["ob_strategy_diagnostics"]
        reasons = [item["reason"] for item in diagnostics["results"] if item["strategy_id"] == "OB_FIVE_STAR_STRICT"]

        self.assertTrue(any(poi["source_field"] == "active_ob" for poi in diagnostics["poi_candidates_seen"]))
        self.assertTrue(reasons)

    def test_ob_evidence_enrichment_detects_imbalance_after_ob(self) -> None:
        ob = {"type": "BULLISH", "fresh": True, "high": 100, "low": 96, "created_at": "2026-04-08T13:05:00+00:00"}
        enriched = enrich_active_ob_with_five_star_evidence(ob, evidence_candles(), {}, "2026-04-08T13:08:00+00:00")

        self.assertTrue(enriched["five_star_evidence"]["imbalance_created"])
        self.assertEqual(enriched["five_star_evidence"]["imbalance_source"], "computed_from_candles")

    def test_ob_evidence_enrichment_detects_sweep_before_ob(self) -> None:
        ob = {"type": "BULLISH", "fresh": True, "high": 100, "low": 96, "created_at": "2026-04-08T13:05:00+00:00"}
        enriched = enrich_active_ob_with_five_star_evidence(ob, evidence_candles(), {}, "2026-04-08T13:08:00+00:00")

        self.assertTrue(enriched["five_star_evidence"]["liquidity_sweep_before"])

    def test_ob_evidence_enrichment_detects_structural_extreme(self) -> None:
        ob = {"type": "BULLISH", "fresh": True, "high": 100, "low": 96, "created_at": "2026-04-08T13:05:00+00:00"}
        enriched = enrich_active_ob_with_five_star_evidence(ob, evidence_candles(), {}, "2026-04-08T13:08:00+00:00")

        self.assertTrue(enriched["five_star_evidence"]["is_extreme_ob"])

    def test_ob_evidence_enrichment_infers_ny_session_created(self) -> None:
        ob = {"type": "BULLISH", "fresh": True, "high": 100, "low": 96, "created_at": "2026-04-08T13:05:00+00:00"}
        enriched = enrich_active_ob_with_five_star_evidence(ob, evidence_candles(), {}, "2026-04-08T13:08:00+00:00")

        self.assertEqual(enriched["five_star_evidence"]["session_created"], "NY")
        self.assertTrue(enriched["five_star_evidence"]["london_or_ny_creation"])

    def test_ob_evidence_enrichment_rejects_tokyo_creation_session(self) -> None:
        ob = {"type": "BULLISH", "fresh": True, "high": 100, "low": 96, "created_at": "2026-04-08T01:05:00+00:00"}
        enriched = enrich_active_ob_with_five_star_evidence(ob, evidence_candles(), {}, "2026-04-08T13:08:00+00:00")

        self.assertEqual(enriched["five_star_evidence"]["session_created"], "TOKYO")
        self.assertFalse(enriched["five_star_evidence"]["london_or_ny_creation"])

    def test_ob_evidence_enrichment_detects_golden_hour_return(self) -> None:
        ob = {"type": "BULLISH", "fresh": True, "high": 100, "low": 96, "return_time": "2026-04-08T14:30:00+00:00"}
        enriched = enrich_active_ob_with_five_star_evidence(ob, evidence_candles(), {}, "2026-04-08T14:30:00+00:00")

        self.assertTrue(enriched["five_star_evidence"]["golden_hour_return"])

    def test_ob_evidence_enrichment_does_not_use_future_candles(self) -> None:
        ob = {"type": "BULLISH", "fresh": True, "high": 100, "low": 96, "created_at": "2026-04-08T13:05:00+00:00"}
        enriched = enrich_active_ob_with_five_star_evidence(ob, evidence_candles(), {}, "2026-04-08T13:05:00+00:00")

        self.assertFalse(enriched["five_star_evidence"]["imbalance_created"])
        self.assertFalse(enriched["five_star_evidence"]["no_lookahead_guard"]["future_candles_used"])

    def test_ob_five_star_score_uses_enriched_evidence(self) -> None:
        ob = {"type": "BULLISH", "fresh": True, "high": 100, "low": 96, "created_at": "2026-04-08T13:05:00+00:00"}
        enriched = enrich_active_ob_with_five_star_evidence(ob, evidence_candles(), {}, "2026-04-08T13:08:00+00:00")
        enriched["lifecycle_normalized"] = "FRESH"
        enriched["has_price_bounds"] = True
        score = score_ob_five_star(enriched, {}, {"trigger_kind": "NONE"})

        self.assertEqual(score["base_star_count"], 5)
        self.assertEqual(score["quality_tier"], "FIVE_STAR_STRICT")

    def test_summary_consistency_semantics(self) -> None:
        result = evaluate_professional_strategies(event_for_strategy(trigger_kind="NONE"))
        semantics = result["strategy_signal_semantics"]

        self.assertLessEqual(semantics["executable_shadow_entry_count"], semantics["selected_count"])
        self.assertLessEqual(semantics["selected_count"], semantics["candidate_count"])
        self.assertLessEqual(semantics["candidate_count"], semantics["evaluation_count"])
        self.assertEqual(semantics["premium_strict_standalone_entries"], 0)


if __name__ == "__main__":
    unittest.main()
