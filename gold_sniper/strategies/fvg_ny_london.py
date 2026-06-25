# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from typing import Any

from strategies.base_strategy import StrategyModule, strategy_result


class FvgNyLondonOnly(StrategyModule):
    strategy_id = "FVG_NY_LONDON_ONLY"
    strategy_family = "FVG"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        applicable = bool(poi["is_fvg"])
        if not applicable:
            permission, score, reason, layer = "REJECT", 0, "NO_FVG_CONTINUATION_POI", "POI"
        elif context["session_bucket"] == "TOKYO":
            permission, score, reason, layer = "WAIT", 15, "TOKYO_FVG_OBSERVATION_ONLY", "TIME"
        elif context["session_bucket"] not in {"NY", "LONDON"}:
            permission, score, reason, layer = "WAIT", 25, "SESSION_NOT_NY_LONDON", "TIME"
        else:
            score = 55 + (15 if poi["distance_bucket"] in {"0-5", "5-10"} else 0) + (10 if trigger["agent5_pass"] else 0)
            permission = "STANDARD_SHADOW" if score >= 70 else "CANDIDATE"
            reason, layer = "FVG_IN_PROFESSIONAL_SESSION", "NONE"
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
            required_next_evidence="NY_OR_LONDON_PLUS_TRIGGER" if permission in {"WAIT", "CANDIDATE"} else "NONE",
            context=context,
            poi=poi,
            trigger=trigger,
            risk=risk,
        )
