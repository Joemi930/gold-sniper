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
    parser = argparse.ArgumentParser(
        description="Diagnose micro contract status and near-misses in decisions.jsonl."
    )
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decisions = _load_decisions(args.decisions)
    report = diagnose_micro_contract(decisions, top=args.top)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "total_decisions_scanned": report["total_decisions_scanned"],
        "true_micro_near_miss_count": report["true_micro_near_miss_count"],
        "micro_confirmed_count": report["micro_confirmed_count"],
    }, ensure_ascii=False))
    return 0


def diagnose_micro_contract(
    decisions: list[dict[str, Any]], *, top: int = 50
) -> dict[str, Any]:
    original = deepcopy(decisions)

    # Distributions over all decisions
    status_distribution: Counter[str] = Counter()
    reason_distribution: Counter[str] = Counter()
    missing_field_distribution: Counter[str] = Counter()
    present_field_distribution: Counter[str] = Counter()

    # Boolean indicator counts
    micro_confirmed_count = 0
    micro_waiting_trigger_count = 0
    micro_missing_data_count = 0
    micro_invalid_count = 0
    micro_outside_poi_count = 0

    # Near-miss tracking
    near_miss_rows: list[dict[str, Any]] = []
    true_micro_near_miss_count = 0

    # Micro confirmed tracking
    confirmed_rows: list[dict[str, Any]] = []

    # Remaining blockers after micro confirmed
    remaining_blockers_after_micro: Counter[str] = Counter()

    for decision in decisions:
        status = str(decision.get("micro_contract_status") or "UNKNOWN")
        reason = str(decision.get("micro_contract_reason") or "UNKNOWN")
        missing = decision.get("micro_contract_missing_fields") or []
        present = decision.get("micro_contract_present_fields") or []

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

        # Count boolean indicators
        if decision.get("micro_contract_confirmed") is True:
            micro_confirmed_count += 1
        if decision.get("micro_contract_waiting_trigger") is True:
            micro_waiting_trigger_count += 1
        if decision.get("micro_contract_missing_data") is True:
            micro_missing_data_count += 1
        if decision.get("micro_contract_invalid") is True:
            micro_invalid_count += 1
        if decision.get("micro_contract_outside_poi") is True:
            micro_outside_poi_count += 1

        # Also count from status string for coverage when boolean flags are absent
        if status == "MICRO_CONFIRMED":
            if decision.get("micro_contract_confirmed") is not True:
                micro_confirmed_count += 1
        elif status == "MICRO_WAITING_TRIGGER":
            if decision.get("micro_contract_waiting_trigger") is not True:
                micro_waiting_trigger_count += 1
        elif status == "MICRO_MISSING_DATA":
            if decision.get("micro_contract_missing_data") is not True:
                micro_missing_data_count += 1
        elif status == "MICRO_INVALID":
            if decision.get("micro_contract_invalid") is not True:
                micro_invalid_count += 1
        elif status == "MICRO_OUTSIDE_POI":
            if decision.get("micro_contract_outside_poi") is not True:
                micro_outside_poi_count += 1

        # Collect near-miss micro decisions
        if _is_true_micro_near_miss(decision):
            true_micro_near_miss_count += 1
            near_miss_rows.append(_compact_near_miss(decision))

        # Collect micro confirmed decisions
        if decision.get("micro_contract_confirmed") is True or status == "MICRO_CONFIRMED":
            confirmed_rows.append(_compact_confirmed(decision))
            blockers = decision.get("enter_eligibility_blockers") or []
            if isinstance(blockers, list):
                for blocker in blockers:
                    remaining_blockers_after_micro[str(blocker)] += 1

    # Guard: analysis must be read-only
    assert decisions == original

    near_miss_rows.sort(key=_near_miss_rank, reverse=True)
    confirmed_rows.sort(key=_confirmed_rank, reverse=True)

    return {
        "total_decisions_scanned": len(decisions),
        "micro_contract_status_distribution": dict(status_distribution.most_common()),
        "micro_contract_reason_distribution": dict(reason_distribution.most_common()),
        "micro_contract_missing_field_distribution": dict(missing_field_distribution.most_common()),
        "micro_contract_present_field_distribution": dict(present_field_distribution.most_common()),
        "true_micro_near_miss_count": true_micro_near_miss_count,
        "micro_confirmed_count": micro_confirmed_count,
        "micro_waiting_trigger_count": micro_waiting_trigger_count,
        "micro_missing_data_count": micro_missing_data_count,
        "micro_invalid_count": micro_invalid_count,
        "micro_outside_poi_count": micro_outside_poi_count,
        "top_near_miss_micro": near_miss_rows[: max(int(top), 0)],
        "top_micro_confirmed": confirmed_rows[: max(int(top), 0)],
        "remaining_blockers_after_micro_confirmed": dict(
            remaining_blockers_after_micro.most_common()
        ),
        "diagnostic": _build_diagnostic(
            len(decisions),
            status_distribution,
            reason_distribution,
            true_micro_near_miss_count,
            micro_confirmed_count,
            micro_waiting_trigger_count,
            micro_missing_data_count,
            micro_invalid_count,
            micro_outside_poi_count,
        ),
    }


def _is_true_micro_near_miss(decision: dict[str, Any]) -> bool:
    """A near-miss micro decision is a SWEEP_REVERSAL that got WATCH_ONLY / WAIT_FOR_TRIGGER
    but could have been a micro-confirmed enter if the micro evidence were stronger."""
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


def _compact_near_miss(decision: dict[str, Any]) -> dict[str, Any]:
    blockers = [str(item) for item in (decision.get("enter_eligibility_blockers") or [])]
    return {
        "timestamp": decision.get("timestamp"),
        "setup_type": decision.get("setup_type"),
        "grade": decision.get("setup_grade"),
        "decision": decision.get("decision"),
        "micro_contract_status": decision.get("micro_contract_status"),
        "micro_contract_reason": decision.get("micro_contract_reason"),
        "micro_contract_confirmed": decision.get("micro_contract_confirmed"),
        "micro_contract_waiting_trigger": decision.get("micro_contract_waiting_trigger"),
        "micro_contract_missing_data": decision.get("micro_contract_missing_data"),
        "micro_contract_invalid": decision.get("micro_contract_invalid"),
        "micro_contract_outside_poi": decision.get("micro_contract_outside_poi"),
        "micro_contract_missing_fields": decision.get("micro_contract_missing_fields") or [],
        "micro_contract_present_fields": decision.get("micro_contract_present_fields") or [],
        "actual_blockers": blockers,
    }


def _compact_confirmed(decision: dict[str, Any]) -> dict[str, Any]:
    micro_evidence = decision.get("micro_evidence") or {}
    if not isinstance(micro_evidence, dict):
        micro_evidence = {}
    blockers = [str(item) for item in (decision.get("enter_eligibility_blockers") or [])]
    return {
        "timestamp": decision.get("timestamp"),
        "setup_type": decision.get("setup_type"),
        "grade": decision.get("setup_grade"),
        "decision": decision.get("decision"),
        "micro_contract_status": decision.get("micro_contract_status"),
        "micro_contract_reason": decision.get("micro_contract_reason"),
        "micro_evidence": {
            "sweep_1m_confirmed": micro_evidence.get("sweep_1m_confirmed"),
            "choch_detected": micro_evidence.get("choch_detected"),
            "trigger_inside_poi": micro_evidence.get("trigger_inside_poi"),
            "retest_confirmed": micro_evidence.get("retest_confirmed"),
            "trigger_confirmed": micro_evidence.get("trigger_confirmed"),
            "candles_1m_count": micro_evidence.get("candles_1m_count"),
            "price_in_agent2_poi": micro_evidence.get("price_in_agent2_poi"),
            "trigger_outside_poi": micro_evidence.get("trigger_outside_poi"),
            "displacement_present": micro_evidence.get("displacement_present"),
            "reclaim_confirmed": micro_evidence.get("reclaim_confirmed"),
        },
        "actual_blockers": blockers,
    }


def _near_miss_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    grade_rank = {"B": 2, "C": 1}.get(str(row.get("grade")), 0)
    status = str(row.get("micro_contract_status") or "")
    # Prioritise rows where micro is close to confirmed
    has_near_confirmed = 1 if status in {"CONFIRMED", "WAITING_TRIGGER"} else 0
    has_missing = len(row.get("micro_contract_missing_fields") or [])
    return grade_rank, has_near_confirmed, -has_missing


def _confirmed_rank(row: dict[str, Any]) -> tuple[int, int]:
    grade_rank = {"A_PLUS": 4, "A": 3, "B": 2, "C": 1}.get(str(row.get("grade")), 0)
    is_sweep = 1 if row.get("setup_type") == "SWEEP_REVERSAL" else 0
    return grade_rank, is_sweep


def _build_diagnostic(
    total: int,
    status_dist: Counter[str],
    reason_dist: Counter[str],
    near_miss_count: int,
    micro_confirmed_count: int,
    micro_waiting_trigger_count: int,
    micro_missing_data_count: int,
    micro_invalid_count: int,
    micro_outside_poi_count: int,
) -> str:
    parts: list[str] = []
    parts.append(f"Scanned {total} decisions.")

    top_status = status_dist.most_common(3)
    if top_status:
        status_summary = ", ".join(f"{s}: {c}" for s, c in top_status)
        parts.append(f"Top micro_contract_status: {status_summary}.")

    top_reason = reason_dist.most_common(3)
    if top_reason:
        reason_summary = ", ".join(f"{r}: {c}" for r, c in top_reason)
        parts.append(f"Top micro_contract_reason: {reason_summary}.")

    parts.append(
        f"Near-miss micro: {near_miss_count} | "
        f"Confirmed: {micro_confirmed_count} | "
        f"Waiting trigger: {micro_waiting_trigger_count} | "
        f"Missing data: {micro_missing_data_count} | "
        f"Invalid: {micro_invalid_count} | "
        f"Outside POI: {micro_outside_poi_count}."
    )

    if near_miss_count == 0 and micro_confirmed_count == 0:
        parts.append("No micro contract activity detected. Possible pipeline gap.")

    return " ".join(parts)


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
