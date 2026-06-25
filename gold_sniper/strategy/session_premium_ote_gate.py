"""Session, news, premium/discount, and OTE gate for the unified strategy.

This module is a pure shadow-only context gate. It never creates entries,
calls a broker, reads environment files, writes files, or reaches the network.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


PASS = "PASS"
WATCH = "WATCH"
BLOCK = "BLOCK"
TIMING_READY = "READY"
TIMING_REDUCED = "REDUCED"
TIMING_WATCH = "WATCH"
TIMING_BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class SessionPremiumOteConfig:
    pass_score: float = 70.0
    watch_score: float = 35.0


@dataclass(frozen=True)
class SessionPremiumOteResult:
    decision: str
    score: float
    confidence: float
    grade: str
    hard_block: bool
    session_status: str
    news_status: str
    premium_discount_status: str
    ote_status: str
    timing_quality_score: float = 0.0
    risk_multiplier: float = 0.0
    execution_readiness: str = TIMING_WATCH
    modulation_reason: str | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    quality_flags: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_session_premium_ote_gate(
    context: dict[str, Any] | None,
    setup_type: str | None = None,
    config: SessionPremiumOteConfig | None = None,
) -> SessionPremiumOteResult:
    """Evaluate contextual permission after POI quality and before micro timing."""
    cfg = config or SessionPremiumOteConfig()
    ctx = deepcopy(context) if isinstance(context, dict) else {}
    setup = _normalize_setup(setup_type or _first_present(ctx, ["setup_type"]))
    direction = _normalize_direction(_first_present(ctx, ["direction", "bias", "htf_bias", "order_flow"]))

    reasons: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []
    flags: dict[str, Any] = {}
    evidence = {"setup_type": setup, "direction": direction}
    score = 0.0

    session_status, session_score = _evaluate_session(ctx, reasons, warnings, missing, flags)
    news_status, news_score = _evaluate_news(ctx, reasons, warnings, missing, flags)
    pd_status, pd_score = _evaluate_premium_discount(ctx, setup, direction, reasons, warnings, missing, flags)
    ote_status, ote_score = _evaluate_ote(ctx, setup, reasons, warnings, missing, flags)
    score += session_score + news_score + pd_score + ote_score

    evidence.update(
        {
            "session_status": session_status,
            "news_status": news_status,
            "premium_discount_status": pd_status,
            "ote_status": ote_status,
        }
    )

    hard_block = bool(flags.get("hard_block"))
    score = max(0.0, min(score, 100.0))
    if hard_block:
        decision = BLOCK
    elif session_status == "UNKNOWN" or news_status == "UNKNOWN":
        decision = WATCH
    elif score >= cfg.pass_score:
        decision = PASS
    elif score >= cfg.watch_score:
        decision = WATCH
    else:
        decision = BLOCK

    if flags.get("continuation_context_conflict") and decision == PASS:
        decision = WATCH
    if flags.get("ote_unusable") and decision == PASS:
        decision = WATCH

    return _result(decision, score, hard_block, session_status, news_status, pd_status, ote_status, reasons, warnings, missing, flags, evidence)


def _evaluate_session(
    ctx: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
) -> tuple[str, float]:
    if ctx.get("trading_allowed") is False or ctx.get("session_allowed") is False:
        flags["hard_block"] = True
        reasons.append("SESSION_EXPLICITLY_BLOCKED")
        return "BLOCKED", 0.0
    session = str(_first_present(ctx, ["session", "session_label", "session_bucket", "current_session"], "")).upper()
    if session in {"TOKYO", "ASIA", "ASIAN"}:
        flags["hard_block"] = True
        reasons.append("SESSION_TOKYO_ASIA_BLOCKED")
        return "BLOCKED", 0.0
    if session in {"LONDON", "LONDON_OPEN", "LONDON OPEN", "NY", "NEW_YORK", "NEW YORK", "NY_OPEN", "NY OPEN", "OVERLAP", "LONDON_NY"}:
        reasons.append("SESSION_ALLOWED_LONDON_NY_OVERLAP")
        return "ALLOWED", 25.0
    missing.append("SESSION_CONTEXT_MISSING")
    warnings.append("SESSION_UNKNOWN")
    return "UNKNOWN", 5.0


def _evaluate_news(
    ctx: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
) -> tuple[str, float]:
    if (
        _truthy(_first_present(ctx, ["news_veto", "high_impact_news", "pre_news_lockout"]))
        or ctx.get("news_clear") is False
        or ctx.get("hard_filter_pass") is False
    ):
        flags["hard_block"] = True
        reasons.append("NEWS_CONTEXT_BLOCKED")
        return "BLOCKED", 0.0
    if _truthy(_first_present(ctx, ["post_news_stealth"])) and ctx.get("news_normalized") is False:
        flags["hard_block"] = True
        reasons.append("POST_NEWS_STEALTH_NOT_NORMALIZED")
        return "BLOCKED", 0.0
    if ctx.get("news_clear") is True or str(_first_present(ctx, ["calendar_status", "news_normalized"], "")).upper() in {"CLEAR", "NORMALIZED", "TRUE"}:
        reasons.append("NEWS_CLEAR")
        return "CLEAR", 20.0
    missing.append("NEWS_CONTEXT_MISSING")
    warnings.append("NEWS_UNKNOWN")
    return "UNKNOWN", 5.0


def _evaluate_premium_discount(
    ctx: dict[str, Any],
    setup: str,
    direction: str,
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
) -> tuple[str, float]:
    zone = _premium_discount_zone(ctx)
    conflict = _pd_conflict(zone, direction) or _truthy(_first_present(ctx, ["premium_forbidden", "discount_forbidden", "premium_discount_conflict"]))
    if zone == "UNKNOWN":
        missing.append("PREMIUM_DISCOUNT_CONTEXT_MISSING")
        warnings.append("PREMIUM_DISCOUNT_UNKNOWN")
        return "UNKNOWN", 5.0
    if conflict and setup in {"REVERSAL", "SNIPER_PULLBACK"}:
        flags["hard_block"] = True
        reasons.append("PREMIUM_DISCOUNT_CONFLICT_BLOCKS_STRICT_SETUP")
        return "CONFLICT", 0.0
    if conflict:
        flags["continuation_context_conflict"] = True
        warnings.append("PREMIUM_DISCOUNT_CONFLICT_CONTINUATION_WATCH")
        return "CONFLICT", 10.0
    reasons.append("PREMIUM_DISCOUNT_ALIGNED")
    return "ALIGNED", 25.0


def _evaluate_ote(
    ctx: dict[str, Any],
    setup: str,
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
) -> tuple[str, float]:
    anchor_value = _first_present(ctx, ["fibonacci_anchor_valid", "fib_anchor_valid", "dealing_range_valid", "fib_range_valid"])
    anchor_known = anchor_value is not None
    anchor_valid = _truthy(anchor_value)
    if anchor_known and not anchor_valid:
        flags["ote_unusable"] = True
        warnings.append("FIBONACCI_ANCHOR_INVALID")
        if setup in {"REVERSAL", "SNIPER_PULLBACK"}:
            flags["hard_block"] = True
            reasons.append("FIBONACCI_ANCHOR_INVALID_BLOCKS_STRICT_SETUP")
            return "INVALID", 0.0
        return "INVALID", 5.0

    if _truthy(_first_present(ctx, ["ote_conflict"])):
        warnings.append("OTE_CONFLICT")
        if setup in {"REVERSAL", "SNIPER_PULLBACK"}:
            flags["hard_block"] = True
            reasons.append("OTE_CONFLICT_BLOCKS_STRICT_SETUP")
            return "CONFLICT", 0.0
        flags["continuation_context_conflict"] = True
        return "CONFLICT", 10.0

    if _truthy(_first_present(ctx, ["in_ote", "ote_aligned", "sweet_spot"])):
        reasons.append("OTE_ALIGNED")
        return "ALIGNED", 25.0

    if setup in {"REVERSAL", "SNIPER_PULLBACK"}:
        missing.append("OTE_CONTEXT_MISSING_FOR_STRICT_SETUP")
        warnings.append("OTE_MISSING_STRICT_SETUP")
        return "UNKNOWN", 5.0

    warnings.append("OTE_MISSING_CONTINUATION_TOLERATED")
    return "UNKNOWN", 10.0


def _result(
    decision: str,
    score: float,
    hard_block: bool,
    session_status: str,
    news_status: str,
    premium_discount_status: str,
    ote_status: str,
    reasons: list[str],
    warnings: list[str],
    missing: list[str],
    flags: dict[str, Any],
    evidence: dict[str, Any],
) -> SessionPremiumOteResult:
    score = round(max(0.0, min(score, 100.0)), 2)
    timing_score, risk_multiplier, readiness, modulation_reason = _timing_modulation(
        decision,
        score,
        hard_block,
        warnings,
        reasons,
    )
    return SessionPremiumOteResult(
        decision=decision,
        score=score,
        confidence=round(score / 100.0, 3),
        grade=_grade(score, hard_block),
        hard_block=hard_block,
        session_status=session_status,
        news_status=news_status,
        premium_discount_status=premium_discount_status,
        ote_status=ote_status,
        timing_quality_score=timing_score,
        risk_multiplier=risk_multiplier,
        execution_readiness=readiness,
        modulation_reason=modulation_reason,
        reasons=list(dict.fromkeys(reasons)),
        warnings=list(dict.fromkeys(warnings)),
        missing_evidence=list(dict.fromkeys(missing)),
        quality_flags=flags,
        evidence=evidence,
    )


def _timing_modulation(
    decision: str,
    score: float,
    hard_block: bool,
    warnings: list[str],
    reasons: list[str],
) -> tuple[float, float, str, str | None]:
    score = round(max(0.0, min(float(score or 0.0), 100.0)), 2)
    if hard_block or decision in {"BLOCK", "REJECT"}:
        return score, 0.0, TIMING_BLOCKED, (reasons[0] if reasons else "TIMING_HARD_BLOCK")
    if decision == "PASS" and score >= 82.0 and not warnings:
        return score, 1.0, TIMING_READY, "TIMING_HIGH_QUALITY"
    if decision == "PASS" and score >= 70.0:
        return score, 0.75, TIMING_REDUCED, "TIMING_GOOD_REDUCED"
    if decision == "WATCH" or score >= 45.0:
        return score, 0.4, TIMING_WATCH, "TIMING_WATCH_REDUCED"
    return score, 0.0, TIMING_BLOCKED, "TIMING_TOO_WEAK"


def _premium_discount_zone(ctx: dict[str, Any]) -> str:
    if _truthy(_first_present(ctx, ["in_premium"])):
        return "PREMIUM"
    if _truthy(_first_present(ctx, ["in_discount"])):
        return "DISCOUNT"
    raw = str(_first_present(ctx, ["premium_discount", "pd_zone", "price_location", "dealing_range_position"], "")).upper()
    if "PREMIUM" in raw:
        return "PREMIUM"
    if "DISCOUNT" in raw:
        return "DISCOUNT"
    return "UNKNOWN"


def _pd_conflict(zone: str, direction: str) -> bool:
    return (direction == "LONG" and zone == "PREMIUM") or (direction == "SHORT" and zone == "DISCOUNT")


def _normalize_setup(raw_value: Any) -> str:
    raw = str(raw_value or "UNKNOWN").upper()
    return raw if raw in {"CONTINUATION", "TREND_CONTINUATION", "REVERSAL", "SNIPER_PULLBACK", "OBSERVATION"} else "UNKNOWN"


def _normalize_direction(raw_value: Any) -> str:
    raw = str(raw_value or "").upper()
    if raw in {"LONG", "BUY", "BULLISH"}:
        return "LONG"
    if raw in {"SHORT", "SELL", "BEARISH"}:
        return "SHORT"
    return "UNKNOWN"


def _first_present(mapping: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in {"1", "TRUE", "YES", "Y", "PASS", "OPEN", "CLEAR", "NORMALIZED", "ALIGNED"}
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
