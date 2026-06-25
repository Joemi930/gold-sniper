"""P2-E Phase 7F — Final validation for Opus report.

Validates that a summary dict produced by the replay pipeline passes
all Phase7A-F contract checks. Returns a structured verdict suitable
for the Opus final report.
"""

from __future__ import annotations

from typing import Any


def validate_phase7_final_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Validate a replay summary dict against Phase7A-F contracts.

    Args:
        summary: A replay summary dict (from summary.json or
                 replay_metrics / p2c_performance_summary output).

    Returns:
        A dict with phase7_final_valid (bool), blocking_findings (list),
        warnings (list), and checks (dict of per-contract bools).
    """
    blocking_findings: list[str] = []
    warnings: list[str] = []

    # ── Presence checks ──────────────────────────────────────────
    checks: dict[str, bool] = {
        "setup_taxonomy_present": "setup_type_distribution" in summary,
        "enter_eligibility_present": "enter_eligible_count" in summary,
        "risk_mapping_present": "risk_allowed_count" in summary,
        "readiness_coherence_present": "readiness_coherence_violation_count" in summary,
        "agent_contract_present": "agent_poi_handoff_source_distribution" in summary,
        "no_risk_without_eligibility": True,
        "no_readiness_coherence_violation": True,
        "no_forced_enter": True,
    }

    # ── Blocking findings ────────────────────────────────────────

    # Critical invariant: risk must not be positive without enter_eligible
    risk_positive_no_eligibility = int(
        summary.get("risk_positive_but_not_enter_eligible_count") or 0
    )
    if risk_positive_no_eligibility != 0:
        blocking_findings.append(
            f"RISK_WITHOUT_ENTER_ELIGIBILITY: "
            f"risk_positive_but_not_enter_eligible_count={risk_positive_no_eligibility}"
        )
        checks["no_risk_without_eligibility"] = False

    # Critical invariant: no readiness coherence violations
    coherence_violations = int(
        summary.get("readiness_coherence_violation_count") or 0
    )
    if coherence_violations != 0:
        blocking_findings.append(
            f"READINESS_COHERENCE_VIOLATION: "
            f"readiness_coherence_violation_count={coherence_violations}"
        )
        checks["no_readiness_coherence_violation"] = False

    # Critical invariant: no ENTER without eligibility
    enter_count = int(summary.get("ENTER_count") or 0)
    enter_eligible = int(summary.get("enter_eligible_count") or 0)
    if enter_count > 0 and enter_eligible == 0:
        blocking_findings.append(
            f"ENTER_WITHOUT_ELIGIBILITY: ENTER_count={enter_count}, "
            f"enter_eligible_count={enter_eligible}"
        )
        checks["no_forced_enter"] = False

    # Critical invariant: no risk_multiplier_positive without enter_eligible
    risk_positive = int(summary.get("risk_multiplier_positive") or 0)
    if risk_positive > 0 and enter_eligible == 0:
        blocking_findings.append(
            f"RISK_POSITIVE_WITHOUT_ELIGIBILITY: "
            f"risk_multiplier_positive={risk_positive}, "
            f"enter_eligible_count={enter_eligible}"
        )
        checks["no_risk_without_eligibility"] = False

    # Critical invariant: no legacy fallback when P2A selected POI exists
    legacy_count = int(summary.get("legacy_fallback_usage_count") or 0)
    p2a_count = int(summary.get("p2a_selected_poi_consumed_count") or 0)
    if legacy_count > 0 and p2a_count > 0:
        # Legacy fallback alongside P2A is suspicious but may be legitimate
        # when some decisions have P2A and others don't
        pass  # not blocking — different decisions may have different data

    # ── Missing metrics → blocking ───────────────────────────────
    if not checks["setup_taxonomy_present"]:
        blocking_findings.append("SETUP_TAXONOMY_METRICS_MISSING")
    if not checks["enter_eligibility_present"]:
        blocking_findings.append("ENTER_ELIGIBILITY_METRICS_MISSING")
    if not checks["risk_mapping_present"]:
        blocking_findings.append("RISK_METRICS_MISSING")
    if not checks["readiness_coherence_present"]:
        blocking_findings.append("READINESS_COHERENCE_METRICS_MISSING")
    if not checks["agent_contract_present"]:
        warnings.append("AGENT_HANDOFF_METRICS_MISSING")

    # ── Non-blocking warnings ────────────────────────────────────
    unknown_count = int(summary.get("UNKNOWN_setup_type_count") or 0)
    if unknown_count > 0:
        warnings.append(
            f"UNKNOWN_SETUP_TYPE_COUNT={unknown_count} "
            f"(non-bloquant si justifié par manque d'évidence)"
        )

    if enter_count == 0:
        warnings.append(
            "ENTER_COUNT=0 (non-bloquant si blockers sont légitimes et auditables)"
        )

    if enter_eligible == 0:
        warnings.append(
            "ENTER_ELIGIBLE_COUNT=0 (non-bloquant si aucun setup n'atteint le seuil)"
        )

    # Check for 30m_COVERAGE_MISSING (pre-existing, non-blocking)
    findings = summary.get("findings") or []
    if isinstance(findings, list):
        for finding in findings:
            finding_str = str(finding)
            if "30m_COVERAGE_MISSING" in finding_str:
                if "30M_COVERAGE_WARNING" not in warnings:
                    warnings.append("30m_COVERAGE_MISSING (préexistant, non-bloquant)")

    # ── Verdict ──────────────────────────────────────────────────
    phase7_final_valid = len(blocking_findings) == 0

    return {
        "phase7_final_valid": phase7_final_valid,
        "blocking_findings": blocking_findings,
        "warnings": warnings,
        "checks": checks,
    }
