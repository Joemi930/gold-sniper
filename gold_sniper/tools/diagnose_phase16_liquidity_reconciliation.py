from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose Phase16 liquidity reconciliation.")
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser


def diagnose_phase16_liquidity_reconciliation(
    decisions: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    *,
    top: int = 50,
) -> dict[str, Any]:
    original = [dict(item) for item in decisions]
    summary = summary or {}

    setup_type_distribution: Counter[str] = Counter()
    liquidity_source_distribution: Counter[str] = Counter()
    dominant_blockers: Counter[str] = Counter()
    synergy_true_rows: list[dict[str, Any]] = []

    poi_micro_synergy_count = 0
    sweep_reversal_count = 0
    liquidity_reconciled_count = 0
    micro_liquidity_confirmed_count = 0
    enter_eligible_count = 0
    risk_multiplier_positive = 0
    enter_count = 0

    for row in decisions:
        setup_type = str(row.get("setup_type") or "UNKNOWN")
        setup_type_distribution[setup_type] += 1
        if setup_type == "SWEEP_REVERSAL":
            sweep_reversal_count += 1

        source = str(row.get("liquidity_evidence_source") or _liquidity_payload(row).get("liquidity_evidence_source") or "NONE")
        liquidity_source_distribution[source] += 1

        if bool(row.get("liquidity_reconciled") or _liquidity_payload(row).get("liquidity_reconciled")):
            liquidity_reconciled_count += 1
        if bool(row.get("micro_liquidity_confirmed") or _liquidity_payload(row).get("micro_liquidity_confirmed")):
            micro_liquidity_confirmed_count += 1

        if bool(row.get("enter_eligible")):
            enter_eligible_count += 1
        if _float(row.get("risk_multiplier")) > 0.0:
            risk_multiplier_positive += 1
        decision = str(row.get("decision") or "").upper()
        if decision in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}:
            enter_count += 1

        blocker = str(row.get("gate_primary_blocker") or _primary_blocker(row) or "UNKNOWN")
        dominant_blockers[blocker] += 1

        if _synergy_true(row):
            poi_micro_synergy_count += 1
            synergy_true_rows.append(_compact_synergy_row(row))

    assert decisions == original
    synergy_true_rows.sort(key=lambda item: str(item.get("timestamp") or ""))

    return {
        "total_decisions": len(decisions),
        "setup_type_distribution": dict(setup_type_distribution.most_common()),
        "poi_micro_synergy_count": poi_micro_synergy_count,
        "SWEEP_REVERSAL_count": sweep_reversal_count,
        "liquidity_reconciled_count": liquidity_reconciled_count,
        "micro_liquidity_confirmed_count": micro_liquidity_confirmed_count,
        "liquidity_source_distribution": dict(liquidity_source_distribution.most_common()),
        "AGENT3_MACRO_count": liquidity_source_distribution.get("AGENT3_MACRO", 0),
        "AGENT5_MICRO_CONTRACT_count": liquidity_source_distribution.get("AGENT5_MICRO_CONTRACT", 0),
        "NONE_count": liquidity_source_distribution.get("NONE", 0),
        "enter_eligible_count": enter_eligible_count,
        "risk_multiplier_positive": risk_multiplier_positive,
        "ENTER_count": enter_count,
        "filled_trades": _summary_int(summary, "filled_trades"),
        "missed_entries": _summary_int(summary, "missed_entries"),
        "dominant_blockers": dict(dominant_blockers.most_common(20)),
        "synergy_true_rows": synergy_true_rows[: max(int(top), 0)],
    }


def _compact_synergy_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": row.get("timestamp") or row.get("ts_utc"),
        "setup_type": row.get("setup_type"),
        "decision": row.get("decision"),
        "enter_eligible": bool(row.get("enter_eligible")),
        "risk_multiplier": row.get("risk_multiplier"),
        "gate_primary_blocker": row.get("gate_primary_blocker") or _primary_blocker(row),
        "readiness_state": row.get("readiness_state"),
        "readiness_by_section": row.get("readiness_by_section") or {},
        "liquidity_evidence_source": row.get("liquidity_evidence_source") or _liquidity_payload(row).get("liquidity_evidence_source"),
        "micro_liquidity_confirmed": bool(row.get("micro_liquidity_confirmed") or _liquidity_payload(row).get("micro_liquidity_confirmed")),
        "liquidity_reconciled": bool(row.get("liquidity_reconciled") or _liquidity_payload(row).get("liquidity_reconciled")),
        "liquidity_reconciliation_reason": row.get("liquidity_reconciliation_reason") or _liquidity_payload(row).get("readiness_reason"),
        "liquidity_reconciliation_blockers": _string_list(
            row.get("liquidity_reconciliation_blockers")
            or _liquidity_payload(row).get("liquidity_reconciliation_blockers")
            or _liquidity_payload(row).get("blockers")
        ),
        "setup_candidates": row.get("setup_candidates") or [],
        "best_setup_candidate": row.get("best_setup_candidate") or {},
        "hard_veto": bool(row.get("hard_veto")),
        "veto_code": row.get("veto_code"),
        "risk_allowed": bool(row.get("risk_allowed")),
        "risk_reason": row.get("risk_reason"),
    }


def _synergy_true(row: dict[str, Any]) -> bool:
    if row.get("poi_micro_synergy") is True:
        return True
    payload = row.get("poi_micro_synergy_payload") if isinstance(row.get("poi_micro_synergy_payload"), dict) else {}
    return bool(payload.get("synergy"))


def _liquidity_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("liquidity_reconciliation_payload")
    if isinstance(payload, dict) and payload:
        return payload
    bundle = row.get("p1_evidence_bundle") if isinstance(row.get("p1_evidence_bundle"), dict) else {}
    liquidity = bundle.get("liquidity") if isinstance(bundle.get("liquidity"), dict) else {}
    return liquidity


def _primary_blocker(row: dict[str, Any]) -> str:
    blockers = row.get("gate_blockers")
    if isinstance(blockers, list) and blockers:
        return str(blockers[0])
    blockers = row.get("enter_eligibility_blockers")
    if isinstance(blockers, list) and blockers:
        return str(blockers[0])
    return "UNKNOWN"


def _summary_int(summary: dict[str, Any], key: str) -> int:
    value = summary.get(key)
    if value is None and isinstance(summary.get("global_summary"), dict):
        value = summary["global_summary"].get(key)
    if value is None and isinstance(summary.get("performance_summary"), dict):
        value = summary["performance_summary"].get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# P2-E Phase16 Liquidity Reconciliation Diagnosis",
        "",
        f"- total_decisions: {report['total_decisions']}",
        f"- poi_micro_synergy_count: {report['poi_micro_synergy_count']}",
        f"- SWEEP_REVERSAL_count: {report['SWEEP_REVERSAL_count']}",
        f"- liquidity_reconciled_count: {report['liquidity_reconciled_count']}",
        f"- micro_liquidity_confirmed_count: {report['micro_liquidity_confirmed_count']}",
        f"- enter_eligible_count: {report['enter_eligible_count']}",
        f"- risk_multiplier_positive: {report['risk_multiplier_positive']}",
        f"- ENTER_count: {report['ENTER_count']}",
        "",
        "## Synergy True Rows",
    ]
    for row in report.get("synergy_true_rows", []):
        lines.extend(
            [
                "",
                f"- timestamp: {row.get('timestamp')}",
                f"  setup_type: {row.get('setup_type')}",
                f"  liquidity_evidence_source: {row.get('liquidity_evidence_source')}",
                f"  liquidity_reconciled: {row.get('liquidity_reconciled')}",
                f"  readiness_by_section: {row.get('readiness_by_section')}",
                f"  enter_eligible: {row.get('enter_eligible')}",
                f"  risk_multiplier: {row.get('risk_multiplier')}",
                f"  decision: {row.get('decision')}",
                f"  gate_primary_blocker: {row.get('gate_primary_blocker')}",
            ]
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decisions = _load_decisions(args.decisions)
    summary = _load_json(args.summary)
    report = diagnose_phase16_liquidity_reconciliation(decisions, summary, top=args.top)
    output_dir = args.summary.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_json = output_dir / "phase16_liquidity_reconciliation.json"
    output_md = output_dir / "phase16_liquidity_reconciliation.md"
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_json": str(output_json),
                "output_md": str(output_md),
                "total_decisions": report["total_decisions"],
                "liquidity_reconciled_count": report["liquidity_reconciled_count"],
                "enter_eligible_count": report["enter_eligible_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
