from __future__ import annotations

from typing import Any

from gold_sniper.strategy.contracts import DecisionAction, EvidenceBundle, RiskPlan, SetupGrade
from gold_sniper.strategy.setup_taxonomy import get_setup_requirement


# ── P2-E Phase 7C: Grade → Risk multiplier mapping ─────────────────

BASE_RISK_PCT = {
    SetupGrade.A_PLUS: 1.00,
    SetupGrade.A: 0.75,
    SetupGrade.B: 0.50,
    SetupGrade.C: 0.25,
    SetupGrade.D: 0.00,
}

GRADE_RISK_MULTIPLIER = BASE_RISK_PCT  # alias for discoverability


def grade_risk_multiplier(grade: SetupGrade | str | None) -> float:
    """Return the risk multiplier for a given grade.

    Contract:
    - A_PLUS → 1.00
    - A      → 0.75
    - B      → 0.50
    - C      → 0.25
    - D      → 0.00
    - unknown → 0.00
    """
    try:
        resolved = grade if isinstance(grade, SetupGrade) else SetupGrade(str(grade or SetupGrade.D.value))
    except Exception:
        resolved = SetupGrade.D
    return float(BASE_RISK_PCT.get(resolved, 0.0))


# ── Public API ─────────────────────────────────────────────────────

def allocate_risk(
    *,
    action: DecisionAction | str,
    grade: SetupGrade | str,
    evidence: EvidenceBundle | dict[str, Any] | None = None,
    capital: float = 100.0,
    enter_eligible: bool | None = None,
) -> RiskPlan:
    """Allocate risk for a decision.

    Args:
        action: Decision action (ENTER_FULL, ENTER_REDUCED, etc.)
        grade: Setup grade (A_PLUS, A, B, C, D)
        evidence: EvidenceBundle or dict
        capital: Account capital for risk amount calculation
        enter_eligible: If False, risk is disallowed regardless of grade.
            If None, legacy compatibility mode (no eligibility gate).
    """
    bundle = evidence if isinstance(evidence, EvidenceBundle) else EvidenceBundle.from_dict(evidence or {})
    resolved_action = action if isinstance(action, DecisionAction) else DecisionAction(str(action))
    resolved_grade = grade if isinstance(grade, SetupGrade) else SetupGrade(str(grade))

    # ── Phase 7C: Enter eligibility guard ─────────────────────────
    if enter_eligible is False:
        return RiskPlan(
            capital=capital,
            allowed=False,
            reason="ENTER_NOT_ELIGIBLE",
            metadata={
                "enter_eligible": False,
                "action": resolved_action.value,
                "grade": resolved_grade.value,
                "setup_type": bundle.setup_type.value,
            },
        )

    if resolved_action not in {DecisionAction.ENTER_FULL, DecisionAction.ENTER_REDUCED}:
        return RiskPlan(capital=capital, allowed=False, reason="NO_EXECUTABLE_SHADOW_ENTRY")

    if _risk_guard_hit(bundle):
        return RiskPlan(capital=capital, allowed=False, reason="RISK_GUARD_HIT")

    base_pct = BASE_RISK_PCT.get(resolved_grade, 0.0)
    if resolved_action == DecisionAction.ENTER_REDUCED:
        base_pct = min(base_pct, 0.50)

    multiplier = _combined_multiplier(bundle)
    requirement = get_setup_requirement(bundle.setup_type)
    risk_pct = round(max(0.0, min(base_pct * multiplier, requirement.max_risk_multiplier, 1.00)), 4)
    amount = round(float(capital) * risk_pct / 100.0, 4)

    return RiskPlan(
        capital=float(capital),
        risk_pct=risk_pct,
        risk_amount=amount,
        risk_multiplier=round(multiplier, 4),
        allowed=risk_pct > 0.0,
        reason="SHADOW_RISK_ALLOCATED" if risk_pct > 0.0 else "ZERO_RISK_AFTER_MULTIPLIERS",
        metadata={
            "action": resolved_action.value,
            "grade": resolved_grade.value,
            "base_risk_pct": base_pct,
            "grade_risk_multiplier": base_pct,
            "combined_modulator": multiplier,
            "setup_type": bundle.setup_type.value,
            "setup_max_risk_multiplier": requirement.max_risk_multiplier,
            "effective_risk_pct": risk_pct,
            "enter_eligible": enter_eligible,
        },
    )


def _risk_guard_hit(bundle: EvidenceBundle) -> bool:
    risk = bundle.risk
    return bool(
        risk.get("max_daily_loss_hit")
        or risk.get("max_weekly_loss_hit")
        or risk.get("max_drawdown_hit")
        or risk.get("kill_switch")
    )


def _combined_multiplier(bundle: EvidenceBundle) -> float:
    values = [
        bundle.session.get("risk_multiplier", 1.0),
        bundle.context.get("risk_multiplier_hint", 1.0),
        bundle.risk.get("atr_risk_multiplier", 1.0),
    ]
    result = 1.0
    for value in values:
        result *= _bounded_multiplier(value)
    return max(0.0, min(result, 1.0))


def _bounded_multiplier(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 1.0
    return max(0.0, min(number, 1.0))
