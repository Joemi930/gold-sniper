"""Phase 3 tests for Agent5 micro readiness classification."""

from __future__ import annotations

import unittest

from agents.base_agent import AgentResult
from gold_sniper.agents.agent_5_microscope import (
    AMD_ACCUMULATION_WINDOW,
    AMD_MAX_CHOCH_DELAY,
    _micro_readiness_from_agent5_result,
)


def _poi() -> dict:
    return {"bottom": 2400.0, "top": 2405.0, "execution_readiness": "READY"}


def _candles(count: int) -> list[dict]:
    return [
        {"open": 2401.0, "high": 2402.0, "low": 2400.0, "close": 2401.5}
        for _ in range(count)
    ]


def _result(reason: str, *, hard_filter_pass: bool = False, payload: dict | None = None) -> AgentResult:
    return AgentResult(
        agent_id="agent_5",
        score=0.0,
        hard_filter_pass=hard_filter_pass,
        direction="LONG",
        reason=reason,
        payload=payload or {},
    )


class TestP2eAgent5MicroReadiness(unittest.TestCase):
    def test_poi_present_but_insufficient_1m_candles_is_unavailable(self) -> None:
        state, reason = _micro_readiness_from_agent5_result(
            _result("NOT_ENOUGH_1M_CANDLES"),
            _poi(),
            _candles(AMD_ACCUMULATION_WINDOW - 1),
        )

        self.assertEqual(state, "UNAVAILABLE")
        self.assertEqual(reason, "MICRO_INSUFFICIENT_1M_CANDLES")

    def test_poi_present_but_direction_missing_is_unavailable(self) -> None:
        state, reason = _micro_readiness_from_agent5_result(
            _result("NO_DIRECTION_FROM_AGENT_1"),
            _poi(),
            _candles(AMD_ACCUMULATION_WINDOW + AMD_MAX_CHOCH_DELAY),
        )

        self.assertEqual(state, "UNAVAILABLE")
        self.assertEqual(reason, "MICRO_NO_DIRECTION")

    def test_poi_present_but_no_trigger_waits_for_trigger(self) -> None:
        state, reason = _micro_readiness_from_agent5_result(
            _result("NO_TRIGGER"),
            _poi(),
            _candles(AMD_ACCUMULATION_WINDOW + AMD_MAX_CHOCH_DELAY),
        )

        self.assertEqual(state, "WAIT_FOR_TRIGGER")
        self.assertEqual(reason, "MICRO_NO_TRIGGER_YET")

    def test_sweep_without_choch_waits_for_trigger(self) -> None:
        state, reason = _micro_readiness_from_agent5_result(
            _result("CHoCH_SANS_SWEEP - risque de fausse cassure", payload={"sweep_1m_confirmed": True}),
            _poi(),
            _candles(AMD_ACCUMULATION_WINDOW + AMD_MAX_CHOCH_DELAY),
        )

        self.assertEqual(state, "WAIT_FOR_TRIGGER")
        self.assertEqual(reason, "MICRO_SWEEP_WAITING_CHOCH")

    def test_sweep_and_choch_with_hard_filter_pass_is_ready(self) -> None:
        state, reason = _micro_readiness_from_agent5_result(
            _result(
                "AMD_COMPLET",
                hard_filter_pass=True,
                payload={"sweep_1m_confirmed": True, "choch_detected": True},
            ),
            _poi(),
            _candles(AMD_ACCUMULATION_WINDOW + AMD_MAX_CHOCH_DELAY),
        )

        self.assertEqual(state, "READY")
        self.assertEqual(reason, "MICRO_READY")


if __name__ == "__main__":
    unittest.main()
