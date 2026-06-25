from __future__ import annotations

import ast
from pathlib import Path

from gold_sniper.strategy.professional_decision_engine import (
    DECISION_ENTER_FULL,
    DECISION_ENTER_REDUCED,
    DECISION_REJECT,
    GRADE_D,
    SHADOW_ONLY,
)
from gold_sniper.strategy.unified_xauusd_strategy import evaluate_unified_xauusd_strategy
from gold_sniper.tests.test_unified_xauusd_strategy_shadow import _complete_event


def test_unified_output_contains_professional_fields():
    decision = evaluate_unified_xauusd_strategy(_complete_event())
    payload = decision.to_dict()
    assert payload["mode"] == SHADOW_ONLY
    assert "setup_grade" in payload
    assert "confidence_score" in payload
    assert "risk_multiplier" in payload
    assert payload["required_execution_mode"] == SHADOW_ONLY
    assert isinstance(payload["professional_decision"], dict)
    assert "score" in payload
    assert "confidence" in payload
    assert "missing_conditions" in payload
    assert "evidence" in payload


def test_unified_news_hard_veto_has_professional_reject_shape():
    event = _complete_event()
    event["agents"]["agent_6"]["news_veto"] = True
    decision = evaluate_unified_xauusd_strategy(event)
    assert decision.decision == DECISION_REJECT
    assert decision.setup_grade == GRADE_D
    assert decision.risk_multiplier == 0.0
    assert decision.required_execution_mode == SHADOW_ONLY
    assert decision.hard_veto is True
    assert decision.hard_veto_reason == "NEWS_HARD_VETO"


def test_unified_strong_setup_uses_professional_enter_without_broker():
    event = _complete_event()
    event["broker"] = object()
    decision = evaluate_unified_xauusd_strategy(event)
    assert decision.decision in {DECISION_ENTER_FULL, DECISION_ENTER_REDUCED}
    assert decision.mode == SHADOW_ONLY
    assert decision.required_execution_mode == SHADOW_ONLY
    assert decision.risk_multiplier in {1.0, 0.75, 0.4}


def test_strategy_package_does_not_import_metatrader5():
    root = Path(__file__).resolve().parents[1] / "strategy"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "MetaTrader5":
                        offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom) and node.module == "MetaTrader5":
                offenders.append(str(path))
    assert offenders == []


def test_unified_passes_kasper_micro_template_to_micro_engine():
    event = _complete_event()
    event["setup_type"] = "REVERSAL_AFTER_SWEEP"
    event["context"]["setup_type"] = "REVERSAL_AFTER_SWEEP"
    event["context"]["sweep_rejected"] = True
    event["context"]["htf_context_available"] = True
    event["context"]["poi_available"] = True
    event["context"]["poi_grade"] = "A"
    event["context"]["risk_valid"] = True
    decision = evaluate_unified_xauusd_strategy(event)
    micro_value = decision.evidence["micro_confirmation"]["value"]
    assert micro_value["template_name"] in {"reversal_strict", "continuation_light"}
    if decision.setup_type == "REVERSAL_AFTER_SWEEP":
        assert micro_value["template_name"] == "reversal_strict"
