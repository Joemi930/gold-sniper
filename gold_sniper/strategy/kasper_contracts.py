"""P1 Kasper Brain Core — normalized agent contracts and scenario result types.

These contracts translate existing AgentResult payloads into structured,
immutable dataclasses consumed by KasperScenarioEngine.  They do NOT replace
the existing AgentResult / EvidenceBundle; they sit alongside as the
"Kasper lens" layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


# ── shared literal types ──────────────────────────────────────────────
Bias = Literal["bullish", "bearish", "neutral"]
Side = Literal["BUY", "SELL", "NONE"]
PassFail = Literal["PASS", "FAIL", "WAIT", "UNKNOWN"]
DecisionRecommendation = Literal["ENTER_ELIGIBLE", "WAIT", "REJECT"]
Grade = Literal["A_PLUS", "A", "B", "C", "D"]


# ── Agent 1 – HTF context / meteo ─────────────────────────────────────
@dataclass(frozen=True)
class Agent1Context:
    agent: str = "agent_1_meteo"
    htf_bias: Bias = "neutral"
    structure_state: str = "unclear"
    last_htf_bos: bool = False
    last_htf_choch: bool = False
    draw_on_liquidity: str = "unknown"
    confidence: float = 0.0
    invalid_reason: Optional[str] = None
    primary_regime: str = "UNKNOWN"  # V2: RANGE / WEAK_UP / WEAK_DOWN / STRONG_UP / STRONG_DOWN


# ── Agent 2 – POI / cartographe ───────────────────────────────────────
@dataclass(frozen=True)
class SelectedPOI:
    type: str = "none"
    low: Optional[float] = None
    high: Optional[float] = None
    midpoint: Optional[float] = None
    freshness: str = "unknown"
    created_by_displacement: bool = False
    created_by_bos_or_choch: bool = False
    near_psych_level: bool = False
    psych_level: Optional[float] = None
    htf_confluence: bool = False
    tradable: bool = False
    mitigation_depth: Optional[float] = None


@dataclass(frozen=True)
class Agent2POIContext:
    agent: str = "agent_2_cartographe"
    selected_poi: SelectedPOI = field(default_factory=SelectedPOI)
    poi_quality: float = 0.0
    invalid_reason: Optional[str] = None


# ── Agent 3 – liquidity / sweep ───────────────────────────────────────
@dataclass(frozen=True)
class LiquidityEvent:
    type: str = "none"
    swept_level: Optional[float] = None
    sweep_time: Optional[str] = None
    close_back_inside: bool = False
    wick_rejection: bool = False
    reclaim_strength: float = 0.0
    displacement_after_sweep: bool = False
    target_after_sweep: str = "unknown"


@dataclass(frozen=True)
class Agent3LiquidityContext:
    agent: str = "agent_3_liquidite"
    liquidity_event: LiquidityEvent = field(default_factory=LiquidityEvent)
    liquidity_quality: float = 0.0
    invalid_reason: Optional[str] = None


# ── Agent 4 – timing / OTE / Fibonacci ────────────────────────────────
@dataclass(frozen=True)
class Agent4TimingContext:
    agent: str = "agent_4_fibonacci"
    in_discount_for_buy: bool = False
    in_premium_for_sell: bool = False
    ote_zone_touched: bool = False
    sweet_spot_touched: bool = False
    pullback_quality: str = "unknown"
    timing_quality: float = 0.0
    invalid_reason: Optional[str] = None


# ── Agent 5 – micro confirmation / trigger ────────────────────────────
@dataclass(frozen=True)
class MicroConfirmation:
    trigger_type: str = "none"
    confirmed: bool = False
    close_breaks_structure: bool = False
    wick_rejection_on_poi: bool = False
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target_liquidity: Optional[float] = None
    rr_estimate: Optional[float] = None


@dataclass(frozen=True)
class Agent5TriggerContext:
    agent: str = "agent_5_microscope"
    micro_confirmation: MicroConfirmation = field(default_factory=MicroConfirmation)
    received_context: Dict[str, Any] = field(default_factory=dict)
    handoff_status: str = "UNKNOWN"
    invalid_reason: Optional[str] = None


# ── Agent 6 – news / sentinel ─────────────────────────────────────────
@dataclass(frozen=True)
class Agent6NewsContext:
    agent: str = "agent_6_sentinelle"
    high_impact_active: bool = False
    minutes_to_next_high_impact: Optional[int] = None
    minutes_since_high_impact: Optional[int] = None
    currency: str = "USD"
    event_name: Optional[str] = None
    news_safe: bool = True
    veto: bool = False
    invalid_reason: Optional[str] = None


# ── Agent 7 – session / chronos ───────────────────────────────────────
@dataclass(frozen=True)
class Agent7SessionContext:
    agent: str = "agent_7_chronos"
    session: str = "unknown"
    killzone_active: bool = False
    asia_block: bool = False
    friday_halt: bool = False
    spread_safe: bool = True
    daily_trade_count: int = 0
    cooldown_active: bool = False
    session_quality: float = 0.0
    veto: bool = False
    invalid_reason: Optional[str] = None


# ── Kasper evidence bundle ────────────────────────────────────────────
@dataclass(frozen=True)
class KasperEvidenceBundle:
    agent1: Agent1Context = field(default_factory=Agent1Context)
    agent2: Agent2POIContext = field(default_factory=Agent2POIContext)
    agent3: Agent3LiquidityContext = field(default_factory=Agent3LiquidityContext)
    agent4: Agent4TimingContext = field(default_factory=Agent4TimingContext)
    agent5: Agent5TriggerContext = field(default_factory=Agent5TriggerContext)
    agent6: Agent6NewsContext = field(default_factory=Agent6NewsContext)
    agent7: Agent7SessionContext = field(default_factory=Agent7SessionContext)
    symbol: str = "XAUUSD"
    timestamp: Optional[str] = None


# ── Kasper scenario identity ───────────────────────────────────────────
@dataclass(frozen=True)
class KasperScenarioIdentity:
    """Unique identity for a single trading opportunity.

    scenario_key: stable across candles for the same real opportunity
                  (symbol + family + side + sweep_type + swept_level +
                   sweep_time + poi_type + poi_bounds + trigger_type)
    decision_id: unique per candle/decision (scenario_key + candle_ts + action)
    scenario_id: backward-compatible hash for legacy consumers
    """
    scenario_key: str = ""
    decision_id: str = ""
    scenario_id: str = ""
    identity_components: Dict[str, str] = field(default_factory=dict)


# ── Kasper scenario result ────────────────────────────────────────────
@dataclass(frozen=True)
class KasperScenarioResult:
    scenario_id: str = ""
    scenario_key: str = ""
    decision_id: str = ""
    side: str = "NONE"
    scenario_type: str = "unknown"
    story: str = ""
    sequence: Dict[str, str] = field(default_factory=dict)
    grade: Grade = "D"
    score: float = 0.0
    decision_recommendation: DecisionRecommendation = "REJECT"
    blocking_reason: Optional[str] = None
    missing_confluence: Optional[str] = None
    entry_reason: Optional[str] = None
    invalidation_reason: Optional[str] = None
    target_reason: Optional[str] = None
    kasper_error: Optional[str] = None


# ── Adapters: Existing EvidenceBundle → KasperEvidenceBundle ──────────

def _safe_str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "pass")
    return default


def _safe_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _payload(ctx: dict | None, key: str, default: Any = None) -> Any:
    if ctx is None:
        return default
    return ctx.get(key, default)


def from_existing_agent1_context(ctx: dict | None) -> Agent1Context:
    """Build Agent1Context from the existing EvidenceBundle.context dict."""
    if ctx is None:
        return Agent1Context()
    bias_map = {"BUY": "bullish", "SELL": "bearish", "LONG": "bullish", "SHORT": "bearish"}
    raw_dir = _safe_str(ctx.get("direction") or ctx.get("htf_direction"), "")
    htf_bias: Bias = bias_map.get(raw_dir.upper(), "neutral")  # type: ignore[assignment]
    # Derive structure_state from direction if not explicitly set
    structure_state = _safe_str(ctx.get("structure_state"), "")
    if not structure_state or structure_state == "unclear":
        if htf_bias == "bullish":
            structure_state = "BULLISH"
        elif htf_bias == "bearish":
            structure_state = "BEARISH"
        else:
            structure_state = "unclear"
    return Agent1Context(
        htf_bias=htf_bias,
        structure_state=structure_state,
        last_htf_bos=_safe_bool(ctx.get("last_htf_bos") or ctx.get("bos_freshness")),
        last_htf_choch=_safe_bool(ctx.get("last_htf_choch") or (ctx.get("last_event") == "CHoCH")),
        draw_on_liquidity=_safe_str(ctx.get("draw_on_liquidity"), "unknown"),
        confidence=_safe_float(ctx.get("confidence") or ctx.get("score")),
        invalid_reason=ctx.get("invalid_reason"),
        primary_regime=_safe_str(ctx.get("primary_regime"), "UNKNOWN").upper(),
    )


def from_existing_agent2_context(poi: dict | None) -> Agent2POIContext:
    """Build Agent2POIContext from the existing EvidenceBundle.poi dict.

    Handles both the contract-style selected_poi sub-dict (with normalized field names)
    and the legacy flat format.
    """
    if poi is None:
        return Agent2POIContext()
    selected = poi.get("selected_poi") or {}
    if isinstance(selected, dict):
        # price_bounds from contract format
        price_bounds = selected.get("price_bounds") if isinstance(selected.get("price_bounds"), dict) else {}
        spo = SelectedPOI(
            type=_safe_str(
                selected.get("type")
                or selected.get("zone_type")
                or selected.get("poi_type_normalized")
                or poi.get("poi_type")
                or poi.get("selected_poi_type"),
                "none",
            ),
            low=_safe_optional_float(
                selected.get("low")
                or selected.get("price_low")
                or price_bounds.get("low")
            ),
            high=_safe_optional_float(
                selected.get("high")
                or selected.get("price_high")
                or price_bounds.get("high")
            ),
            midpoint=_safe_optional_float(
                selected.get("midpoint")
                or selected.get("price_mid")
                or price_bounds.get("mid")
            ),
            freshness=_safe_str(
                selected.get("freshness")
                or selected.get("lifecycle_state")
                or selected.get("lifecycle_normalized")
                or poi.get("lifecycle_state"),
                "unknown",
            ),
            created_by_displacement=_safe_bool(selected.get("created_by_displacement")),
            created_by_bos_or_choch=_safe_bool(selected.get("created_by_bos_or_choch")),
            near_psych_level=_safe_bool(selected.get("near_psych_level")),
            psych_level=_safe_optional_float(selected.get("psych_level")),
            htf_confluence=_safe_bool(
                selected.get("htf_confluence")
                or selected.get("aligned_with_context")
            ),
            tradable=_safe_bool(
                selected.get("tradable")
                or poi.get("poi_available")
                or selected.get("execution_readiness") == "READY"
                # Honor the system-wide resolved POI status. Every other
                # consumer (decision_pipeline, readiness, signal inventory)
                # uses effective_poi_status; Kasper was the only gate reading
                # the raw status, so micro-synergy-revalidated POIs
                # (RECOVERABLE_REJECTED → READY_FOR_TRIGGER) were treated as
                # untradable here and rejected. This is a consistency fix, not
                # a threshold change.
                or str(
                    poi.get("effective_poi_status")
                    or (poi.get("poi_micro_synergy") or {}).get("effective_poi_status")
                    or ""
                ).upper()
                in {
                    "READY",
                    "READY_FOR_TRIGGER",
                    "EXECUTABLE",
                    "SYNERGY_READY",
                    "SYNERGY_READY_FOR_TRIGGER",
                },
                default=False,
            ),
            mitigation_depth=_safe_optional_float(
                selected.get("mitigation_depth")
                or selected.get("mitigation_pct")
                or poi.get("mitigation_depth")
                or poi.get("mitigation_pct")
            ),
        )
    else:
        spo = SelectedPOI()
    # Quality comes from multiple possible sources
    quality = _safe_float(
        poi.get("poi_quality")
        or poi.get("quality_score")
        or poi.get("poi_quality_score")
        or (selected.get("score") if isinstance(selected, dict) else 0.0)
    )
    return Agent2POIContext(
        selected_poi=spo,
        poi_quality=quality,
        invalid_reason=poi.get("invalid_reason") or poi.get("readiness_reason"),
    )


def from_existing_agent3_context(liquidity: dict | None, micro: dict | None = None, extra_sweep_evidence: bool = False) -> Agent3LiquidityContext:
    """Build Agent3LiquidityContext from the existing EvidenceBundle.liquidity dict.

    The EvidenceBundle stores liquidity data in flat fields (sweep_detected, sweep_side,
    sweep_rejected, etc.), NOT in a nested liquidity_event sub-dict. This adapter
    handles both formats.

    Args:
        liquidity: The EvidenceBundle.liquidity section dict.
        micro: The EvidenceBundle.micro section dict (for displacement_after_sweep fallback).
        extra_sweep_evidence: If True, micro_sweep_confirmed or setup_sweep_evidence
            can upgrade sweep_detected from False to True (Phase16 reconciliation).
    """
    if liquidity is None:
        return Agent3LiquidityContext()
    le = liquidity.get("liquidity_event") or {}
    micro = micro or {}

    # ── sweep detection ──────────────────────────────────────────
    # Primary: sweep_detected flat field
    # Secondary: explicit sweep_type/sweep_side != UNKNOWN/none
    # Tertiary: nested liquidity_event.type
    # Extra: micro_sweep_confirmed or setup_sweep_evidence (Phase16 reconciliation)
    sweep_detected = _safe_bool(liquidity.get("sweep_detected"))
    sweep_type = _safe_str(
        liquidity.get("sweep_type")
        or liquidity.get("sweep_side")
        or liquidity.get("liquidity_event_type")
    )
    # ── P2.3: normalize Agent 3 format (SWEEP_BSL/SSL) → Kasper format ──
    # Agent 3 produces "SWEEP_BSL" (BuySide Liquidity sweep) and "SWEEP_SSL"
    # (SellSide Liquidity sweep). The KasperScenarioEngine expects
    # "buyside_sweep" and "sellside_sweep".
    #
    # CRITICAL: Agent 3 uses the same sweep_side format for both SWEEP and BREAK
    # events. BREAK events have sweep_detected=False but sweep_side="SWEEP_BSL".
    # Only rename the type for events where sweep_detected is already True
    # (confirmed by Agent 3's event=="SWEEP" check in build_agent_3_observation).
    #
    # The liquidity_state field stores the raw event type (SWEEP vs BREAK vs NONE).
    # If liquidity_state contains "BREAK", this is NOT a sweep.
    _sweep_type_upper = sweep_type.upper().strip()
    _liq_state = _safe_str(liquidity.get("liquidity_state"), "").upper()
    _is_break = "BREAK" in _liq_state
    if sweep_detected and not _is_break:
        if _sweep_type_upper in ("SWEEP_BSL", "BSL"):
            sweep_type = "buyside_sweep"
        elif _sweep_type_upper in ("SWEEP_SSL", "SSL"):
            sweep_type = "sellside_sweep"
    # If sweep_type is explicitly set to a known sweep, treat as detected
    if not sweep_detected and sweep_type.lower() in ("sellside_sweep", "buyside_sweep", "sellside", "buyside", "sweep"):
        sweep_detected = True
    if not sweep_detected and extra_sweep_evidence:
        # Phase16: reconciled sweep evidence from micro/setup level
        if _safe_bool(liquidity.get("micro_sweep_confirmed")):
            sweep_detected = True
            if not sweep_type or sweep_type.lower() in ("unknown", "", "none"):
                raw_side = _safe_str(liquidity.get("sweep_side"))
                if raw_side.lower() in ("unknown", "", "none"):
                    sweep_type = "sellside_sweep"  # default reversal sweep
                else:
                    sweep_type = raw_side
        elif _safe_bool(liquidity.get("liquidity_reconciled")):
            sweep_detected = True
            if not sweep_type or sweep_type.lower() in ("unknown", "", "none"):
                raw_side = _safe_str(liquidity.get("sweep_side"))
                if raw_side.lower() in ("unknown", "", "none"):
                    sweep_type = "sellside_sweep"  # default reversal sweep
                else:
                    sweep_type = raw_side

    # ── displacement ─────────────────────────────────────────────
    # Primary: displacement_after_sweep in liquidity (P2.3: now set by
    #   evidence builder for Agent3-confirmed sweeps with depth > 0)
    # Secondary: displacement_present in micro section (Agent 5)
    # P2.3: reconciled sweeps (micro_sweep_confirmed / liquidity_reconciled)
    #   imply displacement — the reconciliation confirms a sweep at the
    #   micro level, which inherently involves price displacement.
    displacement = _safe_bool(
        liquidity.get("displacement_after_sweep")
        or micro.get("displacement_present")
        or liquidity.get("micro_sweep_confirmed")
        or liquidity.get("liquidity_reconciled")
    )

    # ── reintegration ────────────────────────────────────────────
    # Primary: sweep_rejected (price came back after sweep)
    # Fallback: retest_confirmed, reclaim_confirmed, trigger_inside_poi in micro
    close_back = _safe_bool(
        liquidity.get("sweep_rejected")
        or micro.get("retest_confirmed")
        or micro.get("reclaim_confirmed")
        or micro.get("trigger_inside_poi")
    )

    # ── build LiquidityEvent ─────────────────────────────────────
    if isinstance(le, dict) and le:
        levt = LiquidityEvent(
            type=sweep_type if sweep_detected else _safe_str(le.get("type"), "none"),
            swept_level=_safe_optional_float(le.get("swept_level")),
            sweep_time=_safe_str(le.get("sweep_time")),
            close_back_inside=close_back or _safe_bool(le.get("close_back_inside")),
            wick_rejection=_safe_bool(le.get("wick_rejection") or liquidity.get("sweep_wick_rejection")),
            reclaim_strength=_safe_float(le.get("reclaim_strength")),
            displacement_after_sweep=displacement or _safe_bool(le.get("displacement_after_sweep")),
            target_after_sweep=_safe_str(le.get("target_after_sweep") or liquidity.get("target_after_sweep"), "unknown"),
        )
    else:
        levt = LiquidityEvent(
            type=(sweep_type if sweep_detected else "none"),
            close_back_inside=close_back,
            wick_rejection=_safe_bool(liquidity.get("sweep_wick_rejection")),
            displacement_after_sweep=displacement,
        )
    return Agent3LiquidityContext(
        liquidity_event=levt,
        liquidity_quality=_safe_float(liquidity.get("liquidity_quality") or liquidity.get("liquidity_state_score")),
        invalid_reason=liquidity.get("invalid_reason") or liquidity.get("readiness_reason"),
    )


def from_existing_agent4_context(timing: dict | None) -> Agent4TimingContext:
    """Build Agent4TimingContext from the existing EvidenceBundle timing dict."""
    if timing is None:
        return Agent4TimingContext()
    pd_val = _safe_str(timing.get("premium_discount") or timing.get("ote_zone"), "")
    return Agent4TimingContext(
        in_discount_for_buy=_safe_bool(timing.get("in_discount") or (pd_val.upper() == "DISCOUNT")),
        in_premium_for_sell=_safe_bool(timing.get("in_premium") or (pd_val.upper() == "PREMIUM")),
        ote_zone_touched=_safe_bool(timing.get("ote_reached") or timing.get("ote_zone_touched")),
        sweet_spot_touched=_safe_bool(timing.get("sweet_spot_touched") or timing.get("in_sweet_spot")),
        pullback_quality=_safe_str(timing.get("pullback_quality"), "unknown"),
        timing_quality=_safe_float(timing.get("timing_quality_score") or timing.get("timing_quality")),
        invalid_reason=timing.get("invalid_reason"),
    )


def from_existing_agent5_context(micro: dict | None) -> Agent5TriggerContext:
    """Build Agent5TriggerContext from the existing EvidenceBundle.micro dict.

    The EvidenceBundle.micro section uses flat fields: trigger_type, choch_detected,
    displacement_present, micro_is_confirmed, retest_confirmed, rr_estimate, etc.
    """
    if micro is None:
        return Agent5TriggerContext()
    mc = micro.get("micro_confirmation") or {}

    # ── trigger type ─────────────────────────────────────────────
    trigger_type = _safe_str(
        (mc.get("trigger_type") if isinstance(mc, dict) else "")
        or micro.get("trigger_type"),
        "none",
    )

    # ── confirmed ────────────────────────────────────────────────
    confirmed = (
        _safe_bool(micro.get("micro_is_confirmed"))
        or _safe_bool(micro.get("trigger_confirmed"))
        or _safe_bool(micro.get("choch_confirmed"))
    )
    if not confirmed and isinstance(mc, dict):
        confirmed = _safe_bool(mc.get("confirmed"))

    # ── close_breaks_structure ───────────────────────────────────
    close_breaks = (
        _safe_bool(micro.get("choch_detected"))
        or _safe_bool(micro.get("displacement_present"))
    )
    if not close_breaks and isinstance(mc, dict):
        close_breaks = _safe_bool(mc.get("close_breaks_structure"))

    # ── wick_rejection_on_poi ────────────────────────────────────
    wick_rejection = (
        _safe_bool(micro.get("retest_confirmed"))
        or _safe_bool(micro.get("trigger_inside_poi"))
    )
    if not wick_rejection and isinstance(mc, dict):
        wick_rejection = _safe_bool(mc.get("wick_rejection_on_poi"))

    # ── RR estimate ──────────────────────────────────────────────
    rr = _safe_optional_float(micro.get("rr_estimate"))
    if rr is None and isinstance(mc, dict):
        rr = _safe_optional_float(mc.get("rr_estimate"))

    mconf = MicroConfirmation(
        trigger_type=trigger_type,
        confirmed=confirmed,
        close_breaks_structure=close_breaks,
        wick_rejection_on_poi=wick_rejection,
        entry_price=_safe_optional_float(
            (mc.get("entry_price") if isinstance(mc, dict) else None)
            or micro.get("entry_price")
            or micro.get("entry_price_candidate")
        ),
        stop_loss=_safe_optional_float(
            (mc.get("stop_loss") if isinstance(mc, dict) else None)
            or micro.get("stop_loss")
            or micro.get("stop_loss_candidate")
        ),
        target_liquidity=_safe_optional_float(
            (mc.get("target_liquidity") if isinstance(mc, dict) else None)
            or micro.get("target_liquidity")
        ),
        rr_estimate=rr if rr is not None else _safe_optional_float(micro.get("rr_estimate")),
    )
    return Agent5TriggerContext(
        micro_confirmation=mconf,
        received_context=micro.get("received_context") or {},
        handoff_status=_safe_str(
            micro.get("handoff_status")
            or micro.get("execution_readiness")
            or micro.get("readiness_state"),
            "UNKNOWN",
        ),
        invalid_reason=micro.get("invalid_reason") or micro.get("readiness_reason"),
    )


def from_existing_agent6_context(news: dict | None) -> Agent6NewsContext:
    """Build Agent6NewsContext from the existing EvidenceBundle.news dict."""
    if news is None:
        return Agent6NewsContext()
    return Agent6NewsContext(
        high_impact_active=_safe_bool(news.get("high_impact_window") or news.get("news_blocked")),
        minutes_to_next_high_impact=_safe_optional_float(news.get("minutes_to_next_high_impact")),
        minutes_since_high_impact=_safe_optional_float(news.get("minutes_since_high_impact")),
        currency=_safe_str(news.get("currency"), "USD"),
        event_name=news.get("event_name"),
        news_safe=_safe_bool(news.get("news_clear") or news.get("news_safe"), default=True),
        veto=_safe_bool(news.get("veto") or news.get("hard_veto") or (not news.get("news_clear", True))),
        invalid_reason=news.get("invalid_reason"),
    )


def from_existing_agent7_context(session: dict | None) -> Agent7SessionContext:
    """Build Agent7SessionContext from the existing EvidenceBundle.session dict."""
    if session is None:
        return Agent7SessionContext()
    return Agent7SessionContext(
        session=_safe_str(session.get("session_label") or session.get("session"), "unknown"),
        killzone_active=_safe_bool(session.get("killzone_active")),
        asia_block=_safe_bool(session.get("asia_block") or session.get("is_hard_block")),
        friday_halt=_safe_bool(session.get("friday_halt")),
        spread_safe=_safe_bool(session.get("spread_safe"), default=True),
        daily_trade_count=int(_safe_float(session.get("daily_trade_count"), 0)),
        cooldown_active=_safe_bool(session.get("cooldown_active")),
        session_quality=_safe_float(session.get("session_score") or session.get("session_quality")),
        veto=_safe_bool(session.get("veto") or session.get("is_hard_block")),
        invalid_reason=session.get("invalid_reason"),
    )


def build_kasper_evidence_bundle(
    context: dict | None = None,
    poi: dict | None = None,
    liquidity: dict | None = None,
    timing: dict | None = None,
    micro: dict | None = None,
    news: dict | None = None,
    session: dict | None = None,
    symbol: str = "XAUUSD",
    timestamp: Optional[str] = None,
    *,
    extra_sweep_evidence: bool = False,
) -> KasperEvidenceBundle:
    """Build a KasperEvidenceBundle from the existing EvidenceBundle section dicts.

    Args:
        extra_sweep_evidence: If True, micro_sweep_confirmed and liquidity_reconciled
            flags in the liquidity section can upgrade sweep_detected from False to True
            (Phase16 reconciliation).
    """
    return KasperEvidenceBundle(
        agent1=from_existing_agent1_context(context),
        agent2=from_existing_agent2_context(poi),
        agent3=from_existing_agent3_context(liquidity, micro=micro, extra_sweep_evidence=extra_sweep_evidence),
        agent4=from_existing_agent4_context(timing),
        agent5=from_existing_agent5_context(micro),
        agent6=from_existing_agent6_context(news),
        agent7=from_existing_agent7_context(session),
        symbol=symbol,
        timestamp=timestamp,
    )
