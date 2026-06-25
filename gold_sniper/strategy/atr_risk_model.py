"""ATR-based risk plan for XAUUSD shadow scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

GRADE_BASE_RISK = {
    "A_PLUS": 1.0,
    "A+": 1.0,
    "A": 0.75,
    "B": 0.4,
    "C": 0.0,
    "D": 0.0,
}


@dataclass(frozen=True)
class AtrRiskPlan:
    risk_pct: float
    atr_value: float | None
    sl_price: float | None
    tp1_price: float | None
    tp2_price: float | None
    rr_to_tp1: float | None
    rr_to_tp2: float | None
    position_size_available: bool
    risk_valid: bool
    reason: str
    base_risk_pct: float = 0.0
    adjusted_risk_pct: float = 0.0
    risk_multiplier: float = 0.0
    risk_band: str = "ZERO"
    sizing_reason: str = ""
    risk_modulators: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_atr_risk_plan(
    entry_price: Any,
    direction: str,
    atr_value: Any,
    *,
    structural_stop: Any = None,
    risk_pct: float | None = None,
    setup_grade: str = "D",
    professional_multiplier: Any = 1.0,
    session_multiplier: Any = 1.0,
    timing_multiplier: Any = 1.0,
    liquidity_multiplier: Any = 1.0,
    hard_veto: bool = False,
) -> AtrRiskPlan:
    entry = _num(entry_price)
    atr = _num(atr_value)
    direction = str(direction or "UNKNOWN").upper()
    stop = _num(structural_stop)
    adjustment = adjust_risk_for_setup(
        setup_grade=setup_grade,
        professional_multiplier=professional_multiplier,
        session_multiplier=session_multiplier,
        timing_multiplier=timing_multiplier,
        liquidity_multiplier=liquidity_multiplier,
        atr_valid=entry is not None and atr is not None and atr > 0 and direction in {"LONG", "SHORT"},
        hard_veto=hard_veto,
    )
    if entry is None or atr is None or atr <= 0 or direction not in {"LONG", "SHORT"}:
        return _plan(adjustment, atr, None, None, None, None, None, False, False, "RISK_INPUT_MISSING", risk_pct)
    if stop is None:
        stop = entry - atr * 1.5 if direction == "LONG" else entry + atr * 1.5
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0:
        adjustment = adjust_risk_for_setup(
            setup_grade=setup_grade,
            professional_multiplier=professional_multiplier,
            session_multiplier=session_multiplier,
            timing_multiplier=timing_multiplier,
            liquidity_multiplier=liquidity_multiplier,
            atr_valid=False,
            hard_veto=hard_veto,
        )
        return _plan(adjustment, atr, stop, None, None, None, None, False, False, "SL_IMPOSSIBLE", risk_pct)
    tp1 = entry + risk if direction == "LONG" else entry - risk
    tp2 = entry + risk * 2 if direction == "LONG" else entry - risk * 2
    return _plan(adjustment, atr, stop, tp1, tp2, 1.0, 2.0, True, True, "ATR_RISK_VALID", risk_pct)


def risk_pct_for_grade(setup_grade: str) -> float:
    return GRADE_BASE_RISK.get(str(setup_grade or "D").upper(), 0.0)


def adjust_risk_for_setup(
    *,
    setup_grade: str,
    professional_multiplier: Any = 1.0,
    session_multiplier: Any = 1.0,
    timing_multiplier: Any = 1.0,
    liquidity_multiplier: Any = 1.0,
    atr_valid: bool = True,
    hard_veto: bool = False,
) -> dict[str, Any]:
    base = risk_pct_for_grade(setup_grade)
    modulators = {
        "professional": _bounded_multiplier(professional_multiplier),
        "session": _bounded_multiplier(session_multiplier),
        "timing": _bounded_multiplier(timing_multiplier),
        "liquidity": _bounded_multiplier(liquidity_multiplier),
    }
    if hard_veto:
        return {
            "base_risk_pct": base,
            "adjusted_risk_pct": 0.0,
            "risk_multiplier": 0.0,
            "risk_band": "ZERO",
            "sizing_reason": "HARD_VETO_ZERO_RISK",
            "risk_modulators": modulators,
        }
    if not atr_valid:
        return {
            "base_risk_pct": base,
            "adjusted_risk_pct": 0.0,
            "risk_multiplier": 0.0,
            "risk_band": "ZERO",
            "sizing_reason": "ATR_INVALID_ZERO_RISK",
            "risk_modulators": modulators,
        }
    multiplier = 1.0
    for value in modulators.values():
        multiplier *= value
    adjusted = round(max(0.0, min(base * multiplier, 1.0)), 4)
    if adjusted >= 0.9:
        band = "FULL"
    elif adjusted >= 0.65:
        band = "REDUCED"
    elif adjusted >= 0.25:
        band = "MICRO"
    elif adjusted > 0.0:
        band = "TINY"
    else:
        band = "ZERO"
    return {
        "base_risk_pct": base,
        "adjusted_risk_pct": adjusted,
        "risk_multiplier": round(multiplier, 4),
        "risk_band": band,
        "sizing_reason": f"{str(setup_grade or 'D').upper()}_{band}",
        "risk_modulators": modulators,
    }


def _plan(
    adjustment: dict[str, Any],
    atr: float | None,
    stop: float | None,
    tp1: float | None,
    tp2: float | None,
    rr1: float | None,
    rr2: float | None,
    size_available: bool,
    risk_valid: bool,
    reason: str,
    legacy_risk_pct: float | None,
) -> AtrRiskPlan:
    adjusted = float(adjustment.get("adjusted_risk_pct", 0.0) or 0.0)
    return AtrRiskPlan(
        adjusted if legacy_risk_pct is None else float(legacy_risk_pct),
        atr,
        stop,
        tp1,
        tp2,
        rr1,
        rr2,
        size_available,
        risk_valid,
        reason,
        base_risk_pct=float(adjustment.get("base_risk_pct", 0.0) or 0.0),
        adjusted_risk_pct=adjusted,
        risk_multiplier=float(adjustment.get("risk_multiplier", 0.0) or 0.0),
        risk_band=str(adjustment.get("risk_band") or "ZERO"),
        sizing_reason=str(adjustment.get("sizing_reason") or ""),
        risk_modulators=dict(adjustment.get("risk_modulators") or {}),
    )


def _bounded_multiplier(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 1.0
    return round(max(0.0, min(number, 1.0)), 4)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
