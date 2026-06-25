"""P2-E Phase 7E — Centralized P2-A selected_poi extraction contract.

All agents that consume Agent2's P2-A POI connectivity payload MUST use
this module instead of maintaining their own local extraction logic.

Rules:
- P2A_SELECTED_POI is priority (Agent2's p2a_poi_connectivity.selected_poi)
- P2A_CANDIDATE_FALLBACK is used only when selected_poi is absent
- LEGACY_AGENT2_FALLBACK is used only when P2-A is entirely absent
- No trading logic, no strategy decisions, no broker coupling
"""

from __future__ import annotations

from typing import Any

try:
    from gold_sniper.agents.base_agent import AgentResult
    from gold_sniper.core.blackboard import BlackBoard
except ImportError:
    from agents.base_agent import AgentResult  # type: ignore[no-redef]
    from core.blackboard import BlackBoard  # type: ignore[no-redef]


def safe_dict(value: Any) -> dict[str, Any]:
    """Coerce value to dict, never None."""
    return value if isinstance(value, dict) else {}


def safe_list(value: Any) -> list[Any]:
    """Coerce value to list, never None."""
    return value if isinstance(value, list) else []


def bounds_from_selected_poi(poi: dict[str, Any] | None) -> tuple[float | None, float | None]:
    """Extract (bottom, top) price bounds from a selected POI dict.

    Returns (None, None) if bounds cannot be resolved.
    """
    if not isinstance(poi, dict) or not poi:
        return None, None

    bounds = poi.get("price_bounds")
    if isinstance(bounds, dict):
        low = bounds.get("low", bounds.get("bottom", bounds.get("entry_zone_bottom")))
        high = bounds.get("high", bounds.get("top", bounds.get("entry_zone_top")))
        if low is not None and high is not None:
            try:
                low_f = float(low)
                high_f = float(high)
                return min(low_f, high_f), max(low_f, high_f)
            except (TypeError, ValueError):
                return None, None

    low = poi.get("low", poi.get("bottom", poi.get("entry_zone_bottom")))
    high = poi.get("high", poi.get("top", poi.get("entry_zone_top")))
    if low is not None and high is not None:
        try:
            low_f = float(low)
            high_f = float(high)
            return min(low_f, high_f), max(low_f, high_f)
        except (TypeError, ValueError):
            return None, None

    return None, None


def read_agent2_result(blackboard: BlackBoard) -> AgentResult | None:
    """Read Agent2's result from the blackboard, never raising."""
    try:
        return blackboard.read_sync("agent_results.agent_2")
    except (KeyError, AttributeError):
        return None


def extract_p2a_selected_poi(
    blackboard: BlackBoard,
    *,
    allow_candidate_fallback: bool = True,
    allow_legacy_fallback: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Extract the canonical P2-A selected_poi anchor from Agent2.

    Priority order:
      1. P2A_SELECTED_POI    — Agent2 p2a_poi_connectivity.selected_poi
      2. P2A_CANDIDATE_FALLBACK — first candidate when selected_poi absent
      3. LEGACY_AGENT2_FALLBACK  — legacy Agent2 fields (poi_zone, active_ob, etc.)

    Returns (anchor, diagnostics). anchor is None when no POI with valid bounds
    can be resolved; diagnostics always explains why.
    """
    diagnostics: dict[str, Any] = {
        "source": "NONE",
        "selected_poi_present": False,
        "candidate_count": 0,
        "bounds_present": False,
        "readiness": "UNKNOWN",
        "failure_reason": None,
        "legacy_fallback_used": False,
    }

    agent2_result = read_agent2_result(blackboard)
    payload = safe_dict(agent2_result.payload if agent2_result else {})
    p2a = safe_dict(payload.get("p2a_poi_connectivity"))

    selected = safe_dict(p2a.get("selected_poi"))
    candidates = safe_list(p2a.get("poi_candidates"))

    if selected:
        diagnostics["source"] = "P2A_SELECTED_POI"
    elif allow_candidate_fallback and candidates:
        selected = safe_dict(candidates[0])
        diagnostics["source"] = "P2A_CANDIDATE_FALLBACK"

    if not selected and allow_legacy_fallback:
        try:
            agent2_state = blackboard.get_agent("agent_2") or {}
        except (KeyError, AttributeError):
            agent2_state = {}

        selected = (
            safe_dict(agent2_state.get("poi_zone"))
            or safe_dict(agent2_state.get("active_ob"))
            or safe_dict(payload.get("poi_zone"))
            or safe_dict(payload.get("active_ob"))
            or safe_dict(payload.get("active_fvg"))
        )

        if selected:
            diagnostics["source"] = "LEGACY_AGENT2_FALLBACK"
            diagnostics["legacy_fallback_used"] = True

    diagnostics["selected_poi_present"] = bool(selected)
    diagnostics["candidate_count"] = len(candidates)

    bottom, top = bounds_from_selected_poi(selected)
    if bottom is None or top is None:
        diagnostics["failure_reason"] = "NO_P2A_POI_OR_BOUNDS"
        return None, diagnostics

    anchor = dict(selected)
    anchor["bottom"] = bottom
    anchor["top"] = top
    anchor["entry_zone_bottom"] = bottom
    anchor["entry_zone_top"] = top
    anchor.setdefault(
        "type",
        selected.get("type")
        or selected.get("poi_type")
        or selected.get("poi_type_normalized")
        or "UNKNOWN",
    )
    anchor.setdefault(
        "poi_type",
        selected.get("poi_type")
        or selected.get("poi_type_normalized")
        or anchor.get("type"),
    )
    anchor.setdefault(
        "execution_readiness",
        selected.get("execution_readiness")
        or selected.get("readiness_state")
        or "UNKNOWN",
    )
    anchor.setdefault("source", diagnostics["source"])

    diagnostics["bounds_present"] = True
    diagnostics["readiness"] = str(anchor.get("execution_readiness") or "UNKNOWN")
    return anchor, diagnostics


def consumed_poi_snapshot(anchor: dict[str, Any] | None) -> dict[str, Any]:
    """Produce a compact snapshot of the consumed POI for agent payloads."""
    return {
        "present": bool(anchor),
        "bottom": anchor.get("bottom") if anchor else None,
        "top": anchor.get("top") if anchor else None,
        "type": anchor.get("type") if anchor else None,
        "poi_type": anchor.get("poi_type") if anchor else None,
        "execution_readiness": anchor.get("execution_readiness") if anchor else None,
        "source": anchor.get("source") if anchor else None,
    }
