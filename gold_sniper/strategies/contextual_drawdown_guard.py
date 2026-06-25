# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from typing import Any

from strategies.base_strategy import StrategyModule, strategy_result


class ContextualDrawdownGuard(StrategyModule):
    strategy_id = "CONTEXTUAL_DRAWDOWN_GUARD"
    strategy_family = "RISK_GUARD"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        return strategy_result(
            strategy_id=self.strategy_id,
            strategy_family=self.strategy_family,
            is_applicable=True,
            permission="WAIT",
            score=50.0,
            confidence=0.5,
            hard_veto=False,
            reason="DRAWDOWN_GUARD_MEASUREMENT_ONLY",
            blocking_layer="RISK",
            required_next_evidence="LONG_REPLAY_ATTRIBUTION",
            context=context,
            poi=poi,
            trigger=trigger,
            risk=risk,
        )
