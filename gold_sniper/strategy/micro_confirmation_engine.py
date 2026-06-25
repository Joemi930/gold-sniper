"""Kasper/ICT micro confirmation engine for the unified XAUUSD strategy.

This module is a pure shadow-only pipeline brick. It confirms entry timing
inside an already accepted POI; it never decides or executes a live trade.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


CONFIRMED = "CONFIRMED"
WATCH = "WATCH"
REJECT = "REJECT"
TEMPLATE_REVERSAL_STRICT = "reversal_strict"
TEMPLATE_CONTINUATION_LIGHT = "continuation_light"
TEMPLATE_FAILED_AUCTION_RECLAIM = "failed_auction_reclaim"
TEMPLATE_SESSION_REVERSAL_MEDIUM = "session_reversal_medium"


@dataclass(frozen=True)
class MicroTemplate:
    name: str
    requires_sweep: bool
    requires_displacement: bool
    requires_reclaim: bool
    requires_retest: bool
    confirm_score: float
    watch_score: float


MICRO_TEMPLATES = {
    TEMPLATE_REVERSAL_STRICT: MicroTemplate(
        name=TEMPLATE_REVERSAL_STRICT,
        requires_sweep=True,
        requires_displacement=True,
        requires_reclaim=True,
        requires_retest=True,
        confirm_score=78.0,
        watch_score=48.0,
    ),
    TEMPLATE_CONTINUATION_LIGHT: MicroTemplate(
        name=TEMPLATE_CONTINUATION_LIGHT,
        requires_sweep=False,
        requires_displacement=True,
        requires_reclaim=True,
        requires_retest=False,
        confirm_score=68.0,
        watch_score=38.0,
    ),
    TEMPLATE_FAILED_AUCTION_RECLAIM: MicroTemplate(
        name=TEMPLATE_FAILED_AUCTION_RECLAIM,
        requires_sweep=True,
        requires_displacement=True,
        requires_reclaim=True,
        requires_retest=False,
        confirm_score=74.0,
        watch_score=45.0,
    ),
    TEMPLATE_SESSION_REVERSAL_MEDIUM: MicroTemplate(
        name=TEMPLATE_SESSION_REVERSAL_MEDIUM,
        requires_sweep=True,
        requires_displacement=True,
        requires_reclaim=True,
        requires_retest=False,
        confirm_score=72.0,
        watch_score=42.0,
    ),
}


@dataclass(frozen=True)
class MicroConfirmationConfig:
    min_displacement_score: float = 0.60
    confirm_score: float = 72.0
    watch_score: float = 40.0


@dataclass(frozen=True)
class MicroConfirmationResult:
    decision: str
    trigger_type: str
    score: float
    confidence: float
    grade: str
    hard_reject: bool
    template_name: str = TEMPLATE_CONTINUATION_LIGHT
    template_requirements: dict[str, bool] = field(default_factory=dict)
    execution_readiness: str = "WATCH"
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    quality_flags: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_micro_confirmation(
    micro: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
    poi_quality: dict[str, Any] | None = None,
    config: MicroConfirmationConfig | None = None,
) -> MicroConfirmationResult:
    """Evaluate shadow micro timing confirmation inside a validated POI."""
    cfg = config or MicroConfirmationConfig()
    micro_data = deepcopy(micro) if isinstance(micro, dict) else {}
    ctx = deepcopy(context) if isinstance(context, dict) else {}
    poi = deepcopy(poi_quality) if isinstance(poi_quality, dict) else {}
    template = _resolve_template(ctx, micro_data)

    reasons: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []
    flags: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    template_requirements = _template_requirements(template)
    evidence["template_name"] = template.name
    evidence["template_requirements"] = template_requirements
    score = 0.0

    trigger_type = _normalize_trigger_type(_first_present(micro_data, ["trigger_type", "trigger_kind", "trigger", "micro_trigger"]))
    evidence["trigger_type"] = trigger_type

    poi_decision = str(_first_present(poi, ["decision"], "")).upper()
    evidence["poi_quality_decision"] = poi_decision or "UNKNOWN"
    if poi_decision == "REJECT":
        flags["hard_reject"] = True
        reasons.append("POI_REJECTED_NO_MICRO_CONFIRMATION")
        return _build_result(REJECT, trigger_type, 0.0, True, reasons, warnings, missing, flags, evidence, template, False)
    if poi_decision == "WATCH":
        flags["poi_watch_caps_decision"] = True
        warnings.append("POI_WATCH_MICRO_MAX_WATCH")
    elif poi_decision == "ACCEPT":
        reasons.append("POI_ACCEPTED_FOR_MICRO_CONFIRMATION")
        score += 12.0
    else:
        missing.append("POI_QUALITY_DECISION_MISSING")
        flags["poi_watch_caps_decision"] = True

    if not micro_data:
        missing.append("MICRO_PAYLOAD_MISSING")
        return _build_result(WATCH, trigger_type, score, False, reasons, warnings, missing, flags, evidence, template, False)

    if trigger_type == "UNKNOWN":
        missing.append("MICRO_TRIGGER_TYPE_MISSING")

    inside_poi = _truthy(_first_present(micro_data, ["poi_context_valid", "inside_poi", "trigger_inside_poi", "poi_bounds_respected"]))
    evidence["trigger_inside_poi"] = inside_poi
    if not inside_poi:
        flags["hard_reject"] = True
        reasons.append("TRIGGER_OUTSIDE_POI")
        return _build_result(REJECT, trigger_type, 0.0, True, reasons, warnings, missing, flags, evidence, template, False)
    reasons.append("TRIGGER_INSIDE_POI")
    score += 12.0

    setup_type = str(_first_present(ctx, ["setup_type"], _first_present(micro_data, ["setup_type"], "UNKNOWN"))).upper()
    evidence["setup_type"] = setup_type

    sweep_present = _has_micro_sweep(micro_data)
    evidence["micro_sweep_present"] = sweep_present
    if sweep_present:
        reasons.append("MICRO_SWEEP_PRESENT")
        score += 10.0
    elif template.requires_sweep:
        missing.append("MICRO_SWEEP_MISSING_FOR_TEMPLATE")
        if setup_type == "REVERSAL":
            missing.append("MICRO_SWEEP_MISSING_FOR_REVERSAL")

    displacement = _has_displacement(micro_data, cfg)
    evidence["displacement_present"] = displacement
    if displacement:
        reasons.append("MICRO_DISPLACEMENT_CONFIRMED")
        score += 18.0
    elif template.requires_displacement:
        missing.append("DISPLACEMENT_MISSING")
        if _first_present(micro_data, ["displacement_score", "micro_shift_strength", "impulse_score"]) is not None:
            warnings.append("DISPLACEMENT_TOO_WEAK")

    reclaim = _truthy(_first_present(micro_data, [
        "reclaim_confirmed",
        "acceptance_confirmed",
        "market_structure_reclaimed",
        "closed_back_inside",
        "mss_confirmed",
    ]))
    evidence["reclaim_or_acceptance_present"] = reclaim
    if reclaim:
        reasons.append("RECLAIM_OR_ACCEPTANCE_CONFIRMED")
        score += 16.0
    elif template.requires_reclaim:
        missing.append("RECLAIM_OR_ACCEPTANCE_MISSING")

    retest = _truthy(_first_present(micro_data, ["retest_confirmed", "has_retest", "first_retest", "micro_retest_valid", "entry_retest_valid"]))
    evidence["retest_present"] = retest
    if retest:
        reasons.append("LOGICAL_RETEST_CONFIRMED")
        score += 16.0
    elif template.requires_retest:
        missing.append("RETEST_MISSING")

    if _truthy(_first_present(micro_data, ["extended_move", "move_extended", "entry_chasing", "too_far_after_trigger"])):
        warnings.append("EXTENDED_MOVE_NO_RETEST")
        flags["extended_move"] = True
        score = min(score, cfg.watch_score + 5.0)

    if trigger_type == "MICRO_CHOCH" and (not displacement or not reclaim or not retest):
        warnings.append("MICRO_CHOCH_ALONE_NOT_DECISIVE")
    if trigger_type == "BOS" and (not retest or poi_decision != "ACCEPT"):
        warnings.append("BOS_NOT_ENTRY_TRIGGER")

    if _truthy(_first_present(ctx, ["news_veto", "news_not_clear"])) or ctx.get("news_clear") is False:
        flags["hard_reject"] = True
        reasons.append("NEWS_VETO_NO_MICRO_CONFIRMATION")
        return _build_result(REJECT, trigger_type, 0.0, True, reasons, warnings, missing, flags, evidence, template, False)

    if str(_first_present(ctx, ["session_permission", "session_allowed"], "ALLOWED")).upper() in {"REJECT", "FALSE", "BLOCKED"}:
        flags["hard_reject"] = True
        reasons.append("SESSION_VETO_NO_MICRO_CONFIRMATION")
        return _build_result(REJECT, trigger_type, 0.0, True, reasons, warnings, missing, flags, evidence, template, False)

    required_checks = {
        "sweep": (not template.requires_sweep) or sweep_present,
        "displacement": (not template.requires_displacement) or displacement,
        "reclaim": (not template.requires_reclaim) or reclaim,
        "retest": (not template.requires_retest) or retest,
    }
    complete = poi_decision == "ACCEPT" and inside_poi and all(required_checks.values())
    evidence["template_required_checks"] = required_checks

    hard_reject = bool(flags.get("hard_reject"))
    score = max(0.0, min(score, 100.0))
    if hard_reject:
        decision = REJECT
    elif complete and score >= template.confirm_score:
        decision = CONFIRMED
    elif score >= template.watch_score or reasons:
        decision = WATCH
    else:
        decision = REJECT

    if flags.get("poi_watch_caps_decision") and decision == CONFIRMED:
        decision = WATCH
    if trigger_type in {"MICRO_CHOCH", "BOS"} and missing and decision == CONFIRMED:
        decision = WATCH

    return _build_result(decision, trigger_type, score, hard_reject, reasons, warnings, missing, flags, evidence, template, complete)


def _build_result(
    decision: str,
    trigger_type: str,
    score: float,
    hard_reject: bool,
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
    evidence: dict[str, Any],
    template: MicroTemplate,
    complete: bool,
) -> MicroConfirmationResult:
    score = round(max(0.0, min(score, 100.0)), 2)
    return MicroConfirmationResult(
        decision=decision,
        trigger_type=trigger_type,
        score=score,
        confidence=round(score / 100.0, 3),
        grade=_grade(score, hard_reject),
        hard_reject=hard_reject,
        template_name=template.name,
        template_requirements=_template_requirements(template),
        execution_readiness=_execution_readiness(decision, hard_reject, complete, template),
        reasons=list(dict.fromkeys(reasons)),
        warnings=list(dict.fromkeys(warnings)),
        missing_evidence=list(dict.fromkeys(missing)),
        quality_flags=flags,
        evidence=evidence,
    )


def _template_requirements(template: MicroTemplate) -> dict[str, bool]:
    return {
        "requires_sweep": template.requires_sweep,
        "requires_displacement": template.requires_displacement,
        "requires_reclaim": template.requires_reclaim,
        "requires_retest": template.requires_retest,
    }


def _resolve_template(ctx: dict[str, Any], micro_data: dict[str, Any]) -> MicroTemplate:
    raw = (
        _first_present(micro_data, ["minimum_micro_template", "micro_template", "template"])
        or _first_present(ctx, ["minimum_micro_template", "micro_template", "required_micro_template"])
    )
    if raw is None:
        setup_type = str(_first_present(ctx, ["setup_type"], _first_present(micro_data, ["setup_type"], ""))).upper()
        if setup_type in {"REVERSAL", "REVERSAL_AFTER_SWEEP"}:
            raw = TEMPLATE_REVERSAL_STRICT
    name = str(raw or TEMPLATE_CONTINUATION_LIGHT).lower()
    return MICRO_TEMPLATES.get(name, MICRO_TEMPLATES[TEMPLATE_CONTINUATION_LIGHT])


def _execution_readiness(decision: str, hard_reject: bool, complete: bool, template: MicroTemplate) -> str:
    del template
    if hard_reject or decision == REJECT:
        return "BLOCKED"
    if decision == CONFIRMED and complete:
        return "READY"
    if decision == WATCH:
        return "WATCH"
    return "BLOCKED"


def _normalize_trigger_type(raw_value: Any) -> str:
    raw = str(raw_value or "").upper()
    if raw in {"MICRO_CHOCH", "CHOCH", "MICRO-CHOCH"}:
        return "MICRO_CHOCH"
    if raw == "BOS":
        return "BOS"
    if "SWEEP" in raw and ("RECLAIM" in raw or "RETEST" in raw):
        return "SWEEP_RECLAIM_RETEST"
    return "UNKNOWN" if raw in {"", "NONE", "UNKNOWN", "NA"} else raw


def _has_micro_sweep(micro: dict[str, Any]) -> bool:
    return _truthy(_first_present(micro, ["micro_sweep_present", "sweep_detected", "micro_sweep", "liquidity_sweep", "stop_hunt"]))


def _has_displacement(micro: dict[str, Any], cfg: MicroConfirmationConfig) -> bool:
    explicit = _first_present(micro, ["displacement_present", "has_displacement", "micro_displacement"])
    if explicit is not None:
        return _truthy(explicit)
    score = _to_float(_first_present(micro, ["displacement_score", "micro_shift_strength", "impulse_score"], None), None)
    return score is not None and score >= cfg.min_displacement_score


def _first_present(mapping: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in {"1", "TRUE", "YES", "Y", "PASS", "OPEN", "DETECTED", "ALIGNED", "CONFIRMED", "ACCEPT"}
    return bool(value)


def _to_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _grade(score: float, hard_reject: bool) -> str:
    if hard_reject:
        return "F"
    if score >= 85:
        return "A"
    if score >= 72:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"
