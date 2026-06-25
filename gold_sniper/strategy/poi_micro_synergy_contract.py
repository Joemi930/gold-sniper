from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from gold_sniper.strategy.micro_readiness_contract import (
    MicroContractResult,
    MicroContractStatus,
)
from gold_sniper.strategy.poi_readiness_contract import (
    POIContractResult,
    POIContractStatus,
)
from gold_sniper.strategy.poi_rejection_contract import POIRejectionCode


class POIMicroSynergyStatus(str, Enum):
    NO_POI = "NO_POI"
    POI_NOT_EXECUTABLE = "POI_NOT_EXECUTABLE"
    MICRO_NOT_CONFIRMED = "MICRO_NOT_CONFIRMED"
    MICRO_OUTSIDE_POI = "MICRO_OUTSIDE_POI"
    POI_TOO_WEAK = "POI_TOO_WEAK"
    POI_INVALID = "POI_INVALID"
    POI_CONSUMED = "POI_CONSUMED"
    SYNERGY_READY_FOR_TRIGGER = "SYNERGY_READY_FOR_TRIGGER"
    SYNERGY_READY = "SYNERGY_READY"


@dataclass(frozen=True)
class POIMicroSynergyResult:
    synergy: bool
    status: POIMicroSynergyStatus
    reason: str
    micro_confirmed: bool
    micro_inside_poi: bool
    micro_outside_poi: bool
    poi_status: str
    micro_status: str
    upgraded_poi_status: str | None
    remaining_blockers: list[str]
    audit: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


def evaluate_poi_micro_synergy(
    poi_contract: POIContractResult,
    micro_contract: MicroContractResult,
    *,
    micro_inside_poi: bool | None = None,
    quality_ready_threshold: float = 50.0,
    quality_ready_for_trigger_threshold: float = 40.0,
) -> POIMicroSynergyResult:
    evidence = micro_contract.evidence
    resolved_micro_inside = (
        bool(micro_inside_poi)
        if micro_inside_poi is not None
        else bool(evidence.price_in_agent2_poi or evidence.trigger_inside_poi)
    )
    micro_outside = bool(evidence.trigger_outside_poi or micro_contract.is_outside_poi)
    micro_confirmed = micro_contract.status == MicroContractStatus.CONFIRMED
    final_score = poi_contract.quality.final_poi_quality_score
    rejection = (poi_contract.audit or {}).get("rejection")
    if not isinstance(rejection, dict):
        rejection = {}
    rejected = "REJECTED" in str(poi_contract.failure_class or "").upper()
    recoverable_status = poi_contract.status == POIContractStatus.RECOVERABLE_REJECTED

    audit = {
        "poi_reason": poi_contract.reason,
        "poi_failure_class": poi_contract.failure_class,
        "poi_rejection": rejection,
        "poi_semantic_status": poi_contract.semantic_status_raw,
        "poi_has_selected_poi": poi_contract.has_selected_poi,
        "poi_has_price_bounds": poi_contract.has_price_bounds,
        "poi_final_score": final_score,
        "poi_score_source": poi_contract.quality.score_source,
        "poi_score_is_computed": poi_contract.quality.score_is_computed,
        "poi_contradictions": list(poi_contract.contradictions),
        "micro_reason": micro_contract.reason,
        "micro_present_fields": list(micro_contract.present_fields),
        "micro_missing_fields": list(micro_contract.missing_fields),
        "micro_contradictions": list(micro_contract.contradictions),
    }

    base_kwargs = {
        "micro_confirmed": micro_confirmed,
        "micro_inside_poi": resolved_micro_inside,
        "micro_outside_poi": micro_outside,
        "poi_status": poi_contract.status.value,
        "micro_status": micro_contract.status.value,
        "upgraded_poi_status": None,
        "audit": audit,
    }

    if not poi_contract.has_selected_poi and not poi_contract.has_price_bounds:
        return _blocked(POIMicroSynergyStatus.NO_POI, "POI_MISSING", ["NO_POI"], **base_kwargs)
    if poi_contract.status == POIContractStatus.INVALID:
        return _blocked(POIMicroSynergyStatus.POI_INVALID, "POI_INVALID", ["POI_INVALID"], **base_kwargs)
    if poi_contract.status == POIContractStatus.CONSUMED:
        return _blocked(POIMicroSynergyStatus.POI_CONSUMED, "POI_CONSUMED", ["POI_CONSUMED"], **base_kwargs)
    if not poi_contract.has_price_bounds:
        return _blocked(
            POIMicroSynergyStatus.POI_NOT_EXECUTABLE,
            "POI_BOUNDS_MISSING",
            ["POI_BOUNDS_MISSING"],
            **base_kwargs,
        )
    if rejected and not recoverable_status:
        rejected_reason = (
            str(rejection.get("code"))
            if rejection.get("code")
            else "POI_REJECTED_FAILURE_CLASS"
        )
        return _blocked(
            POIMicroSynergyStatus.POI_TOO_WEAK,
            rejected_reason,
            [rejected_reason],
            **base_kwargs,
        )
    if poi_contract.status == POIContractStatus.TOO_WEAK and final_score == 0.0:
        return _blocked(
            POIMicroSynergyStatus.POI_TOO_WEAK,
            "POI_QUALITY_ZERO",
            ["POI_QUALITY_ZERO"],
            **base_kwargs,
        )
    if poi_contract.status == POIContractStatus.TOO_WEAK:
        return _blocked(
            POIMicroSynergyStatus.POI_TOO_WEAK,
            poi_contract.reason or "POI_TOO_WEAK",
            [poi_contract.reason or "POI_TOO_WEAK"],
            **base_kwargs,
        )
    if micro_outside:
        return _blocked(
            POIMicroSynergyStatus.MICRO_OUTSIDE_POI,
            "MICRO_OUTSIDE_POI",
            ["MICRO_OUTSIDE_POI"],
            **base_kwargs,
        )
    if not micro_confirmed:
        return _blocked(
            POIMicroSynergyStatus.MICRO_NOT_CONFIRMED,
            "MICRO_NOT_CONFIRMED",
            ["MICRO_NOT_CONFIRMED"],
            **base_kwargs,
        )
    if not resolved_micro_inside:
        return _blocked(
            POIMicroSynergyStatus.MICRO_OUTSIDE_POI,
            "MICRO_OUTSIDE_POI",
            ["MICRO_OUTSIDE_POI"],
            **base_kwargs,
        )
    if recoverable_status and rejection.get("recoverable") is not True:
        return _blocked(
            POIMicroSynergyStatus.POI_TOO_WEAK,
            rejection.get("code") or poi_contract.reason or "POI_REJECTION_NOT_RECOVERABLE",
            [rejection.get("code") or "POI_REJECTION_NOT_RECOVERABLE"],
            **base_kwargs,
        )
    if poi_contract.status not in {
        POIContractStatus.EXECUTABLE,
        POIContractStatus.READY_FOR_TRIGGER,
        POIContractStatus.READY,
        POIContractStatus.RECOVERABLE_REJECTED,
    }:
        return _blocked(
            POIMicroSynergyStatus.POI_NOT_EXECUTABLE,
            "POI_NOT_EXECUTABLE",
            ["POI_NOT_EXECUTABLE"],
            **base_kwargs,
        )

    if recoverable_status:
        allowed_zero_rejections = {
            POIRejectionCode.POI_DEFAULT_ZERO_SCORE_WITH_BOUNDS.value,
            POIRejectionCode.POI_QUALITY_MISSING_WITH_BOUNDS.value,
            POIRejectionCode.POI_UNCLASSIFIED_LEGACY_REJECTED.value,
        }
        rejection_code = str(rejection.get("code") or "")
        score_ok = (
            final_score is None
            or final_score >= float(quality_ready_for_trigger_threshold)
            or (final_score == 0.0 and rejection_code in allowed_zero_rejections)
        )
        if score_ok:
            upgraded = (
                POIContractStatus.READY.value
                if final_score is not None and final_score >= float(quality_ready_threshold)
                else POIContractStatus.READY_FOR_TRIGGER.value
            )
            return POIMicroSynergyResult(
                synergy=True,
                status=(
                    POIMicroSynergyStatus.SYNERGY_READY
                    if upgraded == POIContractStatus.READY.value
                    else POIMicroSynergyStatus.SYNERGY_READY_FOR_TRIGGER
                ),
                reason="RECOVERABLE_POI_REVALIDATED_BY_MICRO",
                remaining_blockers=[],
                upgraded_poi_status=upgraded,
                **{k: v for k, v in base_kwargs.items() if k != "upgraded_poi_status"},
            )
        return _blocked(
            POIMicroSynergyStatus.POI_TOO_WEAK,
            "RECOVERABLE_POI_SCORE_BELOW_SYNERGY_THRESHOLD",
            ["RECOVERABLE_POI_SCORE_BELOW_SYNERGY_THRESHOLD"],
            **base_kwargs,
        )

    if final_score is None:
        upgraded = POIContractStatus.READY_FOR_TRIGGER.value
        return POIMicroSynergyResult(
            synergy=True,
            status=POIMicroSynergyStatus.SYNERGY_READY_FOR_TRIGGER,
            reason="QUALITY_MISSING_BUT_MICRO_CONFIRMED_INSIDE",
            remaining_blockers=[],
            upgraded_poi_status=upgraded,
            **{k: v for k, v in base_kwargs.items() if k != "upgraded_poi_status"},
        )
    if final_score == 0.0:
        return _blocked(
            POIMicroSynergyStatus.POI_TOO_WEAK,
            "POI_QUALITY_ZERO",
            ["POI_QUALITY_ZERO"],
            **base_kwargs,
        )
    if final_score >= float(quality_ready_threshold):
        return POIMicroSynergyResult(
            synergy=True,
            status=POIMicroSynergyStatus.SYNERGY_READY,
            reason="MICRO_CONFIRMED_INSIDE_POI_SCORE_READY",
            remaining_blockers=[],
            upgraded_poi_status=POIContractStatus.READY.value,
            **{k: v for k, v in base_kwargs.items() if k != "upgraded_poi_status"},
        )
    if final_score >= float(quality_ready_for_trigger_threshold):
        return POIMicroSynergyResult(
            synergy=True,
            status=POIMicroSynergyStatus.SYNERGY_READY_FOR_TRIGGER,
            reason="MICRO_CONFIRMED_INSIDE_POI_SCORE_READY_FOR_TRIGGER",
            remaining_blockers=[],
            upgraded_poi_status=POIContractStatus.READY_FOR_TRIGGER.value,
            **{k: v for k, v in base_kwargs.items() if k != "upgraded_poi_status"},
        )
    return _blocked(
        POIMicroSynergyStatus.POI_TOO_WEAK,
        "POI_QUALITY_BELOW_SYNERGY_THRESHOLD",
        ["POI_QUALITY_BELOW_SYNERGY_THRESHOLD"],
        **base_kwargs,
    )


def resolve_effective_poi_status(
    poi_contract: POIContractResult,
    synergy: POIMicroSynergyResult | None,
) -> POIContractStatus:
    if synergy and synergy.synergy and synergy.upgraded_poi_status:
        try:
            return POIContractStatus(str(synergy.upgraded_poi_status))
        except ValueError:
            return poi_contract.status
    return poi_contract.status


def _blocked(
    status: POIMicroSynergyStatus,
    reason: str,
    remaining_blockers: list[str],
    *,
    micro_confirmed: bool,
    micro_inside_poi: bool,
    micro_outside_poi: bool,
    poi_status: str,
    micro_status: str,
    upgraded_poi_status: str | None,
    audit: dict[str, Any],
) -> POIMicroSynergyResult:
    return POIMicroSynergyResult(
        synergy=False,
        status=status,
        reason=reason,
        micro_confirmed=micro_confirmed,
        micro_inside_poi=micro_inside_poi,
        micro_outside_poi=micro_outside_poi,
        poi_status=poi_status,
        micro_status=micro_status,
        upgraded_poi_status=upgraded_poi_status,
        remaining_blockers=list(remaining_blockers),
        audit=audit,
    )
