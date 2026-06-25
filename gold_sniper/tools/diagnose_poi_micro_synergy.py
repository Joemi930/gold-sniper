from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose POI-Micro synergy in decisions.jsonl."
    )
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decisions = _load_decisions(args.decisions)
    report = diagnose_poi_micro_synergy(decisions, top=args.top)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "total_decisions_scanned": report["total_decisions_scanned"],
        "poi_micro_synergy_count": report["poi_micro_synergy_count"],
        "micro_confirmed_count": report["micro_confirmed_count"],
    }, ensure_ascii=False))
    return 0


def diagnose_poi_micro_synergy(
    decisions: list[dict[str, Any]], *, top: int = 50
) -> dict[str, Any]:
    original = [dict(item) for item in decisions]

    synergy_by_setup: Counter[str] = Counter()
    synergy_by_grade: Counter[str] = Counter()
    synergy_by_decision: Counter[str] = Counter()
    status_distribution: Counter[str] = Counter()
    reason_distribution: Counter[str] = Counter()
    remaining_blockers_after_synergy: Counter[str] = Counter()

    synergy_true_cases: list[dict[str, Any]] = []
    micro_confirmed_but_no_synergy: list[dict[str, Any]] = []
    top_cases: list[dict[str, Any]] = []

    poi_micro_synergy_count = 0
    micro_confirmed_count = 0
    micro_confirmed_inside_poi_count = 0
    micro_confirmed_outside_poi_count = 0
    micro_confirmed_without_poi_count = 0

    for decision in decisions:
        synergy = _synergy_payload(decision)
        synergy_enabled = bool(decision.get("poi_micro_synergy") or synergy.get("synergy"))
        status = str(decision.get("poi_micro_synergy_status") or synergy.get("status") or "UNKNOWN")
        reason = str(decision.get("poi_micro_reason") or synergy.get("reason") or "UNKNOWN")
        setup_type = str(decision.get("setup_type") or "UNKNOWN")
        grade = str(decision.get("setup_grade") or "UNKNOWN")
        action = str(decision.get("decision") or "UNKNOWN")
        micro_confirmed = _micro_confirmed(decision, synergy)
        micro_inside = _micro_inside(decision, synergy)
        micro_outside = _micro_outside(decision, synergy)
        has_poi = _has_selected_poi(decision)

        status_distribution[status] += 1
        reason_distribution[reason] += 1

        if micro_confirmed:
            micro_confirmed_count += 1
            if micro_inside:
                micro_confirmed_inside_poi_count += 1
            elif micro_outside:
                micro_confirmed_outside_poi_count += 1
            elif not has_poi:
                micro_confirmed_without_poi_count += 1

        if synergy_enabled:
            poi_micro_synergy_count += 1
            synergy_by_setup[setup_type] += 1
            synergy_by_grade[grade] += 1
            synergy_by_decision[action] += 1
            compact = _compact_case(decision, synergy, reason=reason)
            synergy_true_cases.append(compact)
            top_cases.append(compact)
            for blocker in decision.get("enter_eligibility_blockers") or []:
                remaining_blockers_after_synergy[str(blocker)] += 1
        elif micro_confirmed:
            compact = _compact_case(decision, synergy, reason=reason)
            micro_confirmed_but_no_synergy.append(compact)
            top_cases.append(compact)

    assert decisions == original

    synergy_true_cases.sort(key=_case_rank, reverse=True)
    micro_confirmed_but_no_synergy.sort(key=_case_rank, reverse=True)
    top_cases.sort(key=_case_rank, reverse=True)

    return {
        "total_decisions_scanned": len(decisions),
        "poi_micro_synergy_count": poi_micro_synergy_count,
        "micro_confirmed_count": micro_confirmed_count,
        "micro_confirmed_inside_poi_count": micro_confirmed_inside_poi_count,
        "micro_confirmed_outside_poi_count": micro_confirmed_outside_poi_count,
        "micro_confirmed_without_poi_count": micro_confirmed_without_poi_count,
        "poi_micro_synergy_by_setup": dict(synergy_by_setup.most_common()),
        "poi_micro_synergy_by_grade": dict(synergy_by_grade.most_common()),
        "poi_micro_synergy_by_decision": dict(synergy_by_decision.most_common()),
        "poi_micro_synergy_status_distribution": dict(status_distribution.most_common()),
        "poi_micro_reason_distribution": dict(reason_distribution.most_common(25)),
        "micro_confirmed_but_no_synergy": micro_confirmed_but_no_synergy[: max(int(top), 0)],
        "synergy_true_cases": synergy_true_cases[: max(int(top), 0)],
        "remaining_blockers_after_synergy": dict(remaining_blockers_after_synergy.most_common(25)),
        "top_cases": top_cases[: max(int(top), 0)],
    }


def _compact_case(
    decision: dict[str, Any],
    synergy: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    micro_evidence = decision.get("micro_evidence") if isinstance(decision.get("micro_evidence"), dict) else {}
    return {
        "timestamp": decision.get("timestamp"),
        "setup_type": decision.get("setup_type"),
        "setup_grade": decision.get("setup_grade"),
        "decision": decision.get("decision"),
        "enter_eligible": bool(decision.get("enter_eligible")),
        "risk_multiplier": decision.get("risk_multiplier"),
        "poi_micro_synergy": bool(decision.get("poi_micro_synergy") or synergy.get("synergy")),
        "poi_micro_synergy_status": decision.get("poi_micro_synergy_status") or synergy.get("status"),
        "poi_micro_reason": reason,
        "effective_poi_status": decision.get("effective_poi_status") or synergy.get("effective_poi_status"),
        "poi_micro_upgraded_poi_status": decision.get("poi_micro_upgraded_poi_status") or synergy.get("upgraded_poi_status"),
        "micro_contract_status": decision.get("micro_contract_status"),
        "micro_confirmed": _micro_confirmed(decision, synergy),
        "micro_inside_poi": _micro_inside(decision, synergy),
        "micro_outside_poi": _micro_outside(decision, synergy),
        "micro_evidence": {
            "sweep_1m_confirmed": decision.get("sweep_1m_confirmed") or micro_evidence.get("sweep_1m_confirmed"),
            "choch_detected": decision.get("choch_detected") or micro_evidence.get("choch_detected"),
            "trigger_inside_poi": decision.get("trigger_inside_poi") or micro_evidence.get("trigger_inside_poi"),
            "price_in_agent2_poi": decision.get("price_in_agent2_poi") or micro_evidence.get("price_in_agent2_poi"),
            "trigger_outside_poi": decision.get("trigger_outside_poi") or micro_evidence.get("trigger_outside_poi"),
            "candles_1m_count": decision.get("candles_1m_count") or micro_evidence.get("candles_1m_count"),
        },
        "poi": _compact_poi(decision),
        "enter_eligibility_blockers": [str(item) for item in (decision.get("enter_eligibility_blockers") or [])],
        "readiness_by_section": decision.get("readiness_by_section") or {},
    }


def _compact_poi(decision: dict[str, Any]) -> dict[str, Any]:
    bundle = decision.get("p1_evidence_bundle") or {}
    poi = bundle.get("poi") if isinstance(bundle, dict) and isinstance(bundle.get("poi"), dict) else {}
    quality = decision.get("poi_quality_breakdown") if isinstance(decision.get("poi_quality_breakdown"), dict) else {}
    selected = poi.get("selected_poi") if isinstance(poi.get("selected_poi"), dict) else {}
    return {
        "selected_poi_present": bool(poi.get("selected_poi_present") or selected),
        "has_price_bounds": bool(poi.get("has_price_bounds") or poi.get("price_bounds") or selected.get("price_bounds")),
        "poi_type": poi.get("poi_type"),
        "poi_failure_class": poi.get("poi_failure_class"),
        "poi_semantic_status": poi.get("poi_semantic_status"),
        "poi_contract_status": decision.get("poi_contract_status"),
        "poi_contract_reason": decision.get("poi_contract_reason"),
        "final_poi_quality_score": quality.get("final_poi_quality_score") or poi.get("poi_quality_score") or selected.get("score"),
    }


def _case_rank(row: dict[str, Any]) -> tuple[int, int, float]:
    grade_rank = {"A_PLUS": 4, "A": 3, "B": 2, "C": 1}.get(str(row.get("setup_grade")), 0)
    synergy_rank = 1 if row.get("poi_micro_synergy") else 0
    try:
        score = float(row.get("risk_multiplier") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return synergy_rank, grade_rank, score


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


def _micro_confirmed(decision: dict[str, Any], synergy: dict[str, Any]) -> bool:
    return bool(
        decision.get("micro_confirmed")
        or synergy.get("micro_confirmed")
        or decision.get("micro_contract_confirmed")
        or str(decision.get("micro_contract_status") or "") == "MICRO_CONFIRMED"
    )


def _micro_inside(decision: dict[str, Any], synergy: dict[str, Any]) -> bool:
    evidence = decision.get("micro_evidence") if isinstance(decision.get("micro_evidence"), dict) else {}
    return bool(
        decision.get("micro_inside_poi")
        or synergy.get("micro_inside_poi")
        or decision.get("price_in_agent2_poi")
        or decision.get("trigger_inside_poi")
        or evidence.get("price_in_agent2_poi")
        or evidence.get("trigger_inside_poi")
    )


def _micro_outside(decision: dict[str, Any], synergy: dict[str, Any]) -> bool:
    evidence = decision.get("micro_evidence") if isinstance(decision.get("micro_evidence"), dict) else {}
    return bool(
        decision.get("micro_outside_poi")
        or synergy.get("micro_outside_poi")
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


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
