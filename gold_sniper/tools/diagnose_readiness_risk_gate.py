from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from gold_sniper.strategy.readiness_risk_gate_contract import evaluate_readiness_risk_gate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose readiness/risk gates for synergy true cases.")
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser


def diagnose_readiness_risk_gate(decisions: list[dict[str, Any]], *, top: int = 50) -> dict[str, Any]:
    original = [dict(item) for item in decisions]

    primary_distribution: Counter = Counter()
    blocker_distribution: Counter = Counter()
    synergy_true_primary: Counter = Counter()
    risk_reason_by_primary: dict[str, Counter] = defaultdict(Counter)
    synergy_true_cases: list[dict[str, Any]] = []
    synergy_true_watch_only: list[dict[str, Any]] = []
    synergy_true_no_enter: list[dict[str, Any]] = []

    for row in decisions:
        gate = evaluate_readiness_risk_gate(row)
        primary_distribution[gate.primary_blocker] += 1
        for blocker in gate.blockers:
            blocker_distribution[blocker] += 1

        compact = _compact(row, gate.to_dict())
        if gate.synergy_true:
            synergy_true_cases.append(compact)
            synergy_true_primary[gate.primary_blocker] += 1
            risk_reason_by_primary[gate.primary_blocker][gate.risk_reason] += 1
            if gate.final_decision == "WATCH_ONLY":
                synergy_true_watch_only.append(compact)
            if not gate.enter_eligible:
                synergy_true_no_enter.append(compact)

    assert decisions == original

    return {
        "total_decisions_scanned": len(decisions),
        "gate_primary_blocker_distribution": dict(primary_distribution.most_common()),
        "gate_blocker_distribution": dict(blocker_distribution.most_common(30)),
        "synergy_true_count": len(synergy_true_cases),
        "synergy_true_watch_only_count": len(synergy_true_watch_only),
        "synergy_true_not_enter_eligible_count": len(synergy_true_no_enter),
        "synergy_true_gate_primary_blocker_distribution": dict(synergy_true_primary.most_common()),
        "risk_reason_by_gate_primary_blocker": {
            key: dict(value.most_common(10))
            for key, value in sorted(risk_reason_by_primary.items())
        },
        "synergy_true_cases": synergy_true_cases[:top],
        "synergy_true_watch_only": synergy_true_watch_only[:top],
        "synergy_true_no_enter": synergy_true_no_enter[:top],
    }


def _compact(row: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": row.get("timestamp"),
        "decision": row.get("decision"),
        "setup_type": row.get("setup_type"),
        "setup_grade": row.get("setup_grade"),
        "poi_micro_synergy": row.get("poi_micro_synergy"),
        "effective_poi_status": row.get("effective_poi_status"),
        "micro_contract_status": row.get("micro_contract_status"),
        "readiness_state": row.get("readiness_state"),
        "readiness_reason": row.get("readiness_reason"),
        "readiness_by_section": row.get("readiness_by_section"),
        "readiness_missing_ready_blockers": row.get("readiness_missing_ready_blockers") or [],
        "enter_eligible": row.get("enter_eligible"),
        "enter_eligibility_reason": row.get("enter_eligibility_reason"),
        "enter_eligibility_blockers": row.get("enter_eligibility_blockers") or [],
        "risk_multiplier": row.get("risk_multiplier"),
        "risk_reason": row.get("risk_reason"),
        "risk_preview": _compact_risk_preview(row.get("risk_preview")),
        "best_setup_candidate": row.get("best_setup_candidate") or {},
        "setup_candidates": row.get("setup_candidates") or [],
        "gate_primary_blocker": gate.get("primary_blocker"),
        "gate_blockers": gate.get("blockers"),
        "gate_audit": gate.get("audit") or {},
    }


def _compact_risk_preview(value: Any) -> dict[str, Any]:
    preview = value if isinstance(value, dict) else {}
    metadata = preview.get("metadata") if isinstance(preview.get("metadata"), dict) else {}
    return {
        "allowed": preview.get("allowed"),
        "risk_pct": preview.get("risk_pct"),
        "risk_multiplier": preview.get("risk_multiplier"),
        "reason": preview.get("reason"),
        "setup_max_risk_multiplier": metadata.get("setup_max_risk_multiplier"),
    }


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = _load_decisions(args.decisions)
    report = diagnose_readiness_risk_gate(rows, top=args.top)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "total_decisions_scanned": report["total_decisions_scanned"],
        "synergy_true_count": report["synergy_true_count"],
        "synergy_true_watch_only_count": report["synergy_true_watch_only_count"],
        "synergy_true_not_enter_eligible_count": report["synergy_true_not_enter_eligible_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
