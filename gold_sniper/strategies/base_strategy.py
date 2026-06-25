"""Common contracts for shadow professional strategy modules."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


PERMISSIONS = {"REJECT", "WAIT", "CANDIDATE", "STANDARD_SHADOW", "PREMIUM_SHADOW"}


def strategy_result(
    *,
    strategy_id: str,
    strategy_family: str,
    is_applicable: bool,
    permission: str,
    score: float,
    confidence: float,
    hard_veto: bool,
    reason: str,
    blocking_layer: str,
    required_next_evidence: str,
    context: dict[str, Any],
    poi: dict[str, Any],
    trigger: dict[str, Any],
    risk: dict[str, Any],
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "strategy_family": strategy_family,
        "is_applicable": bool(is_applicable),
        "permission": permission if permission in PERMISSIONS else "REJECT",
        "score": round(float(score), 4),
        "confidence": round(float(confidence), 4),
        "hard_veto": bool(hard_veto),
        "reason": reason,
        "blocking_layer": blocking_layer,
        "required_next_evidence": required_next_evidence,
        "context": context,
        "poi": poi,
        "trigger": trigger,
        "risk": risk,
    }


class StrategyModule(ABC):
    strategy_id: str
    strategy_family: str

    @abstractmethod
    def evaluate(self, context: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


def empty_context() -> dict[str, Any]:
    return {
        "session": "UNKNOWN",
        "primary_regime": "UNKNOWN",
        "delivery_phase": "UNKNOWN",
        "draw_on_liquidity": "UNKNOWN",
        "order_flow": "UNKNOWN",
    }


def empty_poi() -> dict[str, Any]:
    return {"poi_type": "UNKNOWN", "poi_state": "NA", "distance_bucket": "NA", "score": 0.0}


def empty_trigger() -> dict[str, Any]:
    return {
        "trigger_kind": "UNKNOWN",
        "trigger_strength": 0.0,
        "has_retest": False,
        "has_displacement": False,
        "has_sweep": False,
    }


def empty_risk() -> dict[str, Any]:
    return {"risk_flag": "LOW", "drawdown_context_flag": False}


def reject(strategy_id: str, strategy_family: str, reason: str, context: dict[str, Any]) -> dict[str, Any]:
    return strategy_result(
        strategy_id=strategy_id,
        strategy_family=strategy_family,
        is_applicable=False,
        permission="REJECT",
        score=0.0,
        confidence=0.0,
        hard_veto=False,
        reason=reason,
        blocking_layer="NONE",
        required_next_evidence="NOT_APPLICABLE",
        context=context,
        poi=empty_poi(),
        trigger=empty_trigger(),
        risk=empty_risk(),
    )
