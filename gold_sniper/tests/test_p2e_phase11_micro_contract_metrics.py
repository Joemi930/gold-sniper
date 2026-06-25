"""P2-E Phase 11: Micro contract metrics tests.

Tests:
1. micro_contract_status_distribution is calculated.
2. micro_contract_missing_field_distribution is calculated.
3. blockers by micro status are calculated.
4. near_miss_micro_count_by_setup is calculated.
5. micro_missing_data_count is calculated.
"""

import unittest

from gold_sniper.replay.replay_metrics import _micro_contract_metrics


class TestMicroContractMetrics(unittest.TestCase):

    def _make_decision(self, **overrides):
        return {
            "setup_type": overrides.get("setup_type", "SWEEP_REVERSAL"),
            "decision": overrides.get("decision", "WATCH_ONLY"),
            "micro_contract_status": overrides.get("micro_contract_status", "MICRO_WAITING_TRIGGER"),
            "micro_contract_reason": overrides.get("micro_contract_reason", "SWEEP_OR_CHOCH_WITHOUT_TRIGGER"),
            "micro_contract_missing_fields": overrides.get("micro_contract_missing_fields", []),
            "micro_contract_present_fields": overrides.get("micro_contract_present_fields", ["sweep_1m_confirmed"]),
            "micro_contract_contradictions": overrides.get("micro_contract_contradictions", []),
            "enter_eligibility_blockers": overrides.get("enter_eligibility_blockers", ["MICRO_NOT_READY"]),
        }

    def test_status_distribution_is_calculated(self):
        decisions = [
            self._make_decision(micro_contract_status="MICRO_WAITING_TRIGGER"),
            self._make_decision(micro_contract_status="MICRO_WAITING_TRIGGER"),
            self._make_decision(micro_contract_status="MICRO_MISSING_DATA"),
        ]
        metrics = _micro_contract_metrics(decisions)
        self.assertIn("micro_contract_status_distribution", metrics)
        dist = metrics["micro_contract_status_distribution"]
        self.assertEqual(dist.get("MICRO_WAITING_TRIGGER"), 2)
        self.assertEqual(dist.get("MICRO_MISSING_DATA"), 1)

    def test_missing_field_distribution_is_calculated(self):
        decisions = [
            self._make_decision(
                micro_contract_status="MICRO_MISSING_DATA",
                micro_contract_missing_fields=["sweep_1m_confirmed", "choch_detected"],
            ),
        ]
        metrics = _micro_contract_metrics(decisions)
        self.assertIn("micro_contract_missing_field_distribution", metrics)
        dist = metrics["micro_contract_missing_field_distribution"]
        self.assertEqual(dist.get("sweep_1m_confirmed"), 1)
        self.assertEqual(dist.get("choch_detected"), 1)

    def test_blockers_by_micro_status(self):
        decisions = [
            self._make_decision(
                micro_contract_status="MICRO_WAITING_TRIGGER",
                enter_eligibility_blockers=["MICRO_NOT_READY", "GRADE_BELOW_C"],
            ),
        ]
        metrics = _micro_contract_metrics(decisions)
        blockers = metrics["enter_eligibility_blockers_by_micro_contract_status"]
        self.assertIn("MICRO_WAITING_TRIGGER", blockers)
        self.assertIn("MICRO_NOT_READY", blockers["MICRO_WAITING_TRIGGER"])

    def test_near_miss_micro_count_by_setup(self):
        decisions = [
            self._make_decision(
                setup_type="SWEEP_REVERSAL",
                decision="WATCH_ONLY",
                micro_contract_status="MICRO_WAITING_TRIGGER",
            ),
            self._make_decision(
                setup_type="SWEEP_REVERSAL",
                decision="WAIT_FOR_TRIGGER",
                micro_contract_status="MICRO_WAITING_TRIGGER",
            ),
            self._make_decision(
                setup_type="POI_REACTION",
                decision="WATCH_ONLY",
                micro_contract_status="MICRO_MISSING_DATA",
            ),
        ]
        metrics = _micro_contract_metrics(decisions)
        near_miss = metrics["near_miss_micro_count_by_setup"]
        self.assertEqual(near_miss.get("SWEEP_REVERSAL"), 2)
        self.assertEqual(metrics["near_miss_micro_count"], 2)

    def test_micro_missing_data_count(self):
        decisions = [
            self._make_decision(micro_contract_status="MICRO_MISSING_DATA"),
            self._make_decision(micro_contract_status="MICRO_MISSING_DATA"),
            self._make_decision(micro_contract_status="MICRO_WAITING_TRIGGER"),
        ]
        metrics = _micro_contract_metrics(decisions)
        self.assertEqual(metrics["micro_missing_data_count"], 2)

    def test_empty_decisions_returns_zeros(self):
        metrics = _micro_contract_metrics([])
        self.assertEqual(metrics["micro_contract_status_distribution"], {})


if __name__ == "__main__":
    unittest.main()
