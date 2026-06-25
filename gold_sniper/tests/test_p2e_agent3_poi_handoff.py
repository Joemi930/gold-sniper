"""Phase 4 tests for Agent2 P2-A POI handoff into Agent3 liquidity."""

from __future__ import annotations

import unittest

from agents.base_agent import AgentResult
from gold_sniper.agents.agent_3_liquidite import _extract_agent2_p2a_liquidity_anchor


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


class TestP2eAgent3PoiHandoff(unittest.TestCase):
    def test_agent3_extracts_p2a_selected_poi_before_legacy(self) -> None:
        selected = _p2a_poi()
        legacy = {"bottom": 2300.0, "top": 2305.0, "type": "LEGACY"}
        result = AgentResult(
            agent_id="agent_2",
            score=0.0,
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

        anchor, diag = _extract_agent2_p2a_liquidity_anchor(
            _FakeBlackboard(agent2_result=result, agent2_state={"poi_zone": legacy})
        )

        self.assertIsNotNone(anchor)
        self.assertEqual(diag["source"], "P2A_SELECTED_POI")
        self.assertEqual(anchor["bottom"], 2400.0)
        self.assertEqual(anchor["top"], 2405.0)
        self.assertEqual(anchor["entry_zone_bottom"], 2400.0)
        self.assertEqual(anchor["entry_zone_top"], 2405.0)
        self.assertEqual(anchor["execution_readiness"], "READY")

    def test_agent3_falls_back_to_p2a_candidate_when_no_selected_poi(self) -> None:
        candidate = _p2a_poi(readiness="WAITING_TRIGGER")
        result = AgentResult(
            agent_id="agent_2",
            score=0.0,
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

        anchor, diag = _extract_agent2_p2a_liquidity_anchor(_FakeBlackboard(agent2_result=result))

        self.assertIsNotNone(anchor)
        self.assertEqual(diag["source"], "P2A_CANDIDATE_FALLBACK")
        self.assertEqual(anchor["bottom"], 2400.0)
        self.assertEqual(anchor["execution_readiness"], "WAITING_TRIGGER")

    def test_agent3_legacy_fallback_only_when_p2a_absent(self) -> None:
        legacy = {"bottom": 2390.0, "top": 2395.0, "type": "OB", "execution_readiness": "READY"}
        result = AgentResult(
            agent_id="agent_2",
            score=0.0,
            hard_filter_pass=False,
            direction=None,
            reason="NO_P2A",
            payload={},
        )

        anchor, diag = _extract_agent2_p2a_liquidity_anchor(
            _FakeBlackboard(agent2_result=result, agent2_state={"active_ob": legacy})
        )

        self.assertIsNotNone(anchor)
        self.assertEqual(diag["source"], "LEGACY_AGENT2_FALLBACK")
        self.assertEqual(anchor["bottom"], 2390.0)

    def test_agent3_returns_no_poi_when_no_p2a_and_no_legacy(self) -> None:
        anchor, diag = _extract_agent2_p2a_liquidity_anchor(_FakeBlackboard())

        self.assertIsNone(anchor)
        self.assertEqual(diag["source"], "NONE")
        self.assertEqual(diag["failure_reason"], "NO_P2A_POI_OR_BOUNDS")

    def test_agent3_normalizes_selected_poi_bounds(self) -> None:
        result = AgentResult(
            agent_id="agent_2",
            score=0.0,
            hard_filter_pass=False,
            direction="LONG",
            reason="P2A",
            payload={
                "p2a_poi_connectivity": {
                    "selected_poi": {
                        "poi_type": "FVG",
                        "price_bounds": {"high": 2410.0, "low": 2400.0},
                    },
                    "poi_candidates": [],
                }
            },
        )

        anchor, diag = _extract_agent2_p2a_liquidity_anchor(_FakeBlackboard(agent2_result=result))

        self.assertIsNotNone(anchor)
        self.assertEqual(diag["source"], "P2A_SELECTED_POI")
        self.assertEqual(anchor["bottom"], 2400.0)
        self.assertEqual(anchor["top"], 2410.0)
        self.assertEqual(anchor["entry_zone_bottom"], 2400.0)
        self.assertEqual(anchor["entry_zone_top"], 2410.0)


if __name__ == "__main__":
    unittest.main()
