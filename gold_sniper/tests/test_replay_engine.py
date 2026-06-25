from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest

from core.blackboard import BlackBoard
from replay.replay_engine import (
    ReplayEngine,
    _p1_48cbis_extract_agent2_pois,
    _p1_48cbis_ob_exposure_probe_summaries,
    _p1_48cbis_ob_discrepancy_summary,
    _p1_48cbis_ob_funnel,
    _tier_replay_trade_signal,
)
from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager


def candles(count: int) -> list[dict]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "time": start + timedelta(minutes=index),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 1.0,
            "tick_volume": 1.0,
        }
        for index in range(count)
    ]


class _NumpyBoolLike:
    def item(self) -> bool:
        return True


class TestReplayEngine(unittest.IsolatedAsyncioTestCase):
    def test_p1_48cbis_probe_detects_ob_in_shadow_agent2_poi_stack(self) -> None:
        event = {
            "agents": {
                "agent_2": {
                    "payload": {
                        "diagnostic": {
                            "shadow_agent2_poi_stack": [
                                {
                                    "priority_label": "OB_CONTINUATION_WICK_TAGGED",
                                    "human_zone_state_shadow": "wick_tagged",
                                    "score_shadow": 70,
                                }
                            ]
                        }
                    }
                }
            }
        }

        pois = _p1_48cbis_extract_agent2_pois(event)

        self.assertEqual(pois[0]["source_field"], "shadow_agent2_poi_stack")
        self.assertEqual(pois[0]["normalized_poi_type"], "OB")
        self.assertEqual(pois[0]["normalized_lifecycle"], "WICK_TAGGED")

    def test_p1_48cbis_probe_detects_ob_in_best_shadow_poi(self) -> None:
        event = {
            "agents": {
                "agent_2": {
                    "payload": {
                        "diagnostic": {
                            "best_shadow_poi": {
                                "priority_label": "OB_CONTINUATION_FRESH",
                                "human_zone_state_shadow": "FRESH",
                            }
                        }
                    }
                }
            }
        }

        pois = _p1_48cbis_extract_agent2_pois(event)

        self.assertEqual(pois[0]["source_field"], "best_shadow_poi")
        self.assertEqual(pois[0]["normalized_poi_type"], "OB")
        self.assertEqual(pois[0]["normalized_lifecycle"], "FRESH")

    def test_p1_48cbis_probe_detects_alternative_order_block_key(self) -> None:
        event = {
            "agents": {
                "agent_2": {
                    "payload": {
                        "diagnostic": {
                            "order_block": {
                                "type": "OB_PARTIALLY_MITIGATED",
                                "state": "partial_mitigation",
                            }
                        }
                    }
                }
            }
        }

        pois = _p1_48cbis_extract_agent2_pois(event)

        self.assertEqual(pois[0]["normalized_poi_type"], "OB")
        self.assertEqual(pois[0]["normalized_lifecycle"], "PARTIALLY_MITIGATED")

    def test_p1_48cbis2_probe_detects_ob_in_shadow_agent2_ict_poi_stack(self) -> None:
        event = {
            "agents": {
                "agent_2": {
                    "payload": {
                        "diagnostic": {
                            "shadow_agent2_ict_poi_stack": [
                                {
                                    "type": "OB_CONTINUATION",
                                    "human_zone_state_shadow": "WICK_TAGGED",
                                }
                            ]
                        }
                    }
                }
            }
        }

        summary = _p1_48cbis_ob_exposure_probe_summaries(
            [event],
            selections=[],
            strategy_stats={},
            entries=[],
        )

        probe = summary["shadow_agent2_ob_exposure_probe_summary"]
        self.assertEqual(probe["records_with_ob_in_shadow_agent2_ict_poi_stack"], 1)
        self.assertEqual(probe["ob_poi_seen_total"], 1)

    def test_p1_48cbis_funnel_reports_drop_reason_when_ob_not_candidate(self) -> None:
        flat = [
            {
                "source_field": "best_shadow_poi",
                "raw_poi_type": "OB_CONTINUATION_WICK_TAGGED",
                "normalized_poi_type": "OB",
                "raw_lifecycle": "WICK_TAGGED",
                "normalized_lifecycle": "WICK_TAGGED",
                "missing_field": False,
            },
            {
                "source_field": "shadow_agent2_poi_stack",
                "raw_poi_type": "OB_CONTINUATION_WICK_TAGGED",
                "normalized_poi_type": "OB",
                "raw_lifecycle": "WICK_TAGGED",
                "normalized_lifecycle": "WICK_TAGGED",
                "missing_field": False,
            }
        ]
        funnel = _p1_48cbis_ob_funnel(
            flat,
            selections=[],
            strategy_stats={"OB_WICK_TAGGED_RETEST": {"evaluations": 1, "candidate_count": 0}},
            entries=[],
        )

        self.assertEqual(funnel["main_ob_drop_stage"], "strategy_input_poi_stack")
        self.assertEqual(funnel["main_ob_drop_reason"], "OB_NOT_REACHED_SELECTOR")

    def test_p1_48cbis2_probe_limits_examples_to_50(self) -> None:
        events = []
        for index in range(100):
            events.append({
                "time": f"2026-01-01T00:{index:02d}:00Z",
                "agents": {
                    "agent_2": {
                        "payload": {
                            "diagnostic": {
                                "best_shadow_poi": {
                                    "priority_label": "OB_CONTINUATION_WICK_TAGGED",
                                    "human_zone_state_shadow": "WICK_TAGGED",
                                    "score_shadow": index,
                                }
                            }
                        }
                    }
                },
            })

        summary = _p1_48cbis_ob_exposure_probe_summaries(
            events,
            selections=[],
            strategy_stats={},
            entries=[],
        )

        self.assertEqual(len(summary["shadow_ob_probe_examples_summary"]["examples"]), 50)
        self.assertEqual(summary["shadow_ob_wider_probe_window_summary"]["max_examples"], 50)

    def test_p1_48cbis_discrepancy_summary_flags_agent2_strategy_mismatch(self) -> None:
        discrepancy = _p1_48cbis_ob_discrepancy_summary(
            decision_events=[{"event": "decision"}],
            ob_items=[{"normalized_poi_type": "OB"}],
            connectivity={"ob_poi_seen_total": 0},
        )

        self.assertTrue(discrepancy["mismatch_agent2_vs_strategy"])
        self.assertEqual(discrepancy["likely_explanation"], "OB_EXPOSED_BY_AGENT2_BUT_NOT_REACHING_STRATEGY")

    async def test_engine_injects_candles_and_writes_summary(self) -> None:
        board = BlackBoard()
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(16), output_root=tmp, run_id="unit")
            summary = await engine.run()

            summary_path = engine.summary_path
            saved = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["candles"], 16)
        self.assertEqual(saved["run_id"], "unit")
        self.assertEqual(board.read_sync("meta")["run_mode"], "REPLAY")
        self.assertEqual(len(board.read_sync("market_data.candles.1m")), 16)
        self.assertEqual(len(board.read_sync("market_data.candles.15m")), 1)
        self.assertEqual(board.read_sync("market_data.current_tick")["bid"], 115.5)

    async def test_engine_injects_external_higher_timeframes_when_provided(self) -> None:
        board = BlackBoard()
        sample = candles(3)
        higher = {
            "15m": [{**sample[0], "close": 150.0}],
            "4H": [{**sample[0], "close": 400.0}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            await ReplayEngine(
                board,
                sample,
                output_root=tmp,
                run_id="external_tf",
                candles_by_timeframe=higher,
            ).run()

        self.assertEqual(len(board.read_sync("market_data.candles.15m")), 1)
        self.assertEqual(len(board.read_sync("market_data.candles.4H")), 1)
        self.assertEqual(board.read_sync("market_data.candles.15m")[0]["close"], 150.0)
        self.assertEqual(board.read_sync("market_data.candles.4H")[0]["close"], 400.0)

    async def test_engine_hook_can_emit_signal_for_simulator(self) -> None:
        board = BlackBoard()

        async def hook(candle, blackboard):
            if candle["close"] == 100.5:
                await blackboard.write(
                    "trade_signals",
                    {
                        "signal": "BUY",
                        "entry_price": 100.0,
                        "stop_loss": 95.0,
                        "tp1_price": 105.0,
                        "take_profit": 110.0,
                    },
                )

        sample = candles(3)
        sample[1]["high"] = 106.0
        sample[2]["high"] = 111.0
        with tempfile.TemporaryDirectory() as tmp:
            summary = await ReplayEngine(board, sample, output_root=tmp, run_id="hook", on_candle_hook=hook).run()

        self.assertEqual(summary["signals"], 0)
        self.assertEqual(summary["trades"], 0)
        self.assertNotIn("P1_REPLAY_FORBIDS_TRADE_SIGNAL", summary.get("errors", []))

    async def test_engine_writes_decision_events_without_hook(self) -> None:
        board = BlackBoard()
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(2), output_root=tmp, run_id="decisions")
            await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertIn("decision_snapshot", [event["event"] for event in events])
        self.assertIn("decision", [event["event"] for event in events])

    async def test_decision_hook_can_create_signal(self) -> None:
        board = BlackBoard()

        def hook(candle, blackboard):
            del blackboard
            if candle["close"] == 100.5:
                return {
                    "score_final": 88,
                    "reason": "UNIT_SIGNAL",
                    "signal": {
                        "signal": "BUY",
                        "entry_price": 100.0,
                        "stop_loss": 95.0,
                        "tp1_price": 105.0,
                        "take_profit": 110.0,
                    },
                }
            return {"reject_reason": "NO_SIGNAL"}

        sample = candles(3)
        sample[1]["high"] = 106.0
        sample[2]["high"] = 111.0
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, sample, output_root=tmp, run_id="decision_hook", on_decision_hook=hook)
            summary = await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary["trades"], 0)
        self.assertIn("p1_decision_rejected", [event["event"] for event in events])

    async def test_summary_contains_p1_45_agent2_poi_stack_blocks(self) -> None:
        board = BlackBoard()

        def hook(candle, blackboard):
            del candle, blackboard
            return {
                "agents": {
                    "agent_1": {"score": 80, "direction": "LONG", "hard_filter_pass": True, "payload": {}},
                    "agent_2": {
                        "score": 0,
                        "direction": "LONG",
                        "hard_filter_pass": False,
                        "reason": "ZONE_ALREADY_MITIGATED",
                        "payload": {
                            "diagnostic": {
                                "final_reason": "ZONE_ALREADY_MITIGATED",
                                "best_shadow_poi_type": "OB_CONTINUATION_WICK_TAGGED",
                                "best_shadow_poi_reason": "OB_CONTINUATION_AVAILABLE",
                                "best_shadow_poi": {
                                    "zone_type": "OB_CONTINUATION",
                                    "priority_label": "OB_CONTINUATION_WICK_TAGGED",
                                    "human_zone_state_shadow": "WICK_TAGGED",
                                    "zone_still_contextually_usable": True,
                                    "score": 68,
                                },
                                "shadow_agent2_poi_stack": [
                                    {
                                        "zone_type": "OB_CONTINUATION",
                                        "priority_label": "OB_CONTINUATION_WICK_TAGGED",
                                        "human_zone_state_shadow": "WICK_TAGGED",
                                        "zone_rejection_context_reason": "WICK_TAGGED_CONTEXTUALLY_USABLE",
                                        "zone_still_contextually_usable": True,
                                        "score": 68,
                                    }
                                ],
                                "active_ob": {
                                    "type": "BEARISH",
                                    "fresh": True,
                                    "high": 2360.0,
                                    "low": 2350.0,
                                    "timeframe": "15m",
                                    "session_created": "NY",
                                    "imbalance_created": True,
                                    "liquidity_sweep_before": True,
                                    "is_extreme_ob": True,
                                },
                            }
                        },
                    },
                    "agent_5": {"score": 0, "direction": "LONG", "hard_filter_pass": False, "payload": {}},
                },
                "orchestrator": {"decision": "REJECT"},
                "reject_reason": "UNIT_REJECT",
            }

        with tempfile.TemporaryDirectory() as tmp:
            summary = await ReplayEngine(board, candles(1), output_root=tmp, run_id="p145", on_decision_hook=hook).run()

        self.assertIn("shadow_agent2_ict_poi_stack_analysis", summary)
        self.assertIn("shadow_agent2_poi_stack_to_orchestrator_analysis", summary)
        self.assertIn("shadow_poi_stack_outcome_analysis", summary)
        self.assertEqual(
            summary["shadow_agent2_ict_poi_stack_analysis"]["cases_legacy_reject_but_shadow_wick_tagged_poi"],
            1,
        )
        self.assertEqual(
            summary["shadow_agent2_poi_stack_to_orchestrator_analysis"]["candidate_micro_after"],
            1,
        )

    async def test_p1_45_summary_persists_compact_outcome_attribution(self) -> None:
        board = BlackBoard()
        sample = candles(6)
        sample[4]["high"] = 110.0
        sample[5]["high"] = 114.0

        def hook(candle, blackboard):
            del blackboard
            if candle["time"] != sample[3]["time"]:
                return {"reject_reason": "NO_SIGNAL"}
            return {
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
                        "reason": "NO_VALID_OB_SCORE_GE_60",
                        "payload": {
                            "diagnostic": {
                                "final_reason": "NO_VALID_OB_SCORE_GE_60",
                                "best_shadow_poi_type": "FVG_CONTINUATION_ALIGNED",
                                "best_shadow_poi_reason": "FVG_ALTERNATIVE_NO_MATURE_OB",
                                "best_shadow_poi": {
                                    "type": "FVG_CONTINUATION",
                                    "priority_label": "FVG_CONTINUATION_ALIGNED",
                                    "score_shadow": 80,
                                    "state_shadow": "FRESH",
                                    "filled_pct": 0.0,
                                    "distance_to_price": 4.0,
                                    "aligned_with_order_flow": True,
                                },
                                "shadow_agent2_poi_stack": [
                                    {
                                        "type": "FVG_CONTINUATION",
                                        "priority_label": "FVG_CONTINUATION_ALIGNED",
                                        "score_shadow": 80,
                                        "state_shadow": "FRESH",
                                        "reason": "FVG_CONTINUATION_ALIGNED",
                                    },
                                    {
                                        "type": "OB_CONTINUATION",
                                        "zone_type": "OB_CONTINUATION",
                                        "priority_label": "OB_CONTINUATION_WICK_TAGGED",
                                        "score_shadow": 65,
                                        "human_zone_state_shadow": "wick_tagged",
                                        "deepest_penetration_pct": 12.0,
                                        "close_inside_count": 0,
                                        "reason": "OB_CONTINUATION_WICK_TAGGED",
                                    }
                                ],
                                "active_ob": {
                                    "type": "BEARISH",
                                    "fresh": True,
                                    "high": 2360.0,
                                    "low": 2350.0,
                                    "timeframe": "15m",
                                    "session_created": "NY",
                                    "imbalance_created": True,
                                    "liquidity_sweep_before": True,
                                    "is_extreme_ob": True,
                                },
                            }
                        },
                    },
                    "agent_5": {
                        "score": 90,
                        "direction": "LONG",
                        "hard_filter_pass": True,
                        "payload": {
                            "shadow_ict_contract": {
                                "contextual_notes": {
                                    "trigger_kind": "MICRO_CHOCH",
                                    "delivery_phase": "TREND_CONTINUATION",
                                }
                            }
                        },
                    },
                    "agent_7": {
                        "score": 80,
                        "hard_filter_pass": True,
                        "payload": {
                            "session_name": "NY",
                            "shadow_ict_contract": {"contextual_notes": {"session_label": "NY"}},
                        },
                    },
                },
                "orchestrator": {"decision": "REJECT"},
                "reject_reason": "UNIT_REJECT",
            }

        with tempfile.TemporaryDirectory() as tmp:
            summary = await ReplayEngine(board, sample, output_root=tmp, run_id="p145_attr", on_decision_hook=hook).run()

        entries = summary["shadow_theoretical_entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["poi_type"], "FVG_CONTINUATION_ALIGNED")
        self.assertEqual(entries[0]["result"], "TP2")
        self.assertEqual(entries[0]["r_result"], 2.0)
        self.assertEqual(entries[0]["fvg_distance_bucket"], "0-5")
        self.assertEqual(entries[0]["shadow_tier"], "PREMIUM")
        self.assertEqual(summary["shadow_poi_stack_attribution_analysis"]["by_poi_type"]["FVG_CONTINUATION_ALIGNED"]["entries"], 1)
        self.assertEqual(summary["shadow_fvg_alternative_risk_analysis"]["total_fvg_entries"], 1)
        self.assertEqual(summary["shadow_session_risk_analysis"]["sessions"]["NY"]["entries"], 1)
        self.assertEqual(summary["shadow_agent5_trigger_quality_attribution"]["MICRO_CHOCH"]["entries"], 1)
        self.assertIn("shadow_professional_strategy_module_analysis", summary)
        self.assertIn("shadow_professional_strategy_selection_analysis", summary)
        self.assertIn("shadow_professional_strategy_outcome_analysis", summary)
        self.assertIn("shadow_strategy_ranking_summary", summary)
        self.assertIn("shadow_agent_decision_attribution_analysis", summary)
        self.assertIn("shadow_strategy_signal_semantics_summary", summary)
        self.assertIn("shadow_strategy_evaluation_summary", summary)
        self.assertIn("shadow_strategy_candidate_summary", summary)
        self.assertIn("shadow_strategy_selection_summary", summary)
        self.assertIn("shadow_executable_entry_summary", summary)
        self.assertIn("shadow_non_executable_signal_summary", summary)
        self.assertIn("shadow_trigger_none_block_summary", summary)
        self.assertIn("shadow_premium_gate_summary", summary)
        self.assertIn("shadow_gate_module_summary", summary)
        self.assertIn("shadow_ob_strategy_connectivity_summary", summary)
        self.assertIn("shadow_ob_lifecycle_mapping_summary", summary)
        self.assertIn("shadow_ob_candidate_block_reason_summary", summary)
        self.assertIn("shadow_ob_selector_competition_summary", summary)
        self.assertIn("shadow_ob_candidate_vs_selected_summary", summary)
        self.assertIn("shadow_ob_trigger_block_summary", summary)
        self.assertIn("shadow_agent2_ob_exposure_probe_summary", summary)
        self.assertIn("shadow_ob_exposure_funnel_summary", summary)
        self.assertIn("shadow_ob_raw_field_inventory_summary", summary)
        self.assertIn("shadow_ob_lifecycle_raw_distribution_summary", summary)
        self.assertIn("shadow_ob_attribution_discrepancy_summary", summary)
        self.assertIn("shadow_ob_probe_examples_summary", summary)
        self.assertIn("shadow_ob_five_star_scoring_summary", summary)
        self.assertIn("shadow_ob_five_star_breakdown_summary", summary)
        self.assertIn("shadow_ob_five_star_quality_tier_summary", summary)
        self.assertIn("shadow_ob_five_star_candidate_summary", summary)
        self.assertIn("shadow_ob_five_star_selector_competition_summary", summary)
        self.assertIn("shadow_ob_five_star_block_reason_summary", summary)
        self.assertIn("shadow_ob_five_star_examples_summary", summary)
        self.assertIn("shadow_ob_five_star_readiness_for_agent5_summary", summary)
        self.assertIn("shadow_ob_evidence_enrichment_summary", summary)
        self.assertIn("shadow_ob_evidence_coverage_summary", summary)
        self.assertIn("shadow_ob_five_star_after_enrichment_summary", summary)
        self.assertIn("shadow_ob_evidence_missing_reason_summary", summary)
        self.assertIn("shadow_ob_evidence_source_summary", summary)
        self.assertIn("shadow_ob_evidence_examples_summary", summary)
        self.assertIn("shadow_ob_no_lookahead_guard_summary", summary)
        self.assertIn("shadow_strategy_attribution_by_session", summary)
        self.assertIn("shadow_strategy_attribution_by_delivery_phase", summary)
        self.assertIn("shadow_strategy_attribution_by_draw_on_liquidity", summary)
        self.assertIn("shadow_strategy_attribution_by_order_flow", summary)
        self.assertIn("shadow_premium_strict_analysis", summary)
        self.assertIn("shadow_drawdown_guard_analysis", summary)
        self.assertIn("agent_1", summary["shadow_agent_decision_attribution_analysis"])
        self.assertIn("orchestrator", summary["shadow_agent_decision_attribution_analysis"])
        self.assertIn("strong_strategies_wr_60_plus", summary["shadow_strategy_ranking_summary"])
        semantics = summary["shadow_strategy_signal_semantics_summary"]
        self.assertLessEqual(semantics["executable_shadow_entry_count"], semantics["selected_count"])
        self.assertEqual(summary["shadow_trigger_none_block_summary"]["trigger_none_entries"], 0)
        ob_summary = summary["shadow_ob_strategy_connectivity_summary"]
        self.assertGreater(ob_summary["ob_poi_seen_total"], 0)
        self.assertGreater(ob_summary["ob_wick_tagged_seen"], 0)
        self.assertGreater(ob_summary["ob_strategy_evaluations"], 0)
        self.assertIn("OB_WICK_TAGGED_RETEST", ob_summary["by_strategy"])
        self.assertGreater(summary["shadow_agent2_ob_exposure_probe_summary"]["ob_poi_seen_total"], 0)
        active_export = summary["shadow_agent2_active_ob_export_summary"]
        self.assertGreater(active_export["active_ob_seen_total"], 0)
        self.assertGreater(active_export["active_ob_exported_total"], 0)
        routing = summary["shadow_ob_export_routing_summary"]
        self.assertGreater(routing["ob_added_to_strategy_input_poi_stack"], 0)
        self.assertGreater(routing["ob_seen_by_ob_modules"], 0)
        module_visibility = summary["shadow_ob_module_visibility_after_export_summary"]
        self.assertGreater(module_visibility["OB_WICK_TAGGED_RETEST_seen_ob"], 0)
        self.assertGreater(module_visibility["OB_PARTIAL_MITIGATION_WATCH_seen_ob"], 0)
        five_star = summary["shadow_ob_five_star_scoring_summary"]
        self.assertGreater(five_star["ob_scored_total"], 0)
        self.assertEqual(summary["shadow_ob_five_star_candidate_summary"]["executable_entries"], 0)
        enrichment = summary["shadow_ob_evidence_enrichment_summary"]
        self.assertGreater(enrichment["active_ob_enriched_total"], 0)
        self.assertEqual(summary["shadow_ob_no_lookahead_guard_summary"]["status"], "OK")
        after_enrichment = summary["shadow_ob_five_star_after_enrichment_summary"]
        self.assertGreater(after_enrichment["ob_scored_total"], 0)

    async def test_warmup_window_tags_events_and_excludes_warmup_signals(self) -> None:
        board = BlackBoard()
        sample = candles(4)

        def hook(candle, blackboard):
            del blackboard
            if candle["time"] in {sample[0]["time"], sample[2]["time"]}:
                return {
                    "score_final": 88,
                    "reason": "UNIT_SIGNAL",
                    "signal": {
                        "signal": "BUY",
                        "entry_price": 100.0,
                        "stop_loss": 95.0,
                        "tp1_price": 105.0,
                        "take_profit": 110.0,
                    },
                }
            return {"reject_reason": "NO_SIGNAL"}

        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(
                board,
                sample,
                output_root=tmp,
                run_id="warmup_eval",
                on_decision_hook=hook,
                warmup_start=sample[0]["time"],
                eval_start=sample[2]["time"],
                eval_end=sample[3]["time"],
            )
            summary = await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary["total_candles_processed"], 4)
        self.assertEqual(summary["warmup_candles"], 2)
        self.assertEqual(summary["eval_candles"], 2)
        self.assertEqual(summary["evaluation_summary"]["signals"], 0)
        self.assertEqual(summary["evaluation_summary"]["trades"], 0)
        self.assertEqual(summary["evaluation_summary"]["closed_trades"], 0)
        self.assertTrue(all("phase" in event and "eval_active" in event for event in events if event["event"] == "decision"))
        self.assertTrue(
            any(event["event"] == "p1_decision" and event["phase"] == "evaluation" and event["eval_active"] is True for event in events)
        )

    async def test_decision_events_sanitize_non_json_agent_payloads(self) -> None:
        board = BlackBoard()
        payload = {}
        payload["self"] = payload
        payload["numpy_bool_like"] = _NumpyBoolLike()

        def hook(candle, blackboard):
            del candle, blackboard
            return {"agents": {"agent_4": {"payload": payload}}, "reject_reason": "NO_SIGNAL"}

        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(1), output_root=tmp, run_id="json_safe", on_decision_hook=hook)
            await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        decision = next(event for event in events if event["event"] == "decision")
        agent_payload = decision["agents"]["agent_4"]["payload"]
        self.assertEqual(agent_payload["self"], "<circular>")
        self.assertTrue(agent_payload["numpy_bool_like"])

    async def test_tier_simulation_score_bands(self) -> None:
        for score, expected_tier, expected_risk in (
            (65.0, "CANDIDATE_MICRO", 0.5),
            (78.0, "STANDARD_PAPER", 1.0),
            (88.0, "PREMIUM_PAPER", 1.5),
        ):
            board = BlackBoard()

            async def hook(candle, blackboard):
                await blackboard.update_agent(
                    "agent_5",
                    {
                        "entry_price": 100.0,
                        "sl_price": 95.0,
                        "tp1_price": 105.0,
                        "tp2_price": 110.0,
                    },
                )
                return {
                    "score_final": score,
                    "reason": "UNIT_REJECT",
                    "orchestrator": {"decision": "REJECT", "score": score, "direction": "LONG"},
                }

            with tempfile.TemporaryDirectory() as tmp:
                tier_manager = SimulatedTradeManager(
                    board,
                    SimulatedTradeConfig(equity_initial=100.0, write_blackboard_positions=False, event_prefix="tier"),
                )
                engine = ReplayEngine(
                    board,
                    candles(1),
                    output_root=tmp,
                    run_id=f"tier_{score}",
                    on_decision_hook=hook,
                    tier_trade_manager=tier_manager,
                    tier_simulation=True,
                )
                summary = await engine.run()
                events = [
                    json.loads(line)
                    for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]

            self.assertTrue(any(event["event"] == "tier_candidate_created" and event["tier"] == expected_tier for event in events))
            open_event = next(event for event in events if event["event"] == "tier_trade_open")
            self.assertEqual(open_event["tier"], expected_tier)
            self.assertEqual(open_event["risk_pct"], expected_risk)
            self.assertEqual(summary["tier_summary"][expected_tier]["trades"], 1)

    async def test_tier_simulation_score_below_60_creates_no_trade(self) -> None:
        board = BlackBoard()

        def hook(candle, blackboard):
            del candle, blackboard
            return {"score_final": 59.9, "orchestrator": {"decision": "REJECT", "score": 59.9, "direction": "LONG"}}

        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(1), output_root=tmp, run_id="tier_low", on_decision_hook=hook, tier_simulation=True)
            summary = await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertFalse(any(event["event"].startswith("tier_") for event in events))
        self.assertEqual(sum(tier["trades"] for tier in summary["tier_summary"].values()), 0)

    async def test_tier_simulation_rejects_missing_levels(self) -> None:
        board = BlackBoard()

        def hook(candle, blackboard):
            del candle, blackboard
            return {"score_final": 65, "orchestrator": {"decision": "REJECT", "score": 65, "direction": "LONG"}}

        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(1), output_root=tmp, run_id="tier_missing", on_decision_hook=hook, tier_simulation=True)
            await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        rejected = next(event for event in events if event["event"] == "tier_trade_rejected")
        self.assertEqual(rejected["reason"], "MISSING_SL")

    async def test_tier_candidate_uses_agent_direction_current_price_and_agent2_sl(self) -> None:
        board = BlackBoard()

        async def hook(candle, blackboard):
            del candle
            await blackboard.update_agent("agent_1", {"direction": "LONG"})
            await blackboard.update_agent(
                "agent_2",
                {"poi_zone": {"entry_zone_bottom": 95.0, "entry_zone_top": 98.0, "type": "BULLISH"}},
            )
            return {"score_final": 65, "orchestrator": {"decision": "REJECT", "score": 65}}

        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(1), output_root=tmp, run_id="tier_fallback_levels", on_decision_hook=hook, tier_simulation=True)
            await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        open_event = next(event for event in events if event["event"] == "tier_trade_open")
        self.assertEqual(open_event["type"], "BUY")
        self.assertEqual(open_event["entry_price"], 115.5)
        self.assertAlmostEqual(open_event["original_sl"], 94.98)
        self.assertEqual(open_event["direction_source"], "agent_1")
        self.assertEqual(open_event["entry_source"], "current_close")
        self.assertEqual(open_event["sl_source"], "agent_2.poi_structure")

    async def test_tier_signal_rejects_missing_direction_entry_and_sl(self) -> None:
        tier = {"name": "CANDIDATE_MICRO", "risk_pct": 0.5}

        board = BlackBoard()
        await board.update_agent("agent_2", {"poi_zone": {"bottom": 95.0, "top": 98.0}})
        signal, reason = _tier_replay_trade_signal(
            {"score_final": 65},
            board,
            {"time": datetime(2024, 1, 1, tzinfo=timezone.utc), "close": 100.0},
            tier,
            65.0,
            100.0,
        )
        self.assertIsNone(signal)
        self.assertEqual(reason, "MISSING_DIRECTION")

        board = BlackBoard()
        await board.update_agent("agent_1", {"direction": "LONG"})
        await board.update_agent("agent_5", {"sl_price": 95.0})
        signal, reason = _tier_replay_trade_signal(
            {"score_final": 65},
            board,
            {"time": datetime(2024, 1, 1, tzinfo=timezone.utc)},
            tier,
            65.0,
            100.0,
        )
        self.assertIsNone(signal)
        self.assertEqual(reason, "MISSING_ENTRY")

        board = BlackBoard()
        await board.update_agent("agent_1", {"direction": "LONG"})
        signal, reason = _tier_replay_trade_signal(
            {"score_final": 65},
            board,
            {"time": datetime(2024, 1, 1, tzinfo=timezone.utc), "close": 100.0},
            tier,
            65.0,
            100.0,
        )
        self.assertIsNone(signal)
        self.assertEqual(reason, "MISSING_SL")

    async def test_replay_without_tier_flag_has_no_tier_events(self) -> None:
        board = BlackBoard()

        def hook(candle, blackboard):
            del candle, blackboard
            return {"score_final": 88, "orchestrator": {"decision": "REJECT", "score": 88, "direction": "LONG"}}

        with tempfile.TemporaryDirectory() as tmp:
            engine = ReplayEngine(board, candles(1), output_root=tmp, run_id="tier_disabled", on_decision_hook=hook)
            summary = await engine.run()
            events = [
                json.loads(line)
                for line in engine.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertFalse(summary["tier_simulation"])
        self.assertFalse(any(str(event["event"]).startswith("tier_") for event in events))


if __name__ == "__main__":
    unittest.main()
