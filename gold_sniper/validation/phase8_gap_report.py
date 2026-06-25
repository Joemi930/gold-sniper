"""Phase8B final smoke gap report helpers."""

from __future__ import annotations

from typing import Any


BASELINE_RUN_ID = "P2E_PHASE7F_FINAL_SMOKE_2026_05_27_2026_06_05"
CURRENT_RUN_ID = "P2E_PHASE8B_FINAL_SMOKE_2026_05_27_2026_06_05"
PHASE = "P2E_PHASE8B_FINAL_SMOKE"


def build_phase8_gap_report(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    near_miss: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare Phase7F baseline metrics with the final Phase8 smoke metrics."""

    baseline_metrics = _metrics_root(baseline)
    current_metrics = _metrics_root(current)
    baseline_setup_types = _dict(baseline_metrics.get("setup_type_distribution"))
    current_setup_types = _dict(current_metrics.get("setup_type_distribution"))

    baseline_decisions = _decision_counts(baseline_metrics)
    current_decisions = _decision_counts(current_metrics)
    baseline_enter = _enter_count(baseline_metrics)
    current_enter = _enter_count(current_metrics)
    baseline_filled = _int(baseline_metrics.get("filled_trades"))
    current_filled = _int(current_metrics.get("filled_trades"))

    delta = {
        "UNKNOWN": _int(current_setup_types.get("UNKNOWN")) - _int(baseline_setup_types.get("UNKNOWN")),
        "POI_REACTION": _int(current_setup_types.get("POI_REACTION")) - _int(baseline_setup_types.get("POI_REACTION")),
        "SWEEP_REVERSAL": _int(current_setup_types.get("SWEEP_REVERSAL")) - _int(baseline_setup_types.get("SWEEP_REVERSAL")),
        "enter_eligible_count": _int(current_metrics.get("enter_eligible_count")) - _int(baseline_metrics.get("enter_eligible_count")),
        "risk_multiplier_positive": _int(current_metrics.get("risk_multiplier_positive")) - _int(baseline_metrics.get("risk_multiplier_positive")),
        "ENTER": current_enter - baseline_enter,
        "filled_trades": current_filled - baseline_filled,
    }

    technical_status = _technical_status(current_metrics)
    business_status = _business_status(current_metrics, current_enter, current_filled)
    p2f_authorized = bool(
        current_filled > 0
        and current_metrics.get("profit_factor") is not None
        and technical_status == "PASS"
    )

    report = {
        "phase": PHASE,
        "baseline_run_id": str(baseline.get("run_id") or baseline_metrics.get("run_id") or BASELINE_RUN_ID),
        "current_run_id": str(current.get("run_id") or current_metrics.get("run_id") or CURRENT_RUN_ID),
        "technical_status": technical_status,
        "business_status": business_status,
        "delta": delta,
        "improvements": _improvements(delta, current_metrics, near_miss),
        "residual_blockers": _residual_blockers(current_metrics),
        "next_priorities": _next_priorities(current_metrics),
        "p2f_authorized": p2f_authorized,
    }
    return report


def _metrics_root(summary: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    for key in ("p2c_performance_summary", "performance_summary", "p1_replay"):
        value = summary.get(key)
        if isinstance(value, dict):
            return value
    return summary


def _decision_counts(metrics: dict[str, Any]) -> dict[str, int]:
    direct = metrics.get("decision_counts") or metrics.get("decision_distribution")
    if isinstance(direct, dict):
        return {str(key): _int(value) for key, value in direct.items()}
    return {
        "ENTER": _int(metrics.get("ENTER_count")),
        "WATCH_ONLY": _int(metrics.get("WATCH_ONLY_count")),
        "WAIT_FOR_TRIGGER": _int(metrics.get("WAIT_FOR_TRIGGER_count")),
        "REJECT": _int(metrics.get("REJECT_count")),
    }


def _enter_count(metrics: dict[str, Any]) -> int:
    counts = _decision_counts(metrics)
    return (
        _int(counts.get("ENTER"))
        + _int(counts.get("ENTER_FULL"))
        + _int(counts.get("ENTER_REDUCED"))
        + _int(metrics.get("ENTER_count"))
    )


def _technical_status(metrics: dict[str, Any]) -> str:
    violations = (
        _int(metrics.get("risk_positive_but_not_enter_eligible_count"))
        + _int(metrics.get("readiness_coherence_violation_count"))
        + _int(metrics.get("legacy_fallback_usage_count"))
    )
    if violations:
        return "FAIL"
    return "PASS"


def _business_status(metrics: dict[str, Any], enter_count: int, filled_trades: int) -> str:
    if _int(metrics.get("enter_eligible_count")) > 0 or enter_count > 0 or filled_trades > 0:
        return "VALIDATED"
    return "NOT_VALIDATED"


def _improvements(
    delta: dict[str, int],
    current_metrics: dict[str, Any],
    near_miss: dict[str, Any] | None,
) -> list[str]:
    items: list[str] = []
    if delta["SWEEP_REVERSAL"] > 0:
        items.append(f"{delta['SWEEP_REVERSAL']} decisions are now classified as SWEEP_REVERSAL.")
    if _int(current_metrics.get("near_miss_scanned_count")) > 0:
        items.append("Near-misses are counted with explicit scanned, candidate, and selected counts.")
    if near_miss and isinstance(near_miss.get("top_by_setup_type"), dict):
        items.append("Near-miss examples are grouped by current setup type.")
    if _dict(current_metrics.get("enter_eligibility_blockers_by_setup_type")).get("SWEEP_REVERSAL"):
        items.append("SWEEP_REVERSAL blockers are auditable by readiness, eligibility, and risk reason.")
    return items


def _residual_blockers(metrics: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    enter_eligible = _int(metrics.get("enter_eligible_count"))
    if enter_eligible == 0:
        blockers.append("enter_eligible_count remains 0.")

    setup_types = _dict(metrics.get("setup_type_distribution"))
    sweep_count = _int(setup_types.get("SWEEP_REVERSAL"))
    if sweep_count:
        blockers.append(f"{sweep_count}/{sweep_count} SWEEP_REVERSAL remain non eligible.")

    by_setup = _dict(metrics.get("enter_eligibility_blockers_by_setup_type"))
    sweep_blockers = _dict(by_setup.get("SWEEP_REVERSAL"))
    for reason, count in sorted(sweep_blockers.items(), key=lambda item: (-_int(item[1]), str(item[0]))):
        blockers.append(f"{_int(count)}/{sweep_count or _int(count)} SWEEP_REVERSAL have {reason}.")

    readiness_by_setup = _dict(metrics.get("readiness_reason_by_setup_type"))
    sweep_readiness = _dict(readiness_by_setup.get("SWEEP_REVERSAL"))
    for reason, count in sorted(sweep_readiness.items(), key=lambda item: (-_int(item[1]), str(item[0])))[:3]:
        blockers.append(f"{_int(count)}/{sweep_count or _int(count)} SWEEP_REVERSAL readiness reason: {reason}.")
    return blockers


def _next_priorities(metrics: dict[str, Any]) -> list[str]:
    priorities = []
    sweep_blockers = _dict(_dict(metrics.get("enter_eligibility_blockers_by_setup_type")).get("SWEEP_REVERSAL"))
    if _int(sweep_blockers.get("SECTION_NOT_READY:poi")) > 0:
        priorities.append("Audit why SECTION_NOT_READY:poi blocks SWEEP_REVERSAL.")
        priorities.append("Audit the difference between POI_PRESENT and POI_READY.")

    readiness_by_setup = _dict(metrics.get("readiness_reason_by_setup_type"))
    sweep_readiness = _dict(readiness_by_setup.get("SWEEP_REVERSAL"))
    if any("POI" in str(reason) for reason in sweep_readiness):
        priorities.append("Audit POI medium/context interesting proofs.")

    setup_types = _dict(metrics.get("setup_type_distribution"))
    if _int(setup_types.get("SWEEP_REVERSAL")) > 0:
        priorities.append("Audit micro/liquidity readiness for SWEEP_REVERSAL grade B/C.")
        priorities.append("Do not touch thresholds before these causes are isolated.")
    return priorities


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
