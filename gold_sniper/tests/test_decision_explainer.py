from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock

from gold_sniper.strategy.decision_explainer import explain_unified_decision


def _payload(decision: str = "WAIT", missing: list[str] | None = None) -> dict:
    return {
        "decision": decision,
        "mode": "SHADOW_ONLY",
        "missing_conditions": missing or [],
        "passed_steps": ["news_permission", "session_permission"],
        "failed_steps": [],
        "warnings": [],
        "evidence": {
            "mode": "SHADOW_ONLY",
            "news_permission": {"reason": "NEWS_CLEAR"},
            "session_permission": {"reason": "SESSION_ALLOWED"},
            "liquidity_state": {
                "reason": "LIQUIDITY_SUPPORTS_SWEEP_REJECTED",
                "value": {"state": "SWEEP_REJECTED", "decision": "SUPPORTS_SETUP", "extra": "not copied"},
            },
            "poi_placeholder": {"reason": "POI_QUALITY_ACCEPT", "value": {"decision": "ACCEPT", "grade": "A"}},
            "session_premium_ote_gate": {
                "reason": "SESSION_PREMIUM_OTE_PASS",
                "value": {"decision": "PASS", "grade": "A"},
            },
            "micro_confirmation": {
                "reason": "MICRO_CONFIRMATION_CONFIRMED",
                "value": {"decision": "CONFIRMED", "grade": "A"},
            },
            "risk_placeholder": {"reason": "RISK_AVAILABLE"},
            "large_blob": {"not": "included"},
        },
    }


def _enter_payload() -> dict:
    payload = _payload("ENTER")
    payload["passed_steps"] = [
        "news_permission",
        "session_permission",
        "spread_risk_placeholder",
        "htf_context_placeholder",
        "dol_placeholder",
        "liquidity_state",
        "poi_placeholder",
        "session_premium_ote_gate",
        "micro_confirmation",
        "risk_placeholder",
    ]
    return payload


class TestDecisionExplainer(unittest.TestCase):
    def test_none_payload_no_crash(self) -> None:
        result = explain_unified_decision(None)
        self.assertEqual(result.decision, "UNKNOWN")
        self.assertEqual(result.pipeline_stage, "UNKNOWN")

    def test_empty_payload_no_crash(self) -> None:
        result = explain_unified_decision({})
        self.assertEqual(result.decision, "UNKNOWN")
        self.assertEqual(result.trade_readiness, "UNKNOWN")

    def test_news_hard_veto_reject_maps_news(self) -> None:
        result = explain_unified_decision(_payload("REJECT", ["NEWS_HARD_VETO"]))
        self.assertEqual(result.pipeline_stage, "NEWS")
        self.assertEqual(result.trade_readiness, "BLOCKED")

    def test_session_tokyo_reject_maps_session(self) -> None:
        result = explain_unified_decision(_payload("REJECT", ["SESSION_VETO_TOKYO_ASIA"]))
        self.assertEqual(result.pipeline_stage, "SESSION")
        self.assertEqual(result.trade_readiness, "BLOCKED")

    def test_poi_reject_maps_poi(self) -> None:
        self.assertEqual(explain_unified_decision(_payload("REJECT", ["POI_QUALITY_REJECT_F"])).pipeline_stage, "POI")

    def test_micro_reject_maps_micro(self) -> None:
        result = explain_unified_decision(_payload("REJECT", ["MICRO_CONFIRMATION_REJECT_F"]))
        self.assertEqual(result.pipeline_stage, "MICRO_CONFIRMATION")

    def test_liquidity_watch_maps_liquidity(self) -> None:
        result = explain_unified_decision(_payload("WAIT", ["LIQUIDITY_WATCH_APPROACHING_LIQUIDITY"]))
        self.assertEqual(result.pipeline_stage, "LIQUIDITY")
        self.assertEqual(result.trade_readiness, "WAITING_EVIDENCE")

    def test_session_premium_ote_watch_maps_gate(self) -> None:
        result = explain_unified_decision(_payload("WAIT", ["SESSION_PREMIUM_OTE_WATCH_D"]))
        self.assertEqual(result.pipeline_stage, "SESSION_PREMIUM_OTE")

    def test_micro_watch_maps_micro(self) -> None:
        result = explain_unified_decision(_payload("WAIT", ["MICRO_CONFIRMATION_WATCH_D"]))
        self.assertEqual(result.pipeline_stage, "MICRO_CONFIRMATION")

    def test_risk_context_missing_maps_risk(self) -> None:
        result = explain_unified_decision(_payload("WAIT", ["RISK_CONTEXT_MISSING"]))
        self.assertEqual(result.pipeline_stage, "RISK")

    def test_enter_complete_pipeline_ready_shadow(self) -> None:
        result = explain_unified_decision(_enter_payload())
        self.assertEqual(result.trade_readiness, "READY_SHADOW")
        self.assertEqual(result.kasper_alignment, "ALIGNED")

    def test_enter_never_says_live_ready(self) -> None:
        result = explain_unified_decision(_enter_payload())
        combined = " ".join([result.summary, *result.explanation_lines]).lower()
        self.assertNotIn("live-ready", combined)
        self.assertNotIn("live ready", combined)

    def test_enter_summary_contains_shadow(self) -> None:
        self.assertIn("shadow", explain_unified_decision(_enter_payload()).summary.lower())

    def test_evidence_digest_is_compact(self) -> None:
        result = explain_unified_decision(_payload())
        self.assertIn("liquidity_state", result.evidence_digest)
        self.assertNotIn("large_blob", result.evidence_digest)
        self.assertNotIn("extra", result.evidence_digest["liquidity_state"])

    def test_evidence_digest_contains_primary_values(self) -> None:
        digest = explain_unified_decision(_payload()).evidence_digest
        self.assertEqual(digest["liquidity_state"]["state"], "SWEEP_REJECTED")
        self.assertEqual(digest["poi_placeholder"]["grade"], "A")

    def test_function_does_not_mutate_payload(self) -> None:
        payload = _payload()
        before = deepcopy(payload)
        explain_unified_decision(payload)
        self.assertEqual(payload, before)

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "strategy" / "decision_explainer.py"
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
        payload = _payload()
        payload["broker"] = broker
        explain_unified_decision(payload)
        broker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
