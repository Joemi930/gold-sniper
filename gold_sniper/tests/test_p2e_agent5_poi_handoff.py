"""Phase 3 tests for Agent2 P2-A POI handoff into Agent5."""

from __future__ import annotations

import unittest

from agents.base_agent import AgentResult
from gold_sniper.agents.agent_5_microscope import _extract_agent2_p2a_poi_for_agent5


class _FakeBlackboard:
    def __init__(self, *, agent2_result: AgentResult | None = None, agent2_state: dict | None = None) -> None:
        self.agent2_result = agent2_result
        self.agent2_state = agent2_state or {}

    def read_sync(self, path: str):
        if path == "agent_results.agent_2":
            if self.agent2_result is None:
                raise KeyError(path)
            return self.agent2_result
        raise KeyError(path)

    def get_agent(self, key: str) -> dict:
        return self.agent2_state if key == "agent_2" else {}


def _p2a_poi(*, low: float = 2400.0, high: float = 2405.0, readiness: str = "READY") -> dict:
    return {
        "id": "poi-p2a",
        "poi_type_normalized": "OB",
        "price_bounds": {"low": low, "high": high},
        "execution_readiness": readiness,
    }


class TestP2eAgent5PoiHandoff(unittest.TestCase):
    def test_agent5_extracts_p2a_selected_poi_before_legacy(self) -> None:
        selected = _p2a_poi()
        legacy = {"bottom": 2300.0, "top": 2305.0, "type": "LEGACY"}
        result = AgentResult(
            agent_id="agent_2",
            score=80.0,
            hard_filter_pass=False,
            direction="LONG",
            reason="LEGACY_REJECT_WITH_POI",
            payload={
                "p2a_poi_connectivity": {
                    "selected_poi": selected,
                    "poi_candidates": [selected],
                    "audit": {"agent2_has_any_zone": True},
                }
            },
        )

        zone, diag = _extract_agent2_p2a_poi_for_agent5(
            _FakeBlackboard(agent2_result=result, agent2_state={"poi_zone": legacy})
        )

        self.assertIsNotNone(zone)
        self.assertEqual(diag["source"], "P2A_SELECTED_POI")
        self.assertEqual(zone["bottom"], 2400.0)
        self.assertEqual(zone["top"], 2405.0)
        self.assertEqual(zone["entry_zone_bottom"], 2400.0)
        self.assertEqual(zone["entry_zone_top"], 2405.0)
        self.assertEqual(zone["execution_readiness"], "READY")

    def test_agent5_falls_back_to_p2a_candidate_when_no_selected_poi(self) -> None:
        candidate = _p2a_poi(readiness="WAITING_TRIGGER")
        result = AgentResult(
            agent_id="agent_2",
            score=60.0,
            hard_filter_pass=False,
            direction="SHORT",
            reason="CANDIDATE_ONLY",
            payload={
                "p2a_poi_connectivity": {
                    "selected_poi": None,
                    "poi_candidates": [candidate],
                    "audit": {"agent2_has_any_zone": True},
                }
            },
        )

        zone, diag = _extract_agent2_p2a_poi_for_agent5(_FakeBlackboard(agent2_result=result))

        self.assertIsNotNone(zone)
        self.assertEqual(diag["source"], "P2A_CANDIDATE_FALLBACK")
        self.assertEqual(zone["bottom"], 2400.0)
        self.assertEqual(zone["execution_readiness"], "WAITING_TRIGGER")

    def test_agent5_legacy_fallback_only_when_p2a_absent(self) -> None:
        legacy = {"bottom": 2390.0, "top": 2395.0, "type": "OB", "execution_readiness": "READY"}
        result = AgentResult(
            agent_id="agent_2",
            score=0.0,
            hard_filter_pass=False,
            direction=None,
            reason="NO_P2A",
            payload={},
        )

        zone, diag = _extract_agent2_p2a_poi_for_agent5(
            _FakeBlackboard(agent2_result=result, agent2_state={"active_ob": legacy})
        )

        self.assertIsNotNone(zone)
        self.assertEqual(diag["source"], "LEGACY_AGENT2_FALLBACK")
        self.assertEqual(zone["bottom"], 2390.0)

    def test_agent5_returns_no_poi_when_no_p2a_and_no_legacy(self) -> None:
        zone, diag = _extract_agent2_p2a_poi_for_agent5(_FakeBlackboard())

        self.assertIsNone(zone)
        self.assertEqual(diag["source"], "NONE")
        self.assertEqual(diag["failure_reason"], "NO_SELECTED_POI_OR_BOUNDS")


if __name__ == "__main__":
    unittest.main()
