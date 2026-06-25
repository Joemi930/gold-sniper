"""P2-E Phase 7D — Readiness coherence unit tests."""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, ReadinessState
from gold_sniper.strategy.readiness import (
    _core_ready,
    _strict_readiness_blocker,
    _timing_state,
    evaluate_readiness,
)
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.scorecard import evaluate_scorecard


def _ready_bundle(**overrides):
    """Fully ready synthetic bundle that should pass readiness coherence."""
    data = {
        "setup_type": "CONTINUATION_STRICT",
        "side": "BUY",
        "context": {
            "direction": "BUY",
            "htf_aligned": True,
            "in_ote": True,
            "premium_discount": "DISCOUNT",
        },
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB", "score": 80},
            "price_bounds": {"low": 2400, "high": 2405},
            "poi_quality_score": 80,
        },
        "liquidity": {
            "liquidity_semantic_status": "LIQUIDITY_READY",
            "readiness_state": "READY",
            "execution_readiness": "READY",
            "sweep_detected": True,
            "liquidity_quality_score": 80,
        },
        "micro": {
            "micro_semantic_status": "MICRO_READY",
            "readiness_state": "READY",
            "execution_readiness": "READY",
            "retest_confirmed": True,
            "displacement_present": True,
            "reclaim_confirmed": True,
            "trigger_inside_poi": True,
        },
        "news": {
            "news_clear": True,
            "impact_level": "NONE",
            "passed": True,
        },
        "session": {
            "trading_allowed": True,
            "session_grade": "HIGH",
            "session_label": "LONDON",
            "passed": True,
        },
        "risk": {
            "passed": True,
        },
        "raw": {
            "timing": {
                "readiness_state": "READY",
                "execution_readiness": "READY",
                "in_ote": True,
                "premium_discount": "DISCOUNT",
            }
        },
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


def _evaluate(bundle):
    veto = evaluate_hard_veto(bundle)
    scorecard = evaluate_scorecard(bundle, veto)
    return evaluate_readiness(bundle, scorecard, veto)


class TestReadinessCoherenceBlocks(unittest.TestCase):
    """Contract: missing critical sections prevent global READY."""

    def test_missing_session_prevents_ready(self):
        """Empty session section → SESSION_CONTEXT_MISSING → not READY."""
        b = _ready_bundle(session={})
        r = _evaluate(b)
        self.assertNotEqual(r.state, ReadinessState.READY)
        self.assertIn("SESSION_CONTEXT_MISSING", r.reason)

    def test_missing_risk_prevents_ready(self):
        b = _ready_bundle(risk={})
        r = _evaluate(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_missing_news_prevents_ready(self):
        b = _ready_bundle(news={})
        r = _evaluate(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_liquidity_not_ready_prevents_global_ready(self):
        b = _ready_bundle(liquidity={
            "readiness_state": "WAITING_TRIGGER",
            "execution_readiness": "WAITING_TRIGGER",
        })
        r = _evaluate(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_timing_not_ready_prevents_global_ready(self):
        b = _ready_bundle(raw={"timing": {"readiness_state": "WAITING_TRIGGER"}})
        r = _evaluate(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_micro_not_ready_prevents_global_ready(self):
        b = _ready_bundle(micro={
            "execution_readiness": "WAITING_TRIGGER",
            "readiness_state": "WAITING_TRIGGER",
        })
        r = _evaluate(b)
        self.assertNotEqual(r.state, ReadinessState.READY)

    def test_session_reject_leads_readiness_reject(self):
        b = _ready_bundle(session={
            "is_hard_block": True,
            "session": "TOKYO",
            "passed": False,
        })
        r = _evaluate(b)
        self.assertEqual(r.state, ReadinessState.REJECT)

    def test_risk_reject_leads_readiness_reject(self):
        b = _ready_bundle(risk={"passed": False, "max_daily_loss_hit": True})
        r = _evaluate(b)
        self.assertIn(r.state, {ReadinessState.REJECT, ReadinessState.UNAVAILABLE})

    def test_hard_veto_still_reject(self):
        b = _ready_bundle(session={
            "trading_allowed": False,
            "session_label": "TOKYO",
            "is_hard_block": True,
        })
        r = _evaluate(b)
        self.assertEqual(r.state, ReadinessState.REJECT)


class TestReadinessCoherenceReady(unittest.TestCase):
    """Contract: fully ready bundle CAN be READY."""

    def test_fully_ready_bundle_is_ready(self):
        b = _ready_bundle()
        r = _evaluate(b)
        self.assertEqual(r.state, ReadinessState.READY, f"Expected READY, got {r.state}: {r.reason}")

    def test_core_ready_all_sections(self):
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "READY",
            "timing": "READY", "micro": "READY", "session": "READY",
            "risk": "READY", "news": "WATCH_ONLY",
        }
        self.assertTrue(_core_ready(section_states))

    def test_core_ready_fails_when_timing_not_ready(self):
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "READY",
            "timing": "UNAVAILABLE", "micro": "READY", "session": "READY",
            "risk": "READY", "news": "READY",
        }
        self.assertFalse(_core_ready(section_states))

    def test_core_ready_fails_when_liquidity_not_ready(self):
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "WAITING_TRIGGER",
            "timing": "READY", "micro": "READY", "session": "READY",
            "risk": "READY", "news": "READY",
        }
        self.assertFalse(_core_ready(section_states))

    def test_core_ready_fails_when_session_not_ready(self):
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "READY",
            "timing": "READY", "micro": "READY", "session": "UNAVAILABLE",
            "risk": "READY", "news": "READY",
        }
        self.assertFalse(_core_ready(section_states))

    def test_core_ready_fails_when_risk_not_ready(self):
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "READY",
            "timing": "READY", "micro": "READY", "session": "READY",
            "risk": "UNAVAILABLE", "news": "READY",
        }
        self.assertFalse(_core_ready(section_states))

    def test_news_watch_only_does_not_block_ready(self):
        """news=WATCH_ONLY is acceptable for READY (soft news only)."""
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "READY",
            "timing": "READY", "micro": "READY", "session": "READY",
            "risk": "READY", "news": "WATCH_ONLY",
        }
        self.assertTrue(_core_ready(section_states))

    def test_news_reject_blocks_ready(self):
        section_states = {
            "context": "READY", "poi": "READY", "liquidity": "READY",
            "timing": "READY", "micro": "READY", "session": "READY",
            "risk": "READY", "news": "REJECT",
        }
        self.assertFalse(_core_ready(section_states))


class TestReadinessCoherenceMetadata(unittest.TestCase):
    """Contract: readiness_coherence metadata is present and correct."""

    def test_metadata_has_readiness_coherence(self):
        b = _ready_bundle()
        r = _evaluate(b)
        self.assertIn("readiness_coherence", r.metadata)
        rc = r.metadata["readiness_coherence"]
        self.assertIn("missing_ready_blockers", rc)
        self.assertIn("non_ready_sections", rc)
        self.assertIn("can_be_global_ready", rc)

    def test_fully_ready_can_be_global_ready(self):
        b = _ready_bundle()
        r = _evaluate(b)
        self.assertTrue(r.metadata["readiness_coherence"]["can_be_global_ready"])

    def test_missing_session_cannot_be_global_ready(self):
        b = _ready_bundle(session={})
        r = _evaluate(b)
        rc = r.metadata["readiness_coherence"]
        self.assertFalse(rc["can_be_global_ready"])
        self.assertIn("SESSION_CONTEXT_MISSING", rc["missing_ready_blockers"])

    def test_missing_risk_cannot_be_global_ready(self):
        b = _ready_bundle(risk={})
        r = _evaluate(b)
        rc = r.metadata["readiness_coherence"]
        self.assertFalse(rc["can_be_global_ready"])


class TestTimingState(unittest.TestCase):
    """Contract: _timing_state resolves correctly."""

    def test_explicit_ready(self):
        b = _ready_bundle(raw={"timing": {"readiness_state": "READY"}})
        self.assertEqual(_timing_state(b), "READY")

    def test_explicit_waiting_trigger(self):
        b = _ready_bundle(raw={"timing": {"execution_readiness": "WAITING_TRIGGER"}})
        self.assertEqual(_timing_state(b), "WAITING_TRIGGER")

    def test_in_ote_fallback(self):
        b = _ready_bundle(
            context={"direction": "BUY", "in_ote": True},
            raw={"timing": {}},
        )
        self.assertEqual(_timing_state(b), "READY")

    def test_no_timing_unavailable(self):
        b = _ready_bundle(context={}, raw={"timing": {}})
        self.assertEqual(_timing_state(b), "UNAVAILABLE")

    def test_timing_reject(self):
        b = _ready_bundle(raw={"timing": {"readiness_state": "REJECT"}})
        self.assertEqual(_timing_state(b), "REJECT")

    def test_timing_invalid(self):
        b = _ready_bundle(raw={"timing": {"readiness_state": "INVALID"}})
        self.assertEqual(_timing_state(b), "INVALID")


class TestStrictReadinessBlocker(unittest.TestCase):
    """Contract: _strict_readiness_blocker catches missing evidence."""

    def test_context_missing_blocks(self):
        from gold_sniper.strategy.contracts import ScoreCard, SetupGrade
        sc = ScoreCard(grade=SetupGrade.D, missing_evidence=["CONTEXT_MISSING"])
        block = _strict_readiness_blocker({"context": "UNAVAILABLE"}, sc)
        self.assertIsNotNone(block)
        self.assertEqual(block[2], "CONTEXT_MISSING_NOT_READY")

    def test_session_context_missing_blocks(self):
        from gold_sniper.strategy.contracts import ScoreCard, SetupGrade
        sc = ScoreCard(grade=SetupGrade.D, missing_evidence=["SESSION_CONTEXT_MISSING"])
        block = _strict_readiness_blocker({"session": "UNAVAILABLE"}, sc)
        self.assertIsNotNone(block)

    def test_risk_context_missing_blocks(self):
        from gold_sniper.strategy.contracts import ScoreCard, SetupGrade
        sc = ScoreCard(grade=SetupGrade.D, missing_evidence=["RISK_CONTEXT_MISSING"])
        block = _strict_readiness_blocker({"risk": "UNAVAILABLE"}, sc)
        self.assertIsNotNone(block)

    def test_clean_scorecard_no_block(self):
        from gold_sniper.strategy.contracts import ScoreCard, SetupGrade
        sc = ScoreCard(grade=SetupGrade.B, missing_evidence=[])
        all_ready = {k: "READY" for k in ("context", "poi", "liquidity", "timing", "micro", "session", "risk")}
        all_ready["news"] = "WATCH_ONLY"
        block = _strict_readiness_blocker(all_ready, sc)
        self.assertIsNone(block)


if __name__ == "__main__":
    unittest.main()
