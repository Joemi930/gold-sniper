"""P2-C offline performance aggregation helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any

from strategy.readiness_risk_gate_contract import evaluate_readiness_risk_gate


GRADES = ("A_PLUS", "A", "B", "C", "D")


def build_p2c_performance_summary(
    *,
    decisions: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    windows_provided = windows is not None
    decisions = list(decisions or [])
    events = list(events or [])
    windows = list(windows or [])

    if windows_provided:
        return _summary_from_windows(windows)
    return _summary_from_decisions_and_events(decisions, events)


def _summary_from_windows(windows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for item in windows if str(item.get("status") or "").upper() == "PASS")
    partial = sum(1 for item in windows if str(item.get("status") or "").upper() == "PARTIAL_DATA_COVERAGE")
    failed = sum(1 for item in windows if str(item.get("status") or "").upper() not in {"PASS", "PARTIAL_DATA_COVERAGE"})
    totals = [item.get("performance_summary") or item.get("p2c_performance_summary") or {} for item in windows]

    aggregate: dict[str, Any] = {
        "total_windows": len(windows),
        "windows_passed": passed,
        "windows_failed": failed,
        "windows_partial_data": partial,
        "total_decisions": sum(int(item.get("total_decisions") or 0) for item in totals),
        "total_trades": sum(int(item.get("total_trades") or 0) for item in totals),
        "filled_trades": sum(int(item.get("filled_trades") or 0) for item in totals),
        "missed_entries": sum(int(item.get("missed_entries") or 0) for item in totals),
        "skipped_signals": sum(int(item.get("skipped_signals") or 0) for item in totals),
        "wins": sum(int(item.get("wins") or 0) for item in totals),
        "losses": sum(int(item.get("losses") or 0) for item in totals),
        "breakeven": sum(int(item.get("breakeven") or 0) for item in totals),
    }
    r_values: list[float] = []
    for item in totals:
        r_values.extend(_float_list(item.get("r_values") or []))
    aggregate.update(_performance_ratios(aggregate, r_values))
    aggregate["trade_frequency_per_day"] = _aggregate_trade_frequency(windows, aggregate["filled_trades"])
    aggregate["grade_distribution"] = _merge_dicts(item.get("grade_distribution") for item in totals)
    aggregate["trades_by_grade"] = _merge_dicts(item.get("trades_by_grade") for item in totals)
    aggregate["performance_by_grade"] = _merge_performance_by_grade(totals)
    aggregate["decision_distribution"] = _merge_dicts(item.get("decision_distribution") for item in totals)
    aggregate["readiness_distribution"] = _merge_dicts(item.get("readiness_distribution") for item in totals)
    aggregate["setup_candidate_type_distribution"] = _merge_dicts(item.get("setup_candidate_type_distribution") for item in totals)
    aggregate["best_setup_candidate_distribution"] = _merge_dicts(item.get("best_setup_candidate_distribution") for item in totals)
    aggregate["near_miss_scanned_count"] = sum(int(item.get("near_miss_scanned_count") or 0) for item in totals)
    aggregate["near_miss_with_candidates_count"] = sum(int(item.get("near_miss_with_candidates_count") or 0) for item in totals)
    aggregate["near_miss_selected_top_count"] = sum(int(item.get("near_miss_selected_top_count") or 0) for item in totals)
    aggregate["near_miss_by_current_setup_type"] = _merge_dicts(item.get("near_miss_by_current_setup_type") for item in totals)
    aggregate["near_miss_candidate_type_distribution"] = _merge_dicts(item.get("near_miss_candidate_type_distribution") for item in totals)
    aggregate["near_miss_candidate_count"] = sum(int(item.get("near_miss_candidate_count") or 0) for item in totals)
    aggregate["near_miss_by_setup_type"] = _merge_dicts(item.get("near_miss_by_setup_type") for item in totals)
    aggregate["near_miss_missing_signal_distribution"] = _merge_dicts(item.get("near_miss_missing_signal_distribution") for item in totals)
    aggregate["light_setup_count"] = sum(int(item.get("light_setup_count") or 0) for item in totals)
    aggregate["strict_setup_count"] = sum(int(item.get("strict_setup_count") or 0) for item in totals)
    aggregate["light_enter_eligible_count"] = sum(int(item.get("light_enter_eligible_count") or 0) for item in totals)
    aggregate["strict_enter_eligible_count"] = sum(int(item.get("strict_enter_eligible_count") or 0) for item in totals)
    aggregate["enter_eligible_by_setup_type"] = _merge_dicts(item.get("enter_eligible_by_setup_type") for item in totals)
    aggregate["risk_multiplier_positive"] = sum(int(item.get("risk_multiplier_positive") or 0) for item in totals)
    aggregate["risk_positive_by_setup_type"] = _merge_dicts(item.get("risk_positive_by_setup_type") for item in totals)
    aggregate["enter_eligibility_blockers_by_setup_type"] = _merge_nested_dicts(item.get("enter_eligibility_blockers_by_setup_type") for item in totals)
    aggregate["readiness_reason_by_setup_type"] = _merge_nested_dicts(item.get("readiness_reason_by_setup_type") for item in totals)
    aggregate["risk_reason_by_setup_type"] = _merge_nested_dicts(item.get("risk_reason_by_setup_type") for item in totals)
    aggregate["decision_counts_by_setup_type"] = _merge_nested_dicts(item.get("decision_counts_by_setup_type") for item in totals)
    aggregate["grade_distribution_by_setup_type"] = _merge_nested_dicts(item.get("grade_distribution_by_setup_type") for item in totals)
    aggregate["poi_contract_status_distribution"] = _merge_dicts(item.get("poi_contract_status_distribution") for item in totals)
    aggregate["poi_contract_contradiction_count"] = sum(int(item.get("poi_contract_contradiction_count") or 0) for item in totals)
    aggregate["poi_contract_contradiction_distribution"] = _merge_dicts(item.get("poi_contract_contradiction_distribution") for item in totals)
    aggregate["poi_quality_score_source_distribution"] = _merge_dicts(item.get("poi_quality_score_source_distribution") for item in totals)
    aggregate["poi_suspect_count"] = sum(int(item.get("poi_suspect_count") or 0) for item in totals)
    aggregate["poi_suspect_by_reason"] = _merge_dicts(item.get("poi_suspect_by_reason") for item in totals)
    aggregate["sweep_reversal_by_poi_contract_status"] = _merge_dicts(item.get("sweep_reversal_by_poi_contract_status") for item in totals)
    # P2-E Phase 11: micro contract / evidence metrics
    aggregate["micro_contract_status_distribution"] = _merge_dicts(item.get("micro_contract_status_distribution") for item in totals)
    aggregate["micro_contract_reason_distribution"] = _merge_dicts(item.get("micro_contract_reason_distribution") for item in totals)
    aggregate["micro_contract_missing_field_distribution"] = _merge_dicts(item.get("micro_contract_missing_field_distribution") for item in totals)
    aggregate["micro_contract_present_field_distribution"] = _merge_dicts(item.get("micro_contract_present_field_distribution") for item in totals)
    aggregate["micro_contradiction_distribution"] = _merge_dicts(item.get("micro_contradiction_distribution") for item in totals)
    aggregate["micro_missing_data_count"] = sum(int(item.get("micro_missing_data_count") or 0) for item in totals)
    aggregate["near_miss_micro_count"] = sum(int(item.get("near_miss_micro_count") or 0) for item in totals)
    aggregate["near_miss_micro_count_by_setup"] = _merge_dicts(item.get("near_miss_micro_count_by_setup") for item in totals)
    aggregate["enter_eligibility_blockers_by_micro_contract_status"] = _merge_nested_dicts(item.get("enter_eligibility_blockers_by_micro_contract_status") for item in totals)
    # P2-E Phase 12: POI-Micro synergy metrics
    aggregate["poi_micro_synergy_count"] = sum(int(item.get("poi_micro_synergy_count") or 0) for item in totals)
    aggregate["poi_micro_synergy_by_setup"] = _merge_dicts(item.get("poi_micro_synergy_by_setup") for item in totals)
    aggregate["poi_micro_synergy_by_grade"] = _merge_dicts(item.get("poi_micro_synergy_by_grade") for item in totals)
    aggregate["poi_micro_synergy_by_decision"] = _merge_dicts(item.get("poi_micro_synergy_by_decision") for item in totals)
    aggregate["poi_micro_synergy_status_distribution"] = _merge_dicts(item.get("poi_micro_synergy_status_distribution") for item in totals)
    aggregate["poi_micro_reason_distribution"] = _merge_dicts(item.get("poi_micro_reason_distribution") for item in totals)
    aggregate["micro_confirmed_inside_poi_count"] = sum(int(item.get("micro_confirmed_inside_poi_count") or 0) for item in totals)
    aggregate["micro_confirmed_outside_poi_count"] = sum(int(item.get("micro_confirmed_outside_poi_count") or 0) for item in totals)
    aggregate["micro_confirmed_without_poi_count"] = sum(int(item.get("micro_confirmed_without_poi_count") or 0) for item in totals)
    aggregate["confirmed_micro_sweep_reversal_count"] = sum(int(item.get("confirmed_micro_sweep_reversal_count") or 0) for item in totals)
    aggregate["confirmed_micro_poi_reaction_count"] = sum(int(item.get("confirmed_micro_poi_reaction_count") or 0) for item in totals)
    aggregate["confirmed_micro_continuation_count"] = sum(int(item.get("confirmed_micro_continuation_count") or 0) for item in totals)
    aggregate["enter_eligible_by_setup"] = _merge_dicts(item.get("enter_eligible_by_setup") for item in totals)
    aggregate["risk_positive_by_setup"] = _merge_dicts(item.get("risk_positive_by_setup") for item in totals)
    aggregate["remaining_blockers_after_synergy_distribution"] = _merge_dicts(item.get("remaining_blockers_after_synergy_distribution") for item in totals)
    # P2-E Phase 13: POI rejection decomposition
    aggregate["poi_rejection_code_distribution"] = _merge_dicts(item.get("poi_rejection_code_distribution") for item in totals)
    aggregate["poi_rejection_severity_distribution"] = _merge_dicts(item.get("poi_rejection_severity_distribution") for item in totals)
    aggregate["poi_rejection_recoverable_count"] = sum(int(item.get("poi_rejection_recoverable_count") or 0) for item in totals)
    aggregate["poi_rejection_fatal_count"] = sum(int(item.get("poi_rejection_fatal_count") or 0) for item in totals)
    aggregate["micro_confirmed_rejected_poi_count"] = sum(int(item.get("micro_confirmed_rejected_poi_count") or 0) for item in totals)
    aggregate["micro_confirmed_recoverable_poi_count"] = sum(int(item.get("micro_confirmed_recoverable_poi_count") or 0) for item in totals)
    aggregate["sweep_rejected_count"] = sum(int(item.get("sweep_rejected_count") or 0) for item in totals)
    aggregate["poi_micro_synergy_by_rejection_code"] = _merge_nested_dicts(item.get("poi_micro_synergy_by_rejection_code") for item in totals)
    # P2-E Phase 14: readiness/risk gate decomposition
    aggregate["gate_primary_blocker_distribution"] = _merge_dicts(item.get("gate_primary_blocker_distribution") for item in totals)
    aggregate["gate_blocker_distribution"] = _merge_dicts(item.get("gate_blocker_distribution") for item in totals)
    aggregate["synergy_true_gate_primary_blocker_distribution"] = _merge_dicts(item.get("synergy_true_gate_primary_blocker_distribution") for item in totals)
    aggregate["synergy_true_by_setup_type"] = _merge_dicts(item.get("synergy_true_by_setup_type") for item in totals)
    aggregate["synergy_true_enter_eligible_by_setup_type"] = _merge_dicts(item.get("synergy_true_enter_eligible_by_setup_type") for item in totals)
    aggregate["risk_reason_by_gate_primary_blocker"] = _merge_nested_dicts(item.get("risk_reason_by_gate_primary_blocker") for item in totals)
    aggregate["blocked_reason_distribution"] = _merge_dicts(item.get("blocked_reason_distribution") for item in totals)
    aggregate["missed_entry_distribution"] = _merge_dicts(item.get("missed_entry_distribution") for item in totals)
    aggregate["diagnostic"] = _diagnostic(aggregate)
    aggregate["r_values"] = r_values
    return aggregate


def _summary_from_decisions_and_events(decisions: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    open_events = [event for event in events if str(event.get("event") or "") in {"open", "tier_trade_open"}]
    close_events = [event for event in events if str(event.get("event") or "") in {"close", "tier_trade_close"}]
    missed_events = [event for event in events if str(event.get("event") or "") in {"missed_entry", "tier_missed_entry"}]
    rejected_events = [event for event in events if str(event.get("event") or "") in {"rejected", "tier_trade_rejected"}]
    r_values = [_safe_float(event.get("r_multiple")) for event in close_events]
    r_values = [value for value in r_values if value is not None]
    wins = sum(1 for event in close_events if _safe_float(event.get("pnl")) is not None and float(event.get("pnl")) > 0)
    losses = sum(1 for event in close_events if _safe_float(event.get("pnl")) is not None and float(event.get("pnl")) < 0)
    breakeven = sum(1 for event in close_events if _safe_float(event.get("pnl")) == 0)
    summary: dict[str, Any] = {
        "total_windows": 1,
        "windows_passed": 1,
        "windows_failed": 0,
        "windows_partial_data": 0,
        "total_decisions": len(decisions),
        "total_trades": len(open_events) + len(missed_events),
        "filled_trades": len(open_events),
        "missed_entries": len(missed_events),
        "skipped_signals": len(rejected_events),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "grade_distribution": dict(Counter(_normalize_grade(item.get("setup_grade")) for item in decisions).most_common()),
        "trades_by_grade": _events_by_grade(open_events),
        "performance_by_grade": _performance_by_grade(decisions, events),
        "decision_distribution": dict(Counter(str(item.get("decision") or "UNKNOWN") for item in decisions).most_common()),
        "readiness_distribution": dict(Counter(str(item.get("readiness_state") or "UNKNOWN") for item in decisions).most_common()),
        # P2-E Phase 7A: setup taxonomy
        "setup_type_distribution": dict(Counter(str(item.get("setup_type") or "UNKNOWN") for item in decisions).most_common()),
        "setup_family_distribution": dict(Counter(str(item.get("setup_family") or "UNKNOWN") for item in decisions).most_common()),
        # P2-E Phase 7B: enter eligibility
        "enter_eligible_count": sum(1 for item in decisions if item.get("enter_eligible")),
        "enter_eligible_by_setup_type": dict(Counter(
            str(item.get("setup_type") or "UNKNOWN")
            for item in decisions
            if item.get("enter_eligible")
        ).most_common()),
        "enter_eligibility_reason_distribution": dict(Counter(
            str(item.get("enter_eligibility_reason") or "UNKNOWN") for item in decisions
        ).most_common(25)),
        "enter_eligibility_blocker_distribution": dict(
            Counter(blocker for item in decisions for blocker in (item.get("enter_eligibility_blockers") or [])).most_common(25)
        ),
        # P2-E Phase 7C: risk multiplier mapping
        "risk_allowed_count": sum(1 for item in decisions if item.get("risk_allowed")),
        "risk_reason_distribution": dict(Counter(
            str(item.get("risk_reason") or "UNKNOWN") for item in decisions
        ).most_common(25)),
        "grade_risk_multiplier_distribution": dict(Counter(
            _risk_bucket(item.get("grade_risk_multiplier")) for item in decisions
        ).most_common()),
        "effective_risk_pct_distribution": dict(Counter(
            _risk_bucket(item.get("effective_risk_pct")) for item in decisions
        ).most_common()),
        "enter_eligible_with_positive_risk_count": sum(
            1 for item in decisions
            if item.get("enter_eligible") and float(item.get("risk_multiplier") or 0.0) > 0
        ),
        "enter_eligible_without_positive_risk_count": sum(
            1 for item in decisions
            if item.get("enter_eligible") and float(item.get("risk_multiplier") or 0.0) <= 0
        ),
        "risk_positive_but_not_enter_eligible_count": sum(
            1 for item in decisions
            if not item.get("enter_eligible") and float(item.get("risk_multiplier") or 0.0) > 0
        ),
        # P2-E Phase 7D: readiness coherence
        "readiness_coherence_violation_count": sum(
            1 for item in decisions
            if str(item.get("readiness_state")) == "READY"
            and (item.get("readiness_missing_ready_blockers") or item.get("readiness_non_ready_sections"))
        ),
        "READY_with_missing_ready_blockers_count": sum(
            1 for item in decisions
            if str(item.get("readiness_state")) == "READY"
            and item.get("readiness_missing_ready_blockers")
        ),
        "READY_with_non_ready_sections_count": sum(
            1 for item in decisions
            if str(item.get("readiness_state")) == "READY"
            and item.get("readiness_non_ready_sections")
        ),
        "readiness_missing_ready_blocker_distribution": dict(Counter(
            blocker for item in decisions
            for blocker in (item.get("readiness_missing_ready_blockers") or [])
        ).most_common(25)),
        "readiness_non_ready_section_distribution": dict(Counter(
            f"{section}:{state}"
            for item in decisions
            for section, state in (item.get("readiness_non_ready_sections") or {}).items()
        ).most_common(25)),
        # P2-E Phase 7E: aggregated POI handoff metrics
        **_phase7e_handoff_metrics(decisions),
        # P2-E Phase8: setup candidates and near-miss diagnostics
        **_phase8_setup_candidate_summary(decisions),
        # P2-E Phase10: POI contract coherence diagnostics
        **_phase10_poi_contract_summary(decisions),
        # P2-E Phase11: micro contract / evidence diagnostics
        **_micro_contract_summary(decisions),
        # P2-E Phase12: POI-Micro synergy diagnostics
        **_phase12_poi_micro_synergy_summary(decisions),
        # P2-E Phase13: Agent2 POI rejection decomposition
        **_phase13_poi_rejection_summary(decisions),
        # P2-E Phase14: readiness/risk gate decomposition
        **_phase14_gate_decomposition_summary(decisions),
        "blocked_reason_distribution": dict(Counter(str(item.get("readiness_reason") or item.get("veto_code") or "UNKNOWN") for item in decisions).most_common(25)),
        "missed_entry_distribution": dict(Counter(str(event.get("reason") or "UNKNOWN") for event in missed_events).most_common()),
        "r_values": r_values,
    }
    summary.update(_performance_ratios(summary, r_values))
    summary["trade_frequency_per_day"] = None
    summary["diagnostic"] = _diagnostic(summary)
    summary["performance_status"] = _performance_status(
        filled_trades=len(open_events),
        closed_trades=len(close_events),
        open_trades_end=0,  # filled in by replay_engine from trade_summary
    )
    return summary


def _phase8_setup_candidate_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    near_miss_setup_types = {
        "UNKNOWN",
        "POI_REACTION",
        "SWEEP_REVERSAL",
        "CONTINUATION_LIGHT",
        "REVERSAL_LIGHT",
        "OTE_PULLBACK",
    }
    light_types = {
        "CONTINUATION_LIGHT",
        "REVERSAL_LIGHT",
        "OTE_PULLBACK",
        "SESSION_REVERSAL_MEDIUM",
    }
    strict_types = {
        "REVERSAL_STRICT",
        "CONTINUATION_STRICT",
        "SWEEP_REVERSAL",
        "FAILED_AUCTION_RECLAIM",
    }
    candidate_types = Counter()
    best_candidate_types = Counter()
    missing_signals = Counter()
    near_miss_by_setup = Counter()
    blockers_by_setup: dict[str, Counter] = defaultdict(Counter)
    readiness_reason_by_setup: dict[str, Counter] = defaultdict(Counter)
    risk_reason_by_setup: dict[str, Counter] = defaultdict(Counter)
    decision_counts_by_setup: dict[str, Counter] = defaultdict(Counter)
    grade_by_setup: dict[str, Counter] = defaultdict(Counter)
    enter_eligible_by_setup = Counter()
    risk_positive_by_setup = Counter()
    near_miss_scanned_count = 0
    near_miss_with_candidates_count = 0
    light_setup_count = 0
    strict_setup_count = 0
    light_enter_eligible_count = 0
    strict_enter_eligible_count = 0
    risk_multiplier_positive = 0

    for item in decisions:
        setup_type = str(item.get("setup_type") or "UNKNOWN")
        action = str(item.get("decision") or item.get("action") or "UNKNOWN")
        grade = str(item.get("setup_grade") or "UNKNOWN")
        candidates = item.get("setup_candidates") or []
        best = item.get("best_setup_candidate") or {}

        decision_counts_by_setup[setup_type][action] += 1
        grade_by_setup[setup_type][grade] += 1
        readiness_reason_by_setup[setup_type][str(item.get("readiness_reason") or "UNKNOWN")] += 1
        risk_reason_by_setup[setup_type][str(item.get("risk_reason") or "UNKNOWN")] += 1
        for blocker in item.get("enter_eligibility_blockers") or []:
            blockers_by_setup[setup_type][str(blocker)] += 1

        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict):
                    candidate_types[str(candidate.get("candidate_type") or "UNKNOWN")] += 1
        if isinstance(best, dict) and best:
            best_candidate_types[str(best.get("candidate_type") or "UNKNOWN")] += 1

        if setup_type in light_types:
            light_setup_count += 1
        if setup_type in strict_types:
            strict_setup_count += 1

        enter_eligible = bool(item.get("enter_eligible"))
        if enter_eligible:
            enter_eligible_by_setup[setup_type] += 1
            if setup_type in light_types:
                light_enter_eligible_count += 1
            if setup_type in strict_types:
                strict_enter_eligible_count += 1

        if _safe_float(item.get("risk_multiplier")) and float(item.get("risk_multiplier") or 0.0) > 0.0:
            risk_multiplier_positive += 1
            risk_positive_by_setup[setup_type] += 1

        is_near_miss = (
            action in {"WATCH_ONLY", "WAIT_FOR_TRIGGER"}
            and setup_type in near_miss_setup_types
        )
        if is_near_miss:
            near_miss_scanned_count += 1
            near_miss_by_setup[setup_type] += 1
            if candidates:
                near_miss_with_candidates_count += 1
            near_missing = item.get("near_miss_missing_signals")
            if not near_missing:
                signals = item.get("setup_signal_inventory") or {}
                near_missing = signals.get("missing_core", []) if isinstance(signals, dict) else []
            for missing in near_missing or []:
                missing_signals[str(missing)] += 1

    return {
        "setup_candidate_type_distribution": dict(candidate_types.most_common()),
        "best_setup_candidate_distribution": dict(best_candidate_types.most_common()),
        "near_miss_scanned_count": near_miss_scanned_count,
        "near_miss_with_candidates_count": near_miss_with_candidates_count,
        "near_miss_selected_top_count": 0,
        "near_miss_by_current_setup_type": dict(near_miss_by_setup.most_common()),
        "near_miss_candidate_type_distribution": dict(candidate_types.most_common()),
        "near_miss_candidate_count": near_miss_with_candidates_count,
        "near_miss_by_setup_type": dict(near_miss_by_setup.most_common()),
        "near_miss_missing_signal_distribution": dict(missing_signals.most_common(25)),
        "light_setup_count": light_setup_count,
        "strict_setup_count": strict_setup_count,
        "light_enter_eligible_count": light_enter_eligible_count,
        "strict_enter_eligible_count": strict_enter_eligible_count,
        "enter_eligible_by_setup_type": dict(enter_eligible_by_setup.most_common()),
        "risk_multiplier_positive": risk_multiplier_positive,
        "risk_positive_by_setup_type": dict(risk_positive_by_setup.most_common()),
        "enter_eligibility_blockers_by_setup_type": _nested_counter_to_dict(blockers_by_setup),
        "readiness_reason_by_setup_type": _nested_counter_to_dict(readiness_reason_by_setup),
        "risk_reason_by_setup_type": _nested_counter_to_dict(risk_reason_by_setup),
        "decision_counts_by_setup_type": _nested_counter_to_dict(decision_counts_by_setup),
        "grade_distribution_by_setup_type": _nested_counter_to_dict(grade_by_setup),
    }


def _phase10_poi_contract_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    status_distribution = Counter()
    contradiction_distribution = Counter()
    score_source_distribution = Counter()
    suspect_by_reason = Counter()
    sweep_by_status = Counter()
    suspect_count = 0
    contradiction_count = 0

    for decision in decisions:
        signals = decision.get("setup_signal_inventory") or {}
        if not isinstance(signals, dict):
            signals = {}
        quality_breakdown = decision.get("poi_quality_breakdown") or signals.get("poi_quality_breakdown") or {}
        if not isinstance(quality_breakdown, dict):
            quality_breakdown = {}

        status = str(
            decision.get("poi_contract_status")
            or signals.get("poi_contract_status")
            or "UNKNOWN"
        )
        score_source = str(
            decision.get("poi_score_source")
            or signals.get("poi_score_source")
            or quality_breakdown.get("score_source")
            or "UNKNOWN"
        )
        contradictions = (
            decision.get("poi_contract_contradictions")
            or signals.get("poi_contract_contradictions")
            or []
        )
        if not isinstance(contradictions, list):
            contradictions = [contradictions]

        status_distribution[status] += 1
        score_source_distribution[score_source] += 1
        if str(decision.get("setup_type") or "UNKNOWN") == "SWEEP_REVERSAL":
            sweep_by_status[status] += 1

        row_is_suspect = False
        for contradiction in contradictions:
            key = str(contradiction)
            contradiction_distribution[key] += 1
            contradiction_count += 1
            row_is_suspect = True

        suspect_reasons = _poi_suspect_reasons(decision, contradictions, quality_breakdown)
        for reason in suspect_reasons:
            suspect_by_reason[reason] += 1
            row_is_suspect = True

        if row_is_suspect:
            suspect_count += 1

    return {
        "poi_contract_status_distribution": dict(status_distribution.most_common()),
        "poi_contract_contradiction_count": contradiction_count,
        "poi_contract_contradiction_distribution": dict(contradiction_distribution.most_common()),
        "poi_quality_score_source_distribution": dict(score_source_distribution.most_common()),
        "poi_suspect_count": suspect_count,
        "poi_suspect_by_reason": dict(suspect_by_reason.most_common()),
        "sweep_reversal_by_poi_contract_status": dict(sweep_by_status.most_common()),
    }


def _poi_suspect_reasons(
    decision: dict[str, Any],
    contradictions: list[Any],
    quality_breakdown: dict[str, Any],
) -> list[str]:
    reasons = [str(item) for item in contradictions if item]
    bundle = decision.get("p1_evidence_bundle") or {}
    poi = bundle.get("poi") if isinstance(bundle, dict) and isinstance(bundle.get("poi"), dict) else {}
    semantic = str(poi.get("poi_semantic_status") or "").upper()
    failure = str(poi.get("poi_failure_class") or "").upper()
    final_score = _safe_float(
        quality_breakdown.get("final_poi_quality_score")
        if isinstance(quality_breakdown, dict)
        else None
    )
    if final_score is None:
        final_score = _safe_float(poi.get("final_poi_quality_score"))
    if final_score is None:
        final_score = _safe_float(poi.get("poi_quality_score"))
    if "PRESENT" in semantic and ("EXECUTABLE" in semantic or "READY" in semantic):
        if final_score == 0.0:
            reasons.append("SEMANTIC_READY_OR_EXECUTABLE_WITH_ZERO_SCORE")
        elif final_score is None:
            reasons.append("SEMANTIC_READY_OR_EXECUTABLE_WITH_MISSING_SCORE")
    if "REJECTED" in failure:
        reasons.append("POI_FAILURE_REJECTED")
    signals = decision.get("setup_signal_inventory") or {}
    if (
        isinstance(signals, dict)
        and signals.get("price_bounds_present") is True
        and str(decision.get("poi_execution_readiness") or "").upper() == "WATCH_ONLY"
    ):
        reasons.append("WATCH_ONLY_WITH_BOUNDS")
    return sorted(set(reasons))


def _micro_contract_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """P2-E Phase 11: micro contract / evidence metrics.

    Tracks micro_contract_status, reasons, missing/present fields,
    contradictions, and waiting-trigger (near-miss) counts by setup type.
    """
    status_distribution: Counter = Counter()
    reason_distribution: Counter = Counter()
    missing_field_distribution: Counter = Counter()
    present_field_distribution: Counter = Counter()
    contradiction_distribution: Counter = Counter()
    missing_data_count = 0
    waiting_trigger_by_setup: Counter = Counter()
    blockers_by_micro_status: dict[str, Counter] = defaultdict(Counter)

    for decision in decisions:
        signals = decision.get("setup_signal_inventory") or {}
        if not isinstance(signals, dict):
            signals = {}
        nested = decision.get("micro_contract") or {}
        if not isinstance(nested, dict):
            nested = {}

        status = str(
            decision.get("micro_contract_status")
            or signals.get("micro_contract_status")
            or nested.get("status")
            or "UNKNOWN"
        )
        reason = str(
            decision.get("micro_contract_reason")
            or signals.get("micro_contract_reason")
            or nested.get("reason")
            or "UNKNOWN"
        )
        missing = (
            decision.get("micro_missing_fields")
            or signals.get("micro_missing_fields")
            or nested.get("missing_fields")
            or []
        )
        present = (
            decision.get("micro_present_fields")
            or signals.get("micro_present_fields")
            or nested.get("present_fields")
            or []
        )
        contradictions = (
            decision.get("micro_contradictions")
            or signals.get("micro_contradictions")
            or nested.get("contradictions")
            or []
        )
        setup_type = str(decision.get("setup_type") or "UNKNOWN")
        blockers = decision.get("enter_eligibility_blockers") or []

        status_distribution[status] += 1
        reason_distribution[reason] += 1

        if not isinstance(missing, list):
            missing = [missing]
        for field in missing:
            missing_field_distribution[str(field)] += 1

        if not isinstance(present, list):
            present = [present]
        for field in present:
            present_field_distribution[str(field)] += 1

        if not isinstance(contradictions, list):
            contradictions = [contradictions]
        for c in contradictions:
            contradiction_distribution[str(c)] += 1

        if status == "MISSING_DATA":
            missing_data_count += 1

        if status == "WAITING_TRIGGER":
            waiting_trigger_by_setup[setup_type] += 1

        if isinstance(blockers, list):
            for blocker in blockers:
                blockers_by_micro_status[status][str(blocker)] += 1

    return {
        "micro_contract_status_distribution": dict(status_distribution.most_common()),
        "micro_contract_reason_distribution": dict(reason_distribution.most_common(25)),
        "micro_contract_missing_field_distribution": dict(missing_field_distribution.most_common(25)),
        "micro_contract_present_field_distribution": dict(present_field_distribution.most_common(25)),
        "micro_contradiction_distribution": dict(contradiction_distribution.most_common()),
        "micro_missing_data_count": missing_data_count,
        "near_miss_micro_count": sum(waiting_trigger_by_setup.values()),
        "near_miss_micro_count_by_setup": dict(waiting_trigger_by_setup.most_common()),
        "enter_eligibility_blockers_by_micro_contract_status": _nested_counter_to_dict(blockers_by_micro_status),
    }


def _phase12_poi_micro_synergy_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    synergy_by_setup: Counter = Counter()
    synergy_by_grade: Counter = Counter()
    synergy_by_decision: Counter = Counter()
    status_distribution: Counter = Counter()
    reason_distribution: Counter = Counter()
    remaining_blockers: Counter = Counter()
    enter_eligible_by_setup: Counter = Counter()
    risk_positive_by_setup: Counter = Counter()
    synergy_count = 0
    confirmed_inside = 0
    confirmed_outside = 0
    confirmed_without_poi = 0
    confirmed_sweep = 0
    confirmed_poi_reaction = 0
    confirmed_continuation = 0

    for decision in decisions:
        setup_type = str(decision.get("setup_type") or "UNKNOWN")
        grade = str(decision.get("setup_grade") or "UNKNOWN")
        action = str(decision.get("decision") or "UNKNOWN")
        payload = _phase12_synergy_payload(decision)
        synergy = bool(decision.get("poi_micro_synergy") or payload.get("synergy"))
        micro_confirmed = bool(
            decision.get("micro_confirmed")
            or decision.get("micro_contract_confirmed")
            or str(decision.get("micro_contract_status") or "") == "MICRO_CONFIRMED"
        )
        micro_inside = _phase12_micro_inside(decision, payload)
        micro_outside = _phase12_micro_outside(decision, payload)

        status_distribution[str(decision.get("poi_micro_synergy_status") or payload.get("status") or "UNKNOWN")] += 1
        reason_distribution[str(decision.get("poi_micro_reason") or payload.get("reason") or "UNKNOWN")] += 1

        if synergy:
            synergy_count += 1
            synergy_by_setup[setup_type] += 1
            synergy_by_grade[grade] += 1
            synergy_by_decision[action] += 1
            for blocker in decision.get("enter_eligibility_blockers") or []:
                remaining_blockers[str(blocker)] += 1

        if micro_confirmed:
            if setup_type == "SWEEP_REVERSAL":
                confirmed_sweep += 1
            if setup_type == "POI_REACTION":
                confirmed_poi_reaction += 1
            if setup_type.startswith("CONTINUATION"):
                confirmed_continuation += 1
            if micro_inside:
                confirmed_inside += 1
            elif micro_outside:
                confirmed_outside += 1
            elif not _phase12_has_selected_poi(decision):
                confirmed_without_poi += 1

        if decision.get("enter_eligible"):
            enter_eligible_by_setup[setup_type] += 1
        if float(decision.get("risk_multiplier") or 0.0) > 0.0:
            risk_positive_by_setup[setup_type] += 1

    return {
        "poi_micro_synergy_count": synergy_count,
        "poi_micro_synergy_by_setup": dict(synergy_by_setup.most_common()),
        "poi_micro_synergy_by_grade": dict(synergy_by_grade.most_common()),
        "poi_micro_synergy_by_decision": dict(synergy_by_decision.most_common()),
        "poi_micro_synergy_status_distribution": dict(status_distribution.most_common()),
        "poi_micro_reason_distribution": dict(reason_distribution.most_common(25)),
        "micro_confirmed_inside_poi_count": confirmed_inside,
        "micro_confirmed_outside_poi_count": confirmed_outside,
        "micro_confirmed_without_poi_count": confirmed_without_poi,
        "confirmed_micro_sweep_reversal_count": confirmed_sweep,
        "confirmed_micro_poi_reaction_count": confirmed_poi_reaction,
        "confirmed_micro_continuation_count": confirmed_continuation,
        "enter_eligible_by_setup": dict(enter_eligible_by_setup.most_common()),
        "risk_positive_by_setup": dict(risk_positive_by_setup.most_common()),
        "remaining_blockers_after_synergy_distribution": dict(remaining_blockers.most_common(25)),
    }


def _phase13_poi_rejection_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    code_distribution: Counter = Counter()
    severity_distribution: Counter = Counter()
    synergy_by_code: dict[str, Counter] = defaultdict(Counter)
    recoverable_count = 0
    fatal_count = 0
    micro_confirmed_rejected = 0
    micro_confirmed_recoverable = 0
    sweep_rejected = 0

    for decision in decisions:
        rejection = _phase13_rejection_payload(decision)
        code = str(decision.get("poi_rejection_code") or rejection.get("code") or "UNKNOWN")
        severity = str(decision.get("poi_rejection_severity") or rejection.get("severity") or "UNKNOWN")
        recoverable = bool(decision.get("poi_rejection_recoverable") or rejection.get("recoverable"))
        fatal = bool(decision.get("poi_rejection_fatal") or rejection.get("fatal"))
        micro_confirmed = bool(
            decision.get("micro_confirmed")
            or decision.get("micro_contract_confirmed")
            or str(decision.get("micro_contract_status") or "") == "MICRO_CONFIRMED"
        )
        synergy = bool(decision.get("poi_micro_synergy") or _phase12_synergy_payload(decision).get("synergy"))

        code_distribution[code] += 1
        severity_distribution[severity] += 1
        if recoverable:
            recoverable_count += 1
        if fatal:
            fatal_count += 1
        if micro_confirmed and code not in {"POI_REJECTION_NONE", "UNKNOWN"}:
            micro_confirmed_rejected += 1
        if micro_confirmed and recoverable:
            micro_confirmed_recoverable += 1
        if str(decision.get("setup_type") or "UNKNOWN") == "SWEEP_REVERSAL" and code not in {
            "POI_REJECTION_NONE",
            "UNKNOWN",
        }:
            sweep_rejected += 1
        synergy_by_code[code]["synergy_true" if synergy else "synergy_false"] += 1

    return {
        "poi_rejection_code_distribution": dict(code_distribution.most_common()),
        "poi_rejection_severity_distribution": dict(severity_distribution.most_common()),
        "poi_rejection_recoverable_count": recoverable_count,
        "poi_rejection_fatal_count": fatal_count,
        "micro_confirmed_rejected_poi_count": micro_confirmed_rejected,
        "micro_confirmed_recoverable_poi_count": micro_confirmed_recoverable,
        "sweep_rejected_count": sweep_rejected,
        "poi_micro_synergy_by_rejection_code": {
            key: dict(value.most_common())
            for key, value in sorted(synergy_by_code.items())
        },
    }


def _phase14_gate_decomposition_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    primary: Counter = Counter()
    blockers: Counter = Counter()
    synergy_true_primary: Counter = Counter()
    synergy_true_setup: Counter = Counter()
    synergy_true_enter: Counter = Counter()
    risk_reason_by_primary: dict[str, Counter] = defaultdict(Counter)

    for decision in decisions:
        gate = _phase14_gate_payload(decision)
        primary_blocker = str(gate.get("primary_blocker") or "UNKNOWN")
        primary[primary_blocker] += 1
        gate_blockers = gate.get("blockers")
        if not isinstance(gate_blockers, list):
            gate_blockers = []
        for blocker in gate_blockers:
            blockers[str(blocker)] += 1

        synergy = bool(decision.get("poi_micro_synergy") or _phase12_synergy_payload(decision).get("synergy"))
        if synergy:
            setup_type = str(decision.get("setup_type") or gate.get("setup_type") or "UNKNOWN")
            synergy_true_primary[primary_blocker] += 1
            synergy_true_setup[setup_type] += 1
            if decision.get("enter_eligible"):
                synergy_true_enter[setup_type] += 1
            risk_reason_by_primary[primary_blocker][str(decision.get("risk_reason") or gate.get("risk_reason") or "UNKNOWN")] += 1

    return {
        "gate_primary_blocker_distribution": dict(primary.most_common()),
        "gate_blocker_distribution": dict(blockers.most_common(30)),
        "synergy_true_gate_primary_blocker_distribution": dict(synergy_true_primary.most_common()),
        "synergy_true_by_setup_type": dict(synergy_true_setup.most_common()),
        "synergy_true_enter_eligible_by_setup_type": dict(synergy_true_enter.most_common()),
        "risk_reason_by_gate_primary_blocker": {
            key: dict(value.most_common(10))
            for key, value in sorted(risk_reason_by_primary.items())
        },
    }


def _phase14_gate_payload(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("gate_decomposition")
    if isinstance(payload, dict) and payload:
        return payload
    return evaluate_readiness_risk_gate(decision).to_dict()


def _phase12_synergy_payload(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("poi_micro_synergy_payload")
    if isinstance(payload, dict) and payload:
        return payload
    bundle = decision.get("p1_evidence_bundle") or {}
    if isinstance(bundle, dict):
        poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
        raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
        if isinstance(poi.get("poi_micro_synergy"), dict):
            return poi.get("poi_micro_synergy") or {}
        if isinstance(raw.get("poi_micro_synergy"), dict):
            return raw.get("poi_micro_synergy") or {}
    return {}


def _phase13_rejection_payload(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("poi_rejection")
    if isinstance(payload, dict) and payload:
        return payload
    bundle = decision.get("p1_evidence_bundle") or {}
    if isinstance(bundle, dict):
        poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
        if isinstance(poi.get("poi_rejection"), dict):
            return poi.get("poi_rejection") or {}
    synergy = _phase12_synergy_payload(decision)
    audit = synergy.get("audit") if isinstance(synergy.get("audit"), dict) else {}
    rejection = audit.get("poi_rejection") if isinstance(audit.get("poi_rejection"), dict) else {}
    return rejection if isinstance(rejection, dict) else {}


def _phase12_micro_inside(decision: dict[str, Any], payload: dict[str, Any]) -> bool:
    evidence = decision.get("micro_evidence") if isinstance(decision.get("micro_evidence"), dict) else {}
    return bool(
        decision.get("micro_inside_poi")
        or payload.get("micro_inside_poi")
        or decision.get("price_in_agent2_poi")
        or decision.get("trigger_inside_poi")
        or evidence.get("price_in_agent2_poi")
        or evidence.get("trigger_inside_poi")
    )


def _phase12_micro_outside(decision: dict[str, Any], payload: dict[str, Any]) -> bool:
    evidence = decision.get("micro_evidence") if isinstance(decision.get("micro_evidence"), dict) else {}
    return bool(
        decision.get("micro_outside_poi")
        or payload.get("micro_outside_poi")
        or decision.get("trigger_outside_poi")
        or decision.get("micro_contract_outside_poi")
        or evidence.get("trigger_outside_poi")
    )


def _phase12_has_selected_poi(decision: dict[str, Any]) -> bool:
    bundle = decision.get("p1_evidence_bundle") or {}
    if not isinstance(bundle, dict):
        return False
    poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
    return bool(
        poi.get("selected_poi_present")
        or poi.get("selected_poi")
        or poi.get("has_price_bounds")
        or poi.get("price_bounds")
    )


def _phase7e_handoff_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Phase 7E: aggregated POI handoff metrics across Agent3/4/5."""
    from collections import Counter

    handoff_sources = Counter()
    legacy_fallback_count = 0
    p2a_selected_poi_count = 0
    p2a_candidate_fallback_count = 0
    p2a_missing_or_bounds_missing_count = 0

    for item in decisions:
        bundle = item.get("p1_evidence_bundle") or {}
        if not isinstance(bundle, dict):
            continue
        raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
        liquidity = bundle.get("liquidity") if isinstance(bundle.get("liquidity"), dict) else {}
        micro = bundle.get("micro") if isinstance(bundle.get("micro"), dict) else {}
        timing = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}

        candidates = [
            ("agent3", liquidity.get("agent3_poi_handoff") if isinstance(liquidity.get("agent3_poi_handoff"), dict) else {}),
            ("agent4", timing.get("agent4_poi_handoff") if isinstance(timing.get("agent4_poi_handoff"), dict) else {}),
            ("agent5", micro.get("agent5_poi_handoff") if isinstance(micro.get("agent5_poi_handoff"), dict) else {}),
        ]

        for agent_label, handoff in candidates:
            source = str(handoff.get("source") or "UNKNOWN")
            handoff_sources[f"{agent_label}:{source}"] += 1

            if source == "LEGACY_AGENT2_FALLBACK":
                legacy_fallback_count += 1
            elif source == "P2A_SELECTED_POI":
                p2a_selected_poi_count += 1
            elif source == "P2A_CANDIDATE_FALLBACK":
                p2a_candidate_fallback_count += 1

            if handoff.get("failure_reason") == "NO_P2A_POI_OR_BOUNDS":
                p2a_missing_or_bounds_missing_count += 1

    return {
        "agent_poi_handoff_source_distribution": dict(handoff_sources.most_common()),
        "legacy_fallback_usage_count": legacy_fallback_count,
        "p2a_selected_poi_consumed_count": p2a_selected_poi_count,
        "p2a_candidate_fallback_count": p2a_candidate_fallback_count,
        "p2a_missing_or_bounds_missing_count": p2a_missing_or_bounds_missing_count,
    }


def _performance_ratios(summary: dict[str, Any], r_values: list[float]) -> dict[str, Any]:
    wins = int(summary.get("wins") or 0)
    losses = int(summary.get("losses") or 0)
    filled = int(summary.get("filled_trades") or 0)
    gross_win = sum(value for value in r_values if value > 0)
    gross_loss = abs(sum(value for value in r_values if value < 0))
    return {
        "winrate": round(wins / filled, 6) if filled else None,
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss else (None if not gross_win else None),
        "expectancy_R": round(sum(r_values) / len(r_values), 6) if r_values else None,
        "avg_R": round(sum(r_values) / len(r_values), 6) if r_values else None,
        "median_R": round(median(r_values), 6) if r_values else None,
        "max_drawdown_R": round(_max_drawdown(r_values), 6) if r_values else None,
    }


def _performance_by_grade(decisions: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_grade: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        by_grade[_normalize_grade(decision.get("setup_grade"))].append(decision)
    events_by_grade: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_grade[_normalize_grade(event.get("setup_grade") or event.get("tier"))].append(event)
    return {
        grade: _grade_summary(by_grade.get(grade, []), events_by_grade.get(grade, []))
        for grade in GRADES
    }


def _grade_summary(decisions: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    open_events = [event for event in events if str(event.get("event") or "") in {"open", "tier_trade_open"}]
    close_events = [event for event in events if str(event.get("event") or "") in {"close", "tier_trade_close"}]
    missed_events = [event for event in events if str(event.get("event") or "") in {"missed_entry", "tier_missed_entry"}]
    r_values = [_safe_float(event.get("r_multiple")) for event in close_events]
    r_values = [value for value in r_values if value is not None]
    wins = sum(1 for event in close_events if _safe_float(event.get("pnl")) is not None and float(event.get("pnl")) > 0)
    losses = sum(1 for event in close_events if _safe_float(event.get("pnl")) is not None and float(event.get("pnl")) < 0)
    payload = {
        "decisions": len(decisions),
        "signals": sum(1 for item in decisions if str(item.get("decision") or "").startswith("ENTER")),
        "filled_trades": len(open_events),
        "missed_entries": len(missed_events),
        "wins": wins,
        "losses": losses,
        "r_values": r_values,
    }
    payload.update(_performance_ratios(payload, r_values))
    return payload


def _performance_status(
    *,
    filled_trades: int = 0,
    closed_trades: int = 0,
    open_trades_end: int = 0,
) -> str:
    """Phase18: classify the performance summary state.

    Returns one of: NO_TRADES, TRADES_OPEN_ONLY, REALIZED_TRADES_AVAILABLE, MIXED_REALIZED_AND_OPEN
    """
    if filled_trades == 0:
        return "NO_TRADES"
    if closed_trades == 0:
        if open_trades_end > 0:
            return "TRADES_OPEN_ONLY"
        return "NO_TRADES"
    if open_trades_end > 0:
        return "MIXED_REALIZED_AND_OPEN"
    return "REALIZED_TRADES_AVAILABLE"


def _diagnostic(summary: dict[str, Any]) -> str:
    decisions = summary.get("decision_distribution") or {}
    if int(summary.get("filled_trades") or 0) == 0:
        if int(summary.get("missed_entries") or 0) > 0:
            return "MANY_MISSED_ENTRIES"
        enter_count = (
            int(decisions.get("ENTER") or 0)
            + int(decisions.get("ENTER_FULL") or 0)
            + int(decisions.get("ENTER_REDUCED") or 0)
        )
        if enter_count == 0:
            if int(summary.get("poi_micro_synergy_count") or 0) > 0:
                synergy_primary = summary.get("synergy_true_gate_primary_blocker_distribution") or {}
                if int(synergy_primary.get("SETUP_TYPE_POI_REACTION_NOT_TRADABLE") or 0) > 0:
                    return "SETUP_TYPE_POI_REACTION_NOT_TRADABLE_AFTER_SYNERGY"
                if int(synergy_primary.get("NO_TRADABLE_SETUP_CANDIDATE") or 0) > 0:
                    return "NO_TRADABLE_SETUP_CANDIDATE_AFTER_SYNERGY"
                if int(synergy_primary.get("LIQUIDITY_NOT_READY") or 0) > 0:
                    return "LIQUIDITY_NOT_READY_AFTER_SYNERGY"
                if int(summary.get("enter_eligible_count") or 0) == 0:
                    return "ENTER_BLOCKED_BY_READINESS_OR_RISK_AFTER_SYNERGY"
                return "ENTER_BLOCKED_AFTER_ENTER_ELIGIBLE"
            if int(summary.get("micro_confirmed_recoverable_poi_count") or 0) > 0:
                return "POI_MICRO_SYNERGY_STILL_ZERO_AFTER_RECOVERABLE_POI"
            if int(summary.get("poi_rejection_fatal_count") or 0) > 0 and int(summary.get("poi_rejection_recoverable_count") or 0) == 0:
                return "POI_FATAL_REJECTION"
            if int(summary.get("poi_rejection_recoverable_count") or 0) == 0:
                return "NO_RECOVERABLE_POI"
            return "NO_ENTER_SIGNALS"
        if int(summary.get("total_trades") or 0) == 0:
            return "NO_FILLED_TRADES"
    total_decisions = int(summary.get("total_decisions") or 0)
    if total_decisions and int(decisions.get("REJECT") or 0) / total_decisions >= 0.8:
        return "MOSTLY_REJECT"
    if total_decisions and int(decisions.get("WAIT_FOR_TRIGGER") or 0) / total_decisions >= 0.5:
        return "MOSTLY_WAIT_FOR_TRIGGER"
    return "DIAGNOSTIC_AVAILABLE"


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _normalize_grade(value: Any) -> str:
    grade = str(value or "D").upper().replace("+", "_PLUS")
    return grade if grade in GRADES else "D"


def _events_by_grade(events: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(_normalize_grade(event.get("setup_grade") or event.get("tier")) for event in events).most_common())


def _merge_dicts(items: Any) -> dict[str, int]:
    counter: Counter = Counter()
    for item in items or []:
        if isinstance(item, dict):
            for key, value in item.items():
                try:
                    counter[str(key)] += int(value)
                except (TypeError, ValueError):
                    continue
    return dict(counter.most_common())


def _merge_nested_dicts(items: Any) -> dict[str, dict[str, int]]:
    merged: dict[str, Counter] = defaultdict(Counter)
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for outer_key, inner in item.items():
            if not isinstance(inner, dict):
                continue
            for inner_key, value in inner.items():
                try:
                    merged[str(outer_key)][str(inner_key)] += int(value)
                except (TypeError, ValueError):
                    continue
    return _nested_counter_to_dict(merged)


def _nested_counter_to_dict(values: dict[str, Counter]) -> dict[str, dict[str, int]]:
    return {
        key: dict(counter.most_common())
        for key, counter in sorted(values.items())
    }


def _merge_performance_by_grade(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for grade in GRADES:
        totals = [((item.get("performance_by_grade") or {}).get(grade) or {}) for item in items]
        decisions = sum(int(item.get("decisions") or 0) for item in totals)
        events = []
        aggregate = {
            "decisions": decisions,
            "signals": sum(int(item.get("signals") or 0) for item in totals),
            "filled_trades": sum(int(item.get("filled_trades") or 0) for item in totals),
            "missed_entries": sum(int(item.get("missed_entries") or 0) for item in totals),
            "wins": sum(int(item.get("wins") or 0) for item in totals),
            "losses": sum(int(item.get("losses") or 0) for item in totals),
        }
        for item in totals:
            events.extend(_float_list(item.get("r_values") or []))
        aggregate.update(_performance_ratios(aggregate, events))
        merged[grade] = aggregate
    return merged


def _aggregate_trade_frequency(windows: list[dict[str, Any]], filled: int) -> float | None:
    days = sum(float(item.get("window_days") or 0.0) for item in windows)
    return round(float(filled) / days, 6) if days > 0 else None


def _float_list(values: list[Any]) -> list[float]:
    result = []
    for value in values:
        number = _safe_float(value)
        if number is not None:
            result.append(number)
    return result


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _risk_bucket(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if number <= 0:
        return "0.0"
    if number <= 0.25:
        return "0.01-0.25"
    if number <= 0.5:
        return "0.26-0.50"
    if number <= 0.75:
        return "0.51-0.75"
    return "0.76-1.0"
