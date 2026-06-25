# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from typing import Any

from strategies.base_strategy import StrategyModule, strategy_result


class FvgSweepDisplacementRetest(StrategyModule):
    strategy_id = "FVG_SWEEP_DISPLACEMENT_RETEST"
    strategy_family = "FVG"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        applicable = bool(poi["is_fvg"])
        score = 0
        if applicable:
            score += 20 if trigger["has_sweep"] else 0
            score += 20 if trigger["has_displacement"] else 0
            score += 20 if trigger["has_retest"] else 0
            score += 15 if trigger["inside_poi"] else 0
            score += 15 if poi["distance_bucket"] in {"0-5", "5-10"} else 0
            score += 10 if context["session_bucket"] in {"NY", "LONDON"} else 0
        missing = []
        if not trigger["has_sweep"]:
            missing.append("SWEEP")
        if not trigger["has_displacement"]:
            missing.append("DISPLACEMENT")
        if not trigger["has_retest"]:
            missing.append("RETEST")
        if not trigger["inside_poi"]:
            missing.append("TRIGGER_INSIDE_POI")
        if not applicable:
            permission, reason, layer = "REJECT", "NO_FVG_CONTINUATION_POI", "POI"
        elif missing:
            permission, reason, layer = "WAIT", "MISSING_" + "_".join(missing), "TRIGGER"
        else:
            permission, reason, layer = ("PREMIUM_SHADOW" if score >= 85 else "STANDARD_SHADOW"), "FVG_SWEEP_DISPLACEMENT_RETEST_CONFIRMED", "NONE"
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
            required_next_evidence=";".join(missing) if missing else "NONE",
            context=context,
            poi=poi,
            trigger=trigger,
            risk=risk,
        )
