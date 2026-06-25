"""P2-E Phase 7B — Enter Eligibility gate.

Answers: "Is this decision eligible to enter, yes or no?"
Does NOT: modify risk, force ENTER, change thresholds, or tune strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gold_sniper.strategy.contracts import (
    DecisionAction,
    EvidenceBundle,
    ReadinessState,
    SetupGrade,
    SetupType,
)
from gold_sniper.strategy.risk_allocator import allocate_risk


GRADE_RANK: dict[SetupGrade, int] = {
    SetupGrade.D: 0,
    SetupGrade.C: 1,
    SetupGrade.B: 2,
    SetupGrade.A: 3,
    SetupGrade.A_PLUS: 4,
}

STRICT_REQUIRED_SECTIONS_FOR_ENTER: tuple[str, ...] = (
    "context", "poi", "liquidity", "timing", "micro", "session", "risk",
)
LIGHT_REQUIRED_SECTIONS_FOR_ENTER: tuple[str, ...] = (
    "context", "poi", "timing", "session", "risk",
)

REQUIRED_SECTIONS_FOR_ENTER: tuple[str, ...] = STRICT_REQUIRED_SECTIONS_FOR_ENTER

LIGHT_SETUP_TYPES: set[SetupType] = {
    SetupType.CONTINUATION_LIGHT,
    SetupType.REVERSAL_LIGHT,
    SetupType.OTE_PULLBACK,
    SetupType.SESSION_REVERSAL_MEDIUM,
}

STRICT_SETUP_TYPES: set[SetupType] = {
    SetupType.REVERSAL_STRICT,
    SetupType.CONTINUATION_STRICT,
    SetupType.SWEEP_REVERSAL,
    SetupType.FAILED_AUCTION_RECLAIM,
}

POI_MICRO_SYNERGY_REQUIRED_TYPES: set[SetupType] = {
    SetupType.REVERSAL_STRICT,
    SetupType.CONTINUATION_STRICT,
    SetupType.SWEEP_REVERSAL,
    SetupType.OTE_PULLBACK,
}


@dataclass(frozen=True)
class EnterEligibilityResult:
    """Contractual result of enter eligibility evaluation."""
    enter_eligible: bool
    reason: str
    blockers: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    risk_preview: dict[str, Any] = field(default_factory=dict)
    suggested_action_when_blocked: str = "WATCH_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enter_eligible": self.enter_eligible,
            "reason": self.reason,
            "blockers": list(self.blockers),
            "checks": dict(self.checks),
            "risk_preview": dict(self.risk_preview),
            "suggested_action_when_blocked": self.suggested_action_when_blocked,
        }


# ── Public API ───────────────────────────────────────────────────

def evaluate_enter_eligibility(
    *,
    bundle: EvidenceBundle,
    scorecard: Any,
    readiness: Any,
    veto: Any,
) -> EnterEligibilityResult:
    """Evaluate whether a decision is eligible to enter a trade.

    Contract:
    1. setup_type is known and exploitable (not UNKNOWN, not NO_SETUP)
    2. setup_grade is C or better
    3. readiness global is READY
    4. required sections are READY (context, poi, liquidity, timing, micro)
    5. no hard veto (session, news, risk, replay_invalid)
    6. risk preview allows positive risk
    """
    blockers: list[str] = []

    setup_type = _resolve_setup_type(bundle.setup_type)
    grade = _resolve_grade(scorecard.grade)
    readiness_state = getattr(readiness, "state", None)
    readiness_value = getattr(readiness_state, "value", str(readiness_state) if readiness_state else "UNAVAILABLE")

    # ── Rule 1: Setup type ──────────────────────────────────────
    setup_ok = setup_type not in {SetupType.UNKNOWN, SetupType.NO_SETUP}
    if not setup_ok:
        blockers.append("SETUP_TYPE_NOT_ELIGIBLE")
    poi_reaction_synergy = setup_type == SetupType.POI_REACTION and _bundle_has_poi_micro_synergy(bundle)
    if poi_reaction_synergy:
        blockers.append("SETUP_TYPE_POI_REACTION_NOT_TRADABLE")

    # ── Rule 2: Grade ───────────────────────────────────────────
    grade_ok = _grade_is_c_or_better(grade)
    if not grade_ok:
        blockers.append("GRADE_BELOW_C")

    # ── Rule 3: Global readiness ─────────────────────────────────
    readiness_ok = (
        readiness_state == ReadinessState.READY
        or readiness_value == ReadinessState.READY.value
    )
    if not readiness_ok:
        blockers.append("GLOBAL_READINESS_NOT_READY")

    # ── Rule 4: Required sections ────────────────────────────────
    section_states = getattr(readiness, "section_states", {}) or {}
    required_sections = _required_sections_for_bundle(bundle, setup_type)
    section_results = {
        section: _section_is_ready(section_states, section, bundle)
        for section in required_sections
    }
    sections_ok = all(section_results.values())
    for section, ready in section_results.items():
        if not ready:
            blockers.append(f"SECTION_NOT_READY:{section}")

    minimum_light_trigger_ok = True
    if setup_type in LIGHT_SETUP_TYPES:
        minimum_light_trigger_ok = _light_setup_has_minimum_trigger(bundle, section_states)
        if not minimum_light_trigger_ok:
            blockers.append("LIGHT_SETUP_MIN_TRIGGER_MISSING")

    poi_micro_synergy_required = setup_type in POI_MICRO_SYNERGY_REQUIRED_TYPES
    poi_micro_synergy_ok = True
    if poi_micro_synergy_required:
        poi_micro_synergy_ok = _bundle_has_poi_micro_synergy(bundle)
        if not poi_micro_synergy_ok:
            blockers.append("POI_MICRO_SYNERGY_MISSING")

    # ── Rule 4b: News safety (READY or WATCH_ONLY, never missing/reject) ─
    news_state = _resolve_section_state(section_states, "news")
    news_ok = news_state in {ReadinessState.READY.value, ReadinessState.WATCH_ONLY.value}
    if not news_ok:
        blockers.append("NEWS_NOT_SAFE_FOR_ENTER")

    # ── Rule 5: No hard veto ────────────────────────────────────
    veto_ok = not bool(getattr(veto, "hard_veto", False)) and not bool(getattr(veto, "replay_invalid", False))
    if not veto_ok:
        blockers.append("HARD_VETO_OR_REPLAY_INVALID")

    # ── Rule 6: Risk preview positive ────────────────────────────
    risk_preview = _preview_risk(bundle, grade)
    risk_ok = (
        bool(risk_preview.get("allowed"))
        and float(risk_preview.get("risk_pct") or 0.0) > 0.0
        and float(risk_preview.get("risk_multiplier") or 0.0) > 0.0
    )
    if not risk_ok:
        blockers.append("RISK_NOT_ALLOWED")

    # ── Final ────────────────────────────────────────────────────
    enter_eligible = not bool(blockers)

    return EnterEligibilityResult(
        enter_eligible=enter_eligible,
        reason="ENTER_ELIGIBLE" if enter_eligible else _primary_reason(blockers),
        blockers=blockers,
        checks={
            "setup_type": setup_type.value,
            "setup_ok": setup_ok,
            "setup_tradable": setup_type != SetupType.POI_REACTION and setup_ok,
            "poi_reaction_synergy": poi_reaction_synergy,
            "grade": grade.value,
            "grade_ok": grade_ok,
            "readiness_state": readiness_value,
            "readiness_ok": readiness_ok,
            "veto_ok": veto_ok,
            "news_state": news_state,
            "news_ok": news_ok,
            "required_sections": list(required_sections),
            "section_results": section_results,
            "sections_ok": sections_ok,
            "minimum_light_trigger_ok": minimum_light_trigger_ok,
            "poi_micro_synergy_required": poi_micro_synergy_required,
            "poi_micro_synergy_ok": poi_micro_synergy_ok,
            "risk_ok": risk_ok,
        },
        risk_preview=risk_preview,
        suggested_action_when_blocked=_suggested_action(blockers, setup_type),
    )


def required_sections_for_setup(setup_type: SetupType | str | None) -> tuple[str, ...]:
    resolved = _resolve_setup_type(setup_type)
    if resolved in LIGHT_SETUP_TYPES:
        return LIGHT_REQUIRED_SECTIONS_FOR_ENTER
    if resolved in STRICT_SETUP_TYPES:
        return STRICT_REQUIRED_SECTIONS_FOR_ENTER
    return STRICT_REQUIRED_SECTIONS_FOR_ENTER


def _required_sections_for_bundle(
    bundle: EvidenceBundle,
    setup_type: SetupType | str | None,
) -> tuple[str, ...]:
    classification = (bundle.raw or {}).get("setup_classification")
    if isinstance(classification, dict):
        raw_sections = classification.get("required_ready_sections")
        if isinstance(raw_sections, (list, tuple)):
            sections = tuple(
                str(section)
                for section in raw_sections
                if str(section) in {
                    "context",
                    "poi",
                    "liquidity",
                    "timing",
                    "micro",
                    "session",
                    "risk",
                }
            )
            if sections:
                return sections
    return required_sections_for_setup(setup_type)


# ── Helpers: resolution ─────────────────────────────────────────

def _resolve_setup_type(value: Any) -> SetupType:
    if isinstance(value, SetupType):
        return value
    try:
        return SetupType(str(value).upper())
    except Exception:
        return SetupType.UNKNOWN


def _resolve_grade(value: Any) -> SetupGrade:
    if isinstance(value, SetupGrade):
        return value
    try:
        return SetupGrade(str(value).upper())
    except Exception:
        return SetupGrade.D


def _grade_is_c_or_better(grade: SetupGrade) -> bool:
    return GRADE_RANK.get(grade, 0) >= GRADE_RANK[SetupGrade.C]


# ── Helpers: section readiness ──────────────────────────────────

def _section_is_ready(section_states: dict[str, Any], section: str, bundle: EvidenceBundle | None = None) -> bool:
    candidates = _section_aliases(section)

    for key in candidates:
        value = section_states.get(key)
        if value is None:
            continue

        if isinstance(value, dict):
            state = value.get("state") or value.get("readiness_state") or value.get("value")
        else:
            state = getattr(value, "value", value)

        if str(state).upper() == ReadinessState.READY.value:
            return True

    # ── Bundle-level fallback for sections not in readiness.section_states ──
    if bundle is not None and section == "timing":
        timing = (bundle.raw or {}).get("timing") or {}
        timing_state = str(timing.get("readiness_state") or timing.get("execution_readiness") or "").upper()
        if timing_state == "READY":
            return True
        # Only fall back to in_ote when timing provides no explicit state
        if not timing_state:
            if bundle.context.get("in_ote") is True:
                return True

    return False


def _section_aliases(section: str) -> tuple[str, ...]:
    mapping = {
        "context": ("context", "htf_context"),
        "poi": ("poi", "price", "price_or_poi"),
        "liquidity": ("liquidity", "agent3"),
        "timing": ("timing", "ote", "agent4"),
        "micro": ("micro", "agent5"),
        "session": ("session", "agent7"),
        "risk": ("risk", "risk_management"),
    }
    return mapping.get(section, (section,))


def _resolve_section_state(section_states: dict[str, Any], section: str) -> str:
    """Resolve a section's state string from section_states dict."""
    candidates = _section_aliases(section)
    for key in candidates:
        value = section_states.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            state = value.get("state") or value.get("readiness_state") or value.get("value")
        else:
            state = getattr(value, "value", value)
        return str(state or "").upper()
    return "UNAVAILABLE"


def _light_setup_has_minimum_trigger(bundle: EvidenceBundle, section_states: dict[str, Any]) -> bool:
    del section_states
    micro = bundle.micro or {}
    liquidity = bundle.liquidity or {}
    raw = bundle.raw or {}
    timing = raw.get("timing") or {}
    context = bundle.context or {}
    micro_state = str(
        micro.get("micro_semantic_status")
        or micro.get("readiness_state")
        or micro.get("execution_readiness")
        or ""
    ).upper()
    has_partial_micro = bool(
        micro.get("reclaim_confirmed")
        or micro.get("trigger_inside_poi")
        or micro.get("displacement_present")
        or micro.get("retest_confirmed")
        or "WAIT" in micro_state
    )
    has_sweep = bool(
        liquidity.get("sweep_detected")
        or liquidity.get("sweep_rejected")
        or liquidity.get("sweep")
    )
    has_ote = bool(context.get("in_ote") or timing.get("in_ote"))
    return has_partial_micro or has_sweep or has_ote


def _bundle_has_poi_micro_synergy(bundle: EvidenceBundle) -> bool:
    poi = bundle.poi or {}
    raw = bundle.raw or {}
    synergy = poi.get("poi_micro_synergy") if isinstance(poi.get("poi_micro_synergy"), dict) else {}
    if not synergy and isinstance(raw.get("poi_micro_synergy"), dict):
        synergy = raw.get("poi_micro_synergy") or {}
    if isinstance(synergy, dict) and synergy.get("synergy") is True:
        return True
    return bool(poi.get("poi_micro_synergy_enabled"))


# ── Helpers: risk preview ───────────────────────────────────────

def _preview_risk(bundle: EvidenceBundle, grade: SetupGrade) -> dict[str, Any]:
    """Preview risk allocator using hypothetical ENTER actions.

    Does NOT execute, open orders, or modify risk rules.
    """
    previews = []

    for action in (DecisionAction.ENTER_FULL, DecisionAction.ENTER_REDUCED):
        try:
            plan = allocate_risk(action=action, grade=grade, evidence=bundle, capital=100.0, enter_eligible=True)
            previews.append({
                "action": action.value,
                "allowed": bool(plan.allowed),
                "risk_pct": float(getattr(plan, "risk_pct", 0.0) or 0.0),
                "risk_multiplier": float(getattr(plan, "risk_multiplier", 0.0) or 0.0),
                "risk_amount": float(getattr(plan, "risk_amount", 0.0) or 0.0),
                "reason": getattr(plan, "reason", "UNKNOWN"),
                "metadata": getattr(plan, "metadata", {}) or {},
            })
        except Exception as exc:
            previews.append({
                "action": action.value,
                "allowed": False,
                "risk_pct": 0.0,
                "risk_multiplier": 0.0,
                "risk_amount": 0.0,
                "reason": f"RISK_PREVIEW_ERROR:{exc.__class__.__name__}",
                "metadata": {},
            })

    best = max(previews, key=lambda item: (item["risk_pct"], item["risk_multiplier"]), default=None)
    if not best:
        return {
            "allowed": False,
            "risk_pct": 0.0,
            "risk_multiplier": 0.0,
            "risk_amount": 0.0,
            "reason": "NO_RISK_PREVIEW",
            "previews": previews,
        }

    return {**best, "previews": previews}


# ── Helpers: reason / suggested action ──────────────────────────

def _primary_reason(blockers: list[str]) -> str:
    if not blockers:
        return "ENTER_ELIGIBLE"
    return blockers[0]


def _suggested_action(blockers: list[str], setup_type: SetupType | str | None = None) -> str:
    blocker_set = set(blockers)
    resolved_setup_type = _resolve_setup_type(setup_type)

    if "HARD_VETO_OR_REPLAY_INVALID" in blocker_set:
        return "REJECT"

    if (
        "SETUP_TYPE_NOT_ELIGIBLE" in blocker_set
        or "SETUP_TYPE_POI_REACTION_NOT_TRADABLE" in blocker_set
        or "GRADE_BELOW_C" in blocker_set
    ):
        return "WATCH_ONLY"

    if "LIGHT_SETUP_MIN_TRIGGER_MISSING" in blocker_set:
        return "WAIT_FOR_TRIGGER"

    if any(b.startswith("SECTION_NOT_READY:poi") or b.startswith("SECTION_NOT_READY:timing") for b in blocker_set):
        return "WAIT_FOR_BETTER_PRICE"

    if any(b.startswith("SECTION_NOT_READY:liquidity") or b.startswith("SECTION_NOT_READY:micro") for b in blocker_set):
        return "WAIT_FOR_TRIGGER"

    if resolved_setup_type in LIGHT_SETUP_TYPES and "GLOBAL_READINESS_NOT_READY" in blocker_set:
        return "WAIT_FOR_TRIGGER"

    if "GLOBAL_READINESS_NOT_READY" in blocker_set:
        return "WATCH_ONLY"

    if "RISK_NOT_ALLOWED" in blocker_set:
        return "WATCH_ONLY"

    return "WATCH_ONLY"
