import unittest

from gold_sniper.strategy.poi_rejection_contract import (
    POIRejectionCode,
    POIRejectionSeverity,
    normalize_poi_rejection,
)


def _normalize(**overrides):
    payload = dict(
        failure_class=None,
        semantic_status=None,
        final_score=65.0,
        score_source="TEST",
        score_is_computed=True,
        has_selected_poi=True,
        has_price_bounds=True,
        lifecycle=None,
        distance_to_price_score=None,
        direction_mismatch=None,
        session_invalid=None,
        raw={},
    )
    payload.update(overrides)
    return normalize_poi_rejection(**payload)


class TestP2EPhase13POIRejectionContract(unittest.TestCase):
    def test_poi_missing_is_fatal(self):
        result = _normalize(
            failure_class="POI_PRESENT_LEGACY_REJECTED",
            has_selected_poi=False,
            has_price_bounds=False,
        )
        self.assertEqual(result.code, POIRejectionCode.POI_MISSING)
        self.assertTrue(result.fatal)

    def test_schema_invalid_is_fatal(self):
        result = _normalize(failure_class="POI_SCHEMA_INVALID")
        self.assertEqual(result.code, POIRejectionCode.POI_SCHEMA_INVALID)
        self.assertEqual(result.severity, POIRejectionSeverity.FATAL)

    def test_direction_mismatch_is_fatal(self):
        result = _normalize(failure_class="POI_PRESENT_LEGACY_REJECTED", direction_mismatch=True)
        self.assertEqual(result.code, POIRejectionCode.POI_DIRECTION_MISMATCH)
        self.assertTrue(result.fatal)

    def test_session_invalid_is_fatal(self):
        result = _normalize(failure_class="POI_PRESENT_LEGACY_REJECTED", session_invalid=True)
        self.assertEqual(result.code, POIRejectionCode.POI_SESSION_INVALID)
        self.assertTrue(result.fatal)

    def test_too_far_is_fatal(self):
        result = _normalize(failure_class="POI_TOO_FAR")
        self.assertEqual(result.code, POIRejectionCode.POI_TOO_FAR)
        self.assertTrue(result.fatal)

    def test_legacy_rejected_bounds_missing_score_is_recoverable(self):
        result = _normalize(
            failure_class="POI_PRESENT_LEGACY_REJECTED",
            final_score=None,
            score_is_computed=False,
        )
        self.assertEqual(result.code, POIRejectionCode.POI_QUALITY_MISSING_WITH_BOUNDS)
        self.assertTrue(result.recoverable)

    def test_legacy_rejected_default_zero_without_subscores_is_recoverable(self):
        result = _normalize(
            failure_class="POI_PRESENT_LEGACY_REJECTED",
            final_score=0.0,
            raw={"selected_poi": {"price_bounds": {"low": 1, "high": 2}}},
        )
        self.assertEqual(result.code, POIRejectionCode.POI_DEFAULT_ZERO_SCORE_WITH_BOUNDS)
        self.assertTrue(result.recoverable)

    def test_legacy_rejected_low_non_zero_score_is_recoverable(self):
        result = _normalize(failure_class="POI_PRESENT_LEGACY_REJECTED", final_score=35.0)
        self.assertEqual(result.code, POIRejectionCode.POI_SCORE_LOW_BUT_PRESENT)
        self.assertTrue(result.recoverable)

    def test_legacy_rejected_no_bounds_is_unknown(self):
        result = _normalize(
            failure_class="POI_PRESENT_LEGACY_REJECTED",
            has_price_bounds=False,
        )
        self.assertEqual(result.severity, POIRejectionSeverity.UNKNOWN)
        self.assertFalse(result.recoverable)

    def test_unmapped_rejected_is_unknown_not_auto_tradable(self):
        result = _normalize(failure_class="DENIED_UNKNOWN", semantic_status="POI_REJECTED")
        self.assertEqual(result.severity, POIRejectionSeverity.UNKNOWN)
        self.assertFalse(result.recoverable)


if __name__ == "__main__":
    unittest.main()
