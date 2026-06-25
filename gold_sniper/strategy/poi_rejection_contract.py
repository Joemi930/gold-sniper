from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class POIRejectionSeverity(str, Enum):
    NONE = "NONE"
    FATAL = "FATAL"
    RECOVERABLE = "RECOVERABLE"
    UNKNOWN = "UNKNOWN"


class POIRejectionCode(str, Enum):
    NONE = "POI_REJECTION_NONE"
    POI_MISSING = "POI_MISSING"
    POI_SCHEMA_INVALID = "POI_SCHEMA_INVALID"
    POI_DIRECTION_MISMATCH = "POI_DIRECTION_MISMATCH"
    POI_SESSION_INVALID = "POI_SESSION_INVALID"
    POI_DISTANCE_INVALID = "POI_DISTANCE_INVALID"
    POI_TOO_FAR = "POI_TOO_FAR"
    POI_CONSUMED = "POI_CONSUMED"
    POI_INVALIDATED = "POI_INVALIDATED"
    POI_BOUNDS_INVALID = "POI_BOUNDS_INVALID"
    POI_SCORE_LOW_BUT_PRESENT = "POI_SCORE_LOW_BUT_PRESENT"
    POI_QUALITY_MISSING_WITH_BOUNDS = "POI_QUALITY_MISSING_WITH_BOUNDS"
    POI_DEFAULT_ZERO_SCORE_WITH_BOUNDS = "POI_DEFAULT_ZERO_SCORE_WITH_BOUNDS"
    POI_LEGACY_WEAK_BUT_NEAR_MICRO = "POI_LEGACY_WEAK_BUT_NEAR_MICRO"
    POI_UNCLASSIFIED_LEGACY_REJECTED = "POI_UNCLASSIFIED_LEGACY_REJECTED"
    POI_REJECTED_UNMAPPED = "POI_REJECTED_UNMAPPED"
    POI_LEGACY_UNKNOWN = "POI_LEGACY_UNKNOWN"


@dataclass(frozen=True)
class POIRejectionReason:
    code: POIRejectionCode
    source: str
    severity: POIRejectionSeverity
    fatal: bool
    recoverable: bool
    reason: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["code"] = self.code.value
        payload["severity"] = self.severity.value
        return payload


def normalize_poi_rejection(
    *,
    failure_class: str | None,
    semantic_status: str | None,
    final_score: float | None,
    score_source: str | None,
    score_is_computed: bool,
    has_selected_poi: bool,
    has_price_bounds: bool,
    lifecycle: str | None = None,
    distance_to_price_score: float | None = None,
    direction_mismatch: bool | None = None,
    session_invalid: bool | None = None,
    raw: dict[str, Any] | None = None,
) -> POIRejectionReason:
    failure = str(failure_class or "").upper()
    semantic = str(semantic_status or "").upper()
    lifecycle_value = str(lifecycle or "").upper()
    raw = raw or {}

    rejection_markers = (
        "REJECT",
        "LEGACY",
        "SCHEMA",
        "BOUNDS_INVALID",
        "INVALID_BOUNDS",
        "DIRECTION",
        "SESSION",
        "TOO_FAR",
        "DISTANCE",
        "CONSUMED",
        "INVALIDATED",
    )
    if "REJECT" not in semantic and not any(marker in failure for marker in rejection_markers):
        return _reason(
            POIRejectionCode.NONE,
            "NO_REJECTION",
            POIRejectionSeverity.NONE,
            "POI has no rejection marker.",
            _details(locals()),
        )

    if not has_selected_poi and not has_price_bounds:
        return _reason(
            POIRejectionCode.POI_MISSING,
            "CONTRACT",
            POIRejectionSeverity.FATAL,
            "No selected POI and no price bounds.",
            _details(locals()),
        )

    if lifecycle_value in {"CONSUMED", "MITIGATED"} or "CONSUMED" in failure:
        return _reason(
            POIRejectionCode.POI_CONSUMED,
            "LIFECYCLE",
            POIRejectionSeverity.FATAL,
            "POI already consumed or mitigated.",
            _details(locals()),
        )

    if lifecycle_value in {"INVALID", "INVALIDATED"} or "INVALIDATED" in failure:
        return _reason(
            POIRejectionCode.POI_INVALIDATED,
            "LIFECYCLE",
            POIRejectionSeverity.FATAL,
            "POI invalidated by lifecycle.",
            _details(locals()),
        )

    if direction_mismatch is True or ("DIRECTION" in failure and "MISMATCH" in failure):
        return _reason(
            POIRejectionCode.POI_DIRECTION_MISMATCH,
            "VALIDATOR",
            POIRejectionSeverity.FATAL,
            "POI direction mismatch.",
            _details(locals()),
        )

    if session_invalid is True or "SESSION" in failure:
        return _reason(
            POIRejectionCode.POI_SESSION_INVALID,
            "VALIDATOR",
            POIRejectionSeverity.FATAL,
            "POI invalid for session.",
            _details(locals()),
        )

    if "SCHEMA" in failure:
        return _reason(
            POIRejectionCode.POI_SCHEMA_INVALID,
            "CONTRACT",
            POIRejectionSeverity.FATAL,
            "POI schema invalid.",
            _details(locals()),
        )

    if "BOUNDS_INVALID" in failure or "INVALID_BOUNDS" in failure:
        return _reason(
            POIRejectionCode.POI_BOUNDS_INVALID,
            "CONTRACT",
            POIRejectionSeverity.FATAL,
            "POI bounds invalid.",
            _details(locals()),
        )

    if "TOO_FAR" in failure:
        return _reason(
            POIRejectionCode.POI_TOO_FAR,
            "VALIDATOR",
            POIRejectionSeverity.FATAL,
            "POI too far from actionable zone.",
            _details(locals()),
        )

    if "DISTANCE" in failure:
        return _reason(
            POIRejectionCode.POI_DISTANCE_INVALID,
            "VALIDATOR",
            POIRejectionSeverity.FATAL,
            "POI distance invalid.",
            _details(locals()),
        )

    if "LEGACY_REJECTED" in failure or "LEGACY" in failure or "REJECTED" in failure:
        if has_price_bounds and final_score is None:
            return _reason(
                POIRejectionCode.POI_QUALITY_MISSING_WITH_BOUNDS,
                "LEGACY_VALIDATOR",
                POIRejectionSeverity.RECOVERABLE,
                "Legacy rejected POI but bounds exist and quality is missing.",
                _details(locals()),
            )

        if has_price_bounds and final_score == 0.0:
            if not _has_real_subscores(raw):
                return _reason(
                    POIRejectionCode.POI_DEFAULT_ZERO_SCORE_WITH_BOUNDS,
                    "LEGACY_VALIDATOR",
                    POIRejectionSeverity.RECOVERABLE,
                    "Legacy rejected POI with bounds and default zero score.",
                    _details(locals()),
                )
            return _reason(
                POIRejectionCode.POI_REJECTED_UNMAPPED,
                "LEGACY_VALIDATOR",
                POIRejectionSeverity.UNKNOWN,
                "Legacy rejected POI has a true zero score or real weak subscores.",
                _details(locals()),
            )

        if has_price_bounds and final_score is not None and 0.0 < final_score < 50.0:
            return _reason(
                POIRejectionCode.POI_SCORE_LOW_BUT_PRESENT,
                "LEGACY_VALIDATOR",
                POIRejectionSeverity.RECOVERABLE,
                "Legacy rejected POI with bounds and low but non-zero score.",
                _details(locals()),
            )

        if has_price_bounds:
            return _reason(
                POIRejectionCode.POI_UNCLASSIFIED_LEGACY_REJECTED,
                "LEGACY_VALIDATOR",
                POIRejectionSeverity.RECOVERABLE,
                "Legacy rejected POI with bounds but no fatal reason identified.",
                _details(locals()),
            )

        return _reason(
            POIRejectionCode.POI_REJECTED_UNMAPPED,
            "LEGACY_VALIDATOR",
            POIRejectionSeverity.UNKNOWN,
            "Rejected POI could not be classified.",
            _details(locals()),
        )

    return _reason(
        POIRejectionCode.POI_LEGACY_UNKNOWN,
        "UNKNOWN",
        POIRejectionSeverity.UNKNOWN,
        "Unmapped POI rejection.",
        _details(locals()),
    )


def _reason(
    code: POIRejectionCode,
    source: str,
    severity: POIRejectionSeverity,
    reason: str,
    details: dict[str, Any],
) -> POIRejectionReason:
    return POIRejectionReason(
        code=code,
        source=source,
        severity=severity,
        fatal=severity == POIRejectionSeverity.FATAL,
        recoverable=severity == POIRejectionSeverity.RECOVERABLE,
        reason=reason,
        details=details,
    )


def _has_real_subscores(raw: dict[str, Any]) -> bool:
    for container in (raw, raw.get("selected_poi") if isinstance(raw.get("selected_poi"), dict) else {}):
        if not isinstance(container, dict):
            continue
        for key in (
            "structure_score",
            "freshness_score",
            "mitigation_score",
            "distance_to_price_score",
            "bounds_quality_score",
            "proximity_score",
        ):
            value = container.get(key)
            if value not in {None, "", 0, 0.0}:
                return True
    return False


def _details(values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "failure",
        "semantic",
        "final_score",
        "score_source",
        "score_is_computed",
        "has_selected_poi",
        "has_price_bounds",
        "lifecycle_value",
        "distance_to_price_score",
        "direction_mismatch",
        "session_invalid",
    }
    return {key: values.get(key) for key in allowed}
