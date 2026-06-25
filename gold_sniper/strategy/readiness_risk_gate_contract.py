from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class GateBlockerCode(str, Enum):
    NONE = "NONE"

    NOT_SYNERGY_TRUE = "NOT_SYNERGY_TRUE"
    SETUP_NOT_TRADABLE = "SETUP_NOT_TRADABLE"
    SETUP_TYPE_POI_REACTION_NOT_TRADABLE = "SETUP_TYPE_POI_REACTION_NOT_TRADABLE"
    NO_SETUP_CANDIDATE = "NO_SETUP_CANDIDATE"
    NO_TRADABLE_SETUP_CANDIDATE = "NO_TRADABLE_SETUP_CANDIDATE"
    SETUP_GRADE_TOO_LOW = "SETUP_GRADE_TOO_LOW"

    POI_NOT_READY = "POI_NOT_READY"
    MICRO_NOT_READY = "MICRO_NOT_READY"
    LIQUIDITY_NOT_READY = "LIQUIDITY_NOT_READY"
    TIMING_NOT_READY = "TIMING_NOT_READY"
    SESSION_NOT_READY = "SESSION_NOT_READY"
    NEWS_BLOCKED = "NEWS_BLOCKED"
    SPREAD_BLOCKED = "SPREAD_BLOCKED"

    ENTER_ELIGIBILITY_FALSE = "ENTER_ELIGIBILITY_FALSE"
    RISK_NOT_ALLOWED = "RISK_NOT_ALLOWED"
    RISK_MULTIPLIER_ZERO = "RISK_MULTIPLIER_ZERO"
    SETUP_RISK_CAP_ZERO = "SETUP_RISK_CAP_ZERO"

    FINAL_DECISION_WATCH_ONLY = "FINAL_DECISION_WATCH_ONLY"
    FINAL_DECISION_REJECT = "FINAL_DECISION_REJECT"


TRADABLE_SETUP_TYPES = {
    "SWEEP_REVERSAL",
    "REVERSAL_STRICT",
    "REVERSAL_LIGHT",
    "CONTINUATION_STRICT",
    "CONTINUATION_LIGHT",
    "OTE_PULLBACK",
    "FAILED_AUCTION_RECLAIM",
    "SESSION_REVERSAL_MEDIUM",
}


@dataclass(frozen=True)
class ReadinessRiskGateResult:
    synergy_true: bool
    setup_type: str
    setup_grade: str
    setup_tradable: bool
    has_setup_candidate: bool
    has_tradable_setup_candidate: bool
    readiness_state: str
    readiness_reason: str
    readiness_blockers: list[str]
    enter_eligible: bool
    enter_blockers: list[str]
    risk_allowed: bool
    risk_reason: str
    risk_multiplier: float
    final_decision: str
    blockers: list[str]
    primary_blocker: str
    audit: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_readiness_risk_gate(decision: dict[str, Any]) -> ReadinessRiskGateResult:
    setup_type = str(decision.get("setup_type") or "UNKNOWN").upper()
    setup_grade = str(decision.get("setup_grade") or "UNKNOWN").upper()
    final_decision = str(decision.get("decision") or decision.get("action") or "UNKNOWN").upper()

    synergy_true = bool(decision.get("poi_micro_synergy") or _synergy_payload(decision).get("synergy"))
    setup_tradable = setup_type in TRADABLE_SETUP_TYPES

    candidates = _candidate_list(decision.get("setup_candidates"))
    best_candidate = _safe_dict(decision.get("best_setup_candidate"))
    has_setup_candidate = bool(candidates or best_candidate)
    tradable_candidates = [
        candidate
        for candidate in candidates
        if str(candidate.get("candidate_type") or "").upper() in TRADABLE_SETUP_TYPES
    ]
    best_candidate_type = str(best_candidate.get("candidate_type") or "").upper()
    has_tradable_setup_candidate = bool(
        tradable_candidates
        or best_candidate_type in TRADABLE_SETUP_TYPES
    )

    readiness_state = str(decision.get("readiness_state") or "UNKNOWN").upper()
    readiness_reason = str(decision.get("readiness_reason") or "UNKNOWN")
    readiness_by_section = _safe_dict(decision.get("readiness_by_section"))
    non_ready_sections = _safe_dict(decision.get("readiness_non_ready_sections"))
    readiness_missing = [
        str(item)
        for item in decision.get("readiness_missing_ready_blockers") or []
        if item
    ]
    readiness_blockers = _readiness_blockers(readiness_by_section, non_ready_sections, readiness_missing)

    enter_eligible = bool(decision.get("enter_eligible"))
    enter_blockers = _string_list(decision.get("enter_eligibility_blockers"))

    risk_multiplier = _safe_float(decision.get("risk_multiplier"))
    risk_preview = _safe_dict(decision.get("risk_preview"))
    risk_preview_reason = str(risk_preview.get("reason") or "UNKNOWN")
    risk_preview_metadata = _safe_dict(risk_preview.get("metadata"))
    setup_risk_cap = _safe_float(risk_preview_metadata.get("setup_max_risk_multiplier"), default=None)
    risk_reason = str(
        decision.get("risk_reason")
        or decision.get("risk_rejection_reason")
        or risk_preview_reason
        or "UNKNOWN"
    )
    risk_allowed = bool(decision.get("risk_allowed")) or risk_multiplier > 0.0

    blockers: list[str] = []
    if not synergy_true:
        _append_unique(blockers, GateBlockerCode.NOT_SYNERGY_TRUE.value)
    if setup_type == "POI_REACTION":
        _append_unique(blockers, GateBlockerCode.SETUP_TYPE_POI_REACTION_NOT_TRADABLE.value)
    if not setup_tradable:
        _append_unique(blockers, GateBlockerCode.SETUP_NOT_TRADABLE.value)
    if not has_setup_candidate:
        _append_unique(blockers, GateBlockerCode.NO_SETUP_CANDIDATE.value)
    if has_setup_candidate and not has_tradable_setup_candidate:
        _append_unique(blockers, GateBlockerCode.NO_TRADABLE_SETUP_CANDIDATE.value)
    if setup_grade in {"D", "F", "UNKNOWN", ""}:
        _append_unique(blockers, GateBlockerCode.SETUP_GRADE_TOO_LOW.value)

    for blocker in readiness_blockers:
        _append_unique(blockers, blocker)
        mapped = _section_blocker_code(blocker)
        if mapped:
            _append_unique(blockers, mapped)

    if not enter_eligible:
        _append_unique(blockers, GateBlockerCode.ENTER_ELIGIBILITY_FALSE.value)
    for blocker in enter_blockers:
        _append_unique(blockers, str(blocker))
    if not risk_allowed:
        _append_unique(blockers, GateBlockerCode.RISK_NOT_ALLOWED.value)
    if risk_multiplier <= 0.0:
        _append_unique(blockers, GateBlockerCode.RISK_MULTIPLIER_ZERO.value)
    if setup_risk_cap == 0.0:
        _append_unique(blockers, GateBlockerCode.SETUP_RISK_CAP_ZERO.value)
    if final_decision == "WATCH_ONLY":
        _append_unique(blockers, GateBlockerCode.FINAL_DECISION_WATCH_ONLY.value)
    elif final_decision == "REJECT":
        _append_unique(blockers, GateBlockerCode.FINAL_DECISION_REJECT.value)

    primary = _primary_blocker(blockers)

    return ReadinessRiskGateResult(
        synergy_true=synergy_true,
        setup_type=setup_type,
        setup_grade=setup_grade,
        setup_tradable=setup_tradable,
        has_setup_candidate=has_setup_candidate,
        has_tradable_setup_candidate=has_tradable_setup_candidate,
        readiness_state=readiness_state,
        readiness_reason=readiness_reason,
        readiness_blockers=readiness_blockers,
        enter_eligible=enter_eligible,
        enter_blockers=enter_blockers,
        risk_allowed=risk_allowed,
        risk_reason=risk_reason,
        risk_multiplier=risk_multiplier,
        final_decision=final_decision,
        blockers=blockers,
        primary_blocker=primary,
        audit={
            "readiness_by_section": readiness_by_section,
            "readiness_non_ready_sections": non_ready_sections,
            "readiness_missing_ready_blockers": readiness_missing,
            "setup_candidates_count": len(candidates),
            "tradable_setup_candidates_count": len(tradable_candidates),
            "best_setup_candidate_type": best_candidate_type or "UNKNOWN",
            "has_best_setup_candidate": bool(best_candidate),
            "risk_preview_reason": risk_preview_reason,
            "risk_preview_allowed": bool(risk_preview.get("allowed")),
            "risk_preview_multiplier": _safe_float(risk_preview.get("risk_multiplier")),
            "risk_preview_pct": _safe_float(risk_preview.get("risk_pct")),
            "setup_max_risk_multiplier": setup_risk_cap,
            "poi_micro_synergy_status": decision.get("poi_micro_synergy_status"),
            "poi_micro_reason": decision.get("poi_micro_reason"),
            "effective_poi_status": decision.get("effective_poi_status"),
            "micro_contract_status": decision.get("micro_contract_status"),
            "poi_contract_status": decision.get("poi_contract_status"),
        },
    )


def _readiness_blockers(
    readiness_by_section: dict[str, Any],
    non_ready_sections: dict[str, Any],
    missing_ready_blockers: list[str],
) -> list[str]:
    blockers: list[str] = []
    sections = dict(readiness_by_section)
    sections.update(non_ready_sections)
    for section, state in sections.items():
        value = str(state or "").upper()
        if value not in {"READY", "OK", "TRUE"}:
            _append_unique(blockers, f"SECTION_NOT_READY:{section}")
    for blocker in missing_ready_blockers:
        _append_unique(blockers, blocker)
    return blockers


def _section_blocker_code(blocker: str) -> str | None:
    lowered = blocker.lower()
    if "poi" in lowered:
        return GateBlockerCode.POI_NOT_READY.value
    if "micro" in lowered:
        return GateBlockerCode.MICRO_NOT_READY.value
    if "liquidity" in lowered:
        return GateBlockerCode.LIQUIDITY_NOT_READY.value
    if "timing" in lowered or "ote" in lowered:
        return GateBlockerCode.TIMING_NOT_READY.value
    if "session" in lowered:
        return GateBlockerCode.SESSION_NOT_READY.value
    if "news" in lowered:
        return GateBlockerCode.NEWS_BLOCKED.value
    if "spread" in lowered:
        return GateBlockerCode.SPREAD_BLOCKED.value
    return None


def _primary_blocker(blockers: list[str]) -> str:
    priority = [
        GateBlockerCode.SETUP_TYPE_POI_REACTION_NOT_TRADABLE.value,
        GateBlockerCode.NO_TRADABLE_SETUP_CANDIDATE.value,
        GateBlockerCode.SETUP_NOT_TRADABLE.value,
        GateBlockerCode.NO_SETUP_CANDIDATE.value,
        GateBlockerCode.SETUP_GRADE_TOO_LOW.value,
        GateBlockerCode.POI_NOT_READY.value,
        GateBlockerCode.MICRO_NOT_READY.value,
        GateBlockerCode.LIQUIDITY_NOT_READY.value,
        GateBlockerCode.TIMING_NOT_READY.value,
        GateBlockerCode.SESSION_NOT_READY.value,
        GateBlockerCode.NEWS_BLOCKED.value,
        GateBlockerCode.SPREAD_BLOCKED.value,
        GateBlockerCode.ENTER_ELIGIBILITY_FALSE.value,
        GateBlockerCode.SETUP_RISK_CAP_ZERO.value,
        GateBlockerCode.RISK_NOT_ALLOWED.value,
        GateBlockerCode.RISK_MULTIPLIER_ZERO.value,
        GateBlockerCode.FINAL_DECISION_WATCH_ONLY.value,
        GateBlockerCode.FINAL_DECISION_REJECT.value,
        GateBlockerCode.NOT_SYNERGY_TRUE.value,
    ]
    for item in priority:
        if item in blockers:
            return item
    return blockers[0] if blockers else GateBlockerCode.NONE.value


def _candidate_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _synergy_payload(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("poi_micro_synergy_payload")
    if isinstance(payload, dict):
        return payload
    bundle = decision.get("p1_evidence_bundle")
    if isinstance(bundle, dict):
        poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
        raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
        if isinstance(poi.get("poi_micro_synergy"), dict):
            return poi.get("poi_micro_synergy") or {}
        if isinstance(raw.get("poi_micro_synergy"), dict):
            return raw.get("poi_micro_synergy") or {}
    return {}


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)
