from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class MicroContractStatus(str, Enum):
    MISSING_DATA = "MICRO_MISSING_DATA"
    INVALID = "MICRO_INVALID"
    OUTSIDE_POI = "MICRO_OUTSIDE_POI"
    WAITING_TRIGGER = "MICRO_WAITING_TRIGGER"
    CONFIRMED = "MICRO_CONFIRMED"


@dataclass(frozen=True)
class MicroEvidence:
    sweep_1m_confirmed: bool | None
    choch_detected: bool | None
    trigger_inside_poi: bool | None
    retest_confirmed: bool | None
    trigger_confirmed: bool | None
    candles_1m_count: int | None
    price_in_agent2_poi: bool | None
    trigger_outside_poi: bool | None
    displacement_present: bool | None
    reclaim_confirmed: bool | None
    source: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MicroContractResult:
    status: MicroContractStatus
    readiness_state: str
    reason: str
    is_confirmed: bool
    is_waiting_trigger: bool
    is_invalid: bool
    is_missing_data: bool
    is_outside_poi: bool
    missing_fields: list[str]
    present_fields: list[str]
    contradictions: list[str]
    evidence: MicroEvidence

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


def build_micro_evidence(payload: dict | None) -> MicroEvidence:
    payload = _safe_dict(payload)
    ctx = _safe_dict(payload.get("shadow_trigger_context"))
    diag = _safe_dict(
        _first_not_none(
            payload.get("agent5_diagnostic"),
            payload.get("agent_5_diagnostic"),
        )
    )

    sweep_raw = _first_not_none(
        payload.get("sweep_1m_confirmed"),
        payload.get("sweep_detected"),
        payload.get("manipulation_detected"),
        ctx.get("sweep_1m_confirmed"),
        ctx.get("sweep_detected"),
        diag.get("sweep_1m_confirmed"),
        diag.get("sweep_detected"),
    )
    sweep = _bool_or_none(sweep_raw)

    choch_raw = _first_not_none(
        payload.get("choch_detected"),
        ctx.get("choch_detected"),
        diag.get("choch_detected"),
    )
    choch = _bool_or_none(choch_raw)

    trigger_inside_raw = _first_not_none(
        payload.get("trigger_inside_poi"),
        payload.get("price_in_agent2_poi"),
        payload.get("trigger_found"),
        ctx.get("trigger_inside_poi"),
        ctx.get("price_in_agent2_poi"),
        diag.get("trigger_inside_poi"),
        diag.get("price_in_agent2_poi"),
    )
    trigger_inside = _bool_or_none(trigger_inside_raw)

    retest_raw = _first_not_none(
        payload.get("retest_confirmed"),
        ctx.get("retest_confirmed"),
        diag.get("retest_confirmed"),
    )
    retest = _bool_or_none(retest_raw)

    trigger_confirm_raw = _first_not_none(
        payload.get("trigger_confirmed"),
        payload.get("trigger_executed"),
        ctx.get("trigger_confirmed"),
        diag.get("trigger_confirmed"),
    )
    trigger_confirm = _bool_or_none(trigger_confirm_raw)

    candles_raw = _first_not_none(
        payload.get("candles_1m_count"),
        payload.get("candle_count"),
        payload.get("one_minute_candles"),
        ctx.get("candles_1m_count"),
        diag.get("candles_1m_count"),
    )
    candles = _int_or_none(candles_raw)

    price_in_poi_raw = _first_not_none(
        payload.get("price_in_agent2_poi"),
        payload.get("price_in_poi"),
        ctx.get("price_in_agent2_poi"),
        diag.get("price_in_agent2_poi"),
    )
    price_in_poi = _bool_or_none(price_in_poi_raw)

    trigger_outside_raw = _first_not_none(
        payload.get("trigger_outside_poi"),
        payload.get("outside_poi_trigger"),
        ctx.get("trigger_outside_poi"),
        diag.get("trigger_outside_poi"),
    )
    trigger_outside = _bool_or_none(trigger_outside_raw)

    displacement_raw = _first_not_none(
        payload.get("displacement_present"),
        payload.get("displacement_detected"),
        ctx.get("displacement_present"),
        diag.get("displacement_present"),
    )
    displacement = _bool_or_none(displacement_raw)

    reclaim_raw = _first_not_none(
        payload.get("reclaim_confirmed"),
        payload.get("reclaim_detected"),
        ctx.get("reclaim_confirmed"),
        diag.get("reclaim_confirmed"),
    )
    reclaim = _bool_or_none(reclaim_raw)

    source_raw = _first_not_none(
        diag.get("source"),
        ctx.get("source"),
        payload.get("source"),
    )
    source = str(source_raw) if source_raw is not None else "MICRO_READINESS"

    return MicroEvidence(
        sweep_1m_confirmed=sweep,
        choch_detected=choch,
        trigger_inside_poi=trigger_inside,
        retest_confirmed=retest,
        trigger_confirmed=trigger_confirm,
        candles_1m_count=candles,
        price_in_agent2_poi=price_in_poi,
        trigger_outside_poi=trigger_outside,
        displacement_present=displacement,
        reclaim_confirmed=reclaim,
        source=source,
        raw=payload,
    )


def evaluate_micro_readiness(
    payload: dict | MicroEvidence | None,
) -> MicroContractResult:
    evidence = (
        payload
        if isinstance(payload, MicroEvidence)
        else build_micro_evidence(payload)
    )
    contradictions: list[str] = []

    if evidence.trigger_inside_poi is True and evidence.trigger_outside_poi is True:
        contradictions.append("TRIGGER_INSIDE_AND_OUTSIDE_POI_CONFLICT")

    if evidence.candles_1m_count is not None and evidence.candles_1m_count < 3:
        return MicroContractResult(
            status=MicroContractStatus.INVALID,
            readiness_state=micro_status_to_readiness(MicroContractStatus.INVALID),
            reason="INSUFFICIENT_1M_CANDLES",
            is_confirmed=False,
            is_waiting_trigger=False,
            is_invalid=True,
            is_missing_data=False,
            is_outside_poi=False,
            missing_fields=[],
            present_fields=_present_fields(evidence),
            contradictions=contradictions,
            evidence=evidence,
        )

    if evidence.trigger_outside_poi is True:
        return MicroContractResult(
            status=MicroContractStatus.OUTSIDE_POI,
            readiness_state=micro_status_to_readiness(MicroContractStatus.OUTSIDE_POI),
            reason="TRIGGER_OUTSIDE_POI",
            is_confirmed=False,
            is_waiting_trigger=False,
            is_invalid=False,
            is_missing_data=False,
            is_outside_poi=True,
            missing_fields=[],
            present_fields=_present_fields(evidence),
            contradictions=contradictions,
            evidence=evidence,
        )

    if evidence.sweep_1m_confirmed is None or evidence.choch_detected is None:
        missing = []
        if evidence.sweep_1m_confirmed is None:
            missing.append("sweep_1m_confirmed")
        if evidence.choch_detected is None:
            missing.append("choch_detected")
        return MicroContractResult(
            status=MicroContractStatus.MISSING_DATA,
            readiness_state=micro_status_to_readiness(MicroContractStatus.MISSING_DATA),
            reason="MISSING_SWEEP_OR_CHOCH",
            is_confirmed=False,
            is_waiting_trigger=False,
            is_invalid=False,
            is_missing_data=True,
            is_outside_poi=False,
            missing_fields=missing,
            present_fields=_present_fields(evidence),
            contradictions=contradictions,
            evidence=evidence,
        )

    if (
        evidence.sweep_1m_confirmed is True
        and evidence.choch_detected is True
        and (
            evidence.trigger_inside_poi is True
            or evidence.retest_confirmed is True
            or evidence.trigger_confirmed is True
        )
    ):
        return MicroContractResult(
            status=MicroContractStatus.CONFIRMED,
            readiness_state=micro_status_to_readiness(MicroContractStatus.CONFIRMED),
            reason="SWEEP_CHOCH_WITH_TRIGGER_CONFIRMED",
            is_confirmed=True,
            is_waiting_trigger=False,
            is_invalid=False,
            is_missing_data=False,
            is_outside_poi=False,
            missing_fields=[],
            present_fields=_present_fields(evidence),
            contradictions=contradictions,
            evidence=evidence,
        )

    if evidence.retest_confirmed is True and evidence.trigger_inside_poi is True:
        return MicroContractResult(
            status=MicroContractStatus.CONFIRMED,
            readiness_state=micro_status_to_readiness(MicroContractStatus.CONFIRMED),
            reason="RETEST_WITH_TRIGGER_INSIDE_POI",
            is_confirmed=True,
            is_waiting_trigger=False,
            is_invalid=False,
            is_missing_data=False,
            is_outside_poi=False,
            missing_fields=[],
            present_fields=_present_fields(evidence),
            contradictions=contradictions,
            evidence=evidence,
        )

    if evidence.sweep_1m_confirmed is True or evidence.choch_detected is True:
        return MicroContractResult(
            status=MicroContractStatus.WAITING_TRIGGER,
            readiness_state=micro_status_to_readiness(MicroContractStatus.WAITING_TRIGGER),
            reason="SWEEP_OR_CHOCH_WITHOUT_TRIGGER",
            is_confirmed=False,
            is_waiting_trigger=True,
            is_invalid=False,
            is_missing_data=False,
            is_outside_poi=False,
            missing_fields=[],
            present_fields=_present_fields(evidence),
            contradictions=contradictions,
            evidence=evidence,
        )

    return MicroContractResult(
        status=MicroContractStatus.MISSING_DATA,
        readiness_state=micro_status_to_readiness(MicroContractStatus.MISSING_DATA),
        reason="NO_USABLE_EVIDENCE",
        is_confirmed=False,
        is_waiting_trigger=False,
        is_invalid=False,
        is_missing_data=True,
        is_outside_poi=False,
        missing_fields=["sweep_1m_confirmed", "choch_detected"],
        present_fields=_present_fields(evidence),
        contradictions=contradictions,
        evidence=evidence,
    )


def micro_status_to_readiness(status: MicroContractStatus) -> str:
    if status == MicroContractStatus.CONFIRMED:
        return "READY"
    if status == MicroContractStatus.WAITING_TRIGGER:
        return "WAITING_TRIGGER"
    if status == MicroContractStatus.OUTSIDE_POI:
        return "INVALID"
    if status == MicroContractStatus.INVALID:
        return "INVALID"
    if status == MicroContractStatus.MISSING_DATA:
        return "UNAVAILABLE"
    return "UNAVAILABLE"


def _present_fields(evidence: MicroEvidence) -> list[str]:
    fields: list[str] = []
    for field_name in (
        "sweep_1m_confirmed",
        "choch_detected",
        "trigger_inside_poi",
        "retest_confirmed",
        "trigger_confirmed",
        "candles_1m_count",
        "price_in_agent2_poi",
        "trigger_outside_poi",
        "displacement_present",
        "reclaim_confirmed",
    ):
        if getattr(evidence, field_name) is not None:
            fields.append(field_name)
    return fields


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower().strip()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def _first_not_none(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
