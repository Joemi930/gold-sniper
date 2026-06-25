from __future__ import annotations

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, SetupType, TradeSide
from gold_sniper.strategy.professional_decision_engine import evaluate_professional_decision


def _bundle(**overrides) -> EvidenceBundle:
    data = {
        "setup_type": SetupType.CONTINUATION_LIGHT,
        "side": TradeSide.BUY,
        "context": {"direction": "BUY", "htf_aligned": True, "draw_on_liquidity": "PDH", "in_ote": True, "premium_discount": "DISCOUNT"},
        "poi": {"execution_readiness": "READY", "selected_poi": {"score": 80}, "price_bounds": {"low": 1990, "high": 2000}, "lifecycle_normalized": "FRESH"},
        "liquidity": {"sweep_detected": True},
        "micro": {"displacement_present": True, "reclaim_confirmed": True, "retest_confirmed": True, "trigger_inside_poi": True, "score": 90},
        "news": {"news_clear": True, "impact_level": "NONE"},
        "session": {"session": "LONDON", "trading_allowed": True, "session_grade": "HIGH"},
        "risk": {"passed": True},
    }
    data.update(overrides)
    return EvidenceBundle(**data)


class TestP2dPdeDecisionStates(unittest.TestCase):
    def test_context_interesting_poi_absent_waits_for_better_price(self):
        result = evaluate_professional_decision(_bundle(poi={}))

        self.assertEqual(result.decision, "WAIT_FOR_BETTER_PRICE")
        self.assertEqual(result.readiness_state, "WAITING_POI")

    def test_poi_ready_micro_missing_waits_for_trigger(self):
        result = evaluate_professional_decision(_bundle(micro={}))

        self.assertEqual(result.decision, "WAIT_FOR_TRIGGER")
        self.assertEqual(result.readiness_state, "WAITING_TRIGGER")

    def test_medium_poi_watch_only(self):
        result = evaluate_professional_decision(_bundle(poi={"execution_readiness": "READY", "selected_poi": {"score": 30}, "price_bounds": {"low": 1, "high": 2}}))

        self.assertEqual(result.decision, "WATCH_ONLY")
        self.assertEqual(result.readiness_state, "WATCH_ONLY")

    def test_news_high_impact_stays_hard_reject(self):
        result = evaluate_professional_decision(_bundle(news={"high_impact_window": True}))

        self.assertEqual(result.decision, "REJECT")
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "NEWS_HIGH_IMPACT_WINDOW")

    def test_tokyo_session_stays_hard_reject(self):
        result = evaluate_professional_decision(_bundle(session={"session": "TOKYO", "trading_allowed": False}))

        self.assertEqual(result.decision, "REJECT")
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "SESSION_TOKYO_ASIA_BLOCK")

    def test_ready_low_score_watch_only_not_forced_enter(self):
        result = evaluate_professional_decision(_bundle(setup_type=SetupType.UNKNOWN))

        self.assertEqual(result.readiness_state, "READY")
        self.assertEqual(result.decision, "WATCH_ONLY")

    def test_ready_score_sufficient_enters(self):
        result = evaluate_professional_decision(_bundle())

        self.assertEqual(result.readiness_state, "READY")
        self.assertIn(result.decision, {"ENTER_REDUCED", "ENTER_FULL"})


if __name__ == "__main__":
    unittest.main()
