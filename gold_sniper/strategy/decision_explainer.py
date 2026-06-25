"""Decision explanation helpers for the unified XAUUSD shadow pipeline."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


SHADOW_ONLY = "SHADOW_ONLY"
UNKNOWN = "UNKNOWN"

PIPELINE_STAGE_ORDER = [
    "NEWS",
    "SESSION",
    "SPREAD_RISK",
    "HTF_CONTEXT",
    "DOL",
    "LIQUIDITY",
    "POI",
    "SESSION_PREMIUM_OTE",
    "MICRO_CONFIRMATION",
    "RISK",
]

CRITICAL_STEPS = {
    "news_permission",
    "session_permission",
    "spread_risk_placeholder",
    "htf_context_placeholder",
    "dol_placeholder",
    "liquidity_state",
    "poi_placeholder",
    "session_premium_ote_gate",
    "micro_confirmation",
    "risk_placeholder",
}


@dataclass(frozen=True)
class DecisionExplainerConfig:
    max_lines: int = 8


@dataclass(frozen=True)
class DecisionExplanation:
    decision: str = UNKNOWN
    mode: str = UNKNOWN
    primary_reason: str = UNKNOWN
    summary: str = ""
    pipeline_stage: str = UNKNOWN
    blocking_reasons: list[str] = field(default_factory=list)
    missing_conditions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    kasper_alignment: str = UNKNOWN
    trade_readiness: str = UNKNOWN
    next_action: str = "WAIT_FOR_MORE_CONTEXT"
    explanation_lines: list[str] = field(default_factory=list)
    evidence_digest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def explain_unified_decision(
    decision_payload: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
    config: DecisionExplainerConfig | None = None,
) -> DecisionExplanation:
    """Summarize a unified shadow decision without changing the decision."""
    cfg = config or DecisionExplainerConfig()
    payload = deepcopy(decision_payload) if isinstance(decision_payload, dict) else {}
    _ = deepcopy(context) if isinstance(context, dict) else {}

    decision = _normalize_text(payload.get("decision"), UNKNOWN)
    mode = _normalize_text(payload.get("mode"), UNKNOWN)
    missing = _as_list(payload.get("missing_conditions"))
    warnings = _as_list(payload.get("warnings"))
    passed = _as_list(payload.get("passed_steps"))
    failed = _as_list(payload.get("failed_steps"))
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    digest = _build_evidence_digest(evidence)

    primary_reason = _choose_primary_reason(decision, missing, payload)
    stage = _map_reason_to_stage(primary_reason)
    blocking = _blocking_reasons(decision, missing)
    readiness = _trade_readiness(decision)
    alignment = _kasper_alignment(decision, passed, missing)
    next_action = _next_action(stage, decision)
    summary = _summary(decision, primary_reason, stage, readiness, passed)
    lines = _explanation_lines(decision, stage, primary_reason, missing, warnings, passed, cfg.max_lines)

    if decision in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"} and not CRITICAL_STEPS.issubset(set(passed)):
        warnings = list(dict.fromkeys(warnings + ["ENTER_WITH_INCOMPLETE_EVIDENCE"]))
        alignment = "PARTIAL"

    return DecisionExplanation(
        decision=decision,
        mode=mode,
        primary_reason=primary_reason,
        summary=summary,
        pipeline_stage=stage,
        blocking_reasons=blocking,
        missing_conditions=missing,
        warnings=warnings,
        passed_steps=passed,
        failed_steps=failed,
        kasper_alignment=alignment,
        trade_readiness=readiness,
        next_action=next_action,
        explanation_lines=lines,
        evidence_digest=digest,
    )


def _normalize_text(value: Any, default: str) -> str:
    if value in (None, "", [], {}):
        return default
    return str(value).upper()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, "", [], {}):
        return []
    return [str(value)]


def _choose_primary_reason(decision: str, missing: list[str], payload: dict[str, Any]) -> str:
    if payload.get("hard_veto_reason"):
        return str(payload["hard_veto_reason"])
    if missing:
        return missing[0]
    if decision in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}:
        return "COMPLETE_PIPELINE"
    if decision in {"WAIT", "WAIT_FOR_TRIGGER", "WAIT_FOR_BETTER_PRICE", "WATCH_ONLY", "REJECT"}:
        return "DECISION_REASON_MISSING"
    return UNKNOWN


def _blocking_reasons(decision: str, missing: list[str]) -> list[str]:
    if decision == "REJECT":
        return list(missing)
    return [reason for reason in missing if "BLOCK" in reason or "REJECT" in reason or "VETO" in reason]


def _trade_readiness(decision: str) -> str:
    if decision in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}:
        return "READY_SHADOW"
    if decision in {"WAIT", "WAIT_FOR_TRIGGER", "WAIT_FOR_BETTER_PRICE", "WATCH_ONLY"}:
        return "WAITING_EVIDENCE"
    if decision == "REJECT":
        return "BLOCKED"
    return UNKNOWN


def _kasper_alignment(decision: str, passed_steps: list[str], missing: list[str]) -> str:
    if decision == "REJECT":
        return "MISALIGNED"
    if decision in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}:
        return "ALIGNED" if CRITICAL_STEPS.issubset(set(passed_steps)) else "PARTIAL"
    if decision in {"WAIT", "WAIT_FOR_TRIGGER", "WAIT_FOR_BETTER_PRICE", "WATCH_ONLY"}:
        return "PARTIAL" if passed_steps or missing else UNKNOWN
    return UNKNOWN


def _summary(decision: str, reason: str, stage: str, readiness: str, passed_steps: list[str]) -> str:
    if decision == "REJECT":
        return f"Setup rejected by {stage}: {reason}. Trade readiness is {readiness}."
    if decision in {"WAIT", "WAIT_FOR_TRIGGER", "WAIT_FOR_BETTER_PRICE", "WATCH_ONLY"}:
        return f"Pipeline waiting at {stage}: {reason}. Evidence is incomplete."
    if decision in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}:
        if CRITICAL_STEPS.issubset(set(passed_steps)):
            return "ENTER shadow possible: P1 engine pipeline is complete. No live order is authorized."
        return "ENTER shadow flagged with incomplete evidence. No live order is authorized."
    return "Decision payload is incomplete; explanation is unknown."


def _next_action(stage: str, decision: str) -> str:
    if decision in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}:
        return "AUDIT_SHADOW_ENTRY_ONLY"
    if decision in {"WAIT_FOR_TRIGGER"}:
        return "WAIT_FOR_MICRO_TRIGGER"
    if decision in {"WAIT_FOR_BETTER_PRICE"}:
        return "WAIT_FOR_BETTER_PRICE_OR_POI"
    if decision in {"WATCH_ONLY"}:
        return "MONITOR_FOR_IMPROVING_CONDITIONS"
    return {
        "NEWS": "WAIT_FOR_NEWS_CLEARANCE",
        "SESSION": "WAIT_FOR_LONDON_NY_SESSION",
        "SPREAD_RISK": "WAIT_FOR_SPREAD_RISK_CONTEXT",
        "HTF_CONTEXT": "WAIT_FOR_HTF_BIAS",
        "DOL": "WAIT_FOR_DRAW_ON_LIQUIDITY",
        "LIQUIDITY": "WAIT_FOR_CLEAR_LIQUIDITY_STORY",
        "POI": "WAIT_FOR_VALID_POI",
        "SESSION_PREMIUM_OTE": "WAIT_FOR_VALID_SESSION_PREMIUM_OTE_CONTEXT",
        "MICRO_CONFIRMATION": "WAIT_FOR_VALID_MICRO_TRIGGER",
        "RISK": "WAIT_FOR_RISK_CLEARANCE",
    }.get(stage, "WAIT_FOR_MORE_CONTEXT")


def _explanation_lines(
    decision: str,
    stage: str,
    reason: str,
    missing: list[str],
    warnings: list[str],
    passed: list[str],
    max_lines: int,
) -> list[str]:
    lines = [
        f"Decision: {decision}",
        f"Primary stage: {stage}",
        f"Primary reason: {reason}",
    ]
    if passed:
        lines.append(f"Passed steps: {', '.join(passed[:6])}")
    if missing:
        lines.append(f"Missing/blocking: {', '.join(missing[:4])}")
    if warnings:
        lines.append(f"Warnings: {', '.join(warnings[:3])}")
    if decision in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}:
        lines.append("Shadow only: no live order is authorized.")
    return lines[:max_lines]


def _build_evidence_digest(evidence: dict[str, Any]) -> dict[str, Any]:
    digest: dict[str, Any] = {}
    mode = evidence.get("mode")
    if mode is not None:
        digest["mode"] = mode
    for step in (
        "news_permission",
        "session_permission",
        "liquidity_state",
        "poi_placeholder",
        "session_premium_ote_gate",
        "micro_confirmation",
        "risk_placeholder",
    ):
        step_payload = evidence.get(step)
        if not isinstance(step_payload, dict):
            continue
        compact: dict[str, Any] = {"reason": step_payload.get("reason")}
        value = step_payload.get("value")
        if isinstance(value, dict):
            for key in ("state", "decision", "grade"):
                if key in value:
                    compact[key] = value[key]
        digest[step] = {key: value for key, value in compact.items() if value is not None}
    return digest


def _map_reason_to_stage(reason: str) -> str:
    reason = str(reason or "").upper()
    if reason.startswith("NEWS_"):
        return "NEWS"
    if reason.startswith("SESSION_VETO") or reason.startswith("SESSION_CONTEXT"):
        return "SESSION"
    if reason.startswith("SPREAD_RISK"):
        return "SPREAD_RISK"
    if reason.startswith("HTF_"):
        return "HTF_CONTEXT"
    if reason.startswith("DOL_"):
        return "DOL"
    if reason.startswith("LIQUIDITY_"):
        return "LIQUIDITY"
    if reason.startswith("POI_"):
        return "POI"
    if reason.startswith("SESSION_PREMIUM_OTE_"):
        return "SESSION_PREMIUM_OTE"
    if reason.startswith("MICRO_CONFIRMATION_"):
        return "MICRO_CONFIRMATION"
    if reason.startswith("RISK_"):
        return "RISK"
    if reason == "COMPLETE_PIPELINE":
        return "COMPLETE_PIPELINE"
    return UNKNOWN


def explain_professional_decision(
    *,
    decision: Any = None,
    evidence: Any = None,
    hard_veto: Any = None,
    scorecard: Any = None,
    risk_plan: Any = None,
) -> DecisionExplanation:
    """Explain a professional decision without deciding — shadow-only, broker-free."""
    decision_str = _normalize_text(
        getattr(decision, "decision", None) or (decision.get("decision") if isinstance(decision, dict) else None),
        UNKNOWN,
    )
    grade = (
        getattr(decision, "setup_grade", None)
        or (decision.get("setup_grade") if isinstance(decision, dict) else None)
        or UNKNOWN
    )
    blocked_stage = (
        getattr(decision, "blocked_stage", None)
        or getattr(hard_veto, "blocked_stage", None)
        or (decision.get("blocked_stage") if isinstance(decision, dict) else None)
    )
    veto_code = (
        getattr(decision, "veto_code", None)
        or getattr(hard_veto, "veto_code", None)
        or (decision.get("veto_code") if isinstance(decision, dict) else None)
    )
    missing = (
        getattr(decision, "missing_evidence", None)
        or getattr(scorecard, "missing_evidence", None)
        or (decision.get("missing_evidence") if isinstance(decision, dict) else [])
        or []
    )
    soft = (
        getattr(decision, "soft_issues", None)
        or getattr(scorecard, "soft_issues", None)
        or (decision.get("soft_issues") if isinstance(decision, dict) else [])
        or []
    )
    primary_reasons = (
        getattr(decision, "primary_reasons", None)
        or (decision.get("primary_reasons") if isinstance(decision, dict) else [])
        or []
    )
    replay_invalid = (
        getattr(decision, "replay_invalid", None)
        or getattr(hard_veto, "replay_invalid", None)
        or (decision.get("replay_invalid") if isinstance(decision, dict) else False)
    )

    candidates = [
        veto_code,
        primary_reasons[0] if primary_reasons else None,
        str(blocked_stage) if blocked_stage and str(blocked_stage) != "NONE" else None,
        missing[0] if missing else None,
        soft[0] if soft else None,
    ]
    primary_reason = next((str(item) for item in candidates if item), "UNKNOWN")
    stage = _map_reason_to_stage(str(primary_reason))
    blocking = [r for r in missing if "BLOCK" in r or "REJECT" in r or "VETO" in r]
    warnings_list = [str(w) for w in soft]
    readiness = _trade_readiness(decision_str)
    alignment = _kasper_alignment(decision_str, [], list(missing))
    next_action = _next_action(stage, decision_str)
    summary = _summary_professional(decision_str, primary_reason, stage, readiness, replay_invalid)

    lines = [
        f"Decision: {decision_str}",
        f"Grade: {grade}",
        f"Primary stage: {stage}",
        f"Primary reason: {primary_reason}",
    ]
    if replay_invalid:
        lines.append("Replay data invalid — shadow entry not authorized.")
    if blocking:
        lines.append(f"Blocking: {', '.join(blocking[:4])}")
    if warnings_list:
        lines.append(f"Warnings: {', '.join(warnings_list[:3])}")
    lines.append("Shadow only: no live order is authorized.")

    return DecisionExplanation(
        decision=decision_str,
        mode="SHADOW_ONLY",
        primary_reason=str(primary_reason),
        summary=summary,
        pipeline_stage=stage,
        blocking_reasons=blocking,
        missing_conditions=list(dict.fromkeys(missing)),
        warnings=list(dict.fromkeys(warnings_list)),
        passed_steps=[],
        failed_steps=[],
        kasper_alignment=alignment,
        trade_readiness=readiness,
        next_action=next_action,
        explanation_lines=lines[:8],
        evidence_digest={
            "grade": grade,
            "blocked_stage": str(blocked_stage) if blocked_stage else None,
            "veto_code": veto_code,
            "replay_invalid": replay_invalid,
        },
    )


def _summary_professional(
    decision: str,
    reason: str,
    stage: str,
    readiness: str,
    replay_invalid: bool,
) -> str:
    if replay_invalid:
        return f"Replay data invalid at {stage}: {reason}. Setup cannot be evaluated."
    if decision == "REJECT":
        return f"Setup rejected by {stage}: {reason}. Trade readiness is {readiness}."
    if decision in {"WAIT_FOR_TRIGGER", "WAIT_FOR_BETTER_PRICE"}:
        return f"Pipeline waiting at {stage}: {reason}. Evidence is incomplete."
    if decision in {"ENTER_FULL", "ENTER_REDUCED"}:
        return "ENTER shadow possible: P1 engine pipeline complete. No live order is authorized."
    return "Watch only: insufficient evidence for shadow entry."


__all__ = [
    "DecisionExplainerConfig",
    "DecisionExplanation",
    "explain_professional_decision",
    "explain_unified_decision",
]
