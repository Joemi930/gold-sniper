from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any


NEAR_MISS_DECISIONS = {"WATCH_ONLY", "WAIT_FOR_TRIGGER"}
NEAR_MISS_GRADES = {"B", "C"}
NON_DIAGNOSTIC_HARD_BLOCKERS = {
    "HARD_VETO_OR_REPLAY_INVALID",
    "NEWS_NOT_SAFE_FOR_ENTER",
    "SESSION_NOT_ALLOWED",
    "SESSION_CONTEXT_MISSING",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit micro readiness on SWEEP_REVERSAL near-misses.")
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decisions = _load_decisions(args.decisions)
    report = diagnose_micro_readiness(decisions, top=args.top)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "true_near_miss_count": report["true_near_miss_count"],
        "what_if_poi_and_micro_ready_enter_eligible_count": report["what_if_poi_and_micro_ready_enter_eligible_count"],
    }, ensure_ascii=False))
    return 0


def diagnose_micro_readiness(decisions: list[dict[str, Any]], *, top: int = 50) -> dict[str, Any]:
    original = deepcopy(decisions)
    rows: list[dict[str, Any]] = []
    micro_state_distribution: Counter[str] = Counter()
    micro_reason_distribution: Counter[str] = Counter()
    trigger_presence_distribution: Counter[str] = Counter()
    trigger_location_distribution: Counter[str] = Counter()
    agent5_handoff_source_distribution: Counter[str] = Counter()
    micro_missing_field_distribution: Counter[str] = Counter()
    remaining_after_poi_micro: Counter[str] = Counter()
    what_if_poi_ready_count = 0
    what_if_micro_ready_count = 0
    what_if_enter_eligible_count = 0

    for decision in decisions:
        if not is_true_micro_near_miss(decision):
            continue
        micro = _micro_payload(decision)
        row = _compact_case(decision, micro)
        rows.append(row)

        micro_state_distribution[str(row["micro_readiness_state"] or "UNKNOWN")] += 1
        micro_reason_distribution[str(row["micro_readiness_reason"] or "UNKNOWN")] += 1
        trigger_presence_distribution[row["trigger_presence"]] += 1
        trigger_location_distribution[row["trigger_location"]] += 1
        agent5_handoff_source_distribution[row["agent5_poi_handoff_source"]] += 1
        for missing in row["micro_missing_fields"]:
            micro_missing_field_distribution[missing] += 1

        what_if = row["DIAGNOSTIC_ONLY_POI_MICRO_COUNTERFACTUAL"]
        if "SECTION_NOT_READY:poi" in row["actual_blockers"]:
            what_if_poi_ready_count += 1
        if str(row["micro_readiness_state"]).upper() != "READY":
            what_if_micro_ready_count += 1
        if what_if["would_be_enter_eligible_if_poi_micro_ready"]:
            what_if_enter_eligible_count += 1
        remaining = what_if["if_poi_and_micro_ready_remaining_blockers"] or ["DIAGNOSTIC_NO_REMAINING_BLOCKERS"]
        for blocker in remaining:
            remaining_after_poi_micro[str(blocker)] += 1

    # Guard the tool contract used by tests: analysis must be read-only.
    assert decisions == original

    rows.sort(key=_case_rank, reverse=True)
    return {
        "true_near_miss_count": len(rows),
        "micro_state_distribution": dict(micro_state_distribution.most_common()),
        "micro_reason_distribution": dict(micro_reason_distribution.most_common()),
        "trigger_presence_distribution": dict(trigger_presence_distribution.most_common()),
        "trigger_location_distribution": dict(trigger_location_distribution.most_common()),
        "agent5_handoff_source_distribution": dict(agent5_handoff_source_distribution.most_common()),
        "micro_missing_field_distribution": dict(micro_missing_field_distribution.most_common()),
        "what_if_poi_ready_count": what_if_poi_ready_count,
        "what_if_micro_ready_count": what_if_micro_ready_count,
        "what_if_poi_and_micro_ready_enter_eligible_count": what_if_enter_eligible_count,
        "remaining_blockers_after_poi_micro_ready": dict(remaining_after_poi_micro.most_common()),
        "top_cases": rows[: max(int(top), 0)],
    }


def is_true_micro_near_miss(decision: dict[str, Any]) -> bool:
    if str(decision.get("setup_type") or "UNKNOWN") != "SWEEP_REVERSAL":
        return False
    if str(decision.get("setup_grade") or "D") not in NEAR_MISS_GRADES:
        return False
    if str(decision.get("decision") or "UNKNOWN") not in NEAR_MISS_DECISIONS:
        return False
    if decision.get("hard_veto") is True or decision.get("replay_invalid") is True:
        return False
    blockers = set(str(item) for item in (decision.get("enter_eligibility_blockers") or []))
    if blockers and blockers.issubset(NON_DIAGNOSTIC_HARD_BLOCKERS):
        return False
    return True


def _compact_case(decision: dict[str, Any], micro: dict[str, Any]) -> dict[str, Any]:
    state = str(
        micro.get("readiness_state")
        or micro.get("execution_readiness")
        or decision.get("micro_execution_readiness")
        or "UNKNOWN"
    )
    reason = str(
        micro.get("readiness_reason")
        or decision.get("micro_readiness_reason")
        or "UNKNOWN"
    )
    handoff = micro.get("agent5_poi_handoff") if isinstance(micro.get("agent5_poi_handoff"), dict) else {}
    missing_fields = _missing_micro_fields(micro)
    trigger_presence = _trigger_presence(micro)
    trigger_location = _trigger_location(micro)
    blockers = [str(item) for item in (decision.get("enter_eligibility_blockers") or [])]
    why_not_ready = _why_not_ready(micro, state, reason, missing_fields)
    return {
        "timestamp": decision.get("timestamp"),
        "setup_type": decision.get("setup_type"),
        "grade": decision.get("setup_grade"),
        "decision": decision.get("decision"),
        "micro_readiness_state": state,
        "micro_readiness_reason": reason,
        "sweep_1m_confirmed": _optional_bool(micro.get("sweep_1m_confirmed") or micro.get("sweep_detected")),
        "choch_detected": _optional_bool(micro.get("choch_detected")),
        "trigger_inside_poi": _optional_bool(micro.get("trigger_inside_poi") or micro.get("price_in_agent2_poi")),
        "price_in_agent2_poi": _optional_bool(micro.get("price_in_agent2_poi") or micro.get("trigger_inside_poi")),
        "candles_1m_count": micro.get("candles_1m_count"),
        "agent5_poi_handoff_source": str(handoff.get("source") or "UNKNOWN"),
        "trigger_presence": trigger_presence,
        "trigger_location": trigger_location,
        "micro_missing_fields": missing_fields,
        "why_not_ready": why_not_ready,
        "actual_blockers": blockers,
        "DIAGNOSTIC_ONLY_POI_MICRO_COUNTERFACTUAL": _what_if(decision, blockers),
    }


def _what_if(decision: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    actual = list(blockers)
    if_poi = _drop_blockers(actual, {"SECTION_NOT_READY:poi"})
    if_micro = _drop_blockers(actual, {"SECTION_NOT_READY:micro"})
    if_both = _drop_blockers(actual, {"SECTION_NOT_READY:poi", "SECTION_NOT_READY:micro"})
    if_both = _drop_global_if_no_section_blockers(if_both)
    would_be_enter = (
        not if_both
        and str(decision.get("setup_grade") or "D") in NEAR_MISS_GRADES
        and decision.get("hard_veto") is not True
        and decision.get("replay_invalid") is not True
    )
    return {
        "actual_blockers": actual,
        "if_poi_ready_remaining_blockers": if_poi,
        "if_micro_ready_remaining_blockers": if_micro,
        "if_poi_and_micro_ready_remaining_blockers": if_both,
        "would_be_enter_eligible_if_poi_micro_ready": bool(would_be_enter),
    }


def _drop_blockers(blockers: list[str], to_drop: set[str]) -> list[str]:
    return [blocker for blocker in blockers if blocker not in to_drop]


def _drop_global_if_no_section_blockers(blockers: list[str]) -> list[str]:
    remaining = [blocker for blocker in blockers if blocker != "GLOBAL_READINESS_NOT_READY"]
    if any(blocker.startswith("SECTION_NOT_READY:") for blocker in remaining):
        return blockers
    return remaining


def _why_not_ready(
    micro: dict[str, Any],
    state: str,
    reason: str,
    missing_fields: list[str],
) -> str:
    reason_upper = reason.upper()
    if state == "READY":
        return "MICRO_READY"
    if "INSUFFICIENT_1M_CANDLES" in reason_upper or micro.get("candles_1m_count") == 0:
        return "INSUFFICIENT_1M_CANDLES"
    if "PRICE_OUTSIDE_POI" in reason_upper or _is_true(micro.get("trigger_outside_poi")):
        return "TRIGGER_OUTSIDE_POI"
    sweep = _is_true(micro.get("sweep_1m_confirmed") or micro.get("sweep_detected"))
    choch = _is_true(micro.get("choch_detected"))
    if sweep and not choch:
        return "SWEEP_PRESENT_CHOCH_MISSING"
    if choch and not _is_true(micro.get("trigger_inside_poi") or micro.get("price_in_agent2_poi")):
        return "TRIGGER_OUTSIDE_POI"
    if {"sweep_1m_confirmed", "choch_detected"}.intersection(missing_fields):
        return "MICRO_TRIGGER_FIELDS_NOT_PERSISTED"
    if "WAIT" in state.upper() or "WAIT" in reason_upper:
        return "MICRO_WAITING_TRIGGER"
    return "MICRO_NOT_READY_UNCLASSIFIED"


def _trigger_presence(micro: dict[str, Any]) -> str:
    sweep = _is_true(micro.get("sweep_1m_confirmed") or micro.get("sweep_detected"))
    choch = _is_true(micro.get("choch_detected"))
    if sweep and choch:
        return "SWEEP_AND_CHOCH"
    if sweep:
        return "SWEEP_ONLY"
    if choch:
        return "CHOCH_ONLY"
    if "sweep_1m_confirmed" not in micro and "choch_detected" not in micro:
        return "NO_TRIGGER_FIELDS"
    return "NO_TRIGGER"


def _trigger_location(micro: dict[str, Any]) -> str:
    if _is_true(micro.get("trigger_outside_poi")) or _is_true(micro.get("outside_poi")):
        return "OUTSIDE_POI"
    if _is_true(micro.get("trigger_inside_poi") or micro.get("price_in_agent2_poi")):
        return "INSIDE_POI"
    return "UNKNOWN"


def _missing_micro_fields(micro: dict[str, Any]) -> list[str]:
    expected = ("sweep_1m_confirmed", "choch_detected", "trigger_inside_poi", "candles_1m_count")
    return [field for field in expected if field not in micro]


def _case_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    grade_rank = {"B": 2, "C": 1}.get(str(row.get("grade")), 0)
    has_micro_blocker = 1 if "SECTION_NOT_READY:micro" in row.get("actual_blockers", []) else 0
    has_trigger_hint = 1 if row.get("trigger_presence") not in {"NO_TRIGGER_FIELDS", "NO_TRIGGER"} else 0
    return grade_rank, has_micro_blocker, has_trigger_hint


def _micro_payload(decision: dict[str, Any]) -> dict[str, Any]:
    bundle = decision.get("p1_evidence_bundle") or {}
    if isinstance(bundle, dict) and isinstance(bundle.get("micro"), dict):
        return bundle["micro"]
    micro = decision.get("micro")
    return micro if isinstance(micro, dict) else {}


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return _is_true(value)


def _is_true(value: Any) -> bool:
    return value is True or str(value).upper() == "TRUE"


if __name__ == "__main__":
    raise SystemExit(main())
