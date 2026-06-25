# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from typing import Any

from strategies.base_strategy import StrategyModule, strategy_result


class NoTradeTokyo(StrategyModule):
    strategy_id = "NO_TRADE_TOKYO"
    strategy_family = "SESSION_FILTER"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        applicable = context["session_bucket"] == "TOKYO"
        return strategy_result(
            strategy_id=self.strategy_id,
            strategy_family=self.strategy_family,
            is_applicable=applicable,
            permission="WAIT" if applicable else "REJECT",
            score=100.0 if applicable else 0.0,
            confidence=1.0 if applicable else 0.0,
            hard_veto=applicable,
            reason="TOKYO_OBSERVATION_ONLY_FOR_XAUUSD" if applicable else "NOT_TOKYO_SESSION",
            blocking_layer="TIME" if applicable else "NONE",
            required_next_evidence="NY_OR_LONDON_SESSION" if applicable else "NOT_APPLICABLE",
            context=context,
            poi=poi,
            trigger=trigger,
            risk=risk,
        )
