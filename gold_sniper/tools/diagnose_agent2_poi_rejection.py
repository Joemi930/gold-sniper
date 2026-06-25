from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose Agent2 POI rejection decomposition.")
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser


def diagnose_agent2_poi_rejection(
    decisions: list[dict[str, Any]], *, top: int = 50
) -> dict[str, Any]:
    original = [dict(item) for item in decisions]
    code_distribution: Counter[str] = Counter()
    severity_distribution: Counter[str] = Counter()
    synergy_by_code: dict[str, Counter[str]] = {}
    recoverable_cases: list[dict[str, Any]] = []
    fatal_cases: list[dict[str, Any]] = []
    micro_confirmed_rejected: list[dict[str, Any]] = []
    sweep_rejected: list[dict[str, Any]] = []

    for row in decisions:
        rejection = _rejection_payload(row)
        code = str(row.get("poi_rejection_code") or rejection.get("code") or "UNKNOWN")
        severity = str(row.get("poi_rejection_severity") or rejection.get("severity") or "UNKNOWN")
        recoverable = bool(row.get("poi_rejection_recoverable") or rejection.get("recoverable"))
        fatal = bool(row.get("poi_rejection_fatal") or rejection.get("fatal"))
        setup_type = str(row.get("setup_type") or "UNKNOWN")
        micro_confirmed = _micro_confirmed(row)
        synergy = bool(row.get("poi_micro_synergy") or _synergy_payload(row).get("synergy"))

        code_distribution[code] += 1
        severity_distribution[severity] += 1
        synergy_by_code.setdefault(code, Counter())
        synergy_by_code[code]["synergy_true" if synergy else "synergy_false"] += 1

        compact = _compact(row, rejection)
        if recoverable:
            recoverable_cases.append(compact)
        if fatal:
            fatal_cases.append(compact)
        if micro_confirmed and code not in {"POI_REJECTION_NONE", "UNKNOWN"}:
            micro_confirmed_rejected.append(compact)
        if setup_type == "SWEEP_REVERSAL" and code not in {"POI_REJECTION_NONE", "UNKNOWN"}:
            sweep_rejected.append(compact)

    assert decisions == original

    recoverable_cases.sort(key=_case_rank, reverse=True)
    fatal_cases.sort(key=_case_rank, reverse=True)
    micro_confirmed_rejected.sort(key=_case_rank, reverse=True)
    sweep_rejected.sort(key=_case_rank, reverse=True)
    limit = max(int(top), 0)
    return {
        "total_decisions_scanned": len(decisions),
        "poi_rejection_code_distribution": dict(code_distribution.most_common()),
        "poi_rejection_severity_distribution": dict(severity_distribution.most_common()),
        "recoverable_count": len(recoverable_cases),
        "fatal_count": len(fatal_cases),
        "micro_confirmed_rejected_count": len(micro_confirmed_rejected),
        "micro_confirmed_recoverable_count": sum(
            1 for row in micro_confirmed_rejected if row.get("poi_rejection_recoverable")
        ),
        "sweep_rejected_count": len(sweep_rejected),
        "poi_micro_synergy_by_rejection_code": {
            key: dict(counter.most_common())
            for key, counter in sorted(synergy_by_code.items())
        },
        "recoverable_cases": recoverable_cases[:limit],
        "fatal_cases": fatal_cases[:limit],
        "micro_confirmed_rejected_cases": micro_confirmed_rejected[:limit],
        "sweep_rejected_cases": sweep_rejected[:limit],
    }


def _compact(row: dict[str, Any], rejection: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": row.get("timestamp"),
        "setup_type": row.get("setup_type"),
        "setup_grade": row.get("setup_grade"),
        "decision": row.get("decision"),
        "enter_eligible": bool(row.get("enter_eligible")),
        "risk_multiplier": row.get("risk_multiplier"),
        "poi_contract_status": row.get("poi_contract_status"),
        "poi_contract_reason": row.get("poi_contract_reason"),
        "poi_rejection_code": row.get("poi_rejection_code") or rejection.get("code"),
        "poi_rejection_source": row.get("poi_rejection_source") or rejection.get("source"),
        "poi_rejection_severity": row.get("poi_rejection_severity") or rejection.get("severity"),
        "poi_rejection_recoverable": bool(row.get("poi_rejection_recoverable") or rejection.get("recoverable")),
        "poi_rejection_fatal": bool(row.get("poi_rejection_fatal") or rejection.get("fatal")),
        "poi_rejection_reason": row.get("poi_rejection_reason") or rejection.get("reason"),
        "micro_contract_status": row.get("micro_contract_status"),
        "micro_confirmed": _micro_confirmed(row),
        "micro_inside_poi": _micro_inside(row),
        "poi_micro_synergy": bool(row.get("poi_micro_synergy") or _synergy_payload(row).get("synergy")),
        "poi_micro_reason": row.get("poi_micro_reason") or _synergy_payload(row).get("reason"),
        "enter_eligibility_blockers": [str(item) for item in (row.get("enter_eligibility_blockers") or [])],
    }


def _case_rank(row: dict[str, Any]) -> tuple[int, int, float]:
    grade_rank = {"A_PLUS": 4, "A": 3, "B": 2, "C": 1}.get(str(row.get("setup_grade")), 0)
    micro_rank = 1 if row.get("micro_confirmed") else 0
    try:
        risk = float(row.get("risk_multiplier") or 0.0)
    except (TypeError, ValueError):
        risk = 0.0
    return micro_rank, grade_rank, risk


def _rejection_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("poi_rejection")
    if isinstance(payload, dict) and payload:
        return payload
    bundle = row.get("p1_evidence_bundle") or {}
    if isinstance(bundle, dict):
        poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
        if isinstance(poi.get("poi_rejection"), dict):
            return poi.get("poi_rejection") or {}
    synergy = _synergy_payload(row)
    audit = synergy.get("audit") if isinstance(synergy.get("audit"), dict) else {}
    rejection = audit.get("poi_rejection") if isinstance(audit.get("poi_rejection"), dict) else {}
    return rejection if isinstance(rejection, dict) else {}


def _synergy_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("poi_micro_synergy_payload")
    if isinstance(payload, dict) and payload:
        return payload
    bundle = row.get("p1_evidence_bundle") or {}
    if isinstance(bundle, dict):
        poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
        raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
        if isinstance(poi.get("poi_micro_synergy"), dict):
            return poi.get("poi_micro_synergy") or {}
        if isinstance(raw.get("poi_micro_synergy"), dict):
            return raw.get("poi_micro_synergy") or {}
    return {}


def _micro_confirmed(row: dict[str, Any]) -> bool:
    return bool(
        row.get("micro_confirmed")
        or row.get("micro_contract_confirmed")
        or str(row.get("micro_contract_status") or "") == "MICRO_CONFIRMED"
    )


def _micro_inside(row: dict[str, Any]) -> bool:
    evidence = row.get("micro_evidence") if isinstance(row.get("micro_evidence"), dict) else {}
    synergy = _synergy_payload(row)
    return bool(
        row.get("micro_inside_poi")
        or synergy.get("micro_inside_poi")
        or row.get("price_in_agent2_poi")
        or row.get("trigger_inside_poi")
        or evidence.get("price_in_agent2_poi")
        or evidence.get("trigger_inside_poi")
    )


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = _load_decisions(args.decisions)
    report = diagnose_agent2_poi_rejection(rows, top=args.top)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "total_decisions_scanned": report["total_decisions_scanned"],
        "recoverable_count": report["recoverable_count"],
        "fatal_count": report["fatal_count"],
        "micro_confirmed_rejected_count": report["micro_confirmed_rejected_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
