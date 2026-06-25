from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock

from gold_sniper.strategy.liquidity_state_machine import (
    BLOCKS_SETUP,
    SUPPORTS_SETUP,
    WATCH,
    evaluate_liquidity_state,
)


def _context(**overrides) -> dict:
    data = {"setup_type": "CONTINUATION", "htf_aligned": True, "order_flow_aligned": True}
    data.update(overrides)
    return data


class TestLiquidityStateMachine(unittest.TestCase):
    def test_empty_payload_does_not_crash(self) -> None:
        result = evaluate_liquidity_state(None, None)
        self.assertIn(result.decision, {WATCH, BLOCKS_SETUP})

    def test_no_liquidity_story(self) -> None:
        result = evaluate_liquidity_state({}, _context(htf_aligned=False, order_flow_aligned=False))
        self.assertEqual(result.state, "NO_LIQUIDITY_STORY")
        self.assertEqual(result.decision, BLOCKS_SETUP)
        self.assertTrue(result.hard_block)
        self.assertIn("LIQUIDITY_STORY_MISSING_BLOCKS_SETUP", result.reasons)
        self.assertIn("LIQUIDITY_STORY_MISSING", result.missing_evidence)

    def test_open_dol_supports_or_watches(self) -> None:
        result = evaluate_liquidity_state({"draw_on_liquidity": "BUY_SIDE", "liquidity_target_open": True}, _context())
        self.assertEqual(result.state, "LIQUIDITY_OPEN")
        self.assertIn(result.decision, {SUPPORTS_SETUP, WATCH})

    def test_consumed_dol_blocks_without_new_draw(self) -> None:
        result = evaluate_liquidity_state({"dol_status": "CONSUMED"}, _context())
        self.assertEqual(result.decision, BLOCKS_SETUP)
        self.assertEqual(result.state, "LIQUIDITY_CONSUMED")

    def test_sweep_without_rejection_watches(self) -> None:
        result = evaluate_liquidity_state({"sweep_detected": True}, _context(setup_type="REVERSAL"))
        self.assertEqual(result.decision, WATCH)
        self.assertEqual(result.state, "PURGE")

    def test_sweep_rejection_supports(self) -> None:
        result = evaluate_liquidity_state({"sweep_detected": True, "rejection_confirmed": True}, _context(setup_type="REVERSAL"))
        self.assertEqual(result.state, "SWEEP_REJECTED")
        self.assertEqual(result.decision, SUPPORTS_SETUP)

    def test_purge_revert_supports(self) -> None:
        result = evaluate_liquidity_state({"purge_detected": True, "revert_detected": True}, _context(setup_type="REVERSAL"))
        self.assertEqual(result.state, "REVERT")
        self.assertEqual(result.decision, SUPPORTS_SETUP)

    def test_breakout_acceptance_blocks_reversal(self) -> None:
        result = evaluate_liquidity_state({"breakout_acceptance": True}, _context(setup_type="REVERSAL"))
        self.assertEqual(result.state, "BREAKOUT_ACCEPTANCE")
        self.assertEqual(result.decision, BLOCKS_SETUP)

    def test_breakout_acceptance_supports_or_watches_continuation(self) -> None:
        result = evaluate_liquidity_state({"breakout_acceptance": True}, _context(setup_type="CONTINUATION"))
        self.assertEqual(result.state, "BREAKOUT_ACCEPTANCE")
        self.assertIn(result.decision, {SUPPORTS_SETUP, WATCH})

    def test_run_toward_open_dol_supports(self) -> None:
        result = evaluate_liquidity_state({"run_detected": True, "liquidity_target_open": True}, _context())
        self.assertEqual(result.state, "RUN")
        self.assertEqual(result.decision, SUPPORTS_SETUP)

    def test_internal_cleanup_continuation_supports_or_watches(self) -> None:
        result = evaluate_liquidity_state({"internal_cleanup": True}, _context(setup_type="CONTINUATION"))
        self.assertEqual(result.state, "INTERNAL_CLEANUP")
        self.assertIn(result.decision, {SUPPORTS_SETUP, WATCH})

    def test_internal_cleanup_reversal_not_strong_support(self) -> None:
        result = evaluate_liquidity_state({"internal_cleanup": True}, _context(setup_type="REVERSAL"))
        self.assertEqual(result.state, "INTERNAL_CLEANUP")
        self.assertNotEqual(result.decision, SUPPORTS_SETUP)

    def test_approaching_liquidity_watches(self) -> None:
        result = evaluate_liquidity_state({"approaching_liquidity": True}, _context())
        self.assertEqual(result.state, "APPROACHING_LIQUIDITY")
        self.assertEqual(result.decision, WATCH)

    def test_function_does_not_mutate_payloads(self) -> None:
        liquidity = {"sweep_detected": True, "rejection_confirmed": True}
        context = _context()
        before_liquidity = deepcopy(liquidity)
        before_context = deepcopy(context)
        evaluate_liquidity_state(liquidity, context)
        self.assertEqual(liquidity, before_liquidity)
        self.assertEqual(context, before_context)

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "strategy" / "liquidity_state_machine.py"
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
        result = evaluate_liquidity_state({"sweep_detected": True, "rejection_confirmed": True, "broker": broker}, _context())
        self.assertEqual(result.decision, SUPPORTS_SETUP)
        broker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
