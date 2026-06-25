"""Unified Kasper/ICT scenario engine for XAUUSD shadow reasoning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCENARIO_TYPES = {
    "OTE_CONFLUENCE",
    "FAILED_AUCTION",
    "VWAP_M1_SCALP",
    "TREND_CONTINUATION",
    "REVERSAL_AFTER_SWEEP",
    "SESSION_REVERSAL",
    "OBSERVATION",
}

SCENARIO_MICRO_TEMPLATES = {
    "OTE_CONFLUENCE": "continuation_light",
    "FAILED_AUCTION": "failed_auction_reclaim",
    "VWAP_M1_SCALP": "continuation_light",
    "TREND_CONTINUATION": "continuation_light",
    "REVERSAL_AFTER_SWEEP": "reversal_strict",
    "SESSION_REVERSAL": "session_reversal_medium",
    "OBSERVATION": "continuation_light",
}


@dataclass(frozen=True)
class KasperIctScenario:
    scenario_type: str
    direction: str
    status: str
    confidence: float
    hard_block: bool
    tradable: bool
    near_miss: bool
    narrative: str
    required_evidence: list[str] = field(default_factory=list)
    present_evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    invalidation_logic: dict[str, Any] = field(default_factory=dict)
    risk_model: dict[str, Any] = field(default_factory=dict)
    minimum_micro_template: str = "continuation_light"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_kasper_ict_scenarios(context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = context or {}
    hard_blocks = _hard_blocks(ctx)
    scenarios = [
        _ote_confluence(ctx, hard_blocks),
        _failed_auction(ctx, hard_blocks),
        _vwap_scalp(ctx, hard_blocks),
        _trend_continuation(ctx, hard_blocks),
        _reversal_after_sweep(ctx, hard_blocks),
    ]
    if not scenarios:
        scenarios = [_observation(ctx, hard_blocks)]
    best = _rank(scenarios)
    return {
        "best_scenario": best.to_dict(),
        "scenarios": [item.to_dict() for item in scenarios],
        "hard_blocks": hard_blocks,
        "near_miss_count": sum(1 for item in scenarios if item.near_miss),
        "tradable_count": sum(1 for item in scenarios if item.tradable),
    }


def _ote_confluence(ctx: dict[str, Any], hard_blocks: list[str]) -> KasperIctScenario:
    required = ["SESSION_ALLOWED", "NEWS_CLEAR", "HTF_DOL", "POI_STRONG", "OTE_CONFLUENCE", "MICRO_CONFIRMATION", "RISK_VALID"]
    present = _present(ctx, required)
    missing = [item for item in required if item not in present]
    return _scenario("OTE_CONFLUENCE", ctx, hard_blocks, required, present, missing, "OTE + POI confluence candidate")


def _failed_auction(ctx: dict[str, Any], hard_blocks: list[str]) -> KasperIctScenario:
    required = ["SESSION_ALLOWED", "NEWS_CLEAR", "FAILED_AUCTION_RECLAIM", "LIQUIDITY_SWEEP", "RISK_VALID"]
    present = _present(ctx, required)
    missing = [item for item in required if item not in present]
    return _scenario("FAILED_AUCTION", ctx, hard_blocks, required, present, missing, "Value-area reclaim after failed auction")


def _vwap_scalp(ctx: dict[str, Any], hard_blocks: list[str]) -> KasperIctScenario:
    required = ["SESSION_ALLOWED", "NEWS_CLEAR", "EMA200_DIRECTION", "VWAP_RECLAIM", "ATR_RISK_VALID"]
    present = _present(ctx, required)
    missing = [item for item in required if item not in present]
    return _scenario("VWAP_M1_SCALP", ctx, hard_blocks, required, present, missing, "M1 VWAP reclaim/rejection scalp")


def _trend_continuation(ctx: dict[str, Any], hard_blocks: list[str]) -> KasperIctScenario:
    required = ["SESSION_ALLOWED", "NEWS_CLEAR", "HTF_DOL", "LIQUIDITY_CONTINUATION", "POI_USABLE", "MICRO_CONFIRMATION", "RISK_VALID"]
    present = _present(ctx, required)
    missing = [item for item in required if item not in present]
    return _scenario("TREND_CONTINUATION", ctx, hard_blocks, required, present, missing, "HTF/DOL continuation candidate")


def _reversal_after_sweep(ctx: dict[str, Any], hard_blocks: list[str]) -> KasperIctScenario:
    required = ["SESSION_ALLOWED", "NEWS_CLEAR", "HTF_CONTEXT", "SWEEP_REJECTED", "POI_USABLE", "MICRO_CONFIRMATION", "RISK_VALID"]
    present = _present(ctx, required)
    missing = [item for item in required if item not in present]
    return _scenario("REVERSAL_AFTER_SWEEP", ctx, hard_blocks, required, present, missing, "Reversal only after sweep/rejection")


def _observation(ctx: dict[str, Any], hard_blocks: list[str]) -> KasperIctScenario:
    return KasperIctScenario(
        "OBSERVATION",
        _direction(ctx),
        "SCENARIO_WAIT",
        0.0,
        bool(hard_blocks),
        False,
        False,
        "No actionable scenario formed",
        [],
        [],
        [],
        hard_blocks,
        minimum_micro_template="continuation_light",
    )


def _scenario(
    scenario_type: str,
    ctx: dict[str, Any],
    hard_blocks: list[str],
    required: list[str],
    present: list[str],
    missing: list[str],
    narrative: str,
) -> KasperIctScenario:
    fatal_missing = [item for item in missing if item in {"SESSION_ALLOWED", "NEWS_CLEAR", "RISK_VALID", "ATR_RISK_VALID"}]
    near_miss = not hard_blocks and not fatal_missing and 0 < len(missing) <= 2
    tradable = not hard_blocks and not missing
    status = "SCENARIO_VALID" if tradable else "SCENARIO_NEAR_MISS" if near_miss else "SCENARIO_BLOCKED" if hard_blocks or fatal_missing else "SCENARIO_WAIT"
    confidence = round(len(present) / len(required), 3) if required else 0.0
    minimum_micro_template = SCENARIO_MICRO_TEMPLATES.get(scenario_type, "continuation_light")
    return KasperIctScenario(
        scenario_type=scenario_type,
        direction=_direction(ctx),
        status=status,
        confidence=confidence,
        hard_block=bool(hard_blocks or fatal_missing),
        tradable=tradable,
        near_miss=near_miss,
        narrative=narrative,
        required_evidence=required,
        present_evidence=present,
        missing_evidence=missing,
        warnings=list(dict.fromkeys(hard_blocks + fatal_missing)),
        invalidation_logic={"hard_blocks": hard_blocks, "fatal_missing": fatal_missing},
        risk_model=ctx.get("risk_plan", {}) if isinstance(ctx.get("risk_plan"), dict) else {},
        minimum_micro_template=minimum_micro_template,
    )


def _present(ctx: dict[str, Any], required: list[str]) -> list[str]:
    checks = {
        "SESSION_ALLOWED": _truthy(ctx.get("session_allowed")) or str(ctx.get("session_quality")) in {"HIGH", "MEDIUM"},
        "NEWS_CLEAR": ctx.get("news_clear") is True,
        "HTF_DOL": _truthy(ctx.get("htf_context_available")) and _truthy(ctx.get("dol_available")),
        "HTF_CONTEXT": _truthy(ctx.get("htf_context_available")),
        "POI_STRONG": int(ctx.get("poi_stars") or 0) >= 4,
        "POI_USABLE": _truthy(ctx.get("poi_available")) and str(ctx.get("poi_grade", "")) != "INVALID",
        "OTE_CONFLUENCE": _truthy(ctx.get("scenario_ready") or ctx.get("inside_ote")),
        "MICRO_CONFIRMATION": _truthy(ctx.get("micro_trigger") or ctx.get("micro_confirmed")),
        "RISK_VALID": _truthy(ctx.get("risk_valid")),
        "ATR_RISK_VALID": _truthy(ctx.get("risk_valid")),
        "FAILED_AUCTION_RECLAIM": _truthy(ctx.get("auction_failed")) and _truthy(ctx.get("reclaim_confirmed")),
        "LIQUIDITY_SWEEP": _truthy(ctx.get("sweep_detected") or ctx.get("sweep_valid")),
        "LIQUIDITY_CONTINUATION": str(ctx.get("liquidity_story")) in {"RUN_CONTINUATION", "BREAKOUT_ACCEPTANCE"} or _truthy(ctx.get("run_detected")),
        "EMA200_DIRECTION": str(ctx.get("ema_200_m15_bias")) in {"BULLISH", "BEARISH"},
        "VWAP_RECLAIM": _truthy(ctx.get("vwap_reclaim")),
        "SWEEP_REJECTED": _truthy(ctx.get("sweep_rejected") or ctx.get("rejection_after_sweep")),
    }
    return [item for item in required if checks.get(item)]


def _hard_blocks(ctx: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    if ctx.get("news_veto") is True or ctx.get("news_clear") is False:
        blocks.append("NEWS_HARD_VETO")
    if str(ctx.get("session")) == "OFF_SESSION":
        blocks.append("OFF_SESSION_NON_TRADABLE")
    elif ctx.get("session_allowed") is False and str(ctx.get("session")) not in {"ASIA"}:
        blocks.append("SESSION_BLOCKED")
    if str(ctx.get("session")) == "ASIA":
        blocks.append("ASIA_MAPPING_ONLY")
    if ctx.get("trigger_inside_poi") is False:
        blocks.append("TRIGGER_OUTSIDE_POI")
    if str(ctx.get("poi_grade")) == "INVALID":
        blocks.append("POI_INVALID")
    if ctx.get("risk_valid") is False:
        blocks.append("RISK_INVALID")
    return blocks


def _rank(scenarios: list[KasperIctScenario]) -> KasperIctScenario:
    return sorted(scenarios, key=lambda item: (item.tradable, item.near_miss, item.confidence), reverse=True)[0]


def _direction(ctx: dict[str, Any]) -> str:
    raw = str(ctx.get("direction") or ctx.get("bias") or "UNKNOWN").upper()
    if raw in {"BULLISH", "LONG"}:
        return "LONG"
    if raw in {"BEARISH", "SHORT"}:
        return "SHORT"
    return "UNKNOWN"


def _truthy(value: Any) -> bool:
    return str(value).upper() in {"TRUE", "1", "YES", "Y"} if isinstance(value, str) else bool(value)
