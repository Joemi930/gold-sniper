from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gold_sniper.strategy.contracts import EvidenceBundle, SetupType
from gold_sniper.strategy.setup_candidate_mapping import SetupCandidate, map_signals_to_setup_candidates
from gold_sniper.strategy.setup_signal_inventory import SetupSignalInventory, extract_setup_signal_inventory


# ── P2-E Phase 7A: Setup Classification ─────────────────────────

@dataclass(frozen=True)
class SetupClassification:
    """Taxonomy result produced by classify_setup()."""
    setup_type: SetupType
    confidence: float = 0.0
    reason: str = "UNCLASSIFIED"
    family: str = "UNKNOWN"
    required_ready_sections: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_type": self.setup_type.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "family": self.family,
            "required_ready_sections": list(self.required_ready_sections),
            "tags": list(self.tags),
            "evidence": dict(self.evidence),
        }


# ── P2-E Phase 7A: Setup Requirement contracts ──────────────────

@dataclass(frozen=True)
class SetupRequirement:
    setup_type: SetupType
    min_score_enter_full: float
    min_score_enter_reduced: float
    min_score_watch: float
    requires_sweep: bool = False
    requires_micro_retest: bool = False
    allows_reduced_without_retest: bool = False
    max_risk_multiplier: float = 1.0
    required_evidence: list[str] = field(default_factory=list)


SETUP_TAXONOMY: dict[SetupType, SetupRequirement] = {
    # ── Legacy types (preserved) ─────────────────────────────────
    SetupType.REVERSAL_STRICT: SetupRequirement(
        setup_type=SetupType.REVERSAL_STRICT,
        min_score_enter_full=85.0,
        min_score_enter_reduced=75.0,
        min_score_watch=60.0,
        requires_sweep=True,
        requires_micro_retest=True,
        allows_reduced_without_retest=False,
        max_risk_multiplier=1.0,
        required_evidence=["htf_context", "draw_on_liquidity", "poi", "sweep", "micro_confirmation"],
    ),
    SetupType.CONTINUATION_LIGHT: SetupRequirement(
        setup_type=SetupType.CONTINUATION_LIGHT,
        min_score_enter_full=82.0,
        min_score_enter_reduced=70.0,
        min_score_watch=55.0,
        requires_sweep=False,
        requires_micro_retest=False,
        allows_reduced_without_retest=True,
        max_risk_multiplier=0.75,
        required_evidence=["htf_context", "poi", "liquidity", "micro_confirmation"],
    ),
    SetupType.FAILED_AUCTION_RECLAIM: SetupRequirement(
        setup_type=SetupType.FAILED_AUCTION_RECLAIM,
        min_score_enter_full=85.0,
        min_score_enter_reduced=72.0,
        min_score_watch=58.0,
        requires_sweep=True,
        requires_micro_retest=False,
        allows_reduced_without_retest=True,
        max_risk_multiplier=0.75,
        required_evidence=["failed_auction", "reclaim", "poi", "liquidity"],
    ),
    SetupType.SESSION_REVERSAL_MEDIUM: SetupRequirement(
        setup_type=SetupType.SESSION_REVERSAL_MEDIUM,
        min_score_enter_full=999.0,
        min_score_enter_reduced=68.0,
        min_score_watch=55.0,
        requires_sweep=True,
        requires_micro_retest=True,
        allows_reduced_without_retest=False,
        max_risk_multiplier=0.50,
        required_evidence=["session", "sweep", "poi", "micro_confirmation"],
    ),

    # ── Phase 7A new types — ENTER gated at 999.0, risk caps opened (Phase 7C) ──
    SetupType.REVERSAL_LIGHT: SetupRequirement(
        setup_type=SetupType.REVERSAL_LIGHT,
        min_score_enter_full=999.0,
        min_score_enter_reduced=999.0,
        min_score_watch=55.0,
        requires_sweep=False,
        requires_micro_retest=False,
        allows_reduced_without_retest=False,
        max_risk_multiplier=0.25,
        required_evidence=["context", "poi"],
    ),
    SetupType.CONTINUATION_STRICT: SetupRequirement(
        setup_type=SetupType.CONTINUATION_STRICT,
        min_score_enter_full=999.0,
        min_score_enter_reduced=999.0,
        min_score_watch=55.0,
        requires_sweep=False,
        requires_micro_retest=False,
        allows_reduced_without_retest=False,
        max_risk_multiplier=0.75,
        required_evidence=["context", "poi", "liquidity", "micro", "timing"],
    ),
    SetupType.SWEEP_REVERSAL: SetupRequirement(
        setup_type=SetupType.SWEEP_REVERSAL,
        min_score_enter_full=85.0,
        min_score_enter_reduced=75.0,
        min_score_watch=55.0,
        requires_sweep=True,
        requires_micro_retest=True,
        allows_reduced_without_retest=False,
        max_risk_multiplier=0.75,
        required_evidence=["poi", "liquidity", "micro"],
    ),
    SetupType.OTE_PULLBACK: SetupRequirement(
        setup_type=SetupType.OTE_PULLBACK,
        min_score_enter_full=999.0,
        min_score_enter_reduced=999.0,
        min_score_watch=55.0,
        requires_sweep=False,
        requires_micro_retest=False,
        allows_reduced_without_retest=False,
        max_risk_multiplier=0.50,
        required_evidence=["context", "poi", "timing"],
    ),
    SetupType.POI_REACTION: SetupRequirement(
        setup_type=SetupType.POI_REACTION,
        min_score_enter_full=999.0,
        min_score_enter_reduced=999.0,
        min_score_watch=45.0,
        requires_sweep=False,
        requires_micro_retest=False,
        allows_reduced_without_retest=False,
        max_risk_multiplier=0.0,
        required_evidence=["poi"],
    ),
    SetupType.NO_SETUP: SetupRequirement(
        setup_type=SetupType.NO_SETUP,
        min_score_enter_full=999.0,
        min_score_enter_reduced=999.0,
        min_score_watch=999.0,
        max_risk_multiplier=0.0,
        required_evidence=[],
    ),
    SetupType.UNKNOWN: SetupRequirement(
        setup_type=SetupType.UNKNOWN,
        min_score_enter_full=999.0,
        min_score_enter_reduced=999.0,
        min_score_watch=55.0,
        max_risk_multiplier=0.0,
        required_evidence=[],
    ),
}


def get_setup_requirement(setup_type: SetupType | str | None) -> SetupRequirement:
    try:
        resolved = setup_type if isinstance(setup_type, SetupType) else SetupType(str(setup_type or SetupType.UNKNOWN.value))
    except ValueError:
        resolved = SetupType.UNKNOWN
    return SETUP_TAXONOMY.get(resolved, SETUP_TAXONOMY[SetupType.UNKNOWN])


def resolve_setup_type(evidence: EvidenceBundle | dict[str, Any] | None) -> SetupType:
    if isinstance(evidence, EvidenceBundle):
        return evidence.setup_type
    if not isinstance(evidence, dict):
        return SetupType.UNKNOWN
    raw = evidence.get("setup_type") or evidence.get("setup") or evidence.get("context", {}).get("setup_type")
    try:
        return SetupType(str(raw).upper())
    except Exception:
        return SetupType.UNKNOWN


# ── P2-E Phase 7A: Setup classifier ──────────────────────────────

def classify_setup(evidence: EvidenceBundle | dict[str, Any] | None) -> SetupClassification:
    """Classify an EvidenceBundle into a setup type.

    Rules:
    - Returns UNKNOWN only when core evidence is insufficient or unreadable.
    - Returns NO_SETUP when evidence is readable but no exploitable configuration exists.
    - Does NOT decide ENTER, risk_multiplier, or readiness — classifies only.
    """
    bundle = evidence if isinstance(evidence, EvidenceBundle) else EvidenceBundle.from_dict(evidence or {})
    signals = extract_setup_signal_inventory(bundle)
    candidates = map_signals_to_setup_candidates(signals)
    evidence_payload = _classification_evidence(signals, candidates)

    if signals.missing_core:
        return SetupClassification(
            setup_type=SetupType.UNKNOWN,
            confidence=0.0,
            reason="INSUFFICIENT_CORE_EVIDENCE",
            family="UNKNOWN",
            evidence=evidence_payload,
        )

    strict_candidates = [candidate for candidate in candidates if candidate.is_strict_candidate]
    light_candidates = [candidate for candidate in candidates if candidate.is_light_candidate]

    best = _best_candidate_by_priority(strict_candidates, strict=True)
    if best is not None:
        return _classification_from_candidate(best, signals, candidates, strict=True)

    best = _best_candidate_by_priority(light_candidates, strict=False)
    if best is not None:
        return _classification_from_candidate(best, signals, candidates, strict=False)

    poi_reaction = next(
        (candidate for candidate in candidates if candidate.candidate_type == SetupType.POI_REACTION),
        None,
    )
    if poi_reaction is not None:
        return _classification_from_candidate(poi_reaction, signals, candidates, strict=False)

    # ── No setup ─────────────────────────────────────────────────
    return SetupClassification(
        setup_type=SetupType.NO_SETUP,
        confidence=0.0,
        reason="NO_CLASSIFIABLE_SETUP",
        family="NONE",
        evidence=evidence_payload,
    )


def _classification_evidence(
    signals: SetupSignalInventory,
    candidates: list[SetupCandidate],
) -> dict[str, Any]:
    return {
        "signals": signals.to_dict(),
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def _classification_from_candidate(
    candidate: SetupCandidate,
    signals: SetupSignalInventory,
    candidates: list[SetupCandidate],
    *,
    strict: bool,
) -> SetupClassification:
    return SetupClassification(
        setup_type=candidate.candidate_type,
        confidence=candidate.confidence,
        reason=candidate.reason,
        family=_family_for_setup(candidate.candidate_type),
        required_ready_sections=_required_sections_for_classification(
            candidate.candidate_type,
            strict=strict,
        ),
        tags=_tags_for_candidate(candidate),
        evidence=_classification_evidence(signals, candidates),
    )


def _best_candidate_by_priority(
    candidates: list[SetupCandidate],
    *,
    strict: bool,
) -> SetupCandidate | None:
    if not candidates:
        return None
    priority = _strict_priority() if strict else _light_priority()
    return max(
        candidates,
        key=lambda candidate: (
            priority.get(candidate.candidate_type, 0),
            candidate.confidence,
        ),
    )


def _strict_priority() -> dict[SetupType, int]:
    return {
        SetupType.SWEEP_REVERSAL: 120,
        SetupType.REVERSAL_STRICT: 110,
        SetupType.CONTINUATION_STRICT: 100,
        SetupType.OTE_PULLBACK: 90,
        SetupType.FAILED_AUCTION_RECLAIM: 80,
        SetupType.SESSION_REVERSAL_MEDIUM: 70,
    }


def _light_priority() -> dict[SetupType, int]:
    return {
        SetupType.SWEEP_REVERSAL: 90,
        SetupType.CONTINUATION_LIGHT: 80,
        SetupType.REVERSAL_LIGHT: 70,
        SetupType.OTE_PULLBACK: 60,
    }


def _family_for_setup(setup_type: SetupType) -> str:
    if setup_type in {
        SetupType.REVERSAL_STRICT,
        SetupType.REVERSAL_LIGHT,
        SetupType.SWEEP_REVERSAL,
        SetupType.FAILED_AUCTION_RECLAIM,
        SetupType.SESSION_REVERSAL_MEDIUM,
    }:
        return "REVERSAL"
    if setup_type in {SetupType.CONTINUATION_STRICT, SetupType.CONTINUATION_LIGHT}:
        return "CONTINUATION"
    if setup_type == SetupType.OTE_PULLBACK:
        return "PULLBACK"
    if setup_type == SetupType.POI_REACTION:
        return "REACTION"
    if setup_type == SetupType.NO_SETUP:
        return "NONE"
    return "UNKNOWN"


def _required_sections_for_classification(
    setup_type: SetupType,
    *,
    strict: bool,
) -> tuple[str, ...]:
    if setup_type == SetupType.SWEEP_REVERSAL:
        return ("context", "poi", "liquidity", "timing", "micro")
    if strict:
        if setup_type == SetupType.OTE_PULLBACK:
            return ("context", "poi", "liquidity", "timing", "micro")
        return ("context", "poi", "liquidity", "timing", "micro")
    if setup_type == SetupType.POI_REACTION:
        return ("poi",)
    if setup_type == SetupType.OTE_PULLBACK:
        return ("context", "poi", "timing")
    return ("context", "poi")


def _tags_for_candidate(candidate: SetupCandidate) -> tuple[str, ...]:
    legacy_tags = {
        SetupType.SWEEP_REVERSAL: ("sweep", "reclaim", "micro_ready"),
        SetupType.REVERSAL_STRICT: ("counter_trend", "poi", "liquidity_ready", "micro_ready"),
        SetupType.REVERSAL_LIGHT: ("counter_trend", "poi", "micro_waiting"),
        SetupType.CONTINUATION_STRICT: ("trend_aligned", "poi", "ote", "micro_ready"),
        SetupType.CONTINUATION_LIGHT: ("trend_aligned", "poi", "waiting_confirmation"),
        SetupType.OTE_PULLBACK: ("ote", "pullback", "trend_aligned"),
        SetupType.POI_REACTION: ("poi", "reaction"),
    }.get(candidate.candidate_type, ())
    synergy_tags = ("poi_micro_synergy",) if "POI_MICRO_SYNERGY" in candidate.present else ()
    return tuple(dict.fromkeys([*candidate.present, *legacy_tags, *synergy_tags]))


# ── P2-E Phase 7A: Fact extraction ──────────────────────────────

def _extract_setup_facts(bundle: EvidenceBundle) -> dict[str, Any]:
    """Extract normalized boolean facts from EvidenceBundle sections.

    Conservative heuristics — classify, do not optimize.
    """
    context = bundle.context or {}
    poi = bundle.poi or {}
    liquidity = bundle.liquidity or {}
    micro = bundle.micro or {}
    timing = (bundle.raw or {}).get("timing") or {}
    session = bundle.session or {}

    direction = str(context.get("direction") or bundle.side or "NONE").upper()

    poi_status = str(poi.get("poi_semantic_status") or "").upper()
    micro_status = str(
        micro.get("micro_semantic_status")
        or micro.get("readiness_state")
        or micro.get("execution_readiness")
        or ""
    ).upper()
    liquidity_status = str(
        liquidity.get("liquidity_semantic_status")
        or liquidity.get("readiness_state")
        or liquidity.get("execution_readiness")
        or ""
    ).upper()
    timing_state = str(timing.get("readiness_state") or timing.get("execution_readiness") or "").upper()

    selected_poi = poi.get("selected_poi") or {}
    poi_type = str(
        poi.get("poi_type")
        or selected_poi.get("poi_type")
        or selected_poi.get("poi_type_normalized")
        or selected_poi.get("type")
        or "UNKNOWN"
    ).upper()

    premium_discount = str(context.get("premium_discount") or timing.get("premium_discount") or "UNKNOWN").upper()
    in_ote = bool(context.get("in_ote") or timing.get("in_ote"))
    price_position = str(
        context.get("price_position")
        or poi.get("price_position")
        or timing.get("price_position")
        or "UNKNOWN"
    ).upper()

    # Semantic booleans
    poi_present = poi_status.startswith("POI_PRESENT") or bool(
        poi.get("selected_poi") or poi.get("poi_available") or poi.get("price_bounds")
    )
    micro_ready = micro_status in {"READY", "MICRO_READY"} or str(micro.get("readiness_state") or "").upper() == "READY"
    micro_waiting = (
        "WAIT" in micro_status
        or str(micro.get("readiness_state") or "").upper() in {"WAIT_FOR_TRIGGER", "WAITING_TRIGGER"}
    )
    liquidity_ready = (
        liquidity_status in {"READY", "LIQUIDITY_READY"}
        or str(liquidity.get("readiness_state") or "").upper() == "READY"
    )
    liquidity_waiting = (
        "WAIT" in liquidity_status
        or str(liquidity.get("readiness_state") or "").upper() in {"WAIT_FOR_TRIGGER", "WAITING_TRIGGER"}
    )
    ote_ready = timing_state == "READY" or bool(context.get("in_ote") or timing.get("in_ote"))

    sweep = bool(
        liquidity.get("sweep_detected")
        or liquidity.get("sweep_rejected")
        or str(liquidity.get("liquidity_state") or "").upper() == "SWEEP"
    )

    reclaim = bool(
        micro.get("reclaim_confirmed")
        or micro.get("acceptance_confirmed")
        or micro.get("trigger_inside_poi")
    )

    # Conservative alignment heuristics
    trend_aligned_poi = _poi_aligns_with_direction(poi_type, direction)
    counter_trend_poi = _poi_opposes_direction(poi_type, direction)

    insufficient_core_evidence = not bool(
        direction and direction not in {"NONE", "UNKNOWN"}
    ) and not poi_present

    return {
        "direction": direction,
        "poi_status": poi_status,
        "micro_status": micro_status,
        "liquidity_status": liquidity_status,
        "timing_state": timing_state,
        "poi_type": poi_type,
        "premium_discount": premium_discount,
        "price_position": price_position,
        "poi_present": poi_present,
        "micro_ready": micro_ready,
        "micro_waiting": micro_waiting,
        "liquidity_ready": liquidity_ready,
        "liquidity_waiting": liquidity_waiting,
        "ote_ready": ote_ready,
        "in_ote": in_ote,
        "sweep": sweep,
        "price_reclaimed_poi": reclaim,
        "trend_aligned_poi": trend_aligned_poi,
        "counter_trend_poi": counter_trend_poi,
        "liquidity_sweep_ready": sweep and liquidity_ready,
        "insufficient_core_evidence": insufficient_core_evidence,
        "session_label": session.get("session_label") or session.get("session"),
    }


def _poi_aligns_with_direction(poi_type: str, direction: str) -> bool:
    """Return True if the POI type is aligned with the HTF direction."""
    poi_type = str(poi_type or "").upper()
    direction = str(direction or "").upper()
    if direction in {"BUY", "LONG", "BULLISH"}:
        return any(token in poi_type for token in ("BULL", "DEMAND", "BUY", "DISCOUNT"))
    if direction in {"SELL", "SHORT", "BEARISH"}:
        return any(token in poi_type for token in ("BEAR", "SUPPLY", "SELL", "PREMIUM"))
    return False


def _poi_opposes_direction(poi_type: str, direction: str) -> bool:
    """Return True if the POI type opposes the HTF direction (counter-trend)."""
    poi_type = str(poi_type or "").upper()
    direction = str(direction or "").upper()
    if direction in {"BUY", "LONG", "BULLISH"}:
        return any(token in poi_type for token in ("BEAR", "SUPPLY", "SELL", "PREMIUM"))
    if direction in {"SELL", "SHORT", "BEARISH"}:
        return any(token in poi_type for token in ("BULL", "DEMAND", "BUY", "DISCOUNT"))
    return False
