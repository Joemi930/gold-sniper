"""P2-A Connectivity Metrics tests — verify _p2a_connectivity_metrics counts correctly."""

from __future__ import annotations

import unittest

from gold_sniper.replay.replay_metrics import _p2a_connectivity_metrics


class TestP2aConnectivityMetrics(unittest.TestCase):
    def _make_decision(self, *, poi_available=True, selected_present=True,
                       bounds_present=True, poi_type="OB", lifecycle="FRESH",
                       readiness="READY", missing=None, candidates_count=2):
        """Helper: build a single decision dict with p1_evidence_bundle.poi."""
        candidates = []
        for i in range(candidates_count):
            candidates.append({
                "poi_type_normalized": poi_type,
                "lifecycle_normalized": lifecycle,
                "price_bounds": {"low": 2400.0, "high": 2405.0} if bounds_present else None,
                "execution_readiness": readiness,
            })
        return {
            "decision": "REJECT",
            "p1_evidence_bundle": {
                "poi": {
                    "poi_available": poi_available,
                    "selected_poi": {"dummy": True} if selected_present else None,
                    "selected_poi_present": selected_present,
                    "price_bounds": {"low": 2400.0, "high": 2405.0} if bounds_present else None,
                    "has_price_bounds": bounds_present,
                    "poi_type_normalized": poi_type,
                    "poi_type": poi_type,
                    "lifecycle_normalized": lifecycle,
                    "lifecycle_state": lifecycle,
                    "execution_readiness": readiness,
                    "poi_candidates": candidates,
                    "missing_evidence": missing or [],
                    "connectivity_audit": {
                        "agent2_has_any_zone": poi_available,
                        "agent2_has_selected_ob": selected_present,
                        "poi_bounds_present": bounds_present,
                        "selected_poi_present": selected_present,
                    },
                }
            },
        }

    def test_records_with_any_poi(self):
        decisions = [
            self._make_decision(poi_available=True),
            self._make_decision(poi_available=False),
            self._make_decision(poi_available=True),
        ]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["records_with_any_poi"], 2)

    def test_records_with_selected_poi(self):
        decisions = [
            self._make_decision(selected_present=True),
            self._make_decision(selected_present=False, poi_available=False),
            self._make_decision(selected_present=True),
        ]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["records_with_selected_poi"], 2)

    def test_records_with_price_bounds(self):
        decisions = [
            self._make_decision(bounds_present=True),
            self._make_decision(bounds_present=False, poi_available=False),
            self._make_decision(bounds_present=True),
        ]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["records_with_price_bounds"], 2)

    def test_total_poi_candidates(self):
        decisions = [
            self._make_decision(candidates_count=2),
            self._make_decision(candidates_count=0, poi_available=False),
            self._make_decision(candidates_count=3),
        ]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["total_poi_candidates"], 5)

    def test_poi_readiness_distribution(self):
        decisions = [
            self._make_decision(readiness="READY"),
            self._make_decision(readiness="READY"),
            self._make_decision(readiness="WAITING_TRIGGER"),
        ]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["poi_readiness_distribution"]["READY"], 2)
        self.assertEqual(m["poi_readiness_distribution"]["WAITING_TRIGGER"], 1)

    def test_poi_type_distribution(self):
        decisions = [
            self._make_decision(poi_type="OB"),
            self._make_decision(poi_type="OB"),
            self._make_decision(poi_type="FVG"),
        ]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["poi_type_distribution"]["OB"], 2)
        self.assertEqual(m["poi_type_distribution"]["FVG"], 1)

    def test_poi_lifecycle_distribution(self):
        decisions = [
            self._make_decision(lifecycle="FRESH"),
            self._make_decision(lifecycle="WICK_TAGGED"),
            self._make_decision(lifecycle="FRESH"),
        ]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["poi_lifecycle_distribution"]["FRESH"], 2)
        self.assertEqual(m["poi_lifecycle_distribution"]["WICK_TAGGED"], 1)

    def test_poi_missing_evidence_distribution(self):
        decisions = [
            self._make_decision(missing=["INVALID_OR_MISSING_PRICE_BOUNDS"]),
            self._make_decision(missing=["POI_UNAVAILABLE", "POI_LIFECYCLE_UNKNOWN"]),
        ]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["poi_missing_evidence_distribution"]["INVALID_OR_MISSING_PRICE_BOUNDS"], 1)
        self.assertEqual(m["poi_missing_evidence_distribution"]["POI_UNAVAILABLE"], 1)

    def test_empty_decisions(self):
        m = _p2a_connectivity_metrics([])
        self.assertEqual(m["records_with_any_poi"], 0)
        self.assertEqual(m["records_with_selected_poi"], 0)
        self.assertEqual(m["records_with_price_bounds"], 0)
        self.assertEqual(m["total_poi_candidates"], 0)
        self.assertEqual(m["poi_readiness_distribution"], {})

    def test_no_poi_key(self):
        decisions = [{"decision": "REJECT", "p1_evidence_bundle": {}}]
        m = _p2a_connectivity_metrics(decisions)
        self.assertEqual(m["records_with_any_poi"], 0)

    def test_build_p1_replay_metrics_includes_p2a_connectivity(self):
        from gold_sniper.replay.replay_metrics import build_p1_replay_metrics
        decisions = [
            self._make_decision(poi_type="OB", lifecycle="FRESH", readiness="READY"),
            self._make_decision(poi_type="FVG", lifecycle="PARTIAL", readiness="WAITING_TRIGGER"),
        ]
        result = build_p1_replay_metrics(decisions)
        self.assertIn("p2a_connectivity", result)
        c = result["p2a_connectivity"]
        self.assertEqual(c["records_with_any_poi"], 2)
        self.assertIn("OB", c["poi_type_distribution"])
        self.assertIn("FVG", c["poi_type_distribution"])


if __name__ == "__main__":
    unittest.main()
