"""Kasper/ICT POI quality gate for the unified XAUUSD strategy.

This module is a shadow-only pipeline brick. It classifies POI quality and never
decides a final trade, calls a broker, reads environment files, writes files, or
uses network services.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


ACCEPT = "ACCEPT"
WATCH = "WATCH"
REJECT = "REJECT"
EXECUTION_READY = "READY"
EXECUTION_REDUCED = "REDUCED"
EXECUTION_WATCH = "WATCH"
EXECUTION_BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class PoiQualityConfig:
    max_fvg_distance_atr: float = 1.25
    max_fvg_age_sessions: int = 2
    max_fvg_fill_pct: float = 0.55
    max_fvg_close_inside_count: int = 1
    max_ob_wick_penetration_pct: float = 0.25
    max_ob_partial_penetration_pct: float = 0.50
    max_touch_count: int = 2
    min_displacement_score: float = 0.60
    accept_score: float = 72.0
    watch_score: float = 45.0


@dataclass(frozen=True)
class PoiQualityResult:
    decision: str
    poi_type: str
    score: float
    confidence: float
    grade: str
    hard_reject: bool
    invalidity_reason: str | None = None
    quality_score: float = 0.0
    execution_readiness: str = EXECUTION_WATCH
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    quality_flags: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_poi_quality(
    poi: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
    config: PoiQualityConfig | None = None,
) -> PoiQualityResult:
    """Classify a POI as ACCEPT/WATCH/REJECT for the unified shadow pipeline."""
    cfg = config or PoiQualityConfig()
    poi_data = deepcopy(poi) if isinstance(poi, dict) else {}
    ctx = deepcopy(context) if isinstance(context, dict) else {}
    poi_type = _normalize_poi_type(poi_data)
    reasons: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []
    flags: dict[str, Any] = {
        "requires_micro_confirmation": True,
        "micro_confirmation_present": _has_micro_confirmation(ctx),
        "choch_alone_warning": _is_choch_alone(ctx),
        "bos_context_present": _truthy(_first_present(ctx, ["bos_context_present", "bos_detected", "has_bos"])),
    }
    evidence: dict[str, Any] = {"poi_type": poi_type}

    if not _has_bounds(poi_data):
        return _build_result(
            REJECT,
            poi_type,
            0.0,
            True,
            ["POI_BOUNDS_MISSING"],
            warnings,
            missing,
            flags,
            evidence,
        )

    if poi_type == "OB":
        score = _score_ob(poi_data, ctx, cfg, reasons, warnings, missing, flags, evidence)
    elif poi_type == "FVG":
        score = _score_fvg(poi_data, ctx, cfg, reasons, warnings, missing, flags, evidence)
    elif poi_type == "OB_FVG_STACK":
        score = _score_ob(poi_data, ctx, cfg, reasons, warnings, missing, flags, evidence) + 8.0
        score = min(score, 100.0)
        reasons.append("OB_FVG_STACK_CONFLUENCE")
    else:
        return _build_result(
            REJECT,
            poi_type,
            0.0,
            True,
            ["POI_TYPE_UNKNOWN"],
            warnings,
            missing,
            flags,
            evidence,
        )

    _apply_context_quality(ctx, reasons, warnings, missing, flags, evidence)
    score += float(evidence.get("context_score_adjustment", 0.0))
    score = max(0.0, min(score, 100.0))

    if flags["choch_alone_warning"]:
        warnings.append("MICRO_CHOCH_ALONE_NOT_DECISIVE")
        score = min(score, cfg.watch_score + 10.0)

    if _truthy(_first_present(ctx, ["news_veto", "news_not_clear"])) or ctx.get("news_clear") is False:
        warnings.append("NEWS_CONTEXT_NOT_CLEAR")
        score -= 10.0

    score = max(0.0, min(score, 100.0))
    hard_reject = bool(flags.get("hard_reject"))
    if hard_reject:
        decision = REJECT
    elif score >= cfg.accept_score:
        decision = ACCEPT
    elif score >= cfg.watch_score:
        decision = WATCH
    else:
        decision = REJECT

    if poi_type == "FVG" and not flags.get("linked_to_ob") and decision == ACCEPT:
        decision = WATCH
        warnings.append("FVG_CLEAN_BUT_NOT_LINKED_TO_OB")

    if poi_type == "FVG" and flags.get("fvg_required_evidence_missing") and decision == ACCEPT:
        decision = WATCH
        warnings.append("FVG_REQUIRED_EVIDENCE_MISSING_NO_ACCEPT")

    if poi_type == "OB" and flags.get("partial_mitigation_controlled") and decision == ACCEPT:
        decision = WATCH
        warnings.append("PARTIAL_MITIGATION_WATCH_ONLY")

    return _build_result(decision, poi_type, score, hard_reject, reasons, warnings, missing, flags, evidence)


def _score_ob(
    poi: dict[str, Any],
    context: dict[str, Any],
    cfg: PoiQualityConfig,
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
    evidence: dict[str, Any],
) -> float:
    lifecycle = _normalize_lifecycle(_first_present(poi, ["lifecycle_normalized", "lifecycle", "poi_state", "state"]))
    evidence["lifecycle"] = lifecycle
    score = 0.0

    if lifecycle in {"CONSUMED", "INVALIDATED", "MITIGATED"}:
        flags["hard_reject"] = True
        reasons.append(f"OB_{lifecycle}")
        return 0.0

    closes_inside = _to_float(_first_present(poi, ["close_inside_count", "closes_inside"], 0.0))
    if closes_inside > cfg.max_fvg_close_inside_count:
        flags["hard_reject"] = True
        reasons.append("OB_MULTIPLE_CLOSES_INSIDE")
        return 0.0

    touch_count = _to_float(_first_present(poi, ["touch_count", "retest_count", "visits"], None), None)
    if touch_count is None:
        missing.append("OB_TOUCH_COUNT_MISSING")
    elif touch_count > cfg.max_touch_count:
        flags["hard_reject"] = True
        reasons.append("OB_TOO_MANY_TOUCHES")
        return 0.0

    penetration = _to_float(_first_present(poi, ["penetration_pct", "mitigation_pct", "filled_pct"], 0.0))
    if lifecycle == "PARTIALLY_MITIGATED" or penetration > cfg.max_ob_wick_penetration_pct:
        if penetration > cfg.max_ob_partial_penetration_pct:
            flags["hard_reject"] = True
            reasons.append("OB_DEEP_MITIGATION")
            return 0.0
        flags["partial_mitigation_controlled"] = True
        reasons.append("OB_PARTIAL_MITIGATION_CONTROLLED")
        score += 10.0
    elif lifecycle in {"FRESH", "UNMITIGATED"}:
        reasons.append("OB_FRESH_UNMITIGATED")
        score += 18.0
    elif lifecycle == "WICK_TAGGED":
        warnings.append("OB_WICK_TAGGED_LIGHT")
        score += 12.0
    else:
        missing.append("OB_LIFECYCLE_UNKNOWN")
        score += 4.0

    if _has_displacement(poi, context, cfg):
        reasons.append("OB_DISPLACEMENT_AFTER_ZONE")
        score += 15.0
    else:
        warnings.append("OB_NO_DISPLACEMENT")

    if _aligned_with_context(poi, context):
        reasons.append("OB_ALIGNED_WITH_HTF_DOL")
        score += 14.0
    else:
        missing.append("HTF_DOL_ALIGNMENT_MISSING")

    if _truthy(_first_present(poi, ["has_fvg", "imbalance_attached", "fvg_attached", "ob_fvg_stack"])):
        reasons.append("OB_IMBALANCE_ATTACHED")
        score += 12.0
        flags["linked_to_ob"] = True
    else:
        missing.append("OB_IMBALANCE_MISSING")

    if _has_liquidity_story(poi, context):
        reasons.append("OB_LIQUIDITY_SWEEP_CONTEXT")
        score += 12.0
    else:
        missing.append("LIQUIDITY_STORY_MISSING")

    if _truthy(_first_present(poi, ["is_extreme_ob", "at_range_extreme", "range_extreme"])):
        reasons.append("OB_AT_RANGE_EXTREME")
        score += 10.0
    else:
        warnings.append("OB_NOT_CONFIRMED_AT_EXTREME")

    score += _session_score(context, reasons, warnings)
    score += _ote_score(context, reasons, warnings)
    return score


def _score_fvg(
    poi: dict[str, Any],
    context: dict[str, Any],
    cfg: PoiQualityConfig,
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
    evidence: dict[str, Any],
) -> float:
    if not _has_displacement(poi, context, cfg):
        flags["hard_reject"] = True
        reasons.append("FVG_WITHOUT_DISPLACEMENT")
        return 0.0

    score = 18.0
    reasons.append("FVG_CREATED_BY_DISPLACEMENT")
    distance = _to_float(_first_present(poi, ["distance_atr", "distance_to_price_atr", "fvg_distance_atr"], None), None)
    age = _to_float(_first_present(poi, ["age_sessions", "fvg_age_sessions", "age_bars"], None), None)
    fill_pct = _to_float(_first_present(poi, ["fill_pct", "filled_pct", "fvg_filled_pct"], None), None)
    closes_inside = _to_float(_first_present(poi, ["close_inside_count", "closes_inside", "fvg_close_inside_count"], None), None)
    touch_count = _to_float(_first_present(poi, ["touch_count", "retest_count", "visits"], None), None)
    evidence.update({
        "distance_atr": distance,
        "age_sessions": age,
        "fill_pct": fill_pct,
        "close_inside_count": closes_inside,
        "touch_count": touch_count,
    })

    if distance is None:
        missing.append("FVG_DISTANCE_MISSING")
        flags["fvg_required_evidence_missing"] = True
    elif distance <= cfg.max_fvg_distance_atr:
        reasons.append("FVG_NEAR_PRICE")
        score += 14.0
    else:
        warnings.append("FVG_TOO_FAR")
        score -= 20.0

    if age is None:
        missing.append("FVG_AGE_MISSING")
        flags["fvg_required_evidence_missing"] = True
    elif age <= cfg.max_fvg_age_sessions:
        reasons.append("FVG_RECENT")
        score += 10.0
    else:
        warnings.append("FVG_STALE")
        score -= 20.0

    if fill_pct is None:
        missing.append("FVG_FILL_MISSING")
        flags["fvg_required_evidence_missing"] = True
    elif fill_pct <= cfg.max_fvg_fill_pct:
        reasons.append("FVG_NOT_OVERFILLED")
        score += 12.0
    else:
        warnings.append("FVG_TOO_FILLED")
        score -= 25.0

    if closes_inside is None:
        missing.append("FVG_CLOSE_INSIDE_COUNT_MISSING")
        flags["fvg_required_evidence_missing"] = True
    elif closes_inside > cfg.max_fvg_close_inside_count:
        flags["hard_reject"] = True
        reasons.append("FVG_TOO_MANY_CLOSES_INSIDE")
        return 0.0

    if touch_count is None:
        missing.append("FVG_TOUCH_COUNT_MISSING")
        flags["fvg_required_evidence_missing"] = True
    elif touch_count > cfg.max_touch_count:
        flags["hard_reject"] = True
        reasons.append("FVG_TOO_MANY_TOUCHES")
        return 0.0

    if _aligned_with_context(poi, context):
        reasons.append("FVG_ALIGNED_WITH_CONTEXT")
        score += 14.0
    else:
        warnings.append("FVG_CONTEXT_ALIGNMENT_WEAK")
        score -= 8.0

    if _truthy(_first_present(poi, ["linked_ob", "linked_to_ob", "has_ob", "ob_confluence"])):
        reasons.append("FVG_LINKED_TO_OB")
        flags["linked_to_ob"] = True
        score += 12.0
    else:
        flags["linked_to_ob"] = False
        warnings.append("FVG_ISOLATED_WITHOUT_OB")

    if _truthy(_first_present(poi, ["clean_retest", "retest_clean", "has_retest"])):
        reasons.append("FVG_CLEAN_RETEST")
        score += 8.0

    score += _session_score(context, reasons, warnings)
    score += _ote_score(context, reasons, warnings)
    return score


def _apply_context_quality(
    context: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    adjustment = 0.0
    dol = str(_first_present(context, ["draw_on_liquidity", "dol"], "")).upper()
    liquidity_target_open = _truthy(_first_present(context, ["liquidity_target_open", "dol_open"]))
    if dol and dol not in {"UNKNOWN", "NONE"} and liquidity_target_open:
        reasons.append("DOL_OPEN_ALIGNED")
        adjustment += 5.0
    elif not dol or dol in {"UNKNOWN", "NONE"}:
        missing.append("DOL_CONTEXT_MISSING")

    if _has_liquidity_story({}, context):
        adjustment += 4.0
    else:
        warnings.append("NO_LIQUIDITY_STORY")

    if _truthy(_first_present(context, ["ote_conflict", "premium_discount_conflict"])):
        warnings.append("OTE_OR_PREMIUM_DISCOUNT_CONFLICT")
        adjustment -= 5.0
    evidence["context_score_adjustment"] = adjustment
    flags["requires_micro_confirmation"] = True


def _build_result(
    decision: str,
    poi_type: str,
    score: float,
    hard_reject: bool,
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
    evidence: dict[str, Any],
) -> PoiQualityResult:
    score = round(max(0.0, min(score, 100.0)), 2)
    invalidity = _invalidity_reason(hard_reject, reasons)
    readiness = _execution_readiness(decision, score, hard_reject, flags, warnings)
    quality_score = 0.0 if hard_reject else score
    return PoiQualityResult(
        decision=decision,
        poi_type=poi_type,
        score=score,
        confidence=round(score / 100.0, 3),
        grade=_grade(score, hard_reject),
        hard_reject=hard_reject,
        invalidity_reason=invalidity,
        quality_score=quality_score,
        execution_readiness=readiness,
        reasons=list(dict.fromkeys(reasons)),
        warnings=list(dict.fromkeys(warnings)),
        missing_evidence=list(dict.fromkeys(missing)),
        quality_flags=flags,
        evidence=evidence,
    )


def _invalidity_reason(hard_reject: bool, reasons: list[str]) -> str | None:
    if not hard_reject:
        return None
    return str(reasons[0]).upper() if reasons else "POI_INVALID"


def _execution_readiness(
    decision: str,
    score: float,
    hard_reject: bool,
    flags: dict[str, Any],
    warnings: list[str],
) -> str:
    del warnings
    if hard_reject or decision == REJECT:
        return EXECUTION_BLOCKED
    if decision == ACCEPT and score >= 82.0 and not flags.get("partial_mitigation_controlled"):
        return EXECUTION_READY
    if decision == ACCEPT:
        return EXECUTION_REDUCED
    if decision == WATCH:
        return EXECUTION_WATCH
    return EXECUTION_BLOCKED


def _normalize_poi_type(poi: dict[str, Any]) -> str:
    raw = str(_first_present(poi, ["normalized_poi_type", "poi_type", "type", "kind"], "")).upper()
    if "OB" in raw and "FVG" in raw:
        return "OB_FVG_STACK"
    if "ORDER_BLOCK" in raw or raw.startswith("OB") or raw == "ORDERBLOCK":
        return "OB"
    if "FVG" in raw or "IMBALANCE" in raw:
        return "FVG"
    if _truthy(poi.get("is_ob")):
        return "OB"
    if _truthy(poi.get("is_fvg")):
        return "FVG"
    return "UNKNOWN"


def _normalize_lifecycle(raw_value: Any) -> str:
    raw = str(raw_value or "").upper()
    if raw in {"FRESH", "UNMITIGATED"}:
        return "FRESH"
    if "WICK" in raw:
        return "WICK_TAGGED"
    if "PARTIAL" in raw:
        return "PARTIALLY_MITIGATED"
    if "CONSUM" in raw:
        return "CONSUMED"
    if "INVALID" in raw:
        return "INVALIDATED"
    if "MITIGATED" in raw:
        return "MITIGATED"
    return "UNKNOWN"


def _has_bounds(poi: dict[str, Any]) -> bool:
    high = _first_present(poi, ["high", "upper", "top", "zone_high"])
    low = _first_present(poi, ["low", "lower", "bottom", "zone_low"])
    return _to_float(high, None) is not None and _to_float(low, None) is not None


def _has_displacement(poi: dict[str, Any], context: dict[str, Any], cfg: PoiQualityConfig) -> bool:
    explicit = _first_present(poi, ["displacement_after_ob", "displacement_created", "has_displacement", "created_by_displacement"])
    if explicit is not None:
        return _truthy(explicit)
    score = _to_float(_first_present(poi, ["displacement_score", "impulse_score"], None), None)
    if score is not None:
        return score >= cfg.min_displacement_score
    return _truthy(_first_present(context, ["displacement_present", "has_displacement"]))


def _aligned_with_context(poi: dict[str, Any], context: dict[str, Any]) -> bool:
    explicit = _first_present(poi, ["aligned_with_context", "aligned_with_dol", "aligned_with_order_flow"])
    if explicit is not None:
        return _truthy(explicit)
    return _truthy(_first_present(context, ["htf_aligned", "dol_aligned", "order_flow_aligned"]))


def _has_liquidity_story(poi: dict[str, Any], context: dict[str, Any]) -> bool:
    for source in (poi, context):
        if _truthy(_first_present(source, [
            "sweep_detected",
            "liquidity_sweep_before",
            "liquidity_event",
            "idm_swept",
            "buy_side_liquidity",
            "sell_side_liquidity",
            "equal_highs",
            "equal_lows",
        ])):
            return True
    return False


def _session_score(context: dict[str, Any], reasons: list[str], warnings: list[str]) -> float:
    session = str(_first_present(context, ["session", "session_label", "session_bucket", "current_session"], "")).upper()
    if session in {"LONDON", "NY", "NEW_YORK", "NEW YORK", "OVERLAP", "LONDON_NY", "LONDON_KILLZONE", "NY_KILLZONE", "SILVER_BULLET", "LONDON_CLOSE"}:
        reasons.append("SESSION_LONDON_NY_OVERLAP")
        return 8.0
    if session in {"TOKYO", "ASIA", "ASIAN"}:
        warnings.append("ASIA_TOKYO_POI_OBSERVATION_ONLY")
        return -8.0
    if session == "OFF_SESSION":
        warnings.append("OFF_SESSION_POI_OBSERVATION_ONLY")
        return -4.0
    warnings.append("SESSION_CONTEXT_MISSING_OR_UNKNOWN")
    return 0.0


def _ote_score(context: dict[str, Any], reasons: list[str], warnings: list[str]) -> float:
    if _truthy(_first_present(context, ["ote_conflict", "premium_discount_conflict"])):
        warnings.append("FIBONACCI_OTE_CONFLICT")
        return -4.0
    if _truthy(_first_present(context, ["in_ote", "ote_aligned"])):
        reasons.append("OTE_ALIGNED")
        return 5.0
    return 0.0


def _has_micro_confirmation(context: dict[str, Any]) -> bool:
    trigger = str(_first_present(context, ["trigger_kind", "micro_trigger", "trigger"], "")).upper()
    return trigger not in {"", "NONE", "UNKNOWN", "NA"}


def _is_choch_alone(context: dict[str, Any]) -> bool:
    trigger = str(_first_present(context, ["trigger_kind", "micro_trigger", "trigger"], "")).upper()
    if trigger != "MICRO_CHOCH" and trigger != "CHOCH":
        return False
    has_support = _truthy(_first_present(context, ["displacement_present", "has_displacement", "has_retest", "retest_confirmed"]))
    return not has_support


def _first_present(mapping: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in {"1", "TRUE", "YES", "Y", "PASS", "OPEN", "DETECTED", "ALIGNED"}
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
