# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from typing import Any

from strategies.base_strategy import StrategyModule, strategy_result


class PremiumStrict(StrategyModule):
    strategy_id = "PREMIUM_STRICT"
    strategy_family = "PREMIUM_FILTER"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        blockers = []
        if not context.get("news_clear", True):
            blockers.append("NEWS")
        if context["session_bucket"] not in {"NY", "LONDON"}:
            blockers.append("TIME")
        if context["draw_on_liquidity"] == "UNKNOWN":
            blockers.append("DOL")
        if context["order_flow"] == "UNKNOWN":
            blockers.append("IOF")
        if poi["distance_bucket"] in {"10-20", "20+"}:
            blockers.append("POI_DISTANCE")
        if trigger["trigger_kind"] == "MICRO_CHOCH" and not (trigger["has_sweep"] and trigger["has_retest"]):
            blockers.append("MICRO_CHOCH_ONLY")
        if risk.get("drawdown_context_flag"):
            blockers.append("RISK")
        score = max(0, 100 - 15 * len(blockers))
        permission = "PREMIUM_SHADOW" if not blockers and trigger["agent5_pass"] else ("STANDARD_SHADOW" if score >= 75 else "WAIT")
        return strategy_result(
            strategy_id=self.strategy_id,
            strategy_family=self.strategy_family,
            is_applicable=True,
            permission=permission,
            score=score,
            confidence=min(score / 100, 1.0),
            hard_veto=False,
            reason="PREMIUM_READY" if permission == "PREMIUM_SHADOW" else "PREMIUM_BLOCKED_" + "_".join(blockers or ["AGENT5"]),
            blocking_layer=blockers[0] if blockers else "NONE",
            required_next_evidence=";".join(blockers) if blockers else "NONE",
            context=context,
            poi=poi,
            trigger=trigger,
            risk=risk,
        )
