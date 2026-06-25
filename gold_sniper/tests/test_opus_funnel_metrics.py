from __future__ import annotations

import unittest

from gold_sniper.replay.replay_metrics import build_replay_metrics


class TestOpusFunnelMetrics(unittest.TestCase):
    def test_opus_funnel_metrics_are_present_and_decoupled(self) -> None:
        metrics = build_replay_metrics(
            [
                _decision(
                    session="OFF_SESSION",
                    setup_type="OBSERVATION",
                    reason="SESSION_OFF_SESSION_NON_TRADABLE",
                    stage="session",
                    poi_status="POI_NOT_REACHED",
                    micro_status="MICRO_NOT_REACHED",
                ),
                _decision(
                    setup_type="OTE_CONFLUENCE",
                    reason="POI_QUALITY_REJECT_F",
                    stage="poi",
                    poi_status="POI_REJECT_OWN",
                    micro_status="NOT_EVALUATED_INHERITED_POI_REJECT",
                ),
                _decision(
                    setup_type="TREND_CONTINUATION",
                    reason="MICRO_CONFIRMATION_REJECT_F",
                    stage="micro",
                    poi_status="POI_ACCEPT",
                    micro_status="MICRO_REJECT_OWN",
                ),
                _decision(
                    setup_type="TREND_CONTINUATION",
                    reason="MICRO_CONFIRMATION_WATCH_D",
                    stage="micro",
                    poi_status="POI_WATCH_NEAR_MISS",
                    micro_status="MICRO_WATCH_NEAR_MISS",
                    near_miss=True,
                    best_status="SCENARIO_NEAR_MISS",
                ),
            ],
            symbol="XAUUSD",
            timeframe="15m",
            date_start="2026-04-01T00:00:00Z",
            date_end="2026-04-01T01:00:00Z",
            data_profile={},
        )
        self.assertIn("funnel_exit_stage_counts", metrics)
        self.assertEqual(metrics["off_session_count"], 1)
        self.assertEqual(metrics["session_context_unknown_count"], 0)
        self.assertEqual(metrics["poi_reject_own_count"], 1)
        self.assertEqual(metrics["micro_reject_inherited_count"], 1)
        self.assertEqual(metrics["micro_reject_own_count"], 1)
        self.assertEqual(metrics["micro_watch_near_miss_count"], 1)
        self.assertEqual(metrics["near_miss_count"], 1)
        self.assertFalse(metrics["phase_8_ready"])
        self.assertIn("ENTER_LT_30", metrics["phase_8_blocking_reasons"])

    def test_phase_8_ready_false_when_unknown_setup_or_session_unknown(self) -> None:
        metrics = build_replay_metrics(
            [
                _decision(session="UNKNOWN", setup_type="UNKNOWN", reason="SESSION_CONTEXT_UNKNOWN", stage="session"),
                _decision(setup_type="UNKNOWN", reason="POI_MISSING", stage="poi"),
            ],
            symbol="XAUUSD",
            timeframe="15m",
            date_start=None,
            date_end=None,
            data_profile={},
        )
        self.assertGreater(metrics["session_context_unknown_count"], 0)
        self.assertGreaterEqual(metrics["setup_type_unknown_rate"], 0.15)
        self.assertFalse(metrics["phase_8_ready"])
        self.assertIn("SESSION_CONTEXT_UNKNOWN_GT_0", metrics["phase_8_blocking_reasons"])
        self.assertIn("SETUP_TYPE_UNKNOWN_RATE_GTE_15_PCT", metrics["phase_8_blocking_reasons"])


def _decision(
    *,
    session: str = "NY_KILLZONE",
    setup_type: str = "OBSERVATION",
    reason: str = "PIPELINE_COMPLETE",
    stage: str = "complete",
    poi_status: str = "POI_NOT_REACHED",
    micro_status: str = "MICRO_NOT_REACHED",
    near_miss: bool = False,
    best_status: str = "SCENARIO_WAIT",
) -> dict:
    return {
        "timestamp": "2026-04-01T00:00:00Z",
        "month": "2026-04",
        "decision": "WAIT",
        "session": session,
        "setup_type": setup_type,
        "score": 0,
        "confidence": 0,
        "pipeline_stage": stage,
        "missing_conditions": [reason] if reason != "PIPELINE_COMPLETE" else [],
        "warnings": [],
        "funnel_exit_stage": stage,
        "funnel_exit_reason": reason,
        "funnel_near_miss": near_miss,
        "poi_status": poi_status,
        "micro_status": micro_status,
        "best_scenario": setup_type,
        "best_scenario_status": best_status,
        "htf_context_available": True,
        "dol_available": True,
        "liquidity_story_available": True,
        "poi_available": True,
        "premium_discount_available": True,
        "ote_available": True,
        "micro_available": True,
        "micro_trigger": False,
        "agent_1_quality": "HIGH",
        "agent_2_quality": "HIGH",
        "agent_3_quality": "HIGH",
        "agent_4_quality": "HIGH",
        "agent_5_quality": "HIGH",
    }


if __name__ == "__main__":
    unittest.main()
