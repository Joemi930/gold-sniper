from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from gold_sniper.strategy.contracts import EvidenceBundle


READY_VALUES = {"READY", "LIQUIDITY_READY"}
WAITING_VALUES = {"WAIT_FOR_TRIGGER", "WAITING_TRIGGER"}
REJECT_VALUES = {"REJECT", "INVALID"}


@dataclass(frozen=True)
class LiquidityReconciliationResult:
    liquidity_state: str
    execution_readiness: str
    readiness_state: str
    readiness_reason: str

    liquidity_evidence_source: str = "NONE"
    macro_sweep_detected: bool = False
    macro_break_detected: bool = False

    micro_liquidity_confirmed: bool = False
    micro_sweep_confirmed: bool = False
    micro_choch_detected: bool = False
    micro_inside_poi: bool = False
    poi_micro_synergy: bool = False
    poi_anchored: bool = False

    liquidity_reconciled: bool = False
    promoted_by_reconciliation: bool = False
    reconciliation_mode: str = "NONE"

    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reconcile_liquidity_readiness(bundle: EvidenceBundle) -> dict[str, Any]:
    liquidity = _safe_dict(bundle.liquidity)

    base_state = _upper(
        liquidity.get("execution_readiness")
        or liquidity.get("readiness_state")
        or "UNAVAILABLE"
    )
    base_reason = str(liquidity.get("readiness_reason") or "LIQUIDITY_REASON_UNAVAILABLE")

    macro_sweep_detected = bool(liquidity.get("sweep_detected"))
    macro_break_detected = _is_macro_break(liquidity, base_state)

    if macro_break_detected:
        return _merge(
            liquidity,
            LiquidityReconciliationResult(
                liquidity_state=str(liquidity.get("liquidity_state") or "BREAK"),
                execution_readiness="REJECT",
                readiness_state="REJECT",
                readiness_reason=base_reason or "LIQUIDITY_MACRO_BREAK_REJECT",
                liquidity_evidence_source="AGENT3_MACRO",
                macro_sweep_detected=macro_sweep_detected,
                macro_break_detected=True,
                blockers=["AGENT3_MACRO_BREAK_OR_REJECT"],
            ),
        )

    if macro_sweep_detected or base_state in READY_VALUES:
        return _merge(
            liquidity,
            LiquidityReconciliationResult(
                liquidity_state=str(liquidity.get("liquidity_state") or "SWEEP"),
                execution_readiness="READY",
                readiness_state="READY",
                readiness_reason=base_reason or "LIQUIDITY_MACRO_SWEEP_READY",
                liquidity_evidence_source="AGENT3_MACRO",
                macro_sweep_detected=True,
                macro_break_detected=False,
                liquidity_reconciled=False,
                promoted_by_reconciliation=False,
                reconciliation_mode="MACRO_PRIORITY",
            ),
        )

    micro_eval = _evaluate_micro_poi_liquidity(bundle)
    if micro_eval["confirmed"]:
        return _merge(
            liquidity,
            LiquidityReconciliationResult(
                liquidity_state="MICRO_SWEEP_CHOCH",
                execution_readiness="READY",
                readiness_state="READY",
                readiness_reason="LIQUIDITY_POI_ANCHORED_MICRO_SWEEP_READY",
                liquidity_evidence_source="AGENT5_MICRO_CONTRACT",
                macro_sweep_detected=False,
                macro_break_detected=False,
                micro_liquidity_confirmed=True,
                micro_sweep_confirmed=True,
                micro_choch_detected=True,
                micro_inside_poi=True,
                poi_micro_synergy=True,
                poi_anchored=True,
                liquidity_reconciled=True,
                promoted_by_reconciliation=True,
                reconciliation_mode="POI_ANCHORED_MICRO_SWEEP",
                blockers=[],
            ),
        )

    return _merge(
        liquidity,
        LiquidityReconciliationResult(
            liquidity_state=str(liquidity.get("liquidity_state") or "NONE"),
            execution_readiness=_normalize_waiting_state(base_state),
            readiness_state=_normalize_waiting_state(base_state),
            readiness_reason=base_reason if base_state else "LIQUIDITY_WAITING_SWEEP",
            liquidity_evidence_source="NONE",
            macro_sweep_detected=False,
            macro_break_detected=False,
            micro_liquidity_confirmed=False,
            micro_sweep_confirmed=micro_eval["micro_sweep_confirmed"],
            micro_choch_detected=micro_eval["micro_choch_detected"],
            micro_inside_poi=micro_eval["micro_inside_poi"],
            poi_micro_synergy=micro_eval["poi_micro_synergy"],
            poi_anchored=micro_eval["poi_anchored"],
            blockers=list(micro_eval["blockers"]),
        ),
    )


def _evaluate_micro_poi_liquidity(bundle: EvidenceBundle) -> dict[str, Any]:
    micro = _safe_dict(bundle.micro)
    poi = _safe_dict(bundle.poi)

    blockers: list[str] = []
    poi_anchored = _has_selected_poi(poi)
    poi_micro_synergy = _has_poi_micro_synergy(poi)
    micro_ready = _upper(micro.get("execution_readiness") or micro.get("readiness_state")) == "READY"
    micro_sweep_confirmed = bool(micro.get("sweep_1m_confirmed"))
    micro_choch_detected = bool(micro.get("choch_detected"))
    micro_inside_poi = bool(micro.get("price_in_agent2_poi") or micro.get("trigger_inside_poi"))
    micro_outside_poi = bool(micro.get("trigger_outside_poi") or micro.get("outside_poi"))

    if not poi_anchored:
        blockers.append("POI_ANCHOR_MISSING")
    if not poi_micro_synergy:
        blockers.append("POI_MICRO_SYNERGY_MISSING")
    if not micro_ready:
        blockers.append("MICRO_NOT_READY")
    if not micro_sweep_confirmed:
        blockers.append("MICRO_SWEEP_1M_MISSING")
    if not micro_choch_detected:
        blockers.append("MICRO_CHOCH_MISSING")
    if not micro_inside_poi:
        blockers.append("MICRO_NOT_INSIDE_POI")
    if micro_outside_poi:
        blockers.append("MICRO_TRIGGER_OUTSIDE_POI")

    return {
        "confirmed": not blockers,
        "blockers": blockers,
        "poi_anchored": poi_anchored,
        "poi_micro_synergy": poi_micro_synergy,
        "micro_sweep_confirmed": micro_sweep_confirmed,
        "micro_choch_detected": micro_choch_detected,
        "micro_inside_poi": micro_inside_poi,
    }


def _merge(base_liquidity: dict[str, Any], result: LiquidityReconciliationResult) -> dict[str, Any]:
    merged = dict(base_liquidity)
    payload = result.to_dict()
    merged.update(payload)
    merged["liquidity_reconciliation"] = payload
    merged["liquidity_reconciliation_reason"] = result.readiness_reason
    merged["liquidity_reconciliation_blockers"] = list(result.blockers)
    return merged


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _upper(value: Any) -> str:
    return str(value or "").upper()


def _normalize_waiting_state(value: str) -> str:
    value = _upper(value)
    if value in WAITING_VALUES:
        return "WAITING_TRIGGER"
    if value in {"UNAVAILABLE", "REJECT", "INVALID", "READY"}:
        return value
    return "WAITING_TRIGGER"


def _is_macro_break(liquidity: dict[str, Any], base_state: str) -> bool:
    state = _upper(liquidity.get("liquidity_state") or liquidity.get("event"))
    reason = _upper(liquidity.get("readiness_reason") or liquidity.get("reason"))
    return (
        base_state in REJECT_VALUES
        or state == "BREAK"
        or "BREAK" in reason
        or bool(liquidity.get("macro_break_detected"))
    )


def _has_selected_poi(poi: dict[str, Any]) -> bool:
    return bool(
        poi.get("selected_poi_present")
        or poi.get("selected_poi")
        or poi.get("price_bounds")
    )


def _has_poi_micro_synergy(poi: dict[str, Any]) -> bool:
    synergy = poi.get("poi_micro_synergy") if isinstance(poi.get("poi_micro_synergy"), dict) else {}
    return bool(
        poi.get("poi_micro_synergy_enabled")
        or synergy.get("synergy")
    )
