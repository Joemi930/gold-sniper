# LEGACY STRATEGY MODULE - FROZEN after clean repo restart.
# DO NOT EXTEND as an autonomous strategy. Future role: input brick for the unified XAUUSD pipeline.
# Phase 0 preserves behavior; no trading logic is changed here.

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from strategies.base_strategy import StrategyModule, strategy_result


TRIGGER_NONE_VALUES = {"", "NONE", "UNKNOWN", "NA"}


def _flag(data: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key in data:
            return bool(data.get(key))
    return None


def _star(passed: bool | None, reason: str) -> dict[str, Any]:
    return {"passed": bool(passed), "reason": reason}


def _session_bucket(value: Any) -> str:
    text = str(value or "UNKNOWN").upper()
    if "TOKYO" in text or "ASIA" in text:
        return "TOKYO"
    if "LONDON" in text:
        return "LONDON"
    if text.startswith("NY") or "NEW_YORK" in text:
        return "NY"
    return "OTHER"


def _golden_hour_from_time(value: Any) -> bool | None:
    if not value:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        paris = dt.astimezone(ZoneInfo("Europe/Paris"))
        return paris.hour == 16
    except (TypeError, ValueError):
        return None


def score_ob_five_star(poi: dict[str, Any], context: dict[str, Any], trigger: dict[str, Any]) -> dict[str, Any]:
    lifecycle = str(poi.get("lifecycle_normalized") or poi.get("poi_state") or "UNKNOWN").upper()
    has_price_bounds = poi.get("has_price_bounds")
    if has_price_bounds is None:
        has_price_bounds = poi.get("high") is not None and poi.get("low") is not None

    imbalance = _flag(
        poi,
        "imbalance_created",
        "fvg_created_after_ob",
        "fvg_created",
        "has_fvg",
        "has_imbalance",
        "displacement_created",
        "impulse_after_ob",
    )
    sweep = _flag(poi, "liquidity_sweep_before", "sweep_before_ob", "has_sweep_before_ob", "sweep_detected")
    extreme = _flag(poi, "is_extreme_ob", "structural_extreme", "extreme_ob", "at_structure_extreme")
    session_created = poi.get("session_created") or poi.get("created_session") or poi.get("session")
    created_bucket = _session_bucket(session_created)
    golden = _flag(poi, "golden_hour_return", "return_in_golden_hour")
    if golden is None:
        golden = _golden_hour_from_time(poi.get("return_time") or poi.get("touched_at") or trigger.get("timestamp"))

    unmitigated = lifecycle in {"FRESH", "UNMITIGATED"}
    invalid = lifecycle in {"CONSUMED", "INVALIDATED"} or not bool(has_price_bounds)
    stars = {
        "imbalance_created": _star(imbalance, "FVG_OR_DISPLACEMENT_AFTER_OB" if imbalance else "FIELD_MISSING" if imbalance is None else "NO_IMBALANCE_DETECTED"),
        "liquidity_sweep_before": _star(sweep, "LIQUIDITY_SWEEP_BEFORE_OB" if sweep else "FIELD_MISSING" if sweep is None else "NO_SWEEP_DETECTED"),
        "extreme_ob": _star(extreme, "LOCAL_STRUCTURAL_EXTREME" if extreme else "FIELD_MISSING" if extreme is None else "NOT_STRUCTURAL_EXTREME_OR_UNKNOWN"),
        "unmitigated": _star(unmitigated, "OB_REMAINS_FRESH_UNMITIGATED" if unmitigated else f"OB_LIFECYCLE_{lifecycle}"),
        "london_or_ny_creation": _star(created_bucket in {"LONDON", "NY"}, f"OB_CREATED_DURING_{created_bucket}" if created_bucket in {"LONDON", "NY"} else "SESSION_FIELD_MISSING" if not session_created else "ASIAN_OR_LOW_LIQUIDITY_OB_NOT_FIVE_STAR"),
    }
    base_count = sum(1 for star in stars.values() if star["passed"])
    bonus_pass = bool(golden)
    bonus_score = 0.5 if bonus_pass else 0.0
    score_pct = min(100.0, round((base_count / 5.0) * 100.0 + (bonus_score * 10.0), 4))
    if invalid:
        tier = "INVALID_OB"
    elif base_count == 5:
        tier = "FIVE_STAR_STRICT"
    elif base_count == 4:
        tier = "FOUR_STAR_WATCH"
    elif base_count == 3:
        tier = "THREE_STAR_WEAK"
    else:
        tier = "LOW_QUALITY_OB"

    missing = [
        key for key, value in stars.items()
        if not value["passed"] and value["reason"] == "FIELD_MISSING"
    ]
    disqualifying = []
    if invalid:
        disqualifying.append("INVALID_OR_MISSING_PRICE_BOUNDS")
    if lifecycle in {"WICK_TAGGED", "PARTIALLY_MITIGATED", "MITIGATED", "CONSUMED", "INVALIDATED", "STALE", "UNKNOWN"}:
        disqualifying.append(f"NOT_STRICTLY_UNMITIGATED_{lifecycle}")
    if created_bucket == "TOKYO":
        disqualifying.append("TOKYO_CREATION_SESSION")

    return {
        "base_star_count": base_count,
        "bonus_star_count": bonus_score,
        "score_pct": score_pct,
        "quality_tier": tier,
        "stars": stars,
        "bonus": {
            "golden_hour_return": _star(bonus_pass, "GOLDEN_HOUR_RETURN" if bonus_pass else "NO_GOLDEN_HOUR_RETURN"),
        },
        "missing_evidence": missing,
        "disqualifying_reasons": disqualifying,
        "session_created": created_bucket,
    }


class ObFiveStarStrict(StrategyModule):
    strategy_id = "OB_FIVE_STAR_STRICT"
    strategy_family = "OB"

    def evaluate(self, data: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
        del agents
        context, poi, trigger, risk = data["context"], data["poi"], data["trigger"], data["risk"]
        applicable = bool(poi.get("is_ob"))
        scoring = score_ob_five_star(poi, context, trigger)
        tier = scoring["quality_tier"]
        trigger_kind = str(trigger.get("trigger_kind") or "NONE").upper()
        if not poi.get("is_ob"):
            permission, reason, layer = "REJECT", "OB_NOT_EXPOSED_BY_AGENT2", "POI"
        elif tier == "INVALID_OB":
            permission, reason, layer = "REJECT", "OB_FIVE_STAR_INVALID_OB", "POI"
        elif context.get("session_bucket") == "TOKYO" or not context.get("trading_allowed", True):
            permission, reason, layer = "WAIT", "OB_FIVE_STAR_SESSION_VETO", "TIME"
        elif not context.get("news_clear", True):
            permission, reason, layer = "REJECT", "OB_FIVE_STAR_NEWS_VETO", "NEWS"
        elif tier == "FIVE_STAR_STRICT":
            permission, reason, layer = "CANDIDATE", "OB_FIVE_STAR_WAITING_FOR_M1_CONFIRMATION", "TRIGGER"
        elif tier == "FOUR_STAR_WATCH":
            permission, reason, layer = "CANDIDATE", "OB_FOUR_STAR_WATCH", "POI"
        elif tier == "THREE_STAR_WEAK":
            permission, reason, layer = "WAIT", "OB_THREE_STAR_OBSERVATION_ONLY", "POI"
        else:
            permission, reason, layer = "REJECT", "OB_LOW_QUALITY_NOT_FIVE_STAR", "POI"
        if permission == "CANDIDATE" and trigger_kind in TRIGGER_NONE_VALUES and tier == "FIVE_STAR_STRICT":
            reason = "OB_FIVE_STAR_TRIGGER_NONE"

        score = scoring["score_pct"]
        result = strategy_result(
            strategy_id=self.strategy_id,
            strategy_family=self.strategy_family,
            is_applicable=applicable,
            permission=permission,
            score=score if applicable else 0,
            confidence=min(max(score, 0) / 100, 1.0),
            hard_veto=False,
            reason=reason,
            blocking_layer=layer,
            required_next_evidence="M1_CONFIRMATION_RETEST_DISPLACEMENT",
            context=context,
            poi={**poi, "ob_five_star": scoring},
            trigger=trigger,
            risk=risk,
        )
        result["ob_five_star"] = scoring
        return result
