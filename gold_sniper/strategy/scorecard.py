from __future__ import annotations

from typing import Any

from gold_sniper.strategy.contracts import EvidenceBundle, EvidenceSource, HardVetoResult, ScoreCard, ScoreComponent, SetupGrade


WEIGHTS = {
    "context": 0.25,
    "poi": 0.20,
    "liquidity": 0.15,
    "session": 0.10,
    "ote_timing": 0.10,
    "micro": 0.15,
    "news_soft": 0.05,
}


def evaluate_scorecard(evidence: EvidenceBundle | dict[str, Any] | None, hard_veto: HardVetoResult | None = None) -> ScoreCard:
    bundle = evidence if isinstance(evidence, EvidenceBundle) else EvidenceBundle.from_dict(evidence or {})
    veto = hard_veto or HardVetoResult()

    components = [
        ScoreComponent("context", _context_score(bundle), WEIGHTS["context"], source=EvidenceSource.MARKET_STRUCTURE),
        ScoreComponent("poi", _poi_score(bundle), WEIGHTS["poi"], source=EvidenceSource.POI),
        ScoreComponent("liquidity", _liquidity_score(bundle), WEIGHTS["liquidity"], source=EvidenceSource.LIQUIDITY),
        ScoreComponent("session", _session_score(bundle), WEIGHTS["session"], source=EvidenceSource.SESSION),
        ScoreComponent("ote_timing", _timing_score(bundle), WEIGHTS["ote_timing"], source=EvidenceSource.TIMING),
        ScoreComponent("micro", _micro_score(bundle), WEIGHTS["micro"], source=EvidenceSource.MICRO_CONFIRMATION),
        ScoreComponent("news_soft", _news_soft_score(bundle), WEIGHTS["news_soft"], source=EvidenceSource.NEWS),
    ]

    score_before = round(sum(c.weighted() for c in components), 2)
    score_after = 0.0 if veto.hard_veto or veto.replay_invalid else score_before
    missing = _missing_evidence(bundle)
    soft = _soft_issues(bundle)
    grade = _grade(score_after, missing, veto)
    poi_semantic_status = _poi_semantic_status(bundle.poi)
    micro_semantic_status = _micro_semantic_status(bundle.micro)
    liquidity_semantic_status = _liquidity_semantic_status(bundle.liquidity)

    return ScoreCard(
        components=components,
        score_before_veto=score_before,
        score_after_veto=score_after,
        grade=grade,
        missing_evidence=missing,
        soft_issues=soft,
        metadata={
            "component_values": {c.name: c.value for c in components},
            "veto_code": veto.veto_code,
            "missing_evidence": list(missing),
            "soft_issues": list(soft),
            "section_failed_reasons": _section_failed_reasons(bundle),
            "poi_semantic_status": poi_semantic_status,
            "poi_has_selected_or_candidate": _poi_has_selected_or_candidate(bundle.poi),
            "poi_has_bounds": _poi_has_bounds(bundle.poi),
            "micro_semantic_status": micro_semantic_status,
            "micro_has_evidence": _micro_has_evidence(bundle.micro),
            "micro_readiness_state": bundle.micro.get("readiness_state") or bundle.micro.get("execution_readiness"),
            "micro_readiness_reason": bundle.micro.get("readiness_reason"),
            "liquidity_semantic_status": liquidity_semantic_status,
            "liquidity_has_evidence": _liquidity_has_evidence(bundle.liquidity),
            "liquidity_readiness_state": bundle.liquidity.get("readiness_state") or bundle.liquidity.get("execution_readiness"),
            "liquidity_readiness_reason": bundle.liquidity.get("readiness_reason"),
        },
    )


def _context_score(bundle: EvidenceBundle) -> float:
    context = bundle.context
    score = 0.0
    if context.get("htf_context_available") is True or context.get("htf_aligned") is True:
        score += 40.0
    if context.get("draw_on_liquidity") or context.get("dol_available") is True:
        score += 35.0
    if context.get("direction") in {"BUY", "SELL", "LONG", "SHORT"}:
        score += 25.0
    return _bounded(score)


def _poi_score(bundle: EvidenceBundle) -> float:
    poi = bundle.poi
    if not poi:
        return 0.0
    raw = _float(poi.get("poi_quality_score") or poi.get("quality_score") or poi.get("score"), 0.0)
    if poi.get("selected_poi") or poi.get("poi_available") is True:
        raw = max(raw, 50.0)
    if poi.get("lifecycle_state") in {"FRESH", "WICK_TAGGED", "PARTIAL"}:
        raw += 10.0
    return _bounded(raw)


def _liquidity_score(bundle: EvidenceBundle) -> float:
    liq = bundle.liquidity
    if not liq:
        return 0.0
    raw = _float(liq.get("liquidity_quality_score") or liq.get("score"), 0.0)
    if liq.get("micro_liquidity_confirmed") is True and liq.get("poi_micro_synergy") is True:
        raw = max(raw, 80.0)
    elif liq.get("sweep_rejected") is True:
        raw = max(raw, 80.0)
    elif liq.get("sweep_detected") is True:
        raw = max(raw, 65.0)
    elif liq.get("liquidity_state") in {"RUN_TO_DOL", "SWEEP_REJECT", "PURGE_REVERT"}:
        raw = max(raw, 60.0)
    return _bounded(raw)


def _session_score(bundle: EvidenceBundle) -> float:
    session = bundle.session
    raw = _float(session.get("session_score"), 0.0)
    grade = str(session.get("session_grade") or "").upper()
    if grade == "HIGH":
        raw = max(raw, 85.0)
    elif grade == "MEDIUM":
        raw = max(raw, 60.0)
    elif session.get("trading_allowed") is True:
        raw = max(raw, 50.0)
    return _bounded(raw)


def _timing_score(bundle: EvidenceBundle) -> float:
    timing = bundle.raw.get("timing") if isinstance(bundle.raw.get("timing"), dict) else {}
    raw = _float(bundle.context.get("timing_quality_score") or timing.get("timing_quality_score"), 0.0)
    if timing.get("timing_reconciled") is True and timing.get("timing_evidence_source") == "AGENT5_MICRO_CONTRACT":
        raw = max(raw, 70.0)
    if bundle.context.get("in_ote") is True or timing.get("in_ote") is True:
        raw += 35.0
    if bundle.context.get("premium_discount") in {"PREMIUM", "DISCOUNT"}:
        raw += 25.0
    return _bounded(raw)


def _micro_score(bundle: EvidenceBundle) -> float:
    micro = bundle.micro
    raw = _float(micro.get("score") or micro.get("trigger_strength"), 0.0)
    if micro.get("displacement_present") is True:
        raw += 30.0
    if micro.get("reclaim_confirmed") is True:
        raw += 30.0
    if micro.get("retest_confirmed") is True:
        raw += 30.0
    if micro.get("trigger_inside_poi") is True:
        raw += 10.0
    return _bounded(raw)


def _news_soft_score(bundle: EvidenceBundle) -> float:
    news = bundle.news
    if not news:
        return 0.0
    if news.get("medium_impact_nearby") is True:
        return 40.0
    if news.get("news_clear") is True or news.get("impact_level") == "NONE":
        return 100.0
    return 50.0


def _stage_failed(stage: dict[str, Any]) -> bool:
    reason = str(stage.get("reason") or "").upper()
    return (
        stage.get("passed") is False
        or "MISSING" in reason
        or "INVALID" in reason
        or "REJECT" in reason
        or "BLOCK" in reason
    )


def _news_context_missing(news: dict[str, Any]) -> bool:
    """Return True only when news context is genuinely absent.

    Phase18 fix: ``feed_alive=False`` is normal in replay mode (no live feed)
    and MUST NOT be treated as missing context.  Only an empty dict, an
    explicit ``MISSING`` reason, or a ``NEWS_CONTEXT_MISSING`` calendar status
    signals genuinely absent news context.
    """
    if not news:
        return True
    reason = str(news.get("reason") or "").upper()
    calendar_status = str(news.get("calendar_status") or "").upper()
    if "MISSING" in reason:
        return True
    if calendar_status == "NEWS_CONTEXT_MISSING":
        return True
    # feed_alive=False is expected in replay — news is still evaluated from
    # historical data.  Only treat the context as missing when we have
    # literally nothing (empty dict case above).
    return False


def _session_context_missing(session: dict[str, Any]) -> bool:
    if not session:
        return True
    label = str(session.get("session") or session.get("session_label") or "").upper()
    reason = str(session.get("reason") or "").upper()
    grade = str(session.get("session_grade") or "").upper()
    has_identity = label not in {"", "UNKNOWN", "NONE"}
    has_state = (
        session.get("trading_allowed") is not None
        or grade in {"HIGH", "MEDIUM", "LOW"}
        or session.get("is_hard_block") is not None
    )
    return not (has_identity and has_state) or "MISSING" in reason


def _risk_context_missing(risk: dict[str, Any]) -> bool:
    if not risk:
        return True
    reason = str(risk.get("reason") or "").upper()
    if risk.get("passed") is True and "MISSING" not in reason:
        return False
    guard_fields = (
        "max_daily_loss_hit",
        "max_weekly_loss_hit",
        "max_drawdown_hit",
        "kill_switch",
    )
    has_guard_state = any(field in risk for field in guard_fields)
    has_multiplier = (
        risk.get("atr_risk_multiplier") is not None
        or risk.get("session_risk_multiplier") is not None
        or risk.get("risk_multiplier") is not None
    )
    return not (has_guard_state or has_multiplier) or "MISSING" in reason


def _poi_has_selected_or_candidate(poi: dict[str, Any]) -> bool:
    if not poi:
        return False
    selected = poi.get("selected_poi")
    candidates = poi.get("poi_candidates")
    return (
        bool(poi.get("poi_available"))
        or bool(poi.get("selected_poi_present"))
        or (isinstance(selected, dict) and bool(selected))
        or (isinstance(candidates, list) and len(candidates) > 0)
    )


def _poi_has_bounds(poi: dict[str, Any]) -> bool:
    if not poi:
        return False
    bounds = poi.get("price_bounds")
    if isinstance(bounds, dict) and bool(bounds):
        return True
    selected = poi.get("selected_poi")
    if isinstance(selected, dict):
        selected_bounds = selected.get("price_bounds")
        if isinstance(selected_bounds, dict) and bool(selected_bounds):
            return True
    return bool(poi.get("has_price_bounds"))


def _poi_semantic_status(poi: dict[str, Any]) -> str:
    """
    Classify POI evidence without conflating legacy agent rejection with absence.

    Returned values:
    - POI_ABSENT
    - POI_PRESENT_EXECUTABLE
    - POI_PRESENT_WAITING_TRIGGER
    - POI_PRESENT_UNEXECUTABLE
    - POI_PRESENT_LOW_CONFIDENCE
    - POI_PRESENT_INVALID_BOUNDS
    """
    if not poi or not _poi_has_selected_or_candidate(poi):
        return "POI_ABSENT"
    if not _poi_has_bounds(poi):
        return "POI_PRESENT_INVALID_BOUNDS"

    readiness = str(
        poi.get("readiness_state")
        or poi.get("execution_readiness")
        or poi.get("poi_readiness")
        or ""
    ).upper()
    reason = str(
        poi.get("readiness_reason")
        or poi.get("reason")
        or ""
    ).upper()

    if readiness == "READY":
        return "POI_PRESENT_EXECUTABLE"
    if readiness in {"WAITING_TRIGGER", "WAIT_FOR_TRIGGER"}:
        return "POI_PRESENT_WAITING_TRIGGER"
    if "LOW" in reason or "MEDIUM_CONTEXT" in reason:
        return "POI_PRESENT_LOW_CONFIDENCE"
    if readiness in {"WATCH_ONLY", "WAITING_POI", "UNAVAILABLE", "REJECT", "INVALID"}:
        return "POI_PRESENT_UNEXECUTABLE"
    return "POI_PRESENT_UNEXECUTABLE"


def _micro_has_evidence(micro: dict[str, Any]) -> bool:
    if not micro:
        return False
    return (
        bool(micro.get("micro_handoff_status"))
        or bool(micro.get("agent5_poi_handoff"))
        or bool(micro.get("agent5_consumed_poi"))
        or bool(micro.get("execution_readiness"))
        or bool(micro.get("readiness_state"))
        or bool(micro.get("trigger_type") not in {None, "", "UNKNOWN"})
        or bool(micro.get("sweep_1m_confirmed"))
        or bool(micro.get("choch_detected"))
        or bool(micro.get("displacement_present"))
        or bool(micro.get("reclaim_confirmed"))
        or bool(micro.get("trigger_inside_poi"))
    )


def _micro_semantic_status(micro: dict[str, Any]) -> str:
    """
    Classify micro evidence without conflating a waiting trigger with absence.

    Returned values:
    - MICRO_ABSENT
    - MICRO_READY
    - MICRO_WAITING_TRIGGER
    - MICRO_UNAVAILABLE
    - MICRO_INVALID
    - MICRO_REJECTED
    """
    if not micro or not _micro_has_evidence(micro):
        return "MICRO_ABSENT"

    readiness = str(
        micro.get("readiness_state")
        or micro.get("execution_readiness")
        or ""
    ).upper()
    reason = str(
        micro.get("readiness_reason")
        or micro.get("reason")
        or ""
    ).upper()

    if micro.get("outside_poi") is True or micro.get("trigger_outside_poi") is True:
        return "MICRO_INVALID"
    if readiness == "READY":
        return "MICRO_READY"
    if readiness in {"WAIT_FOR_TRIGGER", "WAITING_TRIGGER"}:
        return "MICRO_WAITING_TRIGGER"
    if readiness == "INVALID":
        return "MICRO_INVALID"
    if readiness == "REJECT":
        return "MICRO_REJECTED"
    if readiness == "UNAVAILABLE":
        if reason in {
            "MICRO_POI_MISSING",
            "MICRO_NO_DIRECTION",
            "MICRO_INSUFFICIENT_1M_CANDLES",
        }:
            return "MICRO_UNAVAILABLE"
        return "MICRO_WAITING_TRIGGER"
    if "NO_TRIGGER" in reason or "WAITING_CHOCH" in reason:
        return "MICRO_WAITING_TRIGGER"
    return "MICRO_WAITING_TRIGGER"


def _liquidity_has_evidence(liquidity: dict[str, Any]) -> bool:
    if not liquidity:
        return False
    return (
        bool(liquidity.get("liquidity_handoff_status"))
        or bool(liquidity.get("agent3_poi_handoff"))
        or bool(liquidity.get("agent3_consumed_poi"))
        or bool(liquidity.get("execution_readiness"))
        or bool(liquidity.get("readiness_state"))
        or str(liquidity.get("liquidity_state") or "").upper() not in {"", "UNKNOWN", "NONE"}
        or bool(liquidity.get("sweep_detected"))
        or bool(liquidity.get("sweep_rejected"))
        or bool(liquidity.get("sweep_side") not in {None, "", "UNKNOWN"})
        or bool(liquidity.get("idm_detected"))
        or bool(liquidity.get("idm_swept"))
    )


def _liquidity_semantic_status(liquidity: dict[str, Any]) -> str:
    """
    Classify liquidity evidence without conflating waiting sweep with absence.

    Returned values:
    - LIQUIDITY_ABSENT
    - LIQUIDITY_READY
    - LIQUIDITY_WAITING_SWEEP
    - LIQUIDITY_REJECTED
    - LIQUIDITY_UNAVAILABLE
    - LIQUIDITY_INVALID
    """
    if not liquidity or not _liquidity_has_evidence(liquidity):
        return "LIQUIDITY_ABSENT"

    readiness = str(
        liquidity.get("readiness_state")
        or liquidity.get("execution_readiness")
        or ""
    ).upper()
    reason = str(
        liquidity.get("readiness_reason")
        or liquidity.get("reason")
        or ""
    ).upper()
    state = str(
        liquidity.get("liquidity_state")
        or liquidity.get("event")
        or ""
    ).upper()

    if readiness == "READY":
        return "LIQUIDITY_READY"
    if readiness in {"WAIT_FOR_TRIGGER", "WAITING_TRIGGER"}:
        return "LIQUIDITY_WAITING_SWEEP"
    if readiness == "REJECT":
        return "LIQUIDITY_REJECTED"
    if readiness == "INVALID":
        return "LIQUIDITY_INVALID"
    if readiness == "UNAVAILABLE":
        if reason in {
            "LIQUIDITY_POI_MISSING",
            "LIQUIDITY_WAITING_AGENT2_RESULT",
            "WAITING_INSUFFICIENT_DATA",
        }:
            return "LIQUIDITY_UNAVAILABLE"
        return "LIQUIDITY_WAITING_SWEEP"
    if state == "SWEEP":
        return "LIQUIDITY_READY"
    if state in {"NONE", "APPROACH"}:
        return "LIQUIDITY_WAITING_SWEEP"
    if state == "BREAK":
        return "LIQUIDITY_REJECTED"
    if "WAITING_SWEEP" in reason or "NO_SWEEP" in reason:
        return "LIQUIDITY_WAITING_SWEEP"
    if "BREAK" in reason:
        return "LIQUIDITY_REJECTED"
    return "LIQUIDITY_WAITING_SWEEP"


def _section_failed_reasons(bundle: EvidenceBundle) -> dict[str, Any]:
    return {
        "context": _stage_reason(bundle.context),
        "poi": _stage_reason(bundle.poi),
        "liquidity": _stage_reason(bundle.liquidity),
        "micro": _stage_reason(bundle.micro),
        "news": _stage_reason(bundle.news),
        "session": _stage_reason(bundle.session),
        "risk": _stage_reason(bundle.risk),
    }


def _stage_reason(stage: dict[str, Any]) -> dict[str, Any]:
    if not stage:
        return {"failed": True, "reason": "SECTION_EMPTY"}
    return {
        "failed": _stage_failed(stage),
        "reason": stage.get("reason") or stage.get("readiness_reason") or stage.get("execution_readiness") or "UNKNOWN",
        "passed": stage.get("passed"),
    }


def _missing_evidence(bundle: EvidenceBundle) -> list[str]:
    missing: list[str] = []
    if not bundle.context or _stage_failed(bundle.context):
        missing.append("CONTEXT_MISSING")
    poi_status = _poi_semantic_status(bundle.poi)
    if poi_status == "POI_ABSENT":
        missing.append("POI_MISSING")
    elif poi_status == "POI_PRESENT_INVALID_BOUNDS":
        missing.append("POI_BOUNDS_MISSING")
    liquidity_status = _liquidity_semantic_status(bundle.liquidity)
    if liquidity_status == "LIQUIDITY_ABSENT":
        missing.append("LIQUIDITY_MISSING")
    elif liquidity_status == "LIQUIDITY_UNAVAILABLE":
        missing.append("LIQUIDITY_UNAVAILABLE")
    elif liquidity_status == "LIQUIDITY_INVALID":
        missing.append("LIQUIDITY_INVALID")
    elif liquidity_status == "LIQUIDITY_REJECTED":
        missing.append("LIQUIDITY_REJECTED")
    micro_status = _micro_semantic_status(bundle.micro)
    if micro_status == "MICRO_ABSENT":
        missing.append("MICRO_MISSING")
    elif micro_status == "MICRO_UNAVAILABLE":
        missing.append("MICRO_UNAVAILABLE")
    elif micro_status == "MICRO_INVALID":
        missing.append("MICRO_INVALID")
    if _news_context_missing(bundle.news):
        missing.append("NEWS_CONTEXT_MISSING")
    if _session_context_missing(bundle.session):
        missing.append("SESSION_CONTEXT_MISSING")
    if _risk_context_missing(bundle.risk):
        missing.append("RISK_CONTEXT_MISSING")
    return missing


def _soft_issues(bundle: EvidenceBundle) -> list[str]:
    issues: list[str] = []
    poi_status = _poi_semantic_status(bundle.poi)
    if poi_status in {"POI_PRESENT_UNEXECUTABLE", "POI_PRESENT_LOW_CONFIDENCE", "POI_PRESENT_WAITING_TRIGGER"}:
        issues.append(poi_status)
    if poi_status == "POI_PRESENT_INVALID_BOUNDS":
        issues.append("POI_PRESENT_BOUNDS_INVALID")
    liquidity_status = _liquidity_semantic_status(bundle.liquidity)
    if liquidity_status == "LIQUIDITY_WAITING_SWEEP":
        reason = str(bundle.liquidity.get("readiness_reason") or "LIQUIDITY_WAITING_SWEEP")
        issues.append(reason)
    if liquidity_status == "LIQUIDITY_REJECTED":
        issues.append("LIQUIDITY_REJECTED")
    if liquidity_status == "LIQUIDITY_INVALID":
        issues.append("LIQUIDITY_INVALID")
    micro_status = _micro_semantic_status(bundle.micro)
    if micro_status == "MICRO_WAITING_TRIGGER":
        reason = str(bundle.micro.get("readiness_reason") or "MICRO_WAITING_TRIGGER")
        issues.append(reason)
    if micro_status == "MICRO_REJECTED":
        issues.append("MICRO_REJECTED")
    if micro_status == "MICRO_INVALID":
        issues.append("MICRO_INVALID")
    if bundle.news.get("medium_impact_nearby") is True:
        issues.append("NEWS_MEDIUM_IMPACT_SOFT_PENALTY")
    if bundle.session.get("session_grade") == "MEDIUM":
        issues.append("SESSION_MEDIUM")
    if bundle.micro and bundle.micro.get("retest_confirmed") is not True:
        issues.append("MICRO_RETEST_MISSING")
    return issues


def _grade(score: float, missing: list[str], veto: HardVetoResult) -> SetupGrade:
    if veto.hard_veto or veto.replay_invalid:
        return SetupGrade.D
    severe_missing = {"CONTEXT_MISSING", "POI_MISSING", "LIQUIDITY_MISSING"}
    if severe_missing.intersection(missing) and score < 75.0:
        return SetupGrade.D
    if score >= 85.0:
        return SetupGrade.A_PLUS
    if score >= 75.0:
        return SetupGrade.A
    if score >= 65.0:
        return SetupGrade.B
    if score >= 55.0:
        return SetupGrade.C
    return SetupGrade.D


def _bounded(value: float) -> float:
    return round(max(0.0, min(float(value), 100.0)), 2)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
