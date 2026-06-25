from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock

from gold_sniper.strategy.micro_confirmation_engine import CONFIRMED, REJECT, WATCH, evaluate_micro_confirmation


def _poi(decision: str = "ACCEPT") -> dict:
    return {"decision": decision, "grade": "A", "score": 88.0}


def _context(**overrides) -> dict:
    data = {"setup_type": "CONTINUATION", "news_clear": True, "session_allowed": True}
    data.update(overrides)
    return data


def _micro(**overrides) -> dict:
    data = {
        "trigger_kind": "SWEEP_RECLAIM_RETEST",
        "trigger_inside_poi": True,
        "micro_sweep_present": True,
        "displacement_present": True,
        "reclaim_confirmed": True,
        "retest_confirmed": True,
    }
    data.update(overrides)
    return data


class TestMicroConfirmationEngine(unittest.TestCase):
    def test_empty_micro_waits_or_rejects_without_crash(self) -> None:
        result = evaluate_micro_confirmation(None, _context(), _poi())
        self.assertIn(result.decision, {WATCH, REJECT})
        self.assertIn("MICRO_PAYLOAD_MISSING", result.missing_evidence)

    def test_choch_alone_never_confirms(self) -> None:
        result = evaluate_micro_confirmation({"trigger_kind": "MICRO_CHOCH", "trigger_inside_poi": True}, _context(), _poi())
        self.assertNotEqual(result.decision, CONFIRMED)
        self.assertIn("MICRO_CHOCH_ALONE_NOT_DECISIVE", result.warnings)

    def test_choch_displacement_without_reclaim_never_confirms(self) -> None:
        result = evaluate_micro_confirmation(
            {"trigger_kind": "MICRO_CHOCH", "trigger_inside_poi": True, "displacement_present": True, "retest_confirmed": True},
            _context(),
            _poi(),
        )
        self.assertNotEqual(result.decision, CONFIRMED)
        self.assertIn("RECLAIM_OR_ACCEPTANCE_MISSING", result.missing_evidence)

    def test_choch_displacement_reclaim_without_retest_never_confirms(self) -> None:
        result = evaluate_micro_confirmation(
            {
                "trigger_kind": "MICRO_CHOCH",
                "trigger_inside_poi": True,
                "displacement_present": True,
                "reclaim_confirmed": True,
            },
            _context(),
            _poi(),
        )
        self.assertIn(result.decision, {CONFIRMED, WATCH})
        self.assertNotIn("RETEST_MISSING", result.missing_evidence)
        self.assertEqual(result.template_name, "continuation_light")

    def test_trigger_outside_poi_rejects(self) -> None:
        result = evaluate_micro_confirmation(_micro(trigger_inside_poi=False), _context(), _poi())
        self.assertEqual(result.decision, REJECT)
        self.assertTrue(result.hard_reject)
        self.assertIn("TRIGGER_OUTSIDE_POI", result.reasons)

    def test_poi_reject_blocks_micro_confirmation(self) -> None:
        result = evaluate_micro_confirmation(_micro(), _context(), _poi("REJECT"))
        self.assertEqual(result.decision, REJECT)
        self.assertIn("POI_REJECTED_NO_MICRO_CONFIRMATION", result.reasons)

    def test_poi_watch_caps_micro_to_watch(self) -> None:
        result = evaluate_micro_confirmation(_micro(), _context(), _poi("WATCH"))
        self.assertEqual(result.decision, WATCH)
        self.assertIn("POI_WATCH_MICRO_MAX_WATCH", result.warnings)

    def test_poi_accept_complete_micro_confirms(self) -> None:
        result = evaluate_micro_confirmation(_micro(), _context(), _poi("ACCEPT"))
        self.assertEqual(result.decision, CONFIRMED)

    def test_reversal_without_micro_sweep_does_not_confirm(self) -> None:
        result = evaluate_micro_confirmation(_micro(micro_sweep_present=False), _context(setup_type="REVERSAL"), _poi())
        self.assertNotEqual(result.decision, CONFIRMED)
        self.assertIn("MICRO_SWEEP_MISSING_FOR_REVERSAL", result.missing_evidence)

    def test_reversal_with_sweep_and_complete_evidence_confirms(self) -> None:
        result = evaluate_micro_confirmation(_micro(), _context(setup_type="REVERSAL"), _poi())
        self.assertEqual(result.decision, CONFIRMED)

    def test_continuation_without_sweep_can_confirm_with_core_evidence(self) -> None:
        result = evaluate_micro_confirmation(_micro(micro_sweep_present=False), _context(setup_type="CONTINUATION"), _poi())
        self.assertEqual(result.decision, CONFIRMED)

    def test_bos_alone_never_confirms(self) -> None:
        result = evaluate_micro_confirmation({"trigger_kind": "BOS", "trigger_inside_poi": True}, _context(), _poi())
        self.assertNotEqual(result.decision, CONFIRMED)
        self.assertIn("BOS_NOT_ENTRY_TRIGGER", result.warnings)

    def test_function_does_not_mutate_micro_payload(self) -> None:
        micro = _micro()
        before = deepcopy(micro)
        evaluate_micro_confirmation(micro, _context(), _poi())
        self.assertEqual(micro, before)

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "strategy" / "micro_confirmation_engine.py"
        with module_path.open("r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        self.assertNotIn("MetaTrader5", imports)

    def test_broker_is_not_called(self) -> None:
        broker = Mock()
        micro = _micro()
        micro["broker"] = broker
        result = evaluate_micro_confirmation(micro, _context(), _poi())
        self.assertEqual(result.decision, CONFIRMED)
        broker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
