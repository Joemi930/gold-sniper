import unittest

from gold_sniper.tools.diagnose_poi_contract import diagnose_poi_contract


def _decision(poi):
    return {
        "timestamp": "2026-05-27T00:00:00Z",
        "decision": "WATCH_ONLY",
        "setup_type": "SWEEP_REVERSAL",
        "setup_grade": "B",
        "p1_evidence_bundle": {"poi": poi},
    }


class TestPhase10POIContractAuditTool(unittest.TestCase):
    def test_detects_executable_with_zero_quality(self):
        report = diagnose_poi_contract([
            _decision({
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"low": 2400.0, "high": 2405.0},
                "poi_quality_score": 0.0,
            })
        ])
        self.assertEqual(report["poi_suspect_count"], 1)
        self.assertIn("EXECUTABLE_WITH_ZERO_QUALITY", report["poi_contradiction_distribution"])

    def test_detects_executable_with_legacy_rejected(self):
        report = diagnose_poi_contract([
            _decision({
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "poi_failure_class": "POI_PRESENT_LEGACY_REJECTED",
                "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
                "execution_readiness": "READY",
            })
        ])
        self.assertEqual(report["poi_suspect_count"], 1)
        self.assertIn("EXECUTABLE_WITH_REJECTED_FAILURE_CLASS", report["poi_contradiction_distribution"])

    def test_produces_poi_suspect_count(self):
        report = diagnose_poi_contract([
            _decision({"selected_poi": {"low": 2400.0, "high": 2405.0, "score": 70.0}, "execution_readiness": "READY"}),
            _decision({
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"low": 2400.0, "high": 2405.0},
                "poi_quality_score": 0.0,
            }),
        ])
        self.assertEqual(report["poi_suspect_count"], 1)

    def test_produces_compact_top_suspects(self):
        report = diagnose_poi_contract([
            _decision({
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"low": 2400.0, "high": 2405.0},
                "poi_quality_score": 0.0,
            })
        ], top=1)
        self.assertEqual(len(report["top_suspects"]), 1)
        self.assertIn("poi_contract_status", report["top_suspects"][0])

    def test_does_not_embed_complete_decisions(self):
        report = diagnose_poi_contract([
            _decision({
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"low": 2400.0, "high": 2405.0},
                "poi_quality_score": 0.0,
            })
        ], top=1)
        self.assertNotIn("p1_evidence_bundle", report["top_suspects"][0])


if __name__ == "__main__":
    unittest.main()
