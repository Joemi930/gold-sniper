"""Kasper/ICT liquidity state machine for the unified XAUUSD strategy.

This module is a pure shadow-only pipeline brick. It reads liquidity evidence
and classifies market intent; it never creates entries or touches live systems.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


SUPPORTS_SETUP = "SUPPORTS_SETUP"
WATCH = "WATCH"
BLOCKS_SETUP = "BLOCKS_SETUP"
LIQUIDITY_READY = "READY"
LIQUIDITY_REDUCED = "REDUCED"
LIQUIDITY_WATCH = "WATCH"
LIQUIDITY_BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class LiquidityStateConfig:
    support_score: float = 70.0
    watch_score: float = 35.0


@dataclass(frozen=True)
class LiquidityStateResult:
    state: str
    decision: str
    score: float
    confidence: float
    grade: str
    hard_block: bool
    dol_status: str
    liquidity_side: str
    event_type: str
    liquidity_quality_score: float = 0.0
    risk_multiplier: float = 0.0
    execution_readiness: str = LIQUIDITY_WATCH
    liquidity_grade: str = "D"
    modulation_reason: str | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    quality_flags: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_liquidity_state(
    liquidity: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
    config: LiquidityStateConfig | None = None,
) -> LiquidityStateResult:
    """Classify liquidity intent for the unified shadow pipeline."""
    cfg = config or LiquidityStateConfig()
    liq = deepcopy(liquidity) if isinstance(liquidity, dict) else {}
    ctx = deepcopy(context) if isinstance(context, dict) else {}
    merged = {**ctx, **liq}

    reasons: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []
    flags: dict[str, Any] = {}
    evidence: dict[str, Any] = {}

    setup_type = _normalize_setup(_first_present(merged, ["setup_type"]))
    dol = _first_present(merged, ["draw_on_liquidity", "dol"])
    dol_status = _dol_status(merged)
    side = _liquidity_side(merged, dol)
    event_type = _event_type(merged)
    evidence.update({"setup_type": setup_type, "draw_on_liquidity": dol, "raw_event_type": event_type})

    if dol_status == "CONSUMED" and not _truthy(_first_present(merged, ["new_draw_on_liquidity", "new_dol", "new_liquidity_target_open"])):
        return _result(
            "LIQUIDITY_CONSUMED",
            BLOCKS_SETUP,
            10.0,
            True,
            dol_status,
            side,
            event_type,
            ["DOL_CONSUMED_NO_NEW_DRAW"],
            warnings,
            missing,
            flags,
            evidence,
        )

    if _truthy(_first_present(merged, ["purge_detected"])) and _truthy(_first_present(merged, ["revert_detected"])):
        return _result(
            "REVERT",
            SUPPORTS_SETUP,
            88.0,
            False,
            dol_status,
            side,
            "REVERT",
            ["PURGE_REVERT_CONFIRMED"],
            warnings,
            missing,
            flags,
            evidence,
        )

    if _truthy(_first_present(merged, ["break_accepted", "breakout_acceptance"])) or event_type == "BREAK":
        if setup_type == "REVERSAL":
            return _result(
                "BREAKOUT_ACCEPTANCE",
                BLOCKS_SETUP,
                20.0,
                True,
                dol_status,
                side,
                "BREAK",
                ["BREAKOUT_ACCEPTANCE_BLOCKS_REVERSAL"],
                warnings,
                missing,
                flags,
                evidence,
            )
        decision = SUPPORTS_SETUP if setup_type == "CONTINUATION" else WATCH
        score = 76.0 if decision == SUPPORTS_SETUP else 55.0
        reasons.append("BREAKOUT_ACCEPTANCE_CONTINUATION_CONTEXT")
        return _result("BREAKOUT_ACCEPTANCE", decision, score, False, dol_status, side, "BREAK", reasons, warnings, missing, flags, evidence)

    if _truthy(_first_present(merged, ["sweep_detected", "sweep_type", "sweep_side"])):
        if _truthy(_first_present(merged, ["sweep_rejected", "rejection_confirmed", "revert_detected", "closed_back_inside"])):
            return _result(
                "SWEEP_REJECTED",
                SUPPORTS_SETUP,
                82.0,
                False,
                dol_status,
                side,
                "SWEEP",
                ["SWEEP_REJECTED"],
                warnings,
                missing,
                flags,
                evidence,
            )
        warnings.append("SWEEP_WITHOUT_REJECTION_CONFIRMATION")
        return _result("PURGE", WATCH, 48.0, False, dol_status, side, "PURGE", ["SWEEP_OR_PURGE_UNRESOLVED"], warnings, missing, flags, evidence)

    if _truthy(_first_present(merged, ["run_detected"])) and dol_status == "OPEN":
        return _result(
            "RUN",
            SUPPORTS_SETUP,
            78.0,
            False,
            dol_status,
            side,
            "RUN",
            ["RUN_TOWARD_OPEN_DOL"],
            warnings,
            missing,
            flags,
            evidence,
        )

    if _truthy(_first_present(merged, ["internal_cleanup", "idm_swept"])):
        if setup_type == "REVERSAL":
            warnings.append("INTERNAL_CLEANUP_NOT_REVERSAL_CONFIRMATION")
            return _result("INTERNAL_CLEANUP", WATCH, 50.0, False, dol_status, side, "CLEANUP", ["INTERNAL_CLEANUP_ONLY"], warnings, missing, flags, evidence)
        decision = SUPPORTS_SETUP if setup_type == "CONTINUATION" else WATCH
        score = 72.0 if decision == SUPPORTS_SETUP else 52.0
        return _result("INTERNAL_CLEANUP", decision, score, False, dol_status, side, "CLEANUP", ["INTERNAL_CLEANUP_CONTINUATION_CONTEXT"], warnings, missing, flags, evidence)

    if _truthy(_first_present(merged, ["approaching_liquidity"])):
        return _result(
            "APPROACHING_LIQUIDITY",
            WATCH,
            45.0,
            False,
            dol_status,
            side,
            "APPROACH",
            ["APPROACHING_LIQUIDITY_WAIT_FOR_RESOLUTION"],
            warnings,
            missing,
            flags,
            evidence,
        )

    if dol_status == "OPEN":
        score = 72.0 if _aligned_with_context(merged) else 58.0
        if _aligned_with_context(merged):
            reasons.append("DOL_OPEN_ALIGNED")
        else:
            warnings.append("DOL_OPEN_ALIGNMENT_UNCLEAR")
            reasons.append("DOL_OPEN")
        decision = SUPPORTS_SETUP if score >= cfg.support_score else WATCH
        return _result("LIQUIDITY_OPEN", decision, score, False, dol_status, side, event_type, reasons, warnings, missing, flags, evidence)

    if _has_any_liquidity_marker(merged):
        warnings.append("LIQUIDITY_MARKERS_WITHOUT_CLEAR_STATE")
        return _result("UNKNOWN", WATCH, 35.0, False, dol_status, side, event_type, ["LIQUIDITY_STATE_UNKNOWN"], warnings, missing, flags, evidence)

    missing.append("LIQUIDITY_STORY_MISSING")
    return _result(
        "NO_LIQUIDITY_STORY",
        BLOCKS_SETUP,
        0.0,
        True,
        dol_status,
        side,
        event_type,
        ["LIQUIDITY_STORY_MISSING_BLOCKS_SETUP"],
        warnings,
        missing,
        flags,
        evidence,
    )


def _result(
    state: str,
    decision: str,
    score: float,
    hard_block: bool,
    dol_status: str,
    liquidity_side: str,
    event_type: str,
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
    evidence: dict[str, Any],
) -> LiquidityStateResult:
    score = round(max(0.0, min(score, 100.0)), 2)
    liquidity_grade, liquidity_score, risk_multiplier, readiness, modulation_reason = _liquidity_modulation(
        decision,
        score,
        hard_block,
        reasons,
        warnings,
    )
    return LiquidityStateResult(
        state=state,
        decision=decision,
        score=score,
        confidence=round(score / 100.0, 3),
        grade=_grade(score, hard_block),
        hard_block=hard_block,
        dol_status=dol_status,
        liquidity_side=liquidity_side,
        event_type=event_type,
        liquidity_quality_score=liquidity_score,
        risk_multiplier=risk_multiplier,
        execution_readiness=readiness,
        liquidity_grade=liquidity_grade,
        modulation_reason=modulation_reason,
        reasons=list(dict.fromkeys(reasons)),
        warnings=list(dict.fromkeys(warnings)),
        missing_evidence=list(dict.fromkeys(missing)),
        quality_flags=flags,
        evidence=evidence,
    )


def _liquidity_modulation(
    decision: str,
    score: float,
    hard_block: bool,
    reasons: list[str],
    warnings: list[str],
) -> tuple[str, float, float, str, str | None]:
    del warnings
    score = round(max(0.0, min(float(score or 0.0), 100.0)), 2)
    if hard_block or decision in {"BLOCK", "REJECT", BLOCKS_SETUP}:
        return "D", score, 0.0, LIQUIDITY_BLOCKED, (reasons[0] if reasons else "LIQUIDITY_HARD_BLOCK")
    if score >= 82.0:
        return "A", score, 1.0, LIQUIDITY_READY, "LIQUIDITY_SUPPORTS_SETUP"
    if score >= 65.0:
        return "B", score, 0.75, LIQUIDITY_REDUCED, "LIQUIDITY_GOOD_REDUCED"
    if score >= 45.0:
        return "C", score, 0.4, LIQUIDITY_WATCH, "LIQUIDITY_WATCH_REDUCED"
    return "D", score, 0.0, LIQUIDITY_BLOCKED, "LIQUIDITY_TOO_WEAK"


def _dol_status(data: dict[str, Any]) -> str:
    raw = str(_first_present(data, ["dol_status"], "")).upper()
    if raw in {"OPEN", "CONSUMED"}:
        return raw
    if _truthy(_first_present(data, ["liquidity_target_consumed", "dol_consumed"])):
        return "CONSUMED"
    if _truthy(_first_present(data, ["liquidity_target_open", "dol_open"])):
        return "OPEN"
    return "UNKNOWN"


def _liquidity_side(data: dict[str, Any], dol: Any) -> str:
    raw = str(_first_present(data, ["liquidity_side", "sweep_side"], dol or "")).upper()
    buy = _truthy(_first_present(data, ["buy_side_liquidity", "equal_highs", "eqh"]))
    sell = _truthy(_first_present(data, ["sell_side_liquidity", "equal_lows", "eql"]))
    if buy and sell:
        return "BOTH"
    if "BUY" in raw or buy:
        return "BUY_SIDE"
    if "SELL" in raw or sell:
        return "SELL_SIDE"
    return "UNKNOWN"


def _event_type(data: dict[str, Any]) -> str:
    raw = str(_first_present(data, ["event_type", "liquidity_event"], "")).upper()
    for event in ("SWEEP", "BREAK", "PURGE", "REVERT", "RUN", "CLEANUP", "APPROACH"):
        if event in raw:
            return event
    if _truthy(_first_present(data, ["break_detected", "break_accepted", "breakout_acceptance"])):
        return "BREAK"
    if _truthy(_first_present(data, ["purge_detected"])):
        return "PURGE"
    if _truthy(_first_present(data, ["revert_detected"])):
        return "REVERT"
    if _truthy(_first_present(data, ["run_detected"])):
        return "RUN"
    if _truthy(_first_present(data, ["internal_cleanup", "idm_swept"])):
        return "CLEANUP"
    if _truthy(_first_present(data, ["approaching_liquidity"])):
        return "APPROACH"
    if _truthy(_first_present(data, ["sweep_detected", "sweep_type", "sweep_side"])):
        return "SWEEP"
    return "UNKNOWN"


def _has_any_liquidity_marker(data: dict[str, Any]) -> bool:
    keys = [
        "draw_on_liquidity",
        "dol",
        "buy_side_liquidity",
        "sell_side_liquidity",
        "equal_highs",
        "equal_lows",
        "eqh",
        "eql",
        "liquidity_event",
    ]
    return any(_first_present(data, [key]) not in (None, "", "UNKNOWN", "NONE", [], {}) for key in keys)


def _aligned_with_context(data: dict[str, Any]) -> bool:
    return _truthy(_first_present(data, ["dol_aligned", "htf_aligned", "order_flow_aligned"]))


def _normalize_setup(raw_value: Any) -> str:
    raw = str(raw_value or "UNKNOWN").upper()
    return raw if raw in {"CONTINUATION", "REVERSAL", "OBSERVATION"} else "UNKNOWN"


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


def _grade(score: float, hard_block: bool) -> str:
    if hard_block:
        return "F"
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 35:
        return "D"
    return "F"
