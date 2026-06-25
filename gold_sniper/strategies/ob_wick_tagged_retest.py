# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from typing import Any

from strategies.base_strategy import StrategyModule, strategy_result


class ObWickTaggedRetest(StrategyModule):
    strategy_id = "OB_WICK_TAGGED_RETEST"
    strategy_family = "OB"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        lifecycle = str(poi.get("lifecycle_normalized") or poi.get("poi_state") or "UNKNOWN")
        applicable = bool(poi["is_ob"] and lifecycle == "WICK_TAGGED")
        score = 45
        score += 15 if poi["deepest_penetration_pct"] <= 30 else -25
        score += 15 if poi["close_inside_count"] == 0 else -20
        score += 15 if trigger["has_displacement"] else 0
        score += 10 if trigger["has_retest"] else 0
        trigger_kind = str(trigger.get("trigger_kind") or "NONE").upper()
        if not poi["is_ob"]:
            permission, reason, layer = "REJECT", "OB_NOT_EXPOSED_BY_AGENT2", "POI"
        elif lifecycle == "UNKNOWN":
            permission, reason, layer = "REJECT", "OB_LIFECYCLE_UNKNOWN", "POI"
        elif lifecycle in {"CONSUMED", "INVALIDATED", "STALE"}:
            permission, reason, layer = "REJECT", f"OB_{lifecycle}", "POI"
        elif lifecycle != "WICK_TAGGED":
            permission, reason, layer = "REJECT", "OB_LIFECYCLE_NOT_WICK_TAGGED", "POI"
        elif poi["deepest_penetration_pct"] > 30 or poi["close_inside_count"] > 0:
            permission, reason, layer = "REJECT", "OB_WICK_TAG_INVALID_CONTEXT", "POI"
        elif context.get("session_bucket") == "TOKYO" or not context.get("trading_allowed", True):
            permission, reason, layer = "WAIT", "OB_SESSION_VETO", "TIME"
        elif context.get("draw_on_liquidity") == "UNKNOWN" or context.get("order_flow") == "UNKNOWN":
            permission, reason, layer = "WAIT", "OB_DOL_NOT_ALIGNED", "DOL"
        elif trigger["has_displacement"] and trigger["has_retest"]:
            permission, reason, layer = "STANDARD_SHADOW", "OB_CANDIDATE_READY", "NONE"
        elif trigger_kind in {"", "NONE", "UNKNOWN", "NA"}:
            permission, reason, layer = "CANDIDATE", "OB_TRIGGER_NONE", "TRIGGER"
        elif not trigger["has_retest"]:
            permission, reason, layer = "CANDIDATE", "OB_NO_RETEST", "TRIGGER"
        else:
            permission, reason, layer = "CANDIDATE", "OB_CANDIDATE_READY", "TRIGGER"
        return strategy_result(
            strategy_id=self.strategy_id,
            strategy_family=self.strategy_family,
            is_applicable=applicable,
            permission=permission,
            score=score if applicable else 0,
            confidence=min(max(score, 0) / 100, 1.0),
            hard_veto=False,
            reason=reason,
            blocking_layer=layer,
            required_next_evidence="DISPLACEMENT_RETEST" if permission == "WAIT" else "NONE",
            context=context,
            poi=poi,
            trigger=trigger,
            risk=risk,
        )
