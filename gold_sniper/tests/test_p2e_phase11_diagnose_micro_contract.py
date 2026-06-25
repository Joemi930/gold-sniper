"""P2-E Phase 11: Diagnose micro contract CLI tests.

Tests:
1. Reads decisions list.
2. Produces total_decisions_scanned.
3. Produces top_near_miss_micro.
4. Detects MICRO_MISSING_DATA.
5. Detects MICRO_CONFIRMED.
6. Does not modify decisions.
"""

import json
import unittest

from gold_sniper.tools.diagnose_micro_contract import (
    diagnose_micro_contract,
)


class TestDiagnoseMicroContract(unittest.TestCase):

    def test_reads_decisions_list(self):
        decisions = [
            {"setup_type": "SWEEP_REVERSAL", "setup_grade": "B", "decision": "WATCH_ONLY",
             "micro_contract_status": "MICRO_WAITING_TRIGGER",
             "micro_contract_reason": "SWEEP_OR_CHOCH_WITHOUT_TRIGGER",
             "micro_contract_missing_fields": [], "micro_contract_present_fields": ["sweep_1m_confirmed"],
             "micro_contract_contradictions": [], "enter_eligibility_blockers": ["MICRO_NOT_READY"],
             "hard_veto": False, "replay_invalid": False},
        ]
        result = diagnose_micro_contract(decisions, top=10)
        self.assertEqual(result["total_decisions_scanned"], 1)

    def test_produces_top_near_miss_micro(self):
        decisions = [
            {"setup_type": "SWEEP_REVERSAL", "setup_grade": "B", "decision": "WATCH_ONLY",
             "micro_contract_status": "MICRO_WAITING_TRIGGER",
             "micro_contract_reason": "SWEEP_OR_CHOCH_WITHOUT_TRIGGER",
             "micro_contract_missing_fields": [], "micro_contract_present_fields": [],
             "micro_contract_contradictions": [], "enter_eligibility_blockers": ["MICRO_NOT_READY"],
             "hard_veto": False, "replay_invalid": False,
             "timestamp": "2026-06-01T10:00:00Z"},
        ]
        result = diagnose_micro_contract(decisions, top=10)
        self.assertEqual(result["true_micro_near_miss_count"], 1)

    def test_detects_micro_missing_data(self):
        decisions = [
            {"setup_type": "SWEEP_REVERSAL", "setup_grade": "B", "decision": "WATCH_ONLY",
             "micro_contract_status": "MICRO_MISSING_DATA",
             "micro_contract_reason": "MISSING_SWEEP_OR_CHOCH",
             "micro_contract_missing_fields": ["sweep_1m_confirmed", "choch_detected"],
             "micro_contract_present_fields": [], "micro_contract_contradictions": [],
             "enter_eligibility_blockers": [], "hard_veto": False, "replay_invalid": False},
        ]
        result = diagnose_micro_contract(decisions, top=10)
        self.assertEqual(result["micro_missing_data_count"], 1)
        self.assertTrue(result["diagnostic"])

    def test_detects_micro_confirmed(self):
        decisions = [
            {"setup_type": "SWEEP_REVERSAL", "setup_grade": "B", "decision": "WATCH_ONLY",
             "micro_contract_status": "MICRO_CONFIRMED",
             "micro_contract_reason": "SWEEP_CHOCH_WITH_TRIGGER_CONFIRMED",
             "micro_contract_missing_fields": [],
             "micro_contract_present_fields": ["sweep_1m_confirmed", "choch_detected", "trigger_inside_poi"],
             "micro_contract_contradictions": [], "enter_eligibility_blockers": [],
             "hard_veto": False, "replay_invalid": False,
             "timestamp": "2026-06-01T10:00:00Z"},
        ]
        result = diagnose_micro_contract(decisions, top=10)
        self.assertEqual(result["micro_confirmed_count"], 1)

    def test_does_not_modify_decisions(self):
        decisions = [
            {"setup_type": "SWEEP_REVERSAL", "setup_grade": "B", "decision": "WATCH_ONLY",
             "micro_contract_status": "MICRO_WAITING_TRIGGER",
             "micro_contract_reason": "TEST",
             "micro_contract_missing_fields": [], "micro_contract_present_fields": [],
             "micro_contract_contradictions": [], "enter_eligibility_blockers": [],
             "hard_veto": False, "replay_invalid": False},
        ]
        original = json.dumps(decisions)
        diagnose_micro_contract(decisions, top=10)
        after = json.dumps(decisions)
        self.assertEqual(json.loads(original), json.loads(after))

    def test_empty_list_handled(self):
        result = diagnose_micro_contract([], top=10)
        self.assertEqual(result["total_decisions_scanned"], 0)


if __name__ == "__main__":
    unittest.main()
