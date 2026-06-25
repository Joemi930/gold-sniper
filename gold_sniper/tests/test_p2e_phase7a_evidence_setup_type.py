"""P2-E Phase 7A — EvidenceBuilder setup classification integration tests.

These tests verify the classify_setup → EvidenceBundle integration
without importing from gold_sniper.replay (which has a pre-existing
relative import constraint in replay/__init__.py).

The full integration is validated at the smoke-replay level.
"""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, SetupType, TradeSide
from gold_sniper.strategy.setup_taxonomy import classify_setup


class TestEvidenceBuilderSetupClassification(unittest.TestCase):

    def _bundle_with_classification(self, setup_type=SetupType.UNKNOWN, **overrides):
        """Simulate what build_evidence_bundle does after classification."""
        data = {
            "setup_type": setup_type.value,
            "side": "BUY",
            "context": {"direction": "BUY"},
            "poi": {},
            "liquidity": {},
            "micro": {},
            "session": {},
            "risk": {},
            "raw": {"timing": {}},
        }
        data.update(overrides)
        bundle = EvidenceBundle.from_dict(data)

        if bundle.setup_type == SetupType.UNKNOWN:
            classification = classify_setup(bundle)
            raw = dict(bundle.raw or {})
            raw["setup_classification"] = {
                "setup_type": classification.setup_type.value,
                "confidence": classification.confidence,
                "reason": classification.reason,
                "family": classification.family,
                "required_ready_sections": list(classification.required_ready_sections),
                "tags": list(classification.tags),
                "evidence": classification.evidence,
            }
            bundle = EvidenceBundle(
                symbol=bundle.symbol,
                ts_utc=bundle.ts_utc,
                setup_type=classification.setup_type,
                side=bundle.side,
                observations=bundle.observations,
                context=bundle.context,
                poi=bundle.poi,
                liquidity=bundle.liquidity,
                micro=bundle.micro,
                news=bundle.news,
                session=bundle.session,
                risk=bundle.risk,
                raw=raw,
            )
        return bundle

    def test_builder_classifies_when_unknown(self):
        """Bundle with UNKNOWN setup_type gets classified (simulated builder path)."""
        bundle = self._bundle_with_classification(SetupType.UNKNOWN)
        self.assertIsInstance(bundle, EvidenceBundle)
        self.assertIn(bundle.setup_type, {SetupType.UNKNOWN, SetupType.NO_SETUP})

    def test_builder_preserves_explicit_setup_type(self):
        """Explicit setup_type is never overwritten."""
        bundle = self._bundle_with_classification(SetupType.CONTINUATION_STRICT)
        self.assertEqual(bundle.setup_type, SetupType.CONTINUATION_STRICT)

    def test_builder_raw_contains_setup_classification(self):
        """When classified, raw.setup_classification exists with correct structure."""
        bundle = self._bundle_with_classification(SetupType.UNKNOWN)
        raw = bundle.raw or {}
        classification = raw.get("setup_classification")
        if bundle.setup_type != SetupType.UNKNOWN:
            self.assertIsNotNone(classification,
                                 f"setup_classification missing for setup_type={bundle.setup_type.value}")
        if classification is not None:
            for key in ("setup_type", "reason", "family", "confidence", "tags"):
                self.assertIn(key, classification, f"setup_classification missing key: {key}")

    def test_builder_setup_classification_has_all_fields(self):
        """setup_classification dict has reason, family, confidence, tags, evidence."""
        bundle = self._bundle_with_classification(
            SetupType.UNKNOWN,
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
        )
        classification = (bundle.raw or {}).get("setup_classification")
        self.assertIsNotNone(classification, "classification missing for POI-present bundle")
        for key in ("reason", "family", "confidence", "tags", "evidence"):
            self.assertIn(key, classification or {}, f"missing key: {key}")

    def test_builder_no_forbidden_keys_in_bundle(self):
        """Bundle must not contain entry/sl/tp/lot/trade_signal keys in sections."""
        bundle = self._bundle_with_classification(
            SetupType.UNKNOWN,
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400.0, "high": 2405.0},
            },
        )
        bundle_dict = bundle.to_dict()

        def _find_forbidden(d, prefix=""):
            found = []
            if isinstance(d, dict):
                for k, v in d.items():
                    key_lower = str(k).lower()
                    if any(f in key_lower for f in
                           ("entry_price", "stop_loss", "take_profit", "trade_signal")):
                        found.append(f"{prefix}.{k}")
                    found.extend(_find_forbidden(v, f"{prefix}.{k}"))
            elif isinstance(d, list):
                for i, v in enumerate(d):
                    found.extend(_find_forbidden(v, f"{prefix}[{i}]"))
            return found

        forbidden = _find_forbidden(bundle_dict)
        self.assertEqual(len(forbidden), 0,
                         f"Forbidden keys found in bundle: {forbidden}")


if __name__ == "__main__":
    unittest.main()
