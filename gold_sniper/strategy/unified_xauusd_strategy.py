"""Phase 1 unified XAUUSD strategy skeleton.

This module is intentionally pure and shadow-only. It reads an event/context
payload, applies a minimal sequential pipeline, and returns an explainable
decision without broker, network, file, or environment side effects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from gold_sniper.strategy.atr_risk_model import evaluate_atr_risk_plan
from gold_sniper.strategy.decision_explainer import explain_unified_decision
from gold_sniper.strategy.kasper_ict_scenario_engine import evaluate_kasper_ict_scenarios
from gold_sniper.strategy.liquidity_state_machine import evaluate_liquidity_state
from gold_sniper.strategy.micro_confirmation_engine import evaluate_micro_confirmation
from gold_sniper.strategy.poi_quality_gate import evaluate_poi_quality
from gold_sniper.strategy.professional_decision_engine import (
    DECISION_ENTER_FULL,
    DECISION_ENTER_REDUCED,
    DECISION_REJECT as PROFESSIONAL_DECISION_REJECT,
    DECISION_WAIT_FOR_BETTER_PRICE,
    DECISION_WAIT_FOR_TRIGGER,
    DECISION_WATCH_ONLY,
    SHADOW_ONLY as PROFESSIONAL_SHADOW_ONLY,
    evaluate_professional_decision,
)
from gold_sniper.strategy.session_premium_ote_gate import evaluate_session_premium_ote_gate
from gold_sniper.strategy.xauusd_killzone_model import evaluate_xauusd_killzone


SHADOW_ONLY = "SHADOW_ONLY"
DECISION_ENTER = "ENTER"
DECISION_WAIT = "WAIT"
DECISION_REJECT = "REJECT"


@dataclass(frozen=True)
class UnifiedXauusdDecision:
    decision: str
    mode: str = SHADOW_ONLY
    setup_type: str = "UNKNOWN"
    score: float = 0.0
    confidence: float = 0.0
    hard_veto: bool = False
    hard_veto_reason: str | None = None
    missing_conditions: list[str] = field(default_factory=list)
    passed_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explanation: str = ""
    explanation_detail: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    setup_grade: str = "D"
    confidence_score: float = 0.0
    risk_multiplier: float = 0.0
    required_execution_mode: str = PROFESSIONAL_SHADOW_ONLY
    soft_issues: list[str] = field(default_factory=list)
    professional_decision: dict[str, Any] | None = None
    modulation_summary: dict[str, Any] = field(default_factory=dict)
    final_risk_multiplier: float = 0.0
    session_grade: str = "UNKNOWN"
    timing_quality_score: float = 0.0
    timing_risk_multiplier: float = 0.0
    liquidity_risk_multiplier: float = 0.0
    atr_adjusted_risk_pct: float = 0.0
    risk_band: str = "UNKNOWN"
    micro_template: str = "UNKNOWN"
    poi_execution_readiness: str = "UNKNOWN"
    micro_execution_readiness: str = "UNKNOWN"
    timing_execution_readiness: str = "UNKNOWN"
    liquidity_execution_readiness: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_unified_xauusd_strategy(event: dict[str, Any] | None) -> UnifiedXauusdDecision:
    """Evaluate the future central XAUUSD pipeline in shadow mode only."""
    payload = event if isinstance(event, dict) else {}
    agents = _extract_agents(payload)
    passed_steps: list[str] = []
    failed_steps: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {"mode": SHADOW_ONLY}
    scenario_payload = _evaluate_kasper_scenarios(payload, agents)
    evidence["kasper_ict_scenarios"] = scenario_payload
    resolved_setup_type = _resolve_setup_type(payload, agents, scenario_payload)
    minimum_micro_template = _minimum_micro_template_from_scenario(scenario_payload, resolved_setup_type)
    killzone = _evaluate_killzone_from_payload(payload)
    if killzone:
        evidence["xauusd_killzone"] = {
            "passed": bool(killzone.get("session_allowed")),
            "reason": killzone.get("reason"),
            "value": killzone,
        }

    news = _evaluate_news_permission(agents.get("agent_6"))
    _record_step("news_permission", news, passed_steps, failed_steps, missing, evidence)
    if news["hard_veto"]:
        professional = _professional_decision_from_evidence(evidence, resolved_setup_type)
        return _decision(
            professional.decision,
            passed_steps,
            failed_steps,
            professional.missing_evidence,
            warnings + professional.soft_issues,
            evidence,
            hard_veto=professional.hard_veto,
            hard_veto_reason=professional.hard_veto_reason,
            setup_type=resolved_setup_type,
            score=professional.score_breakdown.get("total", 0.0),
            confidence=professional.confidence_score,
            explanation=professional.explanation,
            professional_result=professional.to_dict(),
        )
    if not news["passed"]:
        return _decision(
            DECISION_WAIT,
            passed_steps,
            failed_steps,
            missing,
            warnings,
            evidence,
            setup_type=resolved_setup_type,
            explanation="Waiting for Agent6 news context before any unified strategy decision.",
        )

    session = _evaluate_session_permission(agents.get("agent_7"))
    _record_step("session_permission", session, passed_steps, failed_steps, missing, evidence)
    if session["hard_veto"]:
        professional = _professional_decision_from_evidence(evidence, resolved_setup_type)
        return _decision(
            professional.decision,
            passed_steps,
            failed_steps,
            professional.missing_evidence,
            warnings + professional.soft_issues,
            evidence,
            hard_veto=professional.hard_veto,
            hard_veto_reason=professional.hard_veto_reason,
            setup_type=resolved_setup_type,
            score=professional.score_breakdown.get("total", 0.0),
            confidence=professional.confidence_score,
            explanation=professional.explanation,
            professional_result=professional.to_dict(),
        )
    if not session["passed"]:
        return _decision(
            DECISION_WAIT,
            passed_steps,
            failed_steps,
            missing,
            warnings,
            evidence,
            setup_type=resolved_setup_type,
            explanation="Waiting for a tradable London/NY/overlap session label.",
        )

    poi_result: dict[str, Any] | None = None
    liquidity_result: dict[str, Any] | None = None
    for step_name, result in (
        ("spread_risk_placeholder", _evaluate_presence(payload, agents, "spread_risk", "SPREAD_RISK_CONTEXT_MISSING")),
        ("htf_context_placeholder", _evaluate_presence(payload, agents, "htf_context", "HTF_CONTEXT_MISSING", agent_id="agent_1")),
        ("dol_placeholder", _evaluate_presence(payload, agents, "draw_on_liquidity", "DOL_MISSING", agent_id="agent_1")),
        ("liquidity_state", _evaluate_liquidity_state(agents.get("agent_3"), payload, agents)),
    ):
        _record_step(step_name, result, passed_steps, failed_steps, missing, evidence)
        if step_name == "liquidity_state" and isinstance(result.get("value"), dict):
            liquidity_result = result["value"]

    for step_name, result in (
        ("poi_placeholder", _evaluate_poi(agents.get("agent_2"), payload, liquidity_result)),
    ):
        _record_step(step_name, result, passed_steps, failed_steps, missing, evidence)
        if step_name == "poi_placeholder" and isinstance(result.get("value"), dict):
            poi_result = result["value"]

    for step_name, result in (
        ("fibonacci_ote_placeholder", _evaluate_presence(payload, agents, "ote", "OTE_CONTEXT_MISSING", agent_id="agent_4")),
        ("session_premium_ote_gate", _evaluate_session_premium_ote_gate(payload, agents, liquidity_result, poi_result)),
        (
            "micro_confirmation",
            _evaluate_micro_confirmation(agents.get("agent_5"), payload, agents, poi_result, minimum_micro_template),
        ),
        ("risk_placeholder", _evaluate_atr_risk(payload, agents)),
    ):
        _record_step(step_name, result, passed_steps, failed_steps, missing, evidence)

    if missing:
        professional = _professional_decision_from_evidence(evidence, resolved_setup_type)
        return _decision(
            professional.decision,
            passed_steps,
            failed_steps,
            professional.missing_evidence,
            warnings + professional.soft_issues,
            evidence,
            setup_type=resolved_setup_type,
            score=professional.score_breakdown.get("total", 0.0),
            confidence=professional.confidence_score,
            hard_veto=professional.hard_veto,
            hard_veto_reason=professional.hard_veto_reason,
            explanation=professional.explanation,
            professional_result=professional.to_dict(),
        )

    professional = _professional_decision_from_evidence(evidence, resolved_setup_type)
    return _decision(
        professional.decision,
        passed_steps,
        failed_steps,
        professional.missing_evidence,
        warnings + professional.soft_issues + ["Professional decision is theoretical shadow output only; no broker action is authorized."],
        evidence,
        setup_type=resolved_setup_type,
        score=professional.score_breakdown.get("total", 0.0),
        confidence=professional.confidence_score,
        hard_veto=professional.hard_veto,
        hard_veto_reason=professional.hard_veto_reason,
        explanation=professional.explanation,
        professional_result=professional.to_dict(),
    )


def _safe_get(mapping: Any, path: list[str], default: Any = None) -> Any:
    current = mapping
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _extract_agents(payload: dict[str, Any]) -> dict[str, Any]:
    direct = payload.get("agents")
    if isinstance(direct, dict):
        return {key: value for key, value in direct.items() if isinstance(value, dict)}
    return {
        key: value
        for key, value in payload.items()
        if isinstance(key, str) and key.startswith("agent_") and isinstance(value, dict)
    }


def _evaluate_news_permission(agent6: dict[str, Any] | None) -> dict[str, Any]:
    if not agent6:
        return _step(False, "NEWS_CONTEXT_MISSING")
    hard_veto = bool(
        agent6.get("hard_veto")
        or agent6.get("news_veto")
        or agent6.get("veto")
        or agent6.get("hard_filter_pass") is False
        or agent6.get("news_clear") is False
        or str(agent6.get("permission", "")).upper() == "REJECT"
    )
    if hard_veto:
        return _step(False, "NEWS_HARD_VETO", hard_veto=True)
    return _step(True, "NEWS_CLEAR")


def _evaluate_session_permission(agent7: dict[str, Any] | None) -> dict[str, Any]:
    if not agent7:
        return _step(False, "SESSION_CONTEXT_MISSING")
    raw_session = (
        agent7.get("session_label")
        or agent7.get("session")
        or agent7.get("session_bucket")
        or agent7.get("current_session")
    )
    session = str(raw_session or "").upper()
    if session in {"TOKYO", "ASIA", "ASIAN"}:
        return _step(False, "SESSION_VETO_TOKYO_ASIA", hard_veto=True, value=session)
    if session == "OFF_SESSION":
        return _step(False, "SESSION_OFF_SESSION_NON_TRADABLE", hard_veto=True, value=session)
    if session in {"LONDON", "NY", "NEW_YORK", "NEW YORK", "OVERLAP", "LONDON_NY", "LONDON_KILLZONE", "NY_KILLZONE", "SILVER_BULLET", "LONDON_CLOSE"}:
        return _step(True, "SESSION_ALLOWED", value=session)
    return _step(False, "SESSION_CONTEXT_UNKNOWN", value=session or None)


def _evaluate_presence(
    payload: dict[str, Any],
    agents: dict[str, Any],
    field_name: str,
    missing_reason: str,
    agent_id: str | None = None,
) -> dict[str, Any]:
    candidates = []
    if agent_id:
        candidates.append(agents.get(agent_id, {}))
    context = payload.get("context", {})
    candidates.extend([payload, context if isinstance(context, dict) else {}])
    for candidate in candidates:
        value = _safe_get(candidate, [field_name])
        if value not in (None, "", "UNKNOWN", "NONE", [], {}):
            return _step(True, f"{field_name.upper()}_AVAILABLE", value=value)
    return _step(False, missing_reason)


def _evaluate_poi(agent2: dict[str, Any] | None, payload: dict[str, Any], liquidity_state: dict[str, Any] | None = None) -> dict[str, Any]:
    context = payload.get("context", {})
    context_for_poi = dict(context) if isinstance(context, dict) else {}
    if isinstance(liquidity_state, dict):
        context_for_poi.setdefault("liquidity_state", liquidity_state.get("state"))
        context_for_poi.setdefault("dol_status", liquidity_state.get("dol_status"))
        context_for_poi.setdefault("liquidity_event_type", liquidity_state.get("event_type"))
    for candidate in (agent2 or {}, payload, context if isinstance(context, dict) else {}):
        for key in ("poi", "best_shadow_poi", "active_ob", "strategy_input_poi_stack", "poi_candidates"):
            value = _safe_get(candidate, [key])
            if value not in (None, "", "UNKNOWN", "NONE", [], {}):
                selected_poi = value[0] if isinstance(value, list) and value else value
                if not isinstance(selected_poi, dict):
                    return _step(False, "POI_INVALID_SHAPE", value=value)
                quality = evaluate_poi_quality(selected_poi, context_for_poi)
                quality_payload = quality.to_dict()
                if quality.decision == "REJECT":
                    return _step(False, f"POI_QUALITY_REJECT_{quality.grade}", value=quality_payload)
                if quality.decision == "WATCH":
                    return _step(False, f"POI_QUALITY_WATCH_{quality.grade}", value=quality_payload)
                return _step(True, "POI_QUALITY_ACCEPT", value=quality_payload)
    return _step(False, "POI_MISSING")


def _evaluate_liquidity_state(agent3: dict[str, Any] | None, payload: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("context", {})
    liquidity_context = dict(context) if isinstance(context, dict) else {}
    liquidity_context.setdefault("setup_type", _infer_setup_type(payload, agents))
    liquidity_payload = {}
    if isinstance(agent3, dict):
        value = agent3.get("liquidity")
        liquidity_payload = value if isinstance(value, dict) else agent3
    if liquidity_payload:
        liquidity_context = {
            key: liquidity_context[key]
            for key in (
                "setup_type",
                "draw_on_liquidity",
                "dol",
                "dol_status",
                "liquidity_target_open",
                "liquidity_target_consumed",
                "dol_aligned",
                "htf_aligned",
                "order_flow_aligned",
            )
            if key in liquidity_context
        }
        liquidity_context.setdefault("setup_type", _infer_setup_type(payload, agents))
    liquidity = evaluate_liquidity_state(liquidity_payload, liquidity_context)
    liquidity_result = liquidity.to_dict()
    if liquidity.decision == "BLOCKS_SETUP":
        return _step(False, f"LIQUIDITY_BLOCKS_SETUP_{liquidity.state}", value=liquidity_result)
    if liquidity.decision == "WATCH":
        return _step(False, f"LIQUIDITY_WATCH_{liquidity.state}", value=liquidity_result)
    return _step(True, f"LIQUIDITY_SUPPORTS_{liquidity.state}", value=liquidity_result)


def _evaluate_session_premium_ote_gate(
    payload: dict[str, Any],
    agents: dict[str, Any],
    liquidity_state: dict[str, Any] | None,
    poi_quality: dict[str, Any] | None,
) -> dict[str, Any]:
    context = payload.get("context", {})
    gate_context = dict(context) if isinstance(context, dict) else {}
    gate_context.setdefault("setup_type", _infer_setup_type(payload, agents))
    if isinstance(liquidity_state, dict):
        gate_context.setdefault("liquidity_state", liquidity_state.get("state"))
        gate_context.setdefault("dol_status", liquidity_state.get("dol_status"))
        gate_context.setdefault("liquidity_event_type", liquidity_state.get("event_type"))
    if isinstance(poi_quality, dict):
        gate_context.setdefault("poi_quality_decision", poi_quality.get("decision"))
    agent1 = agents.get("agent_1")
    if isinstance(agent1, dict):
        for key in ("direction", "bias", "htf_bias", "order_flow", "draw_on_liquidity"):
            if key in agent1:
                gate_context.setdefault(key, agent1.get(key))
        htf_context = agent1.get("htf_context")
        if htf_context and "direction" not in gate_context:
            normalized_htf = str(htf_context).upper()
            if "BULL" in normalized_htf:
                gate_context.setdefault("direction", "LONG")
            elif "BEAR" in normalized_htf:
                gate_context.setdefault("direction", "SHORT")
    agent4 = agents.get("agent_4")
    if isinstance(agent4, dict):
        ote = agent4.get("ote")
        if isinstance(ote, dict):
            for key, value in ote.items():
                gate_context.setdefault(key, value)
        else:
            for key, value in agent4.items():
                gate_context.setdefault(key, value)
    agent6 = agents.get("agent_6")
    if isinstance(agent6, dict):
        for key in (
            "news_clear",
            "news_veto",
            "high_impact_news",
            "pre_news_lockout",
            "post_news_stealth",
            "news_normalized",
            "calendar_status",
            "hard_filter_pass",
        ):
            if key in agent6:
                gate_context.setdefault(key, agent6.get(key))
    agent7 = agents.get("agent_7")
    if isinstance(agent7, dict):
        for key in (
            "session",
            "session_label",
            "session_bucket",
            "current_session",
            "trading_allowed",
            "session_allowed",
        ):
            if key in agent7:
                gate_context.setdefault(key, agent7.get(key))
    gate = evaluate_session_premium_ote_gate(gate_context, gate_context.get("setup_type"))
    gate_payload = gate.to_dict()
    if gate.decision == "BLOCK":
        return _step(False, f"SESSION_PREMIUM_OTE_BLOCK_{gate.grade}", value=gate_payload)
    if gate.decision == "WATCH":
        return _step(False, f"SESSION_PREMIUM_OTE_WATCH_{gate.grade}", value=gate_payload)
    return _step(True, "SESSION_PREMIUM_OTE_PASS", value=gate_payload)


def _evaluate_micro_confirmation(
    agent5: dict[str, Any] | None,
    payload: dict[str, Any],
    agents: dict[str, Any],
    poi_quality: dict[str, Any] | None,
    minimum_micro_template: str | None = None,
) -> dict[str, Any]:
    if isinstance(poi_quality, dict) and poi_quality.get("decision") == "REJECT":
        return _step(
            False,
            "MICRO_REJECT_INHERITED_FROM_POI",
            value={
                "decision": "NOT_EVALUATED",
                "status": "NOT_EVALUATED_INHERITED_POI_REJECT",
                "upstream_blocker": "POI",
                "poi_quality_decision": poi_quality.get("decision"),
                "poi_quality_grade": poi_quality.get("grade"),
            },
        )
    context = payload.get("context", {})
    micro_context = dict(context) if isinstance(context, dict) else {}
    micro_context.setdefault("setup_type", _infer_setup_type(payload, agents))
    if minimum_micro_template:
        micro_context.setdefault("minimum_micro_template", minimum_micro_template)
    micro = evaluate_micro_confirmation(agent5, micro_context, poi_quality)
    micro_payload = micro.to_dict()
    if micro.decision == "REJECT":
        return _step(False, f"MICRO_CONFIRMATION_REJECT_{micro.grade}", value=micro_payload)
    if micro.decision == "WATCH":
        return _step(False, f"MICRO_CONFIRMATION_WATCH_{micro.grade}", value=micro_payload)
    return _step(True, "MICRO_CONFIRMATION_CONFIRMED", value=micro_payload)


def _evaluate_killzone_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = (
        payload.get("timestamp")
        or payload.get("time")
        or payload.get("datetime")
        or payload.get("bar_time")
        or _safe_get(payload, ["context", "timestamp"])
    )
    if not timestamp:
        return None
    return evaluate_xauusd_killzone(timestamp).to_dict()


def _evaluate_atr_risk(payload: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    risk_payload = payload.get("risk") if isinstance(payload.get("risk"), dict) else {}
    entry_price = _first_available((payload, context, risk_payload), ("entry_price",))
    direction = (
        payload.get("direction")
        or context.get("direction")
        or risk_payload.get("direction")
        or _safe_get(agents.get("agent_1"), ["direction"])
    )
    atr_value = _first_available((payload, context, risk_payload), ("atr", "atr_value"))
    structural_stop = (
        _first_available((payload, context, risk_payload), ("structural_stop",))
    )
    if entry_price is None or atr_value is None:
        fallback = _evaluate_presence(payload, agents, "risk", "RISK_CONTEXT_MISSING")
        if fallback["passed"] and isinstance(fallback.get("value"), dict):
            value = dict(fallback["value"])
            value.setdefault("risk_multiplier", 1.0)
            value.setdefault("adjusted_risk_pct", value.get("risk_pct", 1.0))
            value.setdefault("risk_band", "UNKNOWN")
            fallback["value"] = value
        return fallback
    plan = evaluate_atr_risk_plan(
        entry_price,
        direction,
        atr_value,
        structural_stop=structural_stop,
        setup_grade=_preprofessional_risk_grade(payload, agents),
    )
    payload_value = plan.to_dict()
    if plan.risk_valid:
        return _step(True, "ATR_RISK_VALID", value=payload_value)
    return _step(False, plan.reason, value=payload_value)


def _preprofessional_risk_grade(payload: dict[str, Any], agents: dict[str, Any]) -> str:
    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    risk_payload = payload.get("risk") if isinstance(payload.get("risk"), dict) else {}
    raw = (
        payload.get("setup_grade")
        or context.get("setup_grade")
        or risk_payload.get("setup_grade")
        or _safe_get(agents.get("agent_1"), ["setup_grade"])
        or _safe_get(agents.get("agent_2"), ["setup_grade"])
    )
    normalized = str(raw or "").upper()
    if normalized in {"A_PLUS", "A+", "A", "B", "C", "D"}:
        return normalized
    return "A_PLUS"


def _first_available(mappings: tuple[Any, ...], keys: tuple[str, ...]) -> Any:
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        for key in keys:
            if key in mapping and mapping[key] is not None:
                return mapping[key]
    return None


def _evaluate_kasper_scenarios(payload: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
    context = dict(payload.get("context", {})) if isinstance(payload.get("context"), dict) else {}
    for agent_id in ("agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"):
        agent = agents.get(agent_id)
        if isinstance(agent, dict):
            for key, value in agent.items():
                context.setdefault(key, value)
    poi_rating = context.get("poi_rating")
    if isinstance(poi_rating, dict):
        context.setdefault("poi_stars", poi_rating.get("stars"))
        context.setdefault("poi_grade", poi_rating.get("grade"))
    risk_plan = context.get("risk_plan")
    if isinstance(risk_plan, dict):
        context.setdefault("risk_valid", risk_plan.get("risk_valid"))
    elif payload.get("risk") not in (None, "", {}, []):
        context.setdefault("risk_valid", True)
    session = str(
        context.get("session")
        or context.get("session_label")
        or context.get("session_bucket")
        or context.get("current_session")
        or ""
    ).upper()
    if session:
        context.setdefault("session", session)
        context.setdefault("session_allowed", session in {"LONDON", "NY", "NEW_YORK", "NEW YORK", "OVERLAP", "LONDON_NY", "LONDON_KILLZONE", "NY_KILLZONE", "SILVER_BULLET", "LONDON_CLOSE"})
        if session == "OFF_SESSION":
            context.setdefault("session_allowed", False)
    if context.get("htf_context_available") is None:
        htf_context = str(context.get("htf_context") or context.get("bias") or context.get("htf_bias") or "").upper()
        if htf_context and htf_context not in {"UNKNOWN", "NONE"}:
            context["htf_context_available"] = True
    if context.get("dol_available") is None:
        dol = str(context.get("draw_on_liquidity") or context.get("dol") or "").upper()
        if dol and dol not in {"UNKNOWN", "NONE"}:
            context["dol_available"] = True
    if context.get("micro_trigger") is None:
        trigger = str(context.get("trigger_kind") or context.get("trigger_type") or "").upper()
        if trigger not in {"", "NONE", "UNKNOWN"} and context.get("displacement_present") and context.get("reclaim_confirmed") and context.get("retest_confirmed"):
            context["micro_trigger"] = True
    return evaluate_kasper_ict_scenarios(context)


def _record_step(
    step_name: str,
    result: dict[str, Any],
    passed_steps: list[str],
    failed_steps: list[str],
    missing: list[str],
    evidence: dict[str, Any],
) -> None:
    evidence[step_name] = {
        "passed": result["passed"],
        "reason": result["reason"],
        "value": result.get("value"),
    }
    if result["passed"]:
        passed_steps.append(step_name)
    else:
        failed_steps.append(step_name)
        missing.append(result["reason"])


def _step(passed: bool, reason: str, *, hard_veto: bool = False, value: Any = None) -> dict[str, Any]:
    return {"passed": passed, "reason": reason, "hard_veto": hard_veto, "value": value}


def _decision(
    decision: str,
    passed_steps: list[str],
    failed_steps: list[str],
    missing: list[str],
    warnings: list[str],
    evidence: dict[str, Any],
    *,
    setup_type: str = "UNKNOWN",
    score: float | None = None,
    confidence: float | None = None,
    hard_veto: bool = False,
    hard_veto_reason: str | None = None,
    explanation: str,
    professional_result: dict[str, Any] | None = None,
) -> UnifiedXauusdDecision:
    resolved_score = _score_from_steps(passed_steps) if score is None else score
    resolved_confidence = min(resolved_score / 100.0, 1.0) if confidence is None else confidence
    unique_missing = list(dict.fromkeys(missing))
    setup_grade = professional_result.get("setup_grade", "D") if professional_result else "D"
    confidence_score = professional_result.get("confidence_score", resolved_confidence) if professional_result else resolved_confidence
    risk_multiplier = professional_result.get("risk_multiplier", 0.0) if professional_result else 0.0
    required_execution_mode = professional_result.get("required_execution_mode", SHADOW_ONLY) if professional_result else SHADOW_ONLY
    soft_issues = professional_result.get("soft_issues", []) if professional_result else []
    modulation_fields = _modulation_fields(evidence, professional_result)
    decision_payload = {
        "decision": decision,
        "mode": SHADOW_ONLY,
        "setup_type": setup_type,
        "score": resolved_score,
        "confidence": resolved_confidence,
        "hard_veto": hard_veto,
        "hard_veto_reason": hard_veto_reason,
        "missing_conditions": unique_missing,
        "passed_steps": list(passed_steps),
        "failed_steps": list(failed_steps),
        "warnings": list(warnings),
        "explanation": explanation,
        "evidence": evidence,
        "setup_grade": setup_grade,
        "confidence_score": confidence_score,
        "risk_multiplier": risk_multiplier,
        "required_execution_mode": required_execution_mode,
        "soft_issues": soft_issues,
        "professional_decision": professional_result,
        **modulation_fields,
    }
    enriched_evidence = dict(evidence)
    enriched_evidence["funnel_trace"] = _build_funnel_trace(decision_payload, enriched_evidence)
    decision_payload["evidence"] = enriched_evidence
    explanation_detail = explain_unified_decision(_legacy_explainer_payload(decision_payload)).to_dict()
    enriched_evidence["decision_explainer"] = explanation_detail
    return UnifiedXauusdDecision(
        decision=decision,
        setup_type=setup_type,
        score=resolved_score,
        confidence=resolved_confidence,
        hard_veto=hard_veto,
        hard_veto_reason=hard_veto_reason,
        missing_conditions=unique_missing,
        passed_steps=passed_steps,
        failed_steps=failed_steps,
        warnings=warnings,
        explanation=explanation,
        explanation_detail=explanation_detail,
        evidence=enriched_evidence,
        setup_grade=setup_grade,
        confidence_score=confidence_score,
        risk_multiplier=risk_multiplier,
        required_execution_mode=required_execution_mode,
        soft_issues=soft_issues,
        professional_decision=professional_result,
        modulation_summary=modulation_fields["modulation_summary"],
        final_risk_multiplier=modulation_fields["final_risk_multiplier"],
        session_grade=modulation_fields["session_grade"],
        timing_quality_score=modulation_fields["timing_quality_score"],
        timing_risk_multiplier=modulation_fields["timing_risk_multiplier"],
        liquidity_risk_multiplier=modulation_fields["liquidity_risk_multiplier"],
        atr_adjusted_risk_pct=modulation_fields["atr_adjusted_risk_pct"],
        risk_band=modulation_fields["risk_band"],
        micro_template=modulation_fields["micro_template"],
        poi_execution_readiness=modulation_fields["poi_execution_readiness"],
        micro_execution_readiness=modulation_fields["micro_execution_readiness"],
        timing_execution_readiness=modulation_fields["timing_execution_readiness"],
        liquidity_execution_readiness=modulation_fields["liquidity_execution_readiness"],
    )


def _score_from_steps(passed_steps: list[str]) -> float:
    return round(min(len(passed_steps) * 10.0, 100.0), 2)


def _modulation_fields(evidence: dict[str, Any], professional_result: dict[str, Any] | None) -> dict[str, Any]:
    breakdown = professional_result.get("score_breakdown", {}) if isinstance(professional_result, dict) else {}
    risk_modulators = breakdown.get("risk_modulators", {}) if isinstance(breakdown, dict) else {}
    killzone = _stage_value(evidence, "xauusd_killzone")
    timing = _stage_value(evidence, "session_premium_ote_gate")
    liquidity = _stage_value(evidence, "liquidity_state")
    atr = _stage_value(evidence, "risk_placeholder")
    poi = _stage_value(evidence, "poi_placeholder")
    micro = _stage_value(evidence, "micro_confirmation")
    final_risk = professional_result.get("risk_multiplier", 0.0) if isinstance(professional_result, dict) else 0.0
    return {
        "modulation_summary": dict(risk_modulators) if isinstance(risk_modulators, dict) else {},
        "final_risk_multiplier": final_risk,
        "session_grade": str(killzone.get("session_grade") or "UNKNOWN") if isinstance(killzone, dict) else "UNKNOWN",
        "timing_quality_score": _float_from(timing, "timing_quality_score"),
        "timing_risk_multiplier": _float_from(timing, "risk_multiplier"),
        "liquidity_risk_multiplier": _float_from(liquidity, "risk_multiplier"),
        "atr_adjusted_risk_pct": _float_from(atr, "adjusted_risk_pct"),
        "risk_band": str(atr.get("risk_band") or "UNKNOWN") if isinstance(atr, dict) else "UNKNOWN",
        "micro_template": str(micro.get("template_name") or "UNKNOWN") if isinstance(micro, dict) else "UNKNOWN",
        "poi_execution_readiness": str(poi.get("execution_readiness") or "UNKNOWN") if isinstance(poi, dict) else "UNKNOWN",
        "micro_execution_readiness": str(micro.get("execution_readiness") or "UNKNOWN") if isinstance(micro, dict) else "UNKNOWN",
        "timing_execution_readiness": str(timing.get("execution_readiness") or "UNKNOWN") if isinstance(timing, dict) else "UNKNOWN",
        "liquidity_execution_readiness": str(liquidity.get("execution_readiness") or "UNKNOWN") if isinstance(liquidity, dict) else "UNKNOWN",
    }


def _stage_value(evidence: dict[str, Any], key: str) -> Any:
    stage = evidence.get(key, {})
    return stage.get("value") if isinstance(stage, dict) else None


def _float_from(mapping: Any, key: str, default: float = 0.0) -> float:
    if not isinstance(mapping, dict):
        return default
    try:
        return float(mapping.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _professional_decision_from_evidence(evidence: dict[str, Any], setup_type: str):
    return evaluate_professional_decision(evidence, setup_type=setup_type)


def _minimum_micro_template_from_scenario(scenario_payload: dict[str, Any], setup_type: str) -> str | None:
    best = scenario_payload.get("best_scenario") if isinstance(scenario_payload, dict) else {}
    if isinstance(best, dict):
        scenario_type = str(best.get("scenario_type") or "")
        if setup_type and scenario_type and setup_type != scenario_type:
            return None
        value = best.get("minimum_micro_template")
        if value:
            return str(value)
    return None


def _legacy_explainer_payload(decision_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(decision_payload)
    decision = payload.get("decision")
    if decision in {DECISION_ENTER_FULL, DECISION_ENTER_REDUCED}:
        payload["decision"] = DECISION_ENTER
    elif decision in {DECISION_WAIT_FOR_TRIGGER, DECISION_WAIT_FOR_BETTER_PRICE, DECISION_WATCH_ONLY}:
        payload["decision"] = DECISION_WAIT
    elif decision == PROFESSIONAL_DECISION_REJECT:
        payload["decision"] = DECISION_REJECT
    return payload


def _infer_setup_type(payload: dict[str, Any], agents: dict[str, Any]) -> str:
    raw = (
        payload.get("setup_type")
        or _safe_get(payload, ["context", "setup_type"])
        or _safe_get(payload, ["context", "scenario_type"])
        or _safe_get(agents.get("agent_1"), ["setup_type"])
    )
    setup_type = str(raw or "UNKNOWN").upper()
    allowed = {
        "CONTINUATION",
        "TREND_CONTINUATION",
        "REVERSAL",
        "SNIPER_PULLBACK",
        "OBSERVATION",
        "OTE_CONFLUENCE",
        "FAILED_AUCTION",
        "VWAP_M1_SCALP",
        "REVERSAL_AFTER_SWEEP",
    }
    if setup_type in allowed:
        return setup_type
    return "OBSERVATION" if setup_type in {"", "NONE"} else "UNKNOWN"


def _resolve_setup_type(payload: dict[str, Any], agents: dict[str, Any], scenario_payload: dict[str, Any]) -> str:
    best = scenario_payload.get("best_scenario") if isinstance(scenario_payload, dict) else {}
    scenario_type = str((best or {}).get("scenario_type") or "").upper() if isinstance(best, dict) else ""
    inferred = _infer_setup_type(payload, agents)
    if inferred != "UNKNOWN" and not (isinstance(best, dict) and best.get("tradable")):
        return inferred
    scenario_confidence = float((best or {}).get("confidence") or 0.0) if isinstance(best, dict) else 0.0
    if (
        scenario_type
        and scenario_type not in {"UNKNOWN", "OBSERVATION"}
        and (best.get("tradable") or best.get("near_miss") or scenario_confidence >= 0.5)
    ):
        return scenario_type
    if inferred == "UNKNOWN" and _has_known_context_without_setup(payload, agents, scenario_type):
        return "OBSERVATION"
    return "OBSERVATION" if inferred == "UNKNOWN" and scenario_type == "OBSERVATION" else inferred


def _has_known_context_without_setup(payload: dict[str, Any], agents: dict[str, Any], scenario_type: str) -> bool:
    if scenario_type == "OBSERVATION":
        return True
    session = str(
        _safe_get(payload, ["context", "session"])
        or _safe_get(payload, ["context", "session_label"])
        or _safe_get(agents.get("agent_7"), ["session"])
        or _safe_get(agents.get("agent_7"), ["session_label"])
        or ""
    ).upper()
    news_known = agents.get("agent_6") not in (None, {})
    session_known = session not in {"", "UNKNOWN", "NONE"}
    return news_known and session_known


def _build_funnel_trace(decision_payload: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    missing = [str(item) for item in decision_payload.get("missing_conditions", []) or []]
    exit_reason = missing[0] if missing else "PIPELINE_COMPLETE"
    exit_stage = _reason_to_stage(exit_reason, evidence)
    scenario = evidence.get("kasper_ict_scenarios", {})
    best = scenario.get("best_scenario", {}) if isinstance(scenario, dict) else {}
    near_miss = bool(isinstance(best, dict) and best.get("near_miss")) or _is_near_miss_missing_set(missing, decision_payload)
    poi_reason = _safe_get(evidence, ["poi_placeholder", "reason"])
    micro_reason = _safe_get(evidence, ["micro_confirmation", "reason"])
    upstream_blocker = _upstream_blocker(evidence)
    return {
        "exit_stage": exit_stage,
        "exit_reason": exit_reason,
        "hard_veto": bool(decision_payload.get("hard_veto")),
        "near_miss": near_miss,
        "own_reject": _is_own_reject(exit_stage, exit_reason),
        "inherited_reject": _is_inherited_reject(exit_reason, upstream_blocker),
        "upstream_blocker": upstream_blocker,
        "poi_status": _poi_status(poi_reason, upstream_blocker),
        "micro_status": _micro_status(micro_reason, poi_reason),
        "best_scenario": best.get("scenario_type") if isinstance(best, dict) else None,
        "best_scenario_status": best.get("status") if isinstance(best, dict) else None,
        "stage_results": {
            "news": evidence.get("news_permission", {}),
            "session": evidence.get("session_permission", {}),
            "market_weather": evidence.get("market_weather", {}),
            "scenario": best if isinstance(best, dict) else {},
            "liquidity": evidence.get("liquidity_state", {}),
            "poi": evidence.get("poi_placeholder", {}),
            "premium_ote": evidence.get("session_premium_ote_gate", {}),
            "micro": evidence.get("micro_confirmation", {}),
            "risk": evidence.get("risk_placeholder", {}),
        },
    }


def _reason_to_stage(reason: str, evidence: dict[str, Any]) -> str:
    mapping = (
        ("NEWS", "news"),
        ("SESSION", "session"),
        ("HTF", "htf"),
        ("DOL", "dol"),
        ("LIQUIDITY", "liquidity"),
        ("POI", "poi"),
        ("OTE", "premium_ote"),
        ("PREMIUM", "premium_ote"),
        ("MICRO", "micro"),
        ("RISK", "risk"),
        ("SCENARIO", "scenario"),
    )
    for prefix, stage in mapping:
        if reason.startswith(prefix) or prefix in reason:
            return stage
    for stage_name, evidence_key in (
        ("news", "news_permission"),
        ("session", "session_permission"),
        ("liquidity", "liquidity_state"),
        ("poi", "poi_placeholder"),
        ("premium_ote", "session_premium_ote_gate"),
        ("micro", "micro_confirmation"),
        ("risk", "risk_placeholder"),
    ):
        if evidence.get(evidence_key, {}).get("passed") is False:
            return stage_name
    return "complete" if reason == "PIPELINE_COMPLETE" else "unknown"


def _is_near_miss_missing_set(missing: list[str], decision_payload: dict[str, Any]) -> bool:
    fatal = ("NEWS_", "SESSION_VETO", "SESSION_OFF_SESSION", "RISK_", "TRIGGER_OUTSIDE", "POI_QUALITY_REJECT")
    return (
        decision_payload.get("decision") == DECISION_WAIT
        and not decision_payload.get("hard_veto")
        and 0 < len(missing) <= 2
        and not any(any(reason.startswith(prefix) for prefix in fatal) for reason in missing)
    )


def _upstream_blocker(evidence: dict[str, Any]) -> str | None:
    for stage, key in (
        ("NEWS", "news_permission"),
        ("SESSION", "session_permission"),
        ("LIQUIDITY", "liquidity_state"),
        ("POI", "poi_placeholder"),
    ):
        if evidence.get(key, {}).get("passed") is False:
            return stage
    return None


def _poi_status(reason: Any, upstream_blocker: str | None) -> str:
    value = str(reason or "")
    if value.startswith("POI_QUALITY_ACCEPT"):
        return "POI_ACCEPT"
    if value.startswith("POI_QUALITY_WATCH"):
        return "POI_WATCH_NEAR_MISS"
    if value.startswith("POI_QUALITY_REJECT"):
        return "POI_REJECT_OWN"
    if value == "POI_MISSING":
        return "POI_REJECT_INHERITED" if upstream_blocker and upstream_blocker != "POI" else "POI_MISSING"
    return value or "POI_NOT_REACHED"


def _micro_status(reason: Any, poi_reason: Any) -> str:
    value = str(reason or "")
    poi_value = str(poi_reason or "")
    if value == "MICRO_REJECT_INHERITED_FROM_POI" or poi_value.startswith("POI_QUALITY_REJECT"):
        return "NOT_EVALUATED_INHERITED_POI_REJECT"
    if value.startswith("MICRO_CONFIRMATION_REJECT"):
        return "MICRO_REJECT_OWN"
    if value.startswith("MICRO_CONFIRMATION_WATCH"):
        return "MICRO_WATCH_NEAR_MISS"
    if value.startswith("MICRO_CONFIRMATION_CONFIRMED"):
        return "MICRO_CONFIRMED"
    return value or "MICRO_NOT_REACHED"


def _is_own_reject(exit_stage: str, exit_reason: str) -> bool:
    return exit_reason.startswith(("POI_QUALITY_REJECT", "MICRO_CONFIRMATION_REJECT", "LIQUIDITY_BLOCKS_SETUP"))


def _is_inherited_reject(exit_reason: str, upstream_blocker: str | None) -> bool:
    return bool(upstream_blocker and exit_reason in {"MICRO_REJECT_INHERITED_FROM_POI", "POI_MISSING"})
