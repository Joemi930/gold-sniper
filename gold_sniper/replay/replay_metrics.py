"""Compact metrics helpers for Phase 7 unified shadow replay."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

from gold_sniper.replay.offline_market_structure import Candle
from gold_sniper.replay.offline_trade_simulator import simulate_enter_outcomes
from gold_sniper.strategy.readiness_risk_gate_contract import evaluate_readiness_risk_gate


def _graded_entry_active() -> bool:
    """Whether Kasper graded-entry mode is currently enabled (diagnostic)."""
    try:
        from gold_sniper.strategy.kasper_scenario_engine import graded_entry_enabled

        return graded_entry_enabled()
    except Exception:
        return False


def build_replay_metrics(
    decisions: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    date_start: str | None,
    date_end: str | None,
    data_profile: dict[str, Any],
    replay_candles_1m: list[Candle] | None = None,
    replay_candles_15m: list[Candle] | None = None,
) -> dict[str, Any]:
    total = len(decisions)
    decision_counts = Counter(item.get("decision", "UNKNOWN") for item in decisions)
    scores = [float(item.get("score", 0.0) or 0.0) for item in decisions]
    confidences = [float(item.get("confidence", 0.0) or 0.0) for item in decisions]
    missing = Counter()
    warnings = Counter()
    stages = Counter()
    funnel_exit_stages = Counter()
    funnel_exit_reasons = Counter()
    setup_types = Counter()
    best_scenarios = Counter()
    best_scenario_statuses = Counter()
    near_miss_by_stage = Counter()
    by_session: dict[str, Counter] = defaultdict(Counter)
    by_month: dict[str, Counter] = defaultdict(Counter)
    by_setup: dict[str, Counter] = defaultdict(Counter)
    setup_grades = Counter()
    kasper_decisions = Counter()
    kasper_grades = Counter()
    session_grades = Counter()
    risk_bands = Counter()
    micro_templates = Counter()
    poi_readiness = Counter()
    micro_readiness = Counter()
    timing_readiness = Counter()
    liquidity_readiness = Counter()
    risk_multiplier_buckets = Counter()
    # P2-E Phase 7A: setup taxonomy
    setup_families = Counter()
    classification_reasons = Counter()
    classification_confidences: list[float] = []
    # P2-E Phase 7B: enter eligibility
    enter_eligible_count = 0
    enter_eligibility_reasons = Counter()
    enter_eligibility_blockers = Counter()
    enter_eligible_by_setup: dict[str, int] = {}
    enter_eligible_by_grade: dict[str, int] = {}
    risk_preview_reasons = Counter()
    risk_preview_allowed_count = 0
    risk_preview_positive_count = 0
    # P2-E Phase 7C: risk multiplier mapping
    grade_risk_buckets = Counter()
    effective_risk_buckets = Counter()
    risk_allowed_count = 0
    risk_reasons = Counter()
    enter_eligible_with_positive_risk_count = 0
    enter_eligible_without_positive_risk_count = 0
    risk_positive_but_not_enter_eligible_count = 0
    risk_by_grade: dict[str, Counter] = defaultdict(Counter)
    risk_by_setup_type: dict[str, Counter] = defaultdict(Counter)
    # P2-E Phase 7D: readiness coherence
    readiness_coherence_violation_count = 0
    ready_with_missing_blockers_count = 0
    ready_with_non_ready_sections_count = 0
    readiness_missing_ready_blockers = Counter()
    readiness_non_ready_sections = Counter()
    readiness_by_setup_type: dict[str, Counter] = defaultdict(Counter)
    readiness_by_grade: dict[str, Counter] = defaultdict(Counter)
    # P2-E Phase 7E: aggregated POI handoff metrics
    agent_poi_handoff_sources = Counter()
    legacy_fallback_usage_count = 0
    p2a_selected_poi_consumed_count = 0
    p2a_candidate_fallback_count = 0
    p2a_missing_or_bounds_missing_count = 0

    for item in decisions:
        decision = str(item.get("decision", "UNKNOWN"))
        session = str(item.get("session", "UNKNOWN"))
        month = str(item.get("month", "UNKNOWN"))
        setup_type = str(item.get("setup_type", "UNKNOWN"))
        setup_grades[str(item.get("setup_grade") or "UNKNOWN")] += 1
        # Kasper scenario engine's OWN decision + grade (distinct from the
        # setup/OB grade above). Lets us see whether graded entry is producing
        # ENTER_ELIGIBLE / B / A tiers at Kasper level.
        kasper_decisions[str(item.get("kasper_decision_recommendation") or "NONE")] += 1
        kasper_grades[str(item.get("kasper_grade") or "NONE")] += 1
        session_grades[str(item.get("session_grade") or "UNKNOWN")] += 1
        risk_bands[str(item.get("risk_band") or "UNKNOWN")] += 1
        micro_templates[str(item.get("micro_template") or "UNKNOWN")] += 1
        poi_readiness[str(item.get("poi_execution_readiness") or "UNKNOWN")] += 1
        micro_readiness[str(item.get("micro_execution_readiness") or "UNKNOWN")] += 1
        timing_readiness[str(item.get("timing_execution_readiness") or "UNKNOWN")] += 1
        liquidity_readiness[str(item.get("liquidity_execution_readiness") or "UNKNOWN")] += 1
        risk_multiplier_buckets[_risk_bucket(item.get("risk_multiplier"))] += 1
        setup_types[setup_type] += 1
        # Phase 7A: setup taxonomy
        setup_families[str(item.get("setup_family") or "UNKNOWN")] += 1
        classification_reasons[str(item.get("setup_classification_reason") or "UNKNOWN")] += 1
        classification_confidence = item.get("setup_classification_confidence")
        if classification_confidence is not None:
            classification_confidences.append(float(classification_confidence))
        # Phase 7B: enter eligibility
        eligible = bool(item.get("enter_eligible"))
        eligibility_reason = str(item.get("enter_eligibility_reason") or "UNKNOWN")
        enter_eligibility_reasons[eligibility_reason] += 1
        if eligible:
            enter_eligible_count += 1
            enter_eligible_by_setup[setup_type] = enter_eligible_by_setup.get(setup_type, 0) + 1
            enter_eligible_by_grade[str(item.get("setup_grade") or "UNKNOWN")] = (
                enter_eligible_by_grade.get(str(item.get("setup_grade") or "UNKNOWN"), 0) + 1
            )
        for blocker in item.get("enter_eligibility_blockers", []) or []:
            enter_eligibility_blockers[str(blocker)] += 1
        risk_preview = item.get("risk_preview") or {}
        if risk_preview.get("allowed"):
            risk_preview_allowed_count += 1
        if float(risk_preview.get("risk_pct") or 0.0) > 0.0:
            risk_preview_positive_count += 1
        risk_preview_reasons[str(risk_preview.get("reason") or "UNKNOWN")] += 1
        # Phase 7C: risk multiplier mapping
        final_risk = float(item.get("risk_multiplier") or 0.0)
        effective_risk_pct_val = float(item.get("effective_risk_pct") or 0.0)
        grade_risk_val = item.get("grade_risk_multiplier")
        risk_allowed_val = bool(item.get("risk_allowed"))
        risk_reason_val = str(item.get("risk_reason") or "UNKNOWN")
        enter_eligible_val = bool(item.get("enter_eligible"))
        grade_str = str(item.get("setup_grade") or "UNKNOWN")
        setup_type_str = str(item.get("setup_type") or "UNKNOWN")
        if risk_allowed_val:
            risk_allowed_count += 1
        risk_reasons[risk_reason_val] += 1
        grade_risk_buckets[_risk_bucket(grade_risk_val)] += 1
        effective_risk_buckets[_risk_bucket(effective_risk_pct_val)] += 1
        if enter_eligible_val and final_risk > 0:
            enter_eligible_with_positive_risk_count += 1
        if enter_eligible_val and final_risk <= 0:
            enter_eligible_without_positive_risk_count += 1
        if not enter_eligible_val and final_risk > 0:
            risk_positive_but_not_enter_eligible_count += 1
        risk_by_grade[grade_str][_risk_bucket(final_risk)] += 1
        risk_by_setup_type[setup_type_str][_risk_bucket(final_risk)] += 1
        # end Phase 7C
        # Phase 7D: readiness coherence
        readiness_state_val = str(item.get("readiness_state") or "UNKNOWN")
        readiness_blockers = item.get("readiness_missing_ready_blockers", []) or []
        readiness_sections = item.get("readiness_non_ready_sections", {}) or {}
        readiness_by_setup_type[setup_type_str][readiness_state_val] += 1
        readiness_by_grade[grade_str][readiness_state_val] += 1
        for blocker in readiness_blockers:
            readiness_missing_ready_blockers[str(blocker)] += 1
        for section, state in readiness_sections.items():
            readiness_non_ready_sections[f"{section}:{state}"] += 1
        if readiness_state_val == "READY" and readiness_blockers:
            ready_with_missing_blockers_count += 1
        if readiness_state_val == "READY" and readiness_sections:
            ready_with_non_ready_sections_count += 1
        # end Phase 7D
        # Phase 7E: aggregated POI handoff metrics
        bundle = item.get("p1_evidence_bundle") or {}
        if isinstance(bundle, dict):
            raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
            liquidity = bundle.get("liquidity") if isinstance(bundle.get("liquidity"), dict) else {}
            micro = bundle.get("micro") if isinstance(bundle.get("micro"), dict) else {}
            timing = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}

            handoff_candidates = [
                ("agent3", liquidity.get("agent3_poi_handoff") if isinstance(liquidity.get("agent3_poi_handoff"), dict) else {}),
                ("agent4", timing.get("agent4_poi_handoff") if isinstance(timing.get("agent4_poi_handoff"), dict) else {}),
                ("agent5", micro.get("agent5_poi_handoff") if isinstance(micro.get("agent5_poi_handoff"), dict) else {}),
            ]

            for agent_label, handoff in handoff_candidates:
                source = str(handoff.get("source") or "UNKNOWN")
                agent_poi_handoff_sources[f"{agent_label}:{source}"] += 1

                if source == "LEGACY_AGENT2_FALLBACK":
                    legacy_fallback_usage_count += 1
                elif source == "P2A_SELECTED_POI":
                    p2a_selected_poi_consumed_count += 1
                elif source == "P2A_CANDIDATE_FALLBACK":
                    p2a_candidate_fallback_count += 1

                if handoff.get("failure_reason") == "NO_P2A_POI_OR_BOUNDS":
                    p2a_missing_or_bounds_missing_count += 1
        # end Phase 7E
        by_session[session][decision] += 1
        by_month[month][decision] += 1
        by_setup[setup_type][decision] += 1
        for reason in item.get("missing_conditions", []) or []:
            missing[str(reason)] += 1
        for warning in item.get("warnings", []) or []:
            warnings[str(warning)] += 1
        stage = item.get("pipeline_stage")
        if stage:
            stages[str(stage)] += 1
        funnel_stage = str(item.get("funnel_exit_stage") or stage or "UNKNOWN")
        funnel_reason = str(item.get("funnel_exit_reason") or item.get("primary_reason") or "UNKNOWN")
        funnel_exit_stages[funnel_stage] += 1
        funnel_exit_reasons[funnel_reason] += 1
        if item.get("funnel_near_miss") is True:
            near_miss_by_stage[funnel_stage] += 1
        best_scenarios[str(item.get("best_scenario") or "UNKNOWN")] += 1
        best_scenario_statuses[str(item.get("best_scenario_status") or "UNKNOWN")] += 1

    enter_count = decision_counts.get("ENTER", 0)
    trade_metrics = simulate_enter_outcomes(decisions, replay_candles_1m or [], replay_candles_15m or [])
    evidence = _evidence_metrics(decisions)
    opus = _opus_funnel_metrics(decisions, total, setup_types, funnel_exit_stages, funnel_exit_reasons, near_miss_by_stage, best_scenarios, best_scenario_statuses, enter_count, evidence)
    phase8 = _phase8_setup_candidate_metrics(decisions)
    phase10 = _phase10_poi_contract_metrics(decisions)
    phase11_micro = _micro_contract_metrics(decisions)
    phase12_synergy = _phase12_poi_micro_synergy_metrics(decisions)
    phase13_rejection = _phase13_poi_rejection_metrics(decisions)
    phase14_gates = _phase14_gate_decomposition_metrics(decisions)
    return {
        "symbol": symbol,
        "timeframes_used": [timeframe],
        "date_start": date_start,
        "date_end": date_end,
        "total_bars_or_events": total,
        "total_decisions": total,
        "ENTER_count": enter_count,
        "WAIT_count": decision_counts.get("WAIT", 0),
        "REJECT_count": decision_counts.get("REJECT", 0),
        "ENTER_rate": round(enter_count / total, 6) if total else 0.0,
        "main_blocking_stage_counts": dict(stages.most_common()),
        "main_missing_condition_counts": dict(missing.most_common(25)),
        "funnel_exit_stage_counts": dict(funnel_exit_stages.most_common()),
        "funnel_exit_reason_counts": dict(funnel_exit_reasons.most_common(25)),
        "top_missing_conditions": dict(missing.most_common(10)),
        "top_warning_conditions": dict(warnings.most_common(10)),
        "setup_type_distribution": dict(setup_types.most_common()),
        # P2-E Phase 7A: setup taxonomy metrics
        "setup_family_distribution": dict(setup_families.most_common()),
        "setup_classification_reason_distribution": dict(classification_reasons.most_common(25)),
        "setup_classification_confidence_avg": round(sum(classification_confidences) / len(classification_confidences), 6) if classification_confidences else None,
        "UNKNOWN_setup_type_count": setup_types.get("UNKNOWN", 0),
        "NO_SETUP_count": setup_types.get("NO_SETUP", 0),
        "classifiable_setup_count": sum(v for k, v in setup_types.items() if k not in {"UNKNOWN", "NO_SETUP"}),
        # P2-E Phase 7B: enter eligibility metrics
        "enter_eligible_count": enter_eligible_count,
        "enter_eligible_rate": round(enter_eligible_count / total, 6) if total else 0.0,
        "enter_eligibility_reason_distribution": dict(enter_eligibility_reasons.most_common(25)),
        "enter_eligibility_blocker_distribution": dict(enter_eligibility_blockers.most_common(25)),
        "enter_eligible_by_setup_type": enter_eligible_by_setup,
        "enter_eligible_by_grade": enter_eligible_by_grade,
        "risk_preview_allowed_count": risk_preview_allowed_count,
        "risk_preview_positive_count": risk_preview_positive_count,
        "risk_preview_reason_distribution": dict(risk_preview_reasons.most_common(25)),
        # P2-E Phase 7C: risk multiplier mapping
        "grade_risk_multiplier_distribution": dict(grade_risk_buckets.most_common()),
        "effective_risk_pct_distribution": dict(effective_risk_buckets.most_common()),
        "risk_allowed_count": risk_allowed_count,
        "risk_reason_distribution": dict(risk_reasons.most_common(25)),
        "enter_eligible_with_positive_risk_count": enter_eligible_with_positive_risk_count,
        "enter_eligible_without_positive_risk_count": enter_eligible_without_positive_risk_count,
        "risk_positive_but_not_enter_eligible_count": risk_positive_but_not_enter_eligible_count,
        "risk_by_grade": {grade: dict(buckets.most_common()) for grade, buckets in risk_by_grade.items()},
        "risk_by_setup_type": {st: dict(buckets.most_common()) for st, buckets in risk_by_setup_type.items()},
        **phase8,
        **phase10,
        **phase11_micro,
        **phase12_synergy,
        **phase13_rejection,
        **phase14_gates,
        # P2-E Phase 7D: readiness coherence
        "readiness_coherence_violation_count": ready_with_missing_blockers_count + ready_with_non_ready_sections_count,
        "READY_with_missing_ready_blockers_count": ready_with_missing_blockers_count,
        "READY_with_non_ready_sections_count": ready_with_non_ready_sections_count,
        "readiness_missing_ready_blocker_distribution": dict(readiness_missing_ready_blockers.most_common(25)),
        "readiness_non_ready_section_distribution": dict(readiness_non_ready_sections.most_common(25)),
        "readiness_state_by_setup_type": _counters_to_dict(readiness_by_setup_type),
        "readiness_state_by_grade": _counters_to_dict(readiness_by_grade),
        # P2-E Phase 7E: aggregated POI handoff metrics
        "agent_poi_handoff_source_distribution": dict(agent_poi_handoff_sources.most_common()),
        "legacy_fallback_usage_count": legacy_fallback_usage_count,
        "p2a_selected_poi_consumed_count": p2a_selected_poi_consumed_count,
        "p2a_candidate_fallback_count": p2a_candidate_fallback_count,
        "p2a_missing_or_bounds_missing_count": p2a_missing_or_bounds_missing_count,
        "evidence_coverage": evidence["coverage"],
        "htf_context_available_count": evidence["counts"]["htf_context_available"],
        "dol_available_count": evidence["counts"]["dol_available"],
        "liquidity_story_available_count": evidence["counts"]["liquidity_story_available"],
        "poi_available_count": evidence["counts"]["poi_available"],
        "premium_discount_available_count": evidence["counts"]["premium_discount_available"],
        "ote_available_count": evidence["counts"]["ote_available"],
        "micro_available_count": evidence["counts"]["micro_available"],
        "micro_trigger_count": evidence["counts"]["micro_trigger"],
        "evidence_quality_distribution": evidence["quality"],
        "decision_by_session": _counters_to_dict(by_session),
        "decision_by_month": _counters_to_dict(by_month),
        "decision_by_setup_type": _counters_to_dict(by_setup),
        "ENTER_by_session": {key: value.get("ENTER", 0) for key, value in _counters_to_dict(by_session).items()},
        "ENTER_by_setup_type": {key: value.get("ENTER", 0) for key, value in _counters_to_dict(by_setup).items()},
        "average_score": round(mean(scores), 4) if scores else 0.0,
        "average_confidence": round(mean(confidences), 4) if confidences else 0.0,
        "risk_multiplier_distribution": dict(risk_multiplier_buckets.most_common()),
        "average_risk_multiplier": _average_numeric(decisions, "risk_multiplier"),
        "average_final_risk_multiplier": _average_numeric(decisions, "final_risk_multiplier"),
        "setup_grade_distribution": dict(setup_grades.most_common()),
        "kasper_decision_distribution": dict(kasper_decisions.most_common()),
        "kasper_grade_distribution": dict(kasper_grades.most_common()),
        "kasper_graded_entry_active": _graded_entry_active(),
        "session_grade_distribution": dict(session_grades.most_common()),
        "risk_band_distribution": dict(risk_bands.most_common()),
        "timing_readiness_distribution": dict(timing_readiness.most_common()),
        "poi_readiness_distribution": dict(poi_readiness.most_common()),
        "micro_readiness_distribution": dict(micro_readiness.most_common()),
        "liquidity_readiness_distribution": dict(liquidity_readiness.most_common()),
        "micro_template_distribution": dict(micro_templates.most_common()),
        "average_timing_quality_score": _average_numeric(decisions, "timing_quality_score"),
        "average_liquidity_quality_score": _average_numeric(decisions, "liquidity_quality_score"),
        "average_poi_quality_score": _average_numeric(decisions, "poi_quality_score"),
        "trade_simulation": trade_metrics,
        "data_profile": data_profile,
        **opus,
}


def _average_numeric(decisions: list[dict[str, Any]], field: str) -> float:
    values = []
    for item in decisions:
        try:
            values.append(float(item.get(field)))
        except (TypeError, ValueError):
            continue
    return round(mean(values), 4) if values else 0.0


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


def _evidence_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [
        "htf_context_available",
        "dol_available",
        "liquidity_story_available",
        "poi_available",
        "premium_discount_available",
        "ote_available",
        "micro_available",
        "micro_trigger",
    ]
    total = len(decisions)
    counts = {field: sum(1 for item in decisions if item.get(field) is True) for field in fields}
    coverage = {field: round(count / total, 6) if total else 0.0 for field, count in counts.items()}
    quality: dict[str, dict[str, int]] = {}
    for agent in range(1, 6):
        key = f"agent_{agent}_quality"
        quality[f"agent_{agent}"] = dict(Counter(str(item.get(key) or "MISSING") for item in decisions).most_common())
    return {"counts": counts, "coverage": coverage, "quality": quality}


def _counters_to_dict(values: dict[str, Counter]) -> dict[str, dict[str, int]]:
    return {key: dict(counter) for key, counter in sorted(values.items())}


def _opus_funnel_metrics(
    decisions: list[dict[str, Any]],
    total: int,
    setup_types: Counter,
    funnel_exit_stages: Counter,
    funnel_exit_reasons: Counter,
    near_miss_by_stage: Counter,
    best_scenarios: Counter,
    best_scenario_statuses: Counter,
    enter_count: int,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    session_unknown = sum(1 for item in decisions if str(item.get("session") or "UNKNOWN") == "UNKNOWN")
    off_session = sum(1 for item in decisions if str(item.get("session") or "") == "OFF_SESSION")
    setup_unknown = setup_types.get("UNKNOWN", 0)
    setup_unknown_rate = round(setup_unknown / total, 6) if total else 0.0
    poi_reject_own = sum(1 for item in decisions if item.get("poi_status") == "POI_REJECT_OWN")
    poi_reject_inherited = sum(1 for item in decisions if item.get("poi_status") == "POI_REJECT_INHERITED")
    poi_watch_near_miss = sum(1 for item in decisions if item.get("poi_status") == "POI_WATCH_NEAR_MISS")
    poi_accept = sum(1 for item in decisions if item.get("poi_status") == "POI_ACCEPT")
    micro_reject_own = sum(1 for item in decisions if item.get("micro_status") == "MICRO_REJECT_OWN")
    micro_reject_inherited = sum(1 for item in decisions if item.get("micro_status") == "NOT_EVALUATED_INHERITED_POI_REJECT")
    micro_watch_near_miss = sum(1 for item in decisions if item.get("micro_status") == "MICRO_WATCH_NEAR_MISS")
    micro_confirmed = sum(1 for item in decisions if item.get("micro_status") == "MICRO_CONFIRMED")
    micro_evaluated = micro_reject_own + micro_watch_near_miss + micro_confirmed
    near_miss = sum(1 for item in decisions if item.get("funnel_near_miss") is True)
    missing_one = sum(1 for item in decisions if item.get("funnel_near_miss") is True and len(item.get("missing_conditions") or []) == 1)
    coverage = evidence.get("coverage", {})
    evidence_coverage_min = min((float(value) for value in coverage.values()), default=0.0)
    phase_8_blockers: list[str] = []
    if session_unknown > 0:
        phase_8_blockers.append("SESSION_CONTEXT_UNKNOWN_GT_0")
    if setup_unknown_rate >= 0.15:
        phase_8_blockers.append("SETUP_TYPE_UNKNOWN_RATE_GTE_15_PCT")
    if evidence_coverage_min < 0.95:
        phase_8_blockers.append("EVIDENCE_COVERAGE_LT_95_PCT")
    if enter_count < 30:
        phase_8_blockers.append("ENTER_LT_30")
    funnel_decoupled = True
    if poi_reject_own == micro_reject_own and poi_reject_own > 0 and micro_reject_inherited == 0:
        funnel_decoupled = False
        phase_8_blockers.append("FUNNEL_POI_MICRO_NOT_DECOUPLED")
    return {
        "session_context_unknown_count": session_unknown,
        "off_session_count": off_session,
        "session_off_session_non_tradable_count": sum(1 for item in decisions if item.get("funnel_exit_reason") == "SESSION_OFF_SESSION_NON_TRADABLE"),
        "setup_type_unknown_count": setup_unknown,
        "setup_type_unknown_rate": setup_unknown_rate,
        "funnel_decoupled": funnel_decoupled,
        "poi_reject_own_count": poi_reject_own,
        "poi_reject_inherited_count": poi_reject_inherited,
        "poi_watch_near_miss_count": poi_watch_near_miss,
        "poi_accept_count": poi_accept,
        "micro_reject_own_count": micro_reject_own,
        "micro_reject_inherited_count": micro_reject_inherited,
        "micro_watch_near_miss_count": micro_watch_near_miss,
        "micro_confirmed_count": micro_confirmed,
        "micro_evaluated_count": micro_evaluated,
        "micro_skipped_due_to_poi_count": micro_reject_inherited,
        "near_miss_count": near_miss,
        "near_miss_by_stage": dict(near_miss_by_stage.most_common()),
        "near_miss_missing_one_condition_count": missing_one,
        "near_miss_candidates": [
            {
                "timestamp": item.get("timestamp"),
                "setup_type": item.get("setup_type"),
                "stage": item.get("funnel_exit_stage"),
                "reason": item.get("funnel_exit_reason"),
                "best_scenario": item.get("best_scenario"),
            }
            for item in decisions
            if item.get("funnel_near_miss") is True
        ][:25],
        "scenario_valid_count": best_scenario_statuses.get("SCENARIO_VALID", 0),
        "scenario_near_miss_count": best_scenario_statuses.get("SCENARIO_NEAR_MISS", 0),
        "scenario_blocked_count": best_scenario_statuses.get("SCENARIO_BLOCKED", 0),
        "scenario_wait_count": best_scenario_statuses.get("SCENARIO_WAIT", 0),
        "scenario_tradable_count": sum(1 for item in decisions if str(item.get("best_scenario_status")) == "SCENARIO_VALID"),
        "best_scenario_distribution": dict(best_scenarios.most_common()),
        "phase_8_ready": not phase_8_blockers,
        "phase_8_blocking_reasons": phase_8_blockers,
    }


def build_p1_replay_metrics(
    decisions: list[dict[str, Any]],
    *,
    data_quality: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    total = len(decisions)
    decision_counts = Counter(str(item.get("decision") or "UNKNOWN") for item in decisions)
    grade_counts = Counter(str(item.get("setup_grade") or "UNKNOWN") for item in decisions)
    veto_counts = Counter(str(item.get("veto_code") or "NONE") for item in decisions)
    blocked_stage_counts = Counter(str(item.get("blocked_stage") or "NONE") for item in decisions)
    replay_invalid_count = sum(1 for item in decisions if item.get("replay_invalid") is True)
    hard_veto_count = sum(1 for item in decisions if item.get("hard_veto") is True)
    validation_error_count = sum(len(item.get("p1_evidence_validation_errors") or []) for item in decisions)
    scores_before = [_safe_float(item.get("score_before_veto")) for item in decisions]
    scores_after = [_safe_float(item.get("score_after_veto")) for item in decisions]
    risk_values = [_safe_float(item.get("risk_multiplier")) for item in decisions]
    connectivity = _p2a_connectivity_metrics(decisions)
    agent5_micro = _agent5_micro_metrics(decisions)
    agent3_liquidity = _agent3_liquidity_metrics(decisions)
    agent4_timing = _agent4_timing_metrics(decisions)
    readiness_states = Counter()
    readiness_reasons = Counter()
    section_readiness: dict[str, Counter] = defaultdict(Counter)
    for item in decisions:
        readiness_states[str(item.get("readiness_state") or "UNKNOWN")] += 1
        readiness_reasons[str(item.get("readiness_reason") or "UNKNOWN")] += 1
        by_section = item.get("readiness_by_section") or {}
        if isinstance(by_section, dict):
            for section, state in by_section.items():
                section_readiness[str(section)][str(state or "UNKNOWN")] += 1
    phase8 = _phase8_setup_candidate_metrics(decisions)
    phase10 = _phase10_poi_contract_metrics(decisions)
    phase11_micro = _micro_contract_metrics(decisions)
    return {
        "total_decisions": total,
        "decision_counts": dict(decision_counts.most_common()),
        "setup_grade_distribution": dict(grade_counts.most_common()),
        "veto_breakdown": dict(veto_counts.most_common()),
        "blocked_stage_breakdown": dict(blocked_stage_counts.most_common()),
        "hard_veto_count": hard_veto_count,
        "replay_invalid_count": replay_invalid_count,
        "evidence_validation_error_count": validation_error_count,
        "score_before_veto_avg": _avg(scores_before),
        "score_after_veto_avg": _avg(scores_after),
        "risk_multiplier_avg": _avg(risk_values),
        "determinism_hash": _decision_hash(decisions),
        "p2a_connectivity": connectivity,
        "records_with_any_poi": connectivity["records_with_any_poi"],
        "records_with_selected_poi": connectivity["records_with_selected_poi"],
        "records_with_price_bounds": connectivity["records_with_price_bounds"],
        "poi_readiness_distribution": connectivity["poi_readiness_distribution"],
        "poi_type_distribution": connectivity["poi_type_distribution"],
        "records_with_poi_semantic_available": connectivity["records_with_poi_semantic_available"],
        "records_with_poi_present_but_unexecutable": connectivity["records_with_poi_present_but_unexecutable"],
        "records_with_poi_present_legacy_rejected": connectivity["records_with_poi_present_legacy_rejected"],
        "records_with_poi_bounds_missing": connectivity["records_with_poi_bounds_missing"],
        "poi_semantic_status_distribution": connectivity["poi_semantic_status_distribution"],
        "poi_failure_class_distribution": connectivity["poi_failure_class_distribution"],
        "records_with_agent5_poi_consumed": agent5_micro["records_with_agent5_poi_consumed"],
        "agent5_poi_handoff_source_distribution": agent5_micro["agent5_poi_handoff_source_distribution"],
        "agent5_micro_handoff_status_distribution": agent5_micro["agent5_micro_handoff_status_distribution"],
        "agent5_readiness_distribution": agent5_micro["agent5_readiness_distribution"],
        "agent5_readiness_reason_distribution": agent5_micro["agent5_readiness_reason_distribution"],
        "records_with_micro_ready": agent5_micro["records_with_micro_ready"],
        "records_with_micro_wait_for_trigger": agent5_micro["records_with_micro_wait_for_trigger"],
        "records_with_micro_unavailable": agent5_micro["records_with_micro_unavailable"],
        "records_with_agent3_poi_consumed": agent3_liquidity["records_with_agent3_poi_consumed"],
        "agent3_poi_handoff_source_distribution": agent3_liquidity["agent3_poi_handoff_source_distribution"],
        "agent3_liquidity_handoff_status_distribution": agent3_liquidity["agent3_liquidity_handoff_status_distribution"],
        "agent3_readiness_distribution": agent3_liquidity["agent3_readiness_distribution"],
        "agent3_readiness_reason_distribution": agent3_liquidity["agent3_readiness_reason_distribution"],
        "liquidity_state_distribution": agent3_liquidity["liquidity_state_distribution"],
        "liquidity_reason_distribution": agent3_liquidity["liquidity_reason_distribution"],
        "records_with_agent4_poi_consumed": agent4_timing["records_with_agent4_poi_consumed"],
        "agent4_poi_handoff_source_distribution": agent4_timing["agent4_poi_handoff_source_distribution"],
        "agent4_ote_handoff_status_distribution": agent4_timing["agent4_ote_handoff_status_distribution"],
        "agent4_readiness_distribution": agent4_timing["agent4_readiness_distribution"],
        "agent4_readiness_reason_distribution": agent4_timing["agent4_readiness_reason_distribution"],
        "ote_state_distribution": agent4_timing["ote_state_distribution"],
        "ote_reason_distribution": agent4_timing["ote_reason_distribution"],
        "readiness_state_distribution": dict(readiness_states.most_common()),
        "readiness_reason_distribution": dict(readiness_reasons.most_common(25)),
        "readiness_by_section_distribution": {
            section: dict(counter.most_common())
            for section, counter in section_readiness.items()
        },
        **phase8,
        **phase10,
        **phase11_micro,
        "decision_distribution_non_degenerate": len([key for key, value in decision_counts.items() if value > 0]) >= 2,
        "data_quality": data_quality or {},
        "errors_count": len(errors or []),
        "errors": list(errors or [])[:20],
        "reporting_complete": True,
    }


def _phase8_setup_candidate_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
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
    light_setup_count = 0
    strict_setup_count = 0
    light_enter_eligible_count = 0
    strict_enter_eligible_count = 0
    near_miss_scanned_count = 0
    near_miss_with_candidates_count = 0
    risk_multiplier_positive = 0

    for decision in decisions:
        setup_type = str(decision.get("setup_type") or "UNKNOWN")
        action = str(decision.get("decision") or decision.get("action") or "UNKNOWN")
        grade = str(decision.get("setup_grade") or "UNKNOWN")
        candidates = decision.get("setup_candidates") or []
        best = decision.get("best_setup_candidate") or {}

        decision_counts_by_setup[setup_type][action] += 1
        grade_by_setup[setup_type][grade] += 1
        readiness_reason_by_setup[setup_type][str(decision.get("readiness_reason") or "UNKNOWN")] += 1
        risk_reason_by_setup[setup_type][str(decision.get("risk_reason") or "UNKNOWN")] += 1
        for blocker in decision.get("enter_eligibility_blockers") or []:
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

        enter_eligible = bool(decision.get("enter_eligible"))
        if enter_eligible:
            enter_eligible_by_setup[setup_type] += 1
            if setup_type in light_types:
                light_enter_eligible_count += 1
            if setup_type in strict_types:
                strict_enter_eligible_count += 1

        risk_value = _safe_float(decision.get("risk_multiplier"))
        if risk_value > 0.0:
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
            near_missing = decision.get("near_miss_missing_signals")
            if not near_missing:
                signals = decision.get("setup_signal_inventory") or {}
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
        "enter_eligibility_blockers_by_setup_type": _counters_to_dict(blockers_by_setup),
        "readiness_reason_by_setup_type": _counters_to_dict(readiness_reason_by_setup),
        "risk_reason_by_setup_type": _counters_to_dict(risk_reason_by_setup),
        "decision_counts_by_setup_type": _counters_to_dict(decision_counts_by_setup),
        "grade_distribution_by_setup_type": _counters_to_dict(grade_by_setup),
    }


def _phase10_poi_contract_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
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

        suspect_reasons = _poi_suspect_reasons(decision, status, contradictions, quality_breakdown)
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


def _micro_contract_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """P2-E Phase 11: micro contract / evidence metrics.

    Tracks micro_contract_status, reasons, missing/present fields,
    near-miss micro count by setup type, and blockers by micro status.
    """
    status_distribution = Counter()
    reason_distribution = Counter()
    missing_field_distribution: Counter = Counter()
    present_field_distribution: Counter = Counter()
    missing_data_count = 0
    near_miss_micro_by_setup: Counter = Counter()
    blockers_by_micro_status: dict[str, Counter] = defaultdict(Counter)

    for decision in decisions:
        status = str(decision.get("micro_contract_status") or "UNKNOWN")
        reason = str(decision.get("micro_contract_reason") or "UNKNOWN")
        missing = decision.get("micro_contract_missing_fields") or []
        present = decision.get("micro_contract_present_fields") or []
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

        if status == "MICRO_MISSING_DATA":
            missing_data_count += 1

        if status == "MICRO_WAITING_TRIGGER":
            near_miss_micro_by_setup[setup_type] += 1

        for blocker in (blockers if isinstance(blockers, list) else []):
            blockers_by_micro_status[status][str(blocker)] += 1

    return {
        "micro_contract_status_distribution": dict(status_distribution.most_common()),
        "micro_contract_reason_distribution": dict(reason_distribution.most_common(25)),
        "micro_contract_missing_field_distribution": dict(missing_field_distribution.most_common(25)),
        "micro_contract_present_field_distribution": dict(present_field_distribution.most_common(25)),
        "micro_missing_data_count": missing_data_count,
        "near_miss_micro_count": sum(near_miss_micro_by_setup.values()),
        "near_miss_micro_count_by_setup": dict(near_miss_micro_by_setup.most_common()),
        "enter_eligibility_blockers_by_micro_contract_status": _counters_to_dict(blockers_by_micro_status),
    }


def _phase12_poi_micro_synergy_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
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
        synergy = _synergy_bool(decision)
        status = str(decision.get("poi_micro_synergy_status") or _synergy_payload(decision).get("status") or "UNKNOWN")
        reason = str(decision.get("poi_micro_reason") or _synergy_payload(decision).get("reason") or "UNKNOWN")
        micro_confirmed = _micro_confirmed(decision)
        micro_inside = _micro_inside(decision)
        micro_outside = _micro_outside(decision)

        status_distribution[status] += 1
        reason_distribution[reason] += 1

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
            elif not _has_selected_poi(decision):
                confirmed_without_poi += 1

        if decision.get("enter_eligible"):
            enter_eligible_by_setup[setup_type] += 1
        if _safe_float(decision.get("risk_multiplier")) > 0.0:
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


def _phase13_poi_rejection_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    rejection_code_distribution: Counter = Counter()
    rejection_severity_distribution: Counter = Counter()
    rejection_recoverable_count = 0
    rejection_fatal_count = 0
    micro_confirmed_rejected_poi = 0
    micro_confirmed_recoverable_poi = 0
    sweep_rejected_count = 0
    synergy_by_rejection_code: dict[str, Counter] = defaultdict(Counter)

    for decision in decisions:
        code = str(decision.get("poi_rejection_code") or _poi_rejection_payload(decision).get("code") or "UNKNOWN")
        severity = str(
            decision.get("poi_rejection_severity")
            or _poi_rejection_payload(decision).get("severity")
            or "UNKNOWN"
        )
        recoverable = bool(
            decision.get("poi_rejection_recoverable")
            or _poi_rejection_payload(decision).get("recoverable")
        )
        fatal = bool(
            decision.get("poi_rejection_fatal")
            or _poi_rejection_payload(decision).get("fatal")
        )
        micro_confirmed = _micro_confirmed(decision)
        synergy = _synergy_bool(decision)

        rejection_code_distribution[code] += 1
        rejection_severity_distribution[severity] += 1
        if recoverable:
            rejection_recoverable_count += 1
        if fatal:
            rejection_fatal_count += 1
        if micro_confirmed and code not in {"POI_REJECTION_NONE", "UNKNOWN"}:
            micro_confirmed_rejected_poi += 1
        if micro_confirmed and recoverable:
            micro_confirmed_recoverable_poi += 1
        if str(decision.get("setup_type") or "UNKNOWN") == "SWEEP_REVERSAL" and code not in {
            "POI_REJECTION_NONE",
            "UNKNOWN",
        }:
            sweep_rejected_count += 1
        synergy_by_rejection_code[code]["synergy_true" if synergy else "synergy_false"] += 1

    return {
        "poi_rejection_code_distribution": dict(rejection_code_distribution.most_common()),
        "poi_rejection_severity_distribution": dict(rejection_severity_distribution.most_common()),
        "poi_rejection_recoverable_count": rejection_recoverable_count,
        "poi_rejection_fatal_count": rejection_fatal_count,
        "micro_confirmed_rejected_poi_count": micro_confirmed_rejected_poi,
        "micro_confirmed_recoverable_poi_count": micro_confirmed_recoverable_poi,
        "sweep_rejected_count": sweep_rejected_count,
        "poi_micro_synergy_by_rejection_code": {
            key: dict(value.most_common())
            for key, value in sorted(synergy_by_rejection_code.items())
        },
    }


def _phase14_gate_decomposition_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    primary: Counter = Counter()
    blockers: Counter = Counter()
    synergy_true_primary: Counter = Counter()
    synergy_true_setup: Counter = Counter()
    synergy_true_enter: Counter = Counter()
    risk_reason_by_primary: dict[str, Counter] = defaultdict(Counter)

    for decision in decisions:
        gate = _gate_payload(decision)
        primary_blocker = str(gate.get("primary_blocker") or "UNKNOWN")
        primary[primary_blocker] += 1

        gate_blockers = gate.get("blockers")
        if not isinstance(gate_blockers, list):
            gate_blockers = []
        for blocker in gate_blockers:
            blockers[str(blocker)] += 1

        if _synergy_bool(decision):
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


def _gate_payload(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("gate_decomposition")
    if isinstance(payload, dict) and payload:
        return payload
    return evaluate_readiness_risk_gate(decision).to_dict()


def _synergy_payload(decision: dict[str, Any]) -> dict[str, Any]:
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


def _poi_rejection_payload(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("poi_rejection")
    if isinstance(payload, dict) and payload:
        return payload
    bundle = decision.get("p1_evidence_bundle") or {}
    if isinstance(bundle, dict):
        poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
        if isinstance(poi.get("poi_rejection"), dict):
            return poi.get("poi_rejection") or {}
    synergy = _synergy_payload(decision)
    audit = synergy.get("audit") if isinstance(synergy.get("audit"), dict) else {}
    rejection = audit.get("poi_rejection") if isinstance(audit.get("poi_rejection"), dict) else {}
    return rejection if isinstance(rejection, dict) else {}


def _synergy_bool(decision: dict[str, Any]) -> bool:
    payload = _synergy_payload(decision)
    return bool(decision.get("poi_micro_synergy") or payload.get("synergy"))


def _micro_confirmed(decision: dict[str, Any]) -> bool:
    return bool(
        decision.get("micro_confirmed")
        or decision.get("micro_contract_confirmed")
        or str(decision.get("micro_contract_status") or "") == "MICRO_CONFIRMED"
    )


def _micro_inside(decision: dict[str, Any]) -> bool:
    payload = _synergy_payload(decision)
    evidence = decision.get("micro_evidence") if isinstance(decision.get("micro_evidence"), dict) else {}
    return bool(
        decision.get("micro_inside_poi")
        or payload.get("micro_inside_poi")
        or decision.get("price_in_agent2_poi")
        or decision.get("trigger_inside_poi")
        or evidence.get("price_in_agent2_poi")
        or evidence.get("trigger_inside_poi")
    )


def _micro_outside(decision: dict[str, Any]) -> bool:
    payload = _synergy_payload(decision)
    evidence = decision.get("micro_evidence") if isinstance(decision.get("micro_evidence"), dict) else {}
    return bool(
        decision.get("micro_outside_poi")
        or payload.get("micro_outside_poi")
        or decision.get("trigger_outside_poi")
        or decision.get("micro_contract_outside_poi")
        or evidence.get("trigger_outside_poi")
    )


def _has_selected_poi(decision: dict[str, Any]) -> bool:
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


def _poi_suspect_reasons(
    decision: dict[str, Any],
    status: str,
    contradictions: list[Any],
    quality_breakdown: dict[str, Any],
) -> list[str]:
    del status
    reasons = [str(item) for item in contradictions if item]
    bundle = decision.get("p1_evidence_bundle") or {}
    poi = bundle.get("poi") if isinstance(bundle, dict) and isinstance(bundle.get("poi"), dict) else {}
    semantic = str(poi.get("poi_semantic_status") or "").upper()
    failure = str(poi.get("poi_failure_class") or "").upper()
    final_score = _float_or_none(
        quality_breakdown.get("final_poi_quality_score")
        if isinstance(quality_breakdown, dict)
        else None
    )
    if final_score is None:
        final_score = _float_or_none(poi.get("final_poi_quality_score"))
    if final_score is None:
        final_score = _float_or_none(poi.get("poi_quality_score"))
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


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _agent4_timing_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    handoff_sources = Counter()
    handoff_statuses = Counter()
    readiness = Counter()
    readiness_reasons = Counter()
    ote_states = Counter()
    ote_reasons = Counter()
    consumed = 0
    for item in decisions:
        bundle = item.get("p1_evidence_bundle") or {}
        timing = {}
        if isinstance(bundle, dict):
            raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
            timing = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}
            if not timing:
                context = bundle.get("context") if isinstance(bundle.get("context"), dict) else {}
                timing = context if isinstance(context, dict) else {}
        if not isinstance(timing, dict):
            timing = {}
        consumed_poi = timing.get("agent4_consumed_poi") if isinstance(timing.get("agent4_consumed_poi"), dict) else {}
        handoff = timing.get("agent4_poi_handoff") if isinstance(timing.get("agent4_poi_handoff"), dict) else {}
        if consumed_poi.get("present") is True:
            consumed += 1
        handoff_sources[str(handoff.get("source") or "UNKNOWN")] += 1
        handoff_statuses[str(timing.get("ote_handoff_status") or "UNKNOWN")] += 1
        readiness[str(timing.get("readiness_state") or timing.get("execution_readiness") or "UNKNOWN")] += 1
        readiness_reasons[str(timing.get("readiness_reason") or "UNKNOWN")] += 1
        ote_states["IN_OTE" if timing.get("in_ote") is True else "WAITING_OTE"] += 1
        ote_reasons[str(timing.get("reason") or timing.get("readiness_reason") or "UNKNOWN")] += 1
    return {
        "records_with_agent4_poi_consumed": consumed,
        "agent4_poi_handoff_source_distribution": dict(handoff_sources.most_common()),
        "agent4_ote_handoff_status_distribution": dict(handoff_statuses.most_common()),
        "agent4_readiness_distribution": dict(readiness.most_common()),
        "agent4_readiness_reason_distribution": dict(readiness_reasons.most_common(25)),
        "ote_state_distribution": dict(ote_states.most_common()),
        "ote_reason_distribution": dict(ote_reasons.most_common(25)),
    }


def _agent3_liquidity_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    handoff_sources = Counter()
    handoff_statuses = Counter()
    readiness = Counter()
    readiness_reasons = Counter()
    liquidity_states = Counter()
    liquidity_reasons = Counter()
    consumed = 0
    for item in decisions:
        bundle = item.get("p1_evidence_bundle") or {}
        liquidity = bundle.get("liquidity") if isinstance(bundle, dict) else {}
        if not isinstance(liquidity, dict):
            liquidity = {}
        consumed_poi = liquidity.get("agent3_consumed_poi") if isinstance(liquidity.get("agent3_consumed_poi"), dict) else {}
        handoff = liquidity.get("agent3_poi_handoff") if isinstance(liquidity.get("agent3_poi_handoff"), dict) else {}
        if consumed_poi.get("present") is True:
            consumed += 1
        handoff_sources[str(handoff.get("source") or "UNKNOWN")] += 1
        handoff_statuses[str(liquidity.get("liquidity_handoff_status") or "UNKNOWN")] += 1
        readiness[str(liquidity.get("readiness_state") or liquidity.get("execution_readiness") or "UNKNOWN")] += 1
        readiness_reasons[str(liquidity.get("readiness_reason") or "UNKNOWN")] += 1
        liquidity_states[str(liquidity.get("liquidity_state") or "UNKNOWN")] += 1
        liquidity_reasons[str(liquidity.get("reason") or "UNKNOWN")] += 1
    return {
        "records_with_agent3_poi_consumed": consumed,
        "agent3_poi_handoff_source_distribution": dict(handoff_sources.most_common()),
        "agent3_liquidity_handoff_status_distribution": dict(handoff_statuses.most_common()),
        "agent3_readiness_distribution": dict(readiness.most_common()),
        "agent3_readiness_reason_distribution": dict(readiness_reasons.most_common(25)),
        "liquidity_state_distribution": dict(liquidity_states.most_common()),
        "liquidity_reason_distribution": dict(liquidity_reasons.most_common(25)),
    }


def _agent5_micro_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    handoff_sources = Counter()
    handoff_statuses = Counter()
    readiness = Counter()
    readiness_reasons = Counter()
    consumed = 0
    ready = 0
    wait_for_trigger = 0
    unavailable = 0
    for item in decisions:
        bundle = item.get("p1_evidence_bundle") or {}
        micro = bundle.get("micro") if isinstance(bundle, dict) else {}
        if not isinstance(micro, dict):
            micro = {}
        consumed_poi = micro.get("agent5_consumed_poi") if isinstance(micro.get("agent5_consumed_poi"), dict) else {}
        handoff = micro.get("agent5_poi_handoff") if isinstance(micro.get("agent5_poi_handoff"), dict) else {}
        if consumed_poi.get("present") is True:
            consumed += 1
        source = str(handoff.get("source") or "UNKNOWN")
        status = str(micro.get("micro_handoff_status") or "UNKNOWN")
        state = str(micro.get("readiness_state") or micro.get("execution_readiness") or "UNKNOWN")
        reason = str(micro.get("readiness_reason") or "UNKNOWN")
        handoff_sources[source] += 1
        handoff_statuses[status] += 1
        readiness[state] += 1
        readiness_reasons[reason] += 1
        if state == "READY":
            ready += 1
        elif state in {"WAIT_FOR_TRIGGER", "WAITING_TRIGGER"}:
            wait_for_trigger += 1
        elif state == "UNAVAILABLE":
            unavailable += 1
    return {
        "records_with_agent5_poi_consumed": consumed,
        "agent5_poi_handoff_source_distribution": dict(handoff_sources.most_common()),
        "agent5_micro_handoff_status_distribution": dict(handoff_statuses.most_common()),
        "agent5_readiness_distribution": dict(readiness.most_common()),
        "agent5_readiness_reason_distribution": dict(readiness_reasons.most_common(25)),
        "records_with_micro_ready": ready,
        "records_with_micro_wait_for_trigger": wait_for_trigger,
        "records_with_micro_unavailable": unavailable,
    }


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _decision_hash(decisions: list[dict[str, Any]]) -> str:
    stable = json.dumps(decisions, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _p2a_connectivity_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter
    status = Counter()
    readiness = Counter()
    poi_type = Counter()
    lifecycle = Counter()
    semantic_status = Counter()
    failure_class = Counter()
    any_poi = 0
    selected = 0
    bounds = 0
    semantic_available = 0
    present_unexecutable = 0
    present_legacy_rejected = 0
    bounds_missing = 0
    candidates_total = 0
    missing_reasons = Counter()
    for item in decisions:
        bundle = item.get("p1_evidence_bundle") or {}
        poi = bundle.get("poi") if isinstance(bundle, dict) else {}
        if not isinstance(poi, dict):
            poi = {}
        audit = poi.get("connectivity_audit") if isinstance(poi.get("connectivity_audit"), dict) else {}
        if poi.get("poi_available") or audit.get("agent2_has_any_zone"):
            any_poi += 1
        if poi.get("selected_poi") or poi.get("selected_poi_present") or audit.get("selected_poi_present"):
            selected += 1
        if poi.get("price_bounds") or poi.get("has_price_bounds") or audit.get("poi_bounds_present"):
            bounds += 1
        if poi.get("poi_semantic_available") is True:
            semantic_available += 1
        poi_semantic = str(poi.get("poi_semantic_status") or "UNKNOWN")
        poi_failure = str(poi.get("poi_failure_class") or "UNKNOWN")
        semantic_status[poi_semantic] += 1
        failure_class[poi_failure] += 1
        if poi_semantic in {"POI_PRESENT_UNEXECUTABLE", "POI_PRESENT_LOW_CONFIDENCE", "POI_PRESENT_WAITING_TRIGGER"}:
            present_unexecutable += 1
        if poi_failure == "POI_PRESENT_LEGACY_REJECTED":
            present_legacy_rejected += 1
        if poi_semantic == "POI_PRESENT_INVALID_BOUNDS" or poi_failure == "POI_PRESENT_NO_BOUNDS":
            bounds_missing += 1
        candidates = poi.get("poi_candidates")
        if isinstance(candidates, list):
            candidates_total += len(candidates)
        status[str(poi.get("status") or poi.get("schema_version") or "UNKNOWN")] += 1
        readiness[str(poi.get("execution_readiness") or "UNKNOWN")] += 1
        poi_type[str(poi.get("poi_type_normalized") or poi.get("poi_type") or "UNKNOWN")] += 1
        lifecycle[str(poi.get("lifecycle_normalized") or poi.get("lifecycle_state") or "UNKNOWN")] += 1
        for reason in poi.get("missing_evidence") or []:
            missing_reasons[str(reason)] += 1
    return {
        "records_with_any_poi": any_poi,
        "records_with_selected_poi": selected,
        "records_with_price_bounds": bounds,
        "total_poi_candidates": candidates_total,
        "poi_readiness_distribution": dict(readiness.most_common()),
        "poi_type_distribution": dict(poi_type.most_common()),
        "poi_lifecycle_distribution": dict(lifecycle.most_common()),
        "poi_missing_evidence_distribution": dict(missing_reasons.most_common()),
        "records_with_poi_semantic_available": semantic_available,
        "records_with_poi_present_but_unexecutable": present_unexecutable,
        "records_with_poi_present_legacy_rejected": present_legacy_rejected,
        "records_with_poi_bounds_missing": bounds_missing,
        "poi_semantic_status_distribution": dict(semantic_status.most_common()),
        "poi_failure_class_distribution": dict(failure_class.most_common()),
    }
