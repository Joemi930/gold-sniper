"""P2-E Phase 7F — Readiness contradiction tests.

Tests that readiness coherence blocks invalid states and that
news:WATCH_ONLY does not count as a violation (Phase 7E micro-fix).
"""

import unittest

from gold_sniper.strategy.contracts import (
    EvidenceBundle,
    ReadinessState,
    SetupType,
    TradeSide,
)
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.readiness import (
    _core_ready,
    _readiness_coherence_metadata,
    _section_is_non_ready_for_coherence,
    _strict_readiness_blocker,
    evaluate_readiness,
)
from gold_sniper.strategy.scorecard import evaluate_scorecard


def _bundle(**overrides):
    """Base bundle with all critical sections READY."""
    data = {
        "setup_type": SetupType.CONTINUATION_STRICT,
        "side": TradeSide.BUY,
        "context": {"direction": "BUY", "htf_aligned": True, "in_ote": True},
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "execution_readiness": "READY",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB", "score": 85},
            "price_bounds": {"low": 2400, "high": 2405},
        },
        "liquidity": {
            "liquidity_semantic_status": "LIQUIDITY_READY",
            "readiness_state": "READY",
            "sweep_detected": True,
        },
        "micro": {
            "micro_semantic_status": "MICRO_READY",
            "readiness_state": "READY",
            "displacement_present": True,
            "retest_confirmed": True,
            "trigger_inside_poi": True,
        },
        "news": {"news_clear": True, "impact_level": "NONE", "passed": True},
        "session": {
            "session": "LONDON", "trading_allowed": True,
            "session_grade": "HIGH", "passed": True,
        },
        "risk": {"passed": True},
        "raw": {
            "timing": {"readiness_state": "READY", "in_ote": True},
        },
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


def _readiness(bundle):
    veto = evaluate_hard_veto(bundle)
    scorecard = evaluate_scorecard(bundle, veto)
    return evaluate_readiness(bundle, scorecard, veto)


class TestReadinessContradictions(unittest.TestCase):
    """Contract: readiness must not be READY when critical evidence is missing."""

    def test_session_context_missing_prevents_ready(self):
        b = _bundle(session={})
        r = _readiness(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_risk_context_missing_prevents_ready(self):
        b = _bundle(risk={})
        r = _readiness(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_news_context_missing_prevents_ready(self):
        b = _bundle(news={})
        r = _readiness(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_liquidity_waiting_trigger_prevents_ready(self):
        b = _bundle(liquidity={
            "readiness_state": "WAITING_TRIGGER",
            "sweep_detected": False,
        })
        r = _readiness(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_timing_unavailable_prevents_ready(self):
        b = _bundle(raw={"timing": {"readiness_state": "UNAVAILABLE"}})
        r = _readiness(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_micro_waiting_trigger_prevents_ready(self):
        b = _bundle(micro={
            "readiness_state": "WAITING_TRIGGER",
            "displacement_present": True,
            "retest_confirmed": False,
        })
        r = _readiness(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_news_watch_only_allows_ready(self):
        """news=WATCH_ONLY with all critical sections READY → can be READY."""
        b = _bundle(news={
            "medium_impact_nearby": True,
            "news_clear": False,
            "impact_level": "MEDIUM",
            "passed": True,
        })
        r = _readiness(b)
        # news=WATCH_ONLY + all others READY should allow READY
        self.assertEqual(r.state, ReadinessState.READY)

    def test_news_watch_only_no_coherence_violation(self):
        """Phase 7E micro-fix: news:WATCH_ONLY must NOT count as violation."""
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "READY",
            "timing": "READY", "micro": "READY", "session": "READY",
            "risk": "READY", "news": "WATCH_ONLY",
        }
        from gold_sniper.strategy.contracts import ScoreCard, SetupGrade
        sc = ScoreCard(grade=SetupGrade.B, missing_evidence=[])
        meta = _readiness_coherence_metadata(section_states, sc)
        self.assertNotIn("news", meta["non_ready_sections"])
        self.assertTrue(meta["can_be_global_ready"])

    def test_ready_global_impossible_with_blockers(self):
        """READY global and missing_ready_blockers is impossible."""
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "READY",
            "timing": "READY", "micro": "READY", "session": "READY",
            "risk": "READY", "news": "READY",
        }
        from gold_sniper.strategy.contracts import ScoreCard, SetupGrade
        sc = ScoreCard(grade=SetupGrade.B, missing_evidence=["SESSION_CONTEXT_MISSING"])
        meta = _readiness_coherence_metadata(section_states, sc)
        self.assertFalse(meta["can_be_global_ready"])
        self.assertIn("SESSION_CONTEXT_MISSING", meta["missing_ready_blockers"])

    def test_section_is_non_ready_for_coherence(self):
        """Helper must exclude news:WATCH_ONLY."""
        self.assertFalse(_section_is_non_ready_for_coherence("news", "WATCH_ONLY"))
        self.assertTrue(_section_is_non_ready_for_coherence("news", "REJECT"))
        self.assertTrue(_section_is_non_ready_for_coherence("session", "WATCH_ONLY"))
        self.assertTrue(_section_is_non_ready_for_coherence("micro", "UNAVAILABLE"))


if __name__ == "__main__":
    unittest.main()
