# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from typing import Any

from strategies.base_strategy import StrategyModule, strategy_result


class ObPartialMitigationWatch(StrategyModule):
    strategy_id = "OB_PARTIAL_MITIGATION_WATCH"
    strategy_family = "OB"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        lifecycle = str(poi.get("lifecycle_normalized") or poi.get("poi_state") or "UNKNOWN")
        applicable = bool(poi["is_ob"] and lifecycle == "PARTIALLY_MITIGATED")
        strong = trigger["has_displacement"] and trigger["has_retest"]
        trigger_kind = str(trigger.get("trigger_kind") or "NONE").upper()
        if not poi["is_ob"]:
            permission, score, reason, layer = "REJECT", 0, "OB_PARTIAL_NOT_EXPOSED", "POI"
        elif lifecycle == "UNKNOWN":
            permission, score, reason, layer = "REJECT", 0, "OB_PARTIAL_STATE_UNKNOWN", "POI"
        elif lifecycle in {"CONSUMED", "INVALIDATED", "STALE"}:
            permission, score, reason, layer = "REJECT", 0, f"OB_PARTIAL_{lifecycle}", "POI"
        elif lifecycle != "PARTIALLY_MITIGATED":
            permission, score, reason, layer = "REJECT", 0, "OB_PARTIAL_STATE_MISMATCH", "POI"
        elif poi["deepest_penetration_pct"] > 50 or poi["close_inside_count"] > 1:
            permission, score, reason, layer = "REJECT", 0, "OB_PARTIAL_TOO_DEEP", "POI"
        elif context.get("draw_on_liquidity") == "UNKNOWN" or context.get("order_flow") == "UNKNOWN":
            permission, score, reason, layer = "WAIT", 35, "OB_PARTIAL_NEEDS_CONTEXT", "DOL"
        elif strong:
            permission, score, reason, layer = "CANDIDATE", 65, "OB_PARTIAL_CANDIDATE_WATCH", "TRIGGER"
        elif trigger_kind in {"", "NONE", "UNKNOWN", "NA"}:
            permission, score, reason, layer = "CANDIDATE", 45, "OB_PARTIAL_NEEDS_CONFIRMATION", "TRIGGER"
        else:
            permission, score, reason, layer = "CANDIDATE", 45, "OB_PARTIAL_NEEDS_CONFIRMATION", "TRIGGER"
        return strategy_result(
            strategy_id=self.strategy_id,
            strategy_family=self.strategy_family,
            is_applicable=applicable,
            permission=permission,
            score=score,
            confidence=min(score / 100, 1.0),
            hard_veto=False,
            reason=reason,
            blocking_layer=layer,
            required_next_evidence="RECLAIM_DISPLACEMENT_RETEST",
            context=context,
            poi=poi,
            trigger=trigger,
            risk=risk,
        )
