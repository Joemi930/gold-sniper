from __future__ import annotations

import unittest

from gold_sniper.strategy.contracts import DecisionAction, EvidenceBundle, ReadinessState, SetupType, TradeSide
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.readiness import evaluate_readiness
from gold_sniper.strategy.scorecard import evaluate_scorecard


def _bundle(**overrides) -> EvidenceBundle:
    data = {
        "setup_type": SetupType.CONTINUATION_LIGHT,
        "side": TradeSide.BUY,
        "context": {"direction": "BUY", "htf_aligned": True, "draw_on_liquidity": "PDH"},
        "poi": {"execution_readiness": "READY", "selected_poi": {"score": 75}, "price_bounds": {"low": 1990, "high": 2000}, "lifecycle_normalized": "FRESH"},
        "liquidity": {"sweep_detected": True},
        "micro": {"displacement_present": True, "retest_confirmed": True, "trigger_inside_poi": True},
        "news": {"news_clear": True, "impact_level": "NONE"},
        "session": {"session": "LONDON", "trading_allowed": True, "session_grade": "HIGH"},
        "risk": {"passed": True},
        "raw": {"timing": {"readiness_state": "READY", "in_ote": True}},
    }
    data.update(overrides)
    return EvidenceBundle(**data)


def _readiness(bundle: EvidenceBundle):
    veto = evaluate_hard_veto(bundle)
    scorecard = evaluate_scorecard(bundle, veto)
    return evaluate_readiness(bundle, scorecard, veto)


class TestP2dReadiness(unittest.TestCase):
    def test_hard_veto_news_rejects(self):
        result = _readiness(_bundle(news={"high_impact_window": True}))

        self.assertEqual(result.state, ReadinessState.REJECT)
        self.assertEqual(result.suggested_action, DecisionAction.REJECT)

    def test_replay_invalid_is_invalid(self):
        result = _readiness(_bundle(raw={"replay_invalid": True}))

        self.assertEqual(result.state, ReadinessState.INVALID)
        self.assertEqual(result.suggested_action, DecisionAction.REJECT)

    def test_poi_absent_context_interesting_waits_for_better_price(self):
        result = _readiness(_bundle(poi={}))

        self.assertEqual(result.state, ReadinessState.WAITING_POI)
        self.assertEqual(result.suggested_action, DecisionAction.WAIT_FOR_BETTER_PRICE)

    def test_poi_ready_micro_absent_waits_for_trigger(self):
        result = _readiness(_bundle(micro={}))

        self.assertEqual(result.state, ReadinessState.WAITING_TRIGGER)
        self.assertEqual(result.suggested_action, DecisionAction.WAIT_FOR_TRIGGER)

    def test_medium_poi_is_watch_only(self):
        result = _readiness(_bundle(poi={"execution_readiness": "READY", "selected_poi": {"score": 30}, "price_bounds": {"low": 1, "high": 2}}))

        self.assertEqual(result.state, ReadinessState.WATCH_ONLY)

    def test_dead_poi_rejects(self):
        for poi in (
            {"execution_readiness": "READY", "selected_poi": {"score": 80}, "price_bounds": {"low": 1, "high": 2}, "lifecycle_normalized": "INVALIDATED"},
            {"execution_readiness": "READY", "selected_poi": {"score": 80}, "price_bounds": {"low": 1, "high": 2}, "lifecycle_normalized": "CONSUMED"},
            {"execution_readiness": "READY", "selected_poi": {"score": 80}, "price_bounds": {"low": 1, "high": 2}, "mitigation_pct": 51.0},
        ):
            with self.subTest(poi=poi):
                result = _readiness(_bundle(poi=poi))
                self.assertEqual(result.state, ReadinessState.REJECT)

    def test_core_ready(self):
        result = _readiness(_bundle())

        self.assertEqual(result.state, ReadinessState.READY)


if __name__ == "__main__":
    unittest.main()
