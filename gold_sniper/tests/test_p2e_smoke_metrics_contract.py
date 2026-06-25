"""P2-E Fix 1 metrics contract tests for POI handoff smoke summaries."""

from __future__ import annotations

import unittest

from gold_sniper.replay.replay_metrics import build_p1_replay_metrics


def _decision(
    poi: dict,
    micro: dict | None = None,
    liquidity: dict | None = None,
    timing: dict | None = None,
) -> dict:
    return {
        "decision": "WATCH_ONLY",
        "setup_grade": "C",
        "readiness_state": "WATCH_ONLY",
        "readiness_reason": "TEST",
        "p1_evidence_bundle": {
            "poi": poi,
            "micro": micro or {},
            "liquidity": liquidity or {},
            "raw": {"timing": timing or {}},
        },
    }


class TestP2eSmokeMetricsContract(unittest.TestCase):
    def test_candidates_without_selected_count_as_any_poi(self) -> None:
        metrics = build_p1_replay_metrics([
            _decision({
                "poi_available": True,
                "selected_poi": None,
                "selected_poi_present": False,
                "price_bounds": None,
                "has_price_bounds": False,
                "poi_candidates": [
                    {
                        "poi_type_normalized": "OB",
                        "lifecycle_normalized": "FRESH",
                        "execution_readiness": "WAITING_TRIGGER",
                    }
                ],
                "poi_type_normalized": "OB",
                "execution_readiness": "WAITING_TRIGGER",
                "poi_semantic_available": True,
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "poi_failure_class": "POI_CANDIDATES_ONLY",
                "connectivity_audit": {"agent2_has_any_zone": True},
            })
        ])

        self.assertEqual(metrics["records_with_any_poi"], 1)
        self.assertEqual(metrics["records_with_selected_poi"], 0)
        self.assertEqual(metrics["records_with_price_bounds"], 0)
        self.assertEqual(metrics["poi_readiness_distribution"]["WAITING_TRIGGER"], 1)
        self.assertEqual(metrics["poi_type_distribution"]["OB"], 1)
        self.assertEqual(metrics["records_with_poi_semantic_available"], 1)
        self.assertEqual(metrics["poi_semantic_status_distribution"]["POI_PRESENT_WAITING_TRIGGER"], 1)
        self.assertEqual(metrics["poi_failure_class_distribution"]["POI_CANDIDATES_ONLY"], 1)

    def test_selected_poi_counts_selected_and_bounds(self) -> None:
        metrics = build_p1_replay_metrics([
            _decision({
                "poi_available": True,
                "selected_poi": {"poi_type_normalized": "FVG"},
                "selected_poi_present": True,
                "price_bounds": {"low": 2400.0, "high": 2405.0},
                "has_price_bounds": True,
                "poi_candidates": [],
                "poi_type_normalized": "FVG",
                "execution_readiness": "READY",
                "poi_semantic_available": True,
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "poi_failure_class": "POI_SELECTED_READY",
                "connectivity_audit": {
                    "agent2_has_any_zone": True,
                    "selected_poi_present": True,
                    "poi_bounds_present": True,
                },
            })
        ])

        self.assertEqual(metrics["records_with_any_poi"], 1)
        self.assertEqual(metrics["records_with_selected_poi"], 1)
        self.assertEqual(metrics["records_with_price_bounds"], 1)
        self.assertEqual(metrics["p2a_connectivity"]["records_with_any_poi"], 1)
        self.assertEqual(metrics["poi_type_distribution"]["FVG"], 1)
        self.assertEqual(metrics["records_with_poi_semantic_available"], 1)
        self.assertEqual(metrics["poi_semantic_status_distribution"]["POI_PRESENT_EXECUTABLE"], 1)
        self.assertEqual(metrics["poi_failure_class_distribution"]["POI_SELECTED_READY"], 1)

    def test_agent5_micro_handoff_metrics_are_exposed(self) -> None:
        metrics = build_p1_replay_metrics([
            _decision(
                {
                    "poi_available": True,
                    "selected_poi": {"poi_type_normalized": "OB"},
                    "selected_poi_present": True,
                    "price_bounds": {"low": 2400.0, "high": 2405.0},
                    "has_price_bounds": True,
                    "poi_type_normalized": "OB",
                    "execution_readiness": "READY",
                    "connectivity_audit": {"agent2_has_any_zone": True},
                },
                {
                    "readiness_state": "WAIT_FOR_TRIGGER",
                    "readiness_reason": "MICRO_NO_TRIGGER_YET",
                    "micro_handoff_status": "P2A_POI_CONSUMED",
                    "agent5_poi_handoff": {"source": "P2A_SELECTED_POI"},
                    "agent5_consumed_poi": {"present": True, "bottom": 2400.0, "top": 2405.0},
                },
            )
        ])

        self.assertEqual(metrics["records_with_agent5_poi_consumed"], 1)
        self.assertEqual(metrics["agent5_poi_handoff_source_distribution"]["P2A_SELECTED_POI"], 1)
        self.assertEqual(metrics["agent5_micro_handoff_status_distribution"]["P2A_POI_CONSUMED"], 1)
        self.assertEqual(metrics["agent5_readiness_distribution"]["WAIT_FOR_TRIGGER"], 1)
        self.assertEqual(metrics["agent5_readiness_reason_distribution"]["MICRO_NO_TRIGGER_YET"], 1)
        self.assertEqual(metrics["records_with_micro_wait_for_trigger"], 1)

    def test_agent3_liquidity_handoff_metrics_are_exposed(self) -> None:
        metrics = build_p1_replay_metrics([
            _decision(
                {
                    "poi_available": True,
                    "selected_poi": {"poi_type_normalized": "OB"},
                    "selected_poi_present": True,
                    "price_bounds": {"low": 2400.0, "high": 2405.0},
                    "has_price_bounds": True,
                    "poi_type_normalized": "OB",
                    "execution_readiness": "READY",
                    "connectivity_audit": {"agent2_has_any_zone": True},
                },
                liquidity={
                    "liquidity_state": "NONE",
                    "readiness_state": "WAIT_FOR_TRIGGER",
                    "readiness_reason": "LIQUIDITY_WAITING_SWEEP",
                    "liquidity_handoff_status": "P2A_POI_CONSUMED",
                    "agent3_poi_handoff": {"source": "P2A_SELECTED_POI"},
                    "agent3_consumed_poi": {"present": True, "bottom": 2400.0, "top": 2405.0},
                    "reason": "NO_SWEEP_DETECTED",
                },
            )
        ])

        self.assertEqual(metrics["records_with_agent3_poi_consumed"], 1)
        self.assertEqual(metrics["agent3_poi_handoff_source_distribution"]["P2A_SELECTED_POI"], 1)
        self.assertEqual(metrics["agent3_liquidity_handoff_status_distribution"]["P2A_POI_CONSUMED"], 1)
        self.assertEqual(metrics["agent3_readiness_distribution"]["WAIT_FOR_TRIGGER"], 1)
        self.assertEqual(metrics["agent3_readiness_reason_distribution"]["LIQUIDITY_WAITING_SWEEP"], 1)
        self.assertEqual(metrics["liquidity_state_distribution"]["NONE"], 1)
        self.assertEqual(metrics["liquidity_reason_distribution"]["NO_SWEEP_DETECTED"], 1)

    def test_agent4_timing_handoff_metrics_are_exposed(self) -> None:
        metrics = build_p1_replay_metrics([
            _decision(
                {
                    "poi_available": True,
                    "selected_poi": {"poi_type_normalized": "OB"},
                    "selected_poi_present": True,
                    "price_bounds": {"low": 2400.0, "high": 2405.0},
                    "has_price_bounds": True,
                    "poi_type_normalized": "OB",
                    "execution_readiness": "READY",
                    "connectivity_audit": {"agent2_has_any_zone": True},
                },
                timing={
                    "in_ote": False,
                    "readiness_state": "WAIT_FOR_TRIGGER",
                    "readiness_reason": "OTE_WAITING_PRICE",
                    "ote_handoff_status": "P2A_POI_CONSUMED",
                    "agent4_poi_handoff": {"source": "P2A_SELECTED_POI"},
                    "agent4_consumed_poi": {"present": True, "bottom": 2400.0, "top": 2405.0},
                    "reason": "IN_CORRECT_ZONE_BUT_NOT_YET_IN_OTE - Attendre",
                },
            )
        ])

        self.assertEqual(metrics["records_with_agent4_poi_consumed"], 1)
        self.assertEqual(metrics["agent4_poi_handoff_source_distribution"]["P2A_SELECTED_POI"], 1)
        self.assertEqual(metrics["agent4_ote_handoff_status_distribution"]["P2A_POI_CONSUMED"], 1)
        self.assertEqual(metrics["agent4_readiness_distribution"]["WAIT_FOR_TRIGGER"], 1)
        self.assertEqual(metrics["agent4_readiness_reason_distribution"]["OTE_WAITING_PRICE"], 1)
        self.assertEqual(metrics["ote_state_distribution"]["WAITING_OTE"], 1)


class TestP2ePhase7bEnterEligibilityContract(unittest.TestCase):
    """Phase 7B requires enter eligibility fields in replay metrics output."""
    REQUIRED_ENTER_ELIGIBILITY_KEYS = {
        "enter_eligible_count",
        "enter_eligible_rate",
        "enter_eligibility_reason_distribution",
        "enter_eligibility_blocker_distribution",
        "enter_eligible_by_setup_type",
        "enter_eligible_by_grade",
        "risk_preview_positive_count",
        "risk_preview_reason_distribution",
    }

    def test_build_replay_metrics_exposes_enter_eligibility(self):
        from gold_sniper.replay.replay_metrics import build_replay_metrics
        decisions = [{
            "decision": "WATCH_ONLY",
            "setup_type": "POI_REACTION",
            "setup_family": "REACTION",
            "setup_classification_reason": "TEST",
            "setup_classification_confidence": 0.45,
            "setup_grade": "D",
            "readiness_state": "WATCH_ONLY",
            "readiness_reason": "TEST",
            "readiness_by_section": {},
            "enter_eligible": False,
            "enter_eligibility_reason": "RISK_NOT_ALLOWED",
            "enter_eligibility_blockers": ["RISK_NOT_ALLOWED"],
            "risk_preview": {"allowed": False, "risk_pct": 0.0, "reason": "ZERO_RISK"},
            "missing_evidence": [],
            "soft_issues": [],
            "risk_multiplier": 0.0,
        }]
        metrics = build_replay_metrics(
            decisions,
            symbol="XAUUSD", timeframe="1m",
            date_start="2026-01-01", date_end="2026-01-02",
            data_profile={},
        )
        for key in self.REQUIRED_ENTER_ELIGIBILITY_KEYS:
            self.assertIn(key, metrics, f"Missing Phase 7B metric: {key}")


if __name__ == "__main__":
    unittest.main()
