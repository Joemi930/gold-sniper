# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from typing import Any

from strategies.base_strategy import StrategyModule, strategy_result


class FvgNearOnly(StrategyModule):
    strategy_id = "FVG_NEAR_ONLY"
    strategy_family = "FVG"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        if not poi["is_fvg"]:
            applicable = False
            permission = "REJECT"
            score = 0
            reason = "NO_FVG_CONTINUATION_POI"
            layer = "POI"
        elif poi["distance_bucket"] == "20+":
            applicable = True
            permission = "REJECT"
            score = 25
            reason = "FVG_TOO_FAR_20_PLUS"
            layer = "POI"
        else:
            applicable = True
            score = 20
            score += {"0-5": 25, "5-10": 18, "10-20": 5}.get(poi["distance_bucket"], 0)
            score += 20 if context["draw_on_liquidity"] != "UNKNOWN" else 0
            score += 15 if context["order_flow"] != "UNKNOWN" and poi["aligned_with_order_flow"] else 0
            score += 10 if context["delivery_phase"] in {"EXPANSION", "RETRACEMENT", "TREND_CONTINUATION", "SNIPER_PULLBACK"} else 0
            score += 10 if context["session_bucket"] in {"NY", "LONDON"} else 0
            score += 10 if trigger["has_sweep"] and trigger["has_displacement"] and trigger["has_retest"] else 0
            if poi["distance_bucket"] == "10-20":
                permission = "CANDIDATE"
                reason = "FVG_DISTANCE_10_20_NEEDS_STRONGER_CONTEXT"
            elif trigger["trigger_kind"] == "MICRO_CHOCH" and not (trigger["has_sweep"] and trigger["has_retest"]):
                permission = "STANDARD_SHADOW" if score >= 70 else "CANDIDATE"
                reason = "FVG_NEAR_MICRO_CHOCH_NOT_PREMIUM"
            else:
                permission = "PREMIUM_SHADOW" if score >= 85 else "STANDARD_SHADOW"
                reason = "FVG_NEAR_CONTEXT_ALIGNED"
            layer = "NONE"
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
            required_next_evidence="SWEEP_DISPLACEMENT_RETEST" if permission != "PREMIUM_SHADOW" else "NONE",
            context=context,
            poi=poi,
            trigger=trigger,
            risk=risk,
        )
