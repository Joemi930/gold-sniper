import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, SetupType
from gold_sniper.strategy.setup_taxonomy import classify_setup


def _bundle(**overrides):
    data = {
        "setup_type": "UNKNOWN",
        "side": "BUY",
        "context": {"direction": "BUY", "in_ote": False},
        "poi": {},
        "liquidity": {},
        "micro": {},
        "session": {"trading_allowed": True},
        "risk": {"passed": True},
        "raw": {"timing": {}},
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


def _poi(poi_type="BULLISH_OB"):
    return {
        "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
        "selected_poi": {"poi_type_normalized": poi_type},
        "price_bounds": {"low": 2400, "high": 2405},
    }


class TestEnrichedTaxonomy(unittest.TestCase):
    def test_classify_setup_stores_signals_in_evidence(self):
        result = classify_setup(_bundle(poi=_poi()))
        self.assertIn("signals", result.evidence)
        self.assertIsInstance(result.evidence["signals"], dict)

    def test_classify_setup_stores_candidates_in_evidence(self):
        result = classify_setup(_bundle(poi=_poi()))
        self.assertIn("candidates", result.evidence)
        self.assertIsInstance(result.evidence["candidates"], list)

    def test_strict_candidate_priority_over_light(self):
        result = classify_setup(_bundle(
            context={"direction": "BUY", "in_ote": True},
            poi=_poi("BULLISH_OB"),
            liquidity={"readiness_state": "READY"},
            micro={"readiness_state": "READY", "retest_confirmed": True},
            raw={"timing": {"readiness_state": "READY"}},
        ))
        self.assertEqual(result.setup_type, SetupType.CONTINUATION_STRICT)

    def test_continuation_light_before_poi_reaction(self):
        result = classify_setup(_bundle(
            poi=_poi("BULLISH_OB"),
            micro={"readiness_state": "WAITING_TRIGGER"},
        ))
        self.assertEqual(result.setup_type, SetupType.CONTINUATION_LIGHT)

    def test_ote_pullback_when_trend_aligned_and_in_ote(self):
        result = classify_setup(_bundle(
            context={"direction": "BUY", "in_ote": True},
            poi=_poi("BULLISH_OB"),
        ))
        self.assertEqual(result.setup_type, SetupType.OTE_PULLBACK)

    def test_unknown_stays_unknown_if_core_insufficient(self):
        result = classify_setup(_bundle(context={}, poi={}, side="NONE"))
        self.assertEqual(result.setup_type, SetupType.UNKNOWN)
        self.assertEqual(result.reason, "INSUFFICIENT_CORE_EVIDENCE")

    def test_no_setup_stays_no_setup_with_readable_evidence(self):
        result = classify_setup(_bundle(context={"direction": "BUY", "htf_aligned": True}, poi={}))
        self.assertEqual(result.setup_type, SetupType.NO_SETUP)


if __name__ == "__main__":
    unittest.main()
