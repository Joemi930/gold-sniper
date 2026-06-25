from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from gold_sniper.strategy.poi_rejection_contract import normalize_poi_rejection


class POIContractStatus(str, Enum):
    OBSERVABLE = "POI_OBSERVABLE"
    EXECUTABLE = "POI_EXECUTABLE"
    READY_FOR_TRIGGER = "POI_READY_FOR_TRIGGER"
    READY = "POI_READY"
    TOO_WEAK = "POI_TOO_WEAK"
    RECOVERABLE_REJECTED = "POI_RECOVERABLE_REJECTED"
    INVALID = "POI_INVALID"
    CONSUMED = "POI_CONSUMED"


@dataclass(frozen=True)
class POIQualityBreakdown:
    structure_score: float | None
    freshness_score: float | None
    mitigation_score: float | None
    distance_to_price_score: float | None
    bounds_quality_score: float | None
    final_poi_quality_score: float | None
    score_source: str
    score_is_computed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class POIContractResult:
    status: POIContractStatus
    readiness_state: str
    reason: str
    source: str
    has_selected_poi: bool
    has_price_bounds: bool
    is_observable: bool
    is_executable: bool
    is_ready_for_trigger: bool
    is_ready: bool
    is_too_weak: bool
    is_invalid: bool
    is_consumed: bool
    failure_class: str | None
    semantic_status_raw: str | None
    execution_readiness_raw: str | None
    quality: POIQualityBreakdown
    contradictions: list[str]
    audit: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


def evaluate_poi_contract(
    poi: dict[str, Any] | None,
    *,
    selected_poi: dict[str, Any] | None = None,
    current_price: float | None = None,
) -> POIContractResult:
    del current_price
    poi = _safe_dict(poi)
    selected = _safe_dict(selected_poi) or _safe_dict(poi.get("selected_poi"))
    semantic_status_raw = poi.get("poi_semantic_status")
    semantic_status = _upper(semantic_status_raw)
    failure_class_raw = poi.get("poi_failure_class")
    failure_class = _upper(failure_class_raw, "")
    execution_readiness_raw = (
        poi.get("execution_readiness")
        or poi.get("readiness_state")
        or selected.get("execution_readiness")
        or selected.get("readiness_state")
    )
    execution_readiness = _upper(execution_readiness_raw)
    lifecycle = _upper(
        poi.get("lifecycle_normalized")
        or poi.get("lifecycle_state")
        or selected.get("lifecycle_normalized")
        or selected.get("lifecycle_state")
    )
    mitigation_pct = _first_float(
        poi.get("mitigation_pct"),
        selected.get("mitigation_pct"),
        default=0.0,
    )

    has_selected_poi = bool(selected or poi.get("selected_poi_present"))
    has_price_bounds = _has_price_bounds(poi, selected)
    quality = _quality_breakdown(poi, selected)
    final_score = quality.final_poi_quality_score
    raw_for_rejection = {**poi, "selected_poi": selected}
    rejection = normalize_poi_rejection(
        failure_class=str(failure_class_raw) if failure_class_raw is not None else None,
        semantic_status=str(semantic_status_raw) if semantic_status_raw is not None else None,
        final_score=final_score,
        score_source=quality.score_source,
        score_is_computed=quality.score_is_computed,
        has_selected_poi=has_selected_poi,
        has_price_bounds=has_price_bounds,
        lifecycle=lifecycle,
        distance_to_price_score=quality.distance_to_price_score,
        direction_mismatch=_truthy(poi.get("direction_mismatch") or selected.get("direction_mismatch")),
        session_invalid=_truthy(poi.get("session_invalid") or selected.get("session_invalid")),
        raw=raw_for_rejection,
    )

    contradictions: list[str] = []
    if "EXECUTABLE" in semantic_status and final_score == 0.0:
        contradictions.append("EXECUTABLE_WITH_ZERO_QUALITY")
    if "EXECUTABLE" in semantic_status and "REJECTED" in failure_class:
        contradictions.append("EXECUTABLE_WITH_REJECTED_FAILURE_CLASS")
    if execution_readiness == "READY" and final_score == 0.0:
        contradictions.append("READY_WITH_ZERO_QUALITY")
    if execution_readiness == "READY" and "REJECTED" in failure_class:
        contradictions.append("READY_WITH_REJECTED_FAILURE_CLASS")
    if not has_price_bounds and "EXECUTABLE" in semantic_status:
        contradictions.append("EXECUTABLE_WITHOUT_BOUNDS")

    if "CONSUMED" in failure_class or lifecycle in {"CONSUMED", "MITIGATED"}:
        status = POIContractStatus.CONSUMED
        reason = "POI_CONSUMED_BY_LIFECYCLE_OR_FAILURE_CLASS"
    elif lifecycle in {"INVALIDATED", "INVALID"} or execution_readiness == "INVALID":
        status = POIContractStatus.INVALID
        reason = "POI_INVALIDATED"
    elif rejection.fatal:
        status = (
            POIContractStatus.INVALID
            if rejection.code.value in {
                "POI_MISSING",
                "POI_SCHEMA_INVALID",
                "POI_DIRECTION_MISMATCH",
                "POI_SESSION_INVALID",
                "POI_DISTANCE_INVALID",
                "POI_TOO_FAR",
                "POI_BOUNDS_INVALID",
                "POI_INVALIDATED",
            }
            else POIContractStatus.TOO_WEAK
        )
        reason = rejection.code.value
    elif rejection.severity.value == "UNKNOWN" and rejection.code.value != "POI_REJECTION_NONE":
        status = POIContractStatus.TOO_WEAK
        reason = rejection.code.value
    elif "REJECTED" in failure_class:
        if rejection.fatal:
            status = (
                POIContractStatus.INVALID
                if rejection.code.value in {
                    "POI_MISSING",
                    "POI_SCHEMA_INVALID",
                    "POI_DIRECTION_MISMATCH",
                    "POI_SESSION_INVALID",
                    "POI_DISTANCE_INVALID",
                    "POI_TOO_FAR",
                    "POI_BOUNDS_INVALID",
                    "POI_INVALIDATED",
                }
                else POIContractStatus.TOO_WEAK
            )
            reason = rejection.code.value
        elif rejection.recoverable:
            status = POIContractStatus.RECOVERABLE_REJECTED
            reason = rejection.code.value
        else:
            status = POIContractStatus.TOO_WEAK
            reason = rejection.code.value
    elif not has_selected_poi and not has_price_bounds:
        status = POIContractStatus.INVALID
        reason = "POI_ANCHOR_AND_BOUNDS_MISSING"
    elif has_selected_poi and not has_price_bounds:
        status = POIContractStatus.OBSERVABLE
        reason = "POI_SELECTED_WITHOUT_BOUNDS"
    elif has_price_bounds and (final_score is None or not quality.score_is_computed):
        status = POIContractStatus.EXECUTABLE
        reason = "POI_BOUNDS_PRESENT_QUALITY_NOT_COMPUTED"
    elif has_price_bounds and final_score < 50.0:
        status = POIContractStatus.TOO_WEAK
        reason = "POI_QUALITY_BELOW_READY_THRESHOLD"
    elif has_price_bounds and execution_readiness in {"WAITING_TRIGGER", "WAIT_FOR_TRIGGER"}:
        status = POIContractStatus.READY_FOR_TRIGGER
        reason = "POI_READY_FOR_TRIGGER"
    elif has_price_bounds and execution_readiness == "READY" and final_score >= 50.0:
        status = POIContractStatus.READY
        reason = "POI_READY"
    elif has_price_bounds and final_score >= 50.0:
        status = POIContractStatus.READY_FOR_TRIGGER
        reason = "POI_BOUNDS_AND_QUALITY_READY_FOR_TRIGGER"
    else:
        status = POIContractStatus.OBSERVABLE
        reason = "POI_OBSERVABLE_ONLY"

    readiness_state = poi_status_to_readiness(status)
    source = str(
        poi.get("source")
        or selected.get("source")
        or (poi.get("connectivity_audit") or {}).get("source")
        or "P2A_SELECTED_POI"
    )
    is_observable = bool(
        status not in {POIContractStatus.INVALID, POIContractStatus.CONSUMED}
        and (has_selected_poi or has_price_bounds or semantic_status.startswith("POI_PRESENT"))
    )
    return POIContractResult(
        status=status,
        readiness_state=readiness_state,
        reason=reason,
        source=source,
        has_selected_poi=has_selected_poi,
        has_price_bounds=has_price_bounds,
        is_observable=is_observable,
        is_executable=status in {
            POIContractStatus.EXECUTABLE,
            POIContractStatus.READY_FOR_TRIGGER,
            POIContractStatus.READY,
        },
        is_ready_for_trigger=status == POIContractStatus.READY_FOR_TRIGGER,
        is_ready=status == POIContractStatus.READY,
        is_too_weak=status == POIContractStatus.TOO_WEAK,
        is_invalid=status == POIContractStatus.INVALID,
        is_consumed=status == POIContractStatus.CONSUMED,
        failure_class=str(failure_class_raw) if failure_class_raw is not None else None,
        semantic_status_raw=str(semantic_status_raw) if semantic_status_raw is not None else None,
        execution_readiness_raw=str(execution_readiness_raw) if execution_readiness_raw is not None else None,
        quality=quality,
        contradictions=contradictions,
        audit={
            "lifecycle": lifecycle,
            "mitigation_pct": mitigation_pct,
            "semantic_status": semantic_status,
            "failure_class": failure_class,
            "execution_readiness": execution_readiness,
            "rejection": rejection.to_dict(),
        },
    )


def poi_status_to_readiness(status: POIContractStatus) -> str:
    if status == POIContractStatus.READY:
        return "READY"
    if status == POIContractStatus.READY_FOR_TRIGGER:
        return "WAITING_TRIGGER"
    if status == POIContractStatus.EXECUTABLE:
        return "WATCH_ONLY"
    if status == POIContractStatus.OBSERVABLE:
        return "WAITING_POI"
    if status == POIContractStatus.TOO_WEAK:
        return "WATCH_ONLY"
    if status == POIContractStatus.RECOVERABLE_REJECTED:
        return "WATCH_ONLY"
    if status in {POIContractStatus.INVALID, POIContractStatus.CONSUMED}:
        return "INVALID"
    return "UNAVAILABLE"


def _quality_breakdown(poi: dict[str, Any], selected: dict[str, Any]) -> POIQualityBreakdown:
    source, value, computed = _quality_source(poi, selected)
    return POIQualityBreakdown(
        structure_score=_float_or_none(poi.get("structure_score") or selected.get("structure_score")),
        freshness_score=_float_or_none(poi.get("freshness_score") or selected.get("freshness_score")),
        mitigation_score=_float_or_none(poi.get("mitigation_score") or selected.get("mitigation_score")),
        distance_to_price_score=_float_or_none(
            poi.get("distance_to_price_score")
            or selected.get("distance_to_price_score")
            or poi.get("proximity_score")
            or selected.get("proximity_score")
        ),
        bounds_quality_score=_float_or_none(poi.get("bounds_quality_score") or selected.get("bounds_quality_score")),
        final_poi_quality_score=value,
        score_source=source,
        score_is_computed=computed,
    )


def _quality_source(poi: dict[str, Any], selected: dict[str, Any]) -> tuple[str, float | None, bool]:
    ordered = (
        ("FINAL_POI_QUALITY_SCORE", poi.get("final_poi_quality_score"), True),
        ("POI_QUALITY_SCORE", poi.get("poi_quality_score"), True),
        ("QUALITY_SCORE", poi.get("quality_score"), True),
        ("SELECTED_POI_QUALITY_SCORE", selected.get("quality_score"), True),
        ("SELECTED_POI_SCORE", selected.get("score"), True),
    )
    for source, raw, computed in ordered:
        if raw is not None:
            return source, _float_or_none(raw), computed
    return "MISSING", None, False


def _has_price_bounds(poi: dict[str, Any], selected: dict[str, Any]) -> bool:
    return bool(
        poi.get("price_bounds")
        or selected.get("price_bounds")
        or (
            selected.get("low") is not None
            and selected.get("high") is not None
        )
        or (
            selected.get("bottom") is not None
            and selected.get("top") is not None
        )
        or poi.get("has_price_bounds")
    )


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _upper(value: Any, default: str = "UNKNOWN") -> str:
    return str(value or default).upper()


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any, default: float = 0.0) -> float:
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return default


def _truthy(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return bool(value)
