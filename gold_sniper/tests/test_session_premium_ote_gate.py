from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock

from gold_sniper.strategy.session_premium_ote_gate import BLOCK, PASS, WATCH, evaluate_session_premium_ote_gate


def _ctx(**overrides) -> dict:
    data = {
        "session_label": "NY",
        "news_clear": True,
        "premium_discount": "DISCOUNT",
        "direction": "LONG",
        "setup_type": "REVERSAL",
        "in_ote": True,
        "fibonacci_anchor_valid": True,
    }
    data.update(overrides)
    return data


class TestSessionPremiumOteGate(unittest.TestCase):
    def test_london_pass_possible(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(session_label="LONDON")).decision, PASS)

    def test_ny_pass_possible(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(session_label="NY")).decision, PASS)

    def test_overlap_pass_possible(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(session_label="OVERLAP")).decision, PASS)

    def test_tokyo_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(session_label="TOKYO")).decision, BLOCK)

    def test_asia_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(session_label="ASIA")).decision, BLOCK)

    def test_unknown_session_watches(self) -> None:
        result = evaluate_session_premium_ote_gate(_ctx(session_label="UNKNOWN"))
        self.assertEqual(result.decision, WATCH)
        self.assertEqual(result.session_status, "UNKNOWN")

    def test_news_clear_false_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(news_clear=False)).decision, BLOCK)

    def test_news_veto_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(news_veto=True)).decision, BLOCK)

    def test_high_impact_news_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(high_impact_news=True)).decision, BLOCK)

    def test_pre_news_lockout_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(pre_news_lockout=True)).decision, BLOCK)

    def test_post_news_stealth_not_normalized_never_passes(self) -> None:
        result = evaluate_session_premium_ote_gate(_ctx(post_news_stealth=True, news_normalized=False))
        self.assertNotEqual(result.decision, PASS)

    def test_news_unknown_watches(self) -> None:
        data = _ctx()
        data.pop("news_clear")
        result = evaluate_session_premium_ote_gate(data)
        self.assertEqual(result.decision, WATCH)

    def test_long_discount_pass_possible(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(direction="LONG", premium_discount="DISCOUNT")).decision, PASS)

    def test_long_premium_reversal_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(direction="LONG", premium_discount="PREMIUM", setup_type="REVERSAL")).decision, BLOCK)

    def test_short_premium_pass_possible(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(direction="SHORT", premium_discount="PREMIUM")).decision, PASS)

    def test_short_discount_reversal_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(direction="SHORT", premium_discount="DISCOUNT", setup_type="REVERSAL")).decision, BLOCK)

    def test_continuation_premium_conflict_watches(self) -> None:
        result = evaluate_session_premium_ote_gate(_ctx(direction="LONG", premium_discount="PREMIUM", setup_type="CONTINUATION"))
        self.assertEqual(result.decision, WATCH)

    def test_reversal_premium_discount_conflict_blocks(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(premium_discount_conflict=True, setup_type="REVERSAL")).decision, BLOCK)

    def test_ote_aligned_pass_possible(self) -> None:
        self.assertEqual(evaluate_session_premium_ote_gate(_ctx(in_ote=True, fibonacci_anchor_valid=True)).decision, PASS)

    def test_ote_conflict_reversal_blocks_or_severe_watches(self) -> None:
        result = evaluate_session_premium_ote_gate(_ctx(ote_conflict=True, setup_type="REVERSAL"))
        self.assertIn(result.decision, {BLOCK, WATCH})
        self.assertNotEqual(result.decision, PASS)

    def test_ote_conflict_continuation_watches(self) -> None:
        result = evaluate_session_premium_ote_gate(_ctx(ote_conflict=True, setup_type="CONTINUATION"))
        self.assertEqual(result.decision, WATCH)

    def test_ote_conflict_trend_continuation_watches_without_hard_block(self) -> None:
        result = evaluate_session_premium_ote_gate(_ctx(ote_conflict=True, setup_type="TREND_CONTINUATION"))
        self.assertEqual(result.decision, WATCH)
        self.assertFalse(result.hard_block)
        self.assertIn("OTE_CONFLICT", result.warnings)

    def test_sniper_pullback_long_premium_blocks(self) -> None:
        result = evaluate_session_premium_ote_gate(_ctx(direction="LONG", premium_discount="PREMIUM", setup_type="SNIPER_PULLBACK"))
        self.assertEqual(result.decision, BLOCK)
        self.assertTrue(result.hard_block)

    def test_invalid_fib_anchor_not_auto_pass(self) -> None:
        result = evaluate_session_premium_ote_gate(_ctx(fibonacci_anchor_valid=False, setup_type="CONTINUATION"))
        self.assertNotEqual(result.decision, PASS)

    def test_ote_alone_without_session_news_clear_does_not_pass(self) -> None:
        result = evaluate_session_premium_ote_gate({"in_ote": True, "fibonacci_anchor_valid": True})
        self.assertNotEqual(result.decision, PASS)

    def test_function_does_not_mutate_context(self) -> None:
        context = _ctx()
        before = deepcopy(context)
        evaluate_session_premium_ote_gate(context)
        self.assertEqual(context, before)

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "strategy" / "session_premium_ote_gate.py"
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
        context = _ctx(broker=broker)
        result = evaluate_session_premium_ote_gate(context)
        self.assertEqual(result.decision, PASS)
        broker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
