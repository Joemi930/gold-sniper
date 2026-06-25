"""P2-E Phase 7A — Setup taxonomy classifier unit tests."""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, SetupType, TradeSide
from gold_sniper.strategy.setup_taxonomy import (
    SetupClassification,
    classify_setup,
    get_setup_requirement,
    resolve_setup_type,
)


def _bundle(**overrides):
    """Build a minimal EvidenceBundle with sensible defaults for testing."""
    data: dict = {
        "setup_type": "UNKNOWN",
        "side": "BUY",
        "context": {"direction": "BUY", "in_ote": False},
        "poi": {},
        "liquidity": {},
        "micro": {},
        "session": {"trading_allowed": True},
        "risk": {},
        "raw": {"timing": {}},
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


class TestSetupTaxonomyUnknown(unittest.TestCase):
    def test_unknown_when_no_core_evidence(self):
        """classify_setup returns UNKNOWN when no direction and no POI."""
        b = _bundle(context={}, poi={}, side="NONE")
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.UNKNOWN)
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("INSUFFICIENT_CORE_EVIDENCE", result.reason)

    def test_unknown_when_empty_bundle(self):
        """classify_setup returns UNKNOWN for truly empty bundle."""
        result = classify_setup(None)
        self.assertEqual(result.setup_type, SetupType.UNKNOWN)
        self.assertEqual(result.confidence, 0.0)


class TestSetupTaxonomyNoSetup(unittest.TestCase):
    def test_no_setup_when_evidence_readable_but_no_pattern(self):
        """classify_setup returns NO_SETUP when context exists but no POI and no setup signals."""
        b = _bundle(
            context={"direction": "BUY", "htf_aligned": True},
            poi={},
            micro={},
            liquidity={},
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.NO_SETUP)
        self.assertEqual(result.family, "NONE")
        self.assertEqual(result.confidence, 0.0)


class TestSetupTaxonomyPoiReaction(unittest.TestCase):
    def test_poi_reaction_when_only_poi_present(self):
        """POI_REACTION when POI exists but no micro/liquidity/OTE."""
        b = _bundle(
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            }
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.POI_REACTION)
        self.assertEqual(result.family, "REACTION")
        self.assertGreaterEqual(result.confidence, 0.0)


class TestSetupTaxonomyContinuationLight(unittest.TestCase):
    def test_continuation_light_when_trend_aligned_poi_waiting(self):
        """CONTINUATION_LIGHT when trend-aligned POI + micro waiting."""
        b = _bundle(
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
            micro={"readiness_state": "WAITING_TRIGGER"},
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.CONTINUATION_LIGHT)
        self.assertEqual(result.family, "CONTINUATION")
        self.assertIn("waiting_confirmation", result.tags)

    def test_continuation_light_when_trend_aligned_poi_liquidity_waiting(self):
        """CONTINUATION_LIGHT when trend-aligned POI + liquidity waiting (without micro waiting)."""
        b = _bundle(
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
            micro={},
            liquidity={"readiness_state": "WAITING_TRIGGER"},
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.CONTINUATION_LIGHT)


class TestSetupTaxonomyContinuationStrict(unittest.TestCase):
    def test_continuation_strict_when_all_ready(self):
        """CONTINUATION_STRICT when trend-aligned POI + liquidity READY + micro READY + OTE READY."""
        b = _bundle(
            context={"direction": "BUY", "in_ote": True},
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
            liquidity={"readiness_state": "READY"},
            micro={"readiness_state": "READY"},
            raw={"timing": {"readiness_state": "READY"}},
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.CONTINUATION_STRICT)
        self.assertEqual(result.family, "CONTINUATION")
        self.assertGreaterEqual(result.confidence, 0.80)
        self.assertIn("trend_aligned", result.tags)
        self.assertIn("micro_ready", result.tags)


class TestSetupTaxonomyOtePullback(unittest.TestCase):
    def test_ote_pullback_when_ote_ready_and_trend_aligned_poi(self):
        """OTE_PULLBACK when OTE ready + trend-aligned POI."""
        b = _bundle(
            context={"direction": "BUY", "in_ote": True},
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
            raw={"timing": {"readiness_state": "READY"}},
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.OTE_PULLBACK)
        self.assertEqual(result.family, "PULLBACK")
        self.assertIn("pullback", result.tags)


class TestSetupTaxonomyReversalLight(unittest.TestCase):
    def test_reversal_light_when_counter_trend_poi_waiting(self):
        """REVERSAL_LIGHT when counter-trend POI + micro waiting."""
        b = _bundle(
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "selected_poi": {"poi_type_normalized": "BEARISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
            micro={"readiness_state": "WAITING_TRIGGER"},
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.REVERSAL_LIGHT)
        self.assertEqual(result.family, "REVERSAL")
        self.assertIn("counter_trend", result.tags)


class TestSetupTaxonomyReversalStrict(unittest.TestCase):
    def test_reversal_strict_when_counter_trend_with_liquidity_and_micro(self):
        """REVERSAL_STRICT when counter-trend POI + liquidity READY + micro READY."""
        b = _bundle(
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BEARISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
            liquidity={"readiness_state": "READY"},
            micro={"readiness_state": "READY"},
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.REVERSAL_STRICT)
        self.assertEqual(result.family, "REVERSAL")
        self.assertGreaterEqual(result.confidence, 0.80)


class TestSetupTaxonomySweepReversal(unittest.TestCase):
    def test_sweep_reversal_when_sweep_plus_micro_reclaim(self):
        """SWEEP_REVERSAL when liquidity sweep + micro reclaim confirmed."""
        b = _bundle(
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BEARISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
            liquidity={
                "readiness_state": "READY",
                "sweep_detected": True,
                "liquidity_state": "SWEEP",
            },
            micro={
                "readiness_state": "READY",
                "trigger_inside_poi": True,
            },
        )
        result = classify_setup(b)
        self.assertEqual(result.setup_type, SetupType.SWEEP_REVERSAL)
        self.assertEqual(result.family, "REVERSAL")
        self.assertGreaterEqual(result.confidence, 0.85)
        self.assertIn("sweep", result.tags)
        self.assertIn("reclaim", result.tags)


class TestSetupTaxonomyLegacyBackwardCompat(unittest.TestCase):
    def test_get_setup_requirement_works_for_legacy_types(self):
        """Legacy types still resolve via get_setup_requirement."""
        req = get_setup_requirement(SetupType.FAILED_AUCTION_RECLAIM)
        self.assertEqual(req.setup_type, SetupType.FAILED_AUCTION_RECLAIM)
        self.assertGreater(req.min_score_enter_full, 0)

    def test_get_setup_requirement_works_for_new_types(self):
        """New types stay gated except the Phase17 SWEEP_REVERSAL chain."""
        # Phase17 opens only SWEEP_REVERSAL after strict POI/micro/liquidity/timing gates.
        sweep_req = get_setup_requirement(SetupType.SWEEP_REVERSAL)
        self.assertEqual(sweep_req.min_score_enter_full, 85.0)
        self.assertEqual(sweep_req.min_score_enter_reduced, 75.0)

        # Other new types remain ENTER-gated at 999.0.
        for st in [SetupType.CONTINUATION_STRICT, SetupType.POI_REACTION, SetupType.NO_SETUP]:
            req = get_setup_requirement(st)
            self.assertGreaterEqual(req.min_score_enter_full, 999.0,
                                    f"{st.value} must be ENTER-gated at 999.0")
        # Risk caps: POI_REACTION and NO_SETUP stay at 0.0
        self.assertEqual(get_setup_requirement(SetupType.POI_REACTION).max_risk_multiplier, 0.0)
        self.assertEqual(get_setup_requirement(SetupType.NO_SETUP).max_risk_multiplier, 0.0)
        # Phase7C opened caps for these
        self.assertEqual(get_setup_requirement(SetupType.CONTINUATION_STRICT).max_risk_multiplier, 0.75)
        self.assertEqual(get_setup_requirement(SetupType.SWEEP_REVERSAL).max_risk_multiplier, 0.75)

    def test_resolve_setup_type_preserves_explicit(self):
        """resolve_setup_type returns the bundle's explicit setup_type."""
        b = EvidenceBundle.from_dict({"setup_type": "CONTINUATION_STRICT"})
        self.assertEqual(resolve_setup_type(b), SetupType.CONTINUATION_STRICT)

    def test_classify_returns_setup_classification_dataclass(self):
        """classify_setup always returns a SetupClassification with expected fields."""
        b = _bundle(
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            }
        )
        result = classify_setup(b)
        self.assertIsInstance(result, SetupClassification)
        self.assertTrue(hasattr(result, "setup_type"))
        self.assertTrue(hasattr(result, "confidence"))
        self.assertTrue(hasattr(result, "reason"))
        self.assertTrue(hasattr(result, "family"))
        self.assertTrue(hasattr(result, "tags"))
        self.assertTrue(hasattr(result, "evidence"))


if __name__ == "__main__":
    unittest.main()
