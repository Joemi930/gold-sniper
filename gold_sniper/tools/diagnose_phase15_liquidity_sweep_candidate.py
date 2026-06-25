from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


TRADABLE_SETUP_TYPES = {
    "SWEEP_REVERSAL",
    "REVERSAL_STRICT",
    "REVERSAL_LIGHT",
    "CONTINUATION_STRICT",
    "CONTINUATION_LIGHT",
    "OTE_PULLBACK",
    "FAILED_AUCTION_RECLAIM",
    "SESSION_REVERSAL_MEDIUM",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose Phase15 liquidity/sweep evidence and tradable candidate formation."
    )
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser


def diagnose_phase15_liquidity_sweep_candidate(
    decisions: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    *,
    top: int = 50,
) -> dict[str, Any]:
    original = [dict(item) for item in decisions]
    summary = summary or {}

    synergy_setup_type_distribution: Counter[str] = Counter()
    synergy_candidate_distribution: Counter[str] = Counter()
    synergy_gate_primary_blocker_distribution: Counter[str] = Counter()
    sweep_source_distribution: Counter[str] = Counter()
    agent3_liquidity_status_distribution: Counter[str] = Counter()
    root_cause_distribution: Counter[str] = Counter()

    synergy_true_cases: list[dict[str, Any]] = []
    micro_sweep_confirmed_count = 0
    agent3_sweep_detected_count = 0
    micro_sweep_without_agent3_sweep_count = 0
    tradable_candidate_count = 0
    enter_eligible_count = 0
    risk_multiplier_positive = 0
    enter_count = 0

    for row in decisions:
        signals = _signals(row)
        liquidity = _bundle_section(row, "liquidity")
        agent3_sweep = _agent3_sweep_detected(row, signals, liquidity)
        micro_sweep = _micro_sweep_confirmed(row, signals)
        source = _sweep_source(row, signals, agent3_sweep=agent3_sweep, micro_sweep=micro_sweep)
        candidates = _candidate_list(row.get("setup_candidates"))
        has_tradable_candidate = any(_candidate_type(item) in TRADABLE_SETUP_TYPES for item in candidates)
        liquidity_status = _liquidity_status(liquidity)

        sweep_source_distribution[source] += 1
        agent3_liquidity_status_distribution[liquidity_status] += 1

        if micro_sweep:
            micro_sweep_confirmed_count += 1
        if agent3_sweep:
            agent3_sweep_detected_count += 1
        if micro_sweep and not agent3_sweep:
            micro_sweep_without_agent3_sweep_count += 1
        if has_tradable_candidate:
            tradable_candidate_count += 1
        if bool(row.get("enter_eligible")):
            enter_eligible_count += 1
        if _float(row.get("risk_multiplier")) > 0.0:
            risk_multiplier_positive += 1
        if str(row.get("decision") or "").upper() in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}:
            enter_count += 1

        if _synergy_true(row):
            case = _compact_synergy_case(row)
            root_cause_distribution[str(case["root_cause_guess"])] += 1
            synergy_true_cases.append(case)
            synergy_setup_type_distribution[str(row.get("setup_type") or "UNKNOWN")] += 1
            synergy_gate_primary_blocker_distribution[str(row.get("gate_primary_blocker") or "UNKNOWN")] += 1
            if candidates:
                for candidate in candidates:
                    synergy_candidate_distribution[_candidate_type(candidate)] += 1
            else:
                synergy_candidate_distribution["NONE"] += 1

    assert decisions == original

    synergy_true_cases.sort(key=lambda item: str(item.get("ts_utc") or ""))
    dominant_root_cause = (
        root_cause_distribution.most_common(1)[0][0]
        if root_cause_distribution
        else "UNKNOWN"
    )

    return {
        "total_decisions": len(decisions),
        "synergy_true_count": len(synergy_true_cases),
        "synergy_true_cases": synergy_true_cases[: max(int(top), 0)],
        "synergy_setup_type_distribution": dict(synergy_setup_type_distribution.most_common()),
        "synergy_candidate_distribution": dict(synergy_candidate_distribution.most_common()),
        "synergy_gate_primary_blocker_distribution": dict(synergy_gate_primary_blocker_distribution.most_common()),
        "sweep_source_distribution": dict(sweep_source_distribution.most_common()),
        "agent3_liquidity_status_distribution": dict(agent3_liquidity_status_distribution.most_common()),
        "micro_sweep_confirmed_count": micro_sweep_confirmed_count,
        "agent3_sweep_detected_count": agent3_sweep_detected_count,
        "micro_sweep_without_agent3_sweep_count": micro_sweep_without_agent3_sweep_count,
        "tradable_candidate_count": tradable_candidate_count,
        "enter_eligible_count": enter_eligible_count,
        "risk_multiplier_positive": risk_multiplier_positive,
        "enter_count": enter_count,
        "filled_trades": _summary_int(summary, "filled_trades"),
        "missed_entries": _summary_int(summary, "missed_entries"),
        "dominant_root_cause": dominant_root_cause,
    }


def _compact_synergy_case(row: dict[str, Any]) -> dict[str, Any]:
    signals = _signals(row)
    liquidity = _bundle_section(row, "liquidity")
    micro = _bundle_section(row, "micro")
    poi = _bundle_section(row, "poi")
    timing = _timing(row)
    candidates = _candidate_list(row.get("setup_candidates"))
    best = row.get("best_setup_candidate") if isinstance(row.get("best_setup_candidate"), dict) else {}
    agent3_sweep = _agent3_sweep_detected(row, signals, liquidity)
    micro_sweep = _micro_sweep_confirmed(row, signals)
    liquidity_ready = bool(signals.get("liquidity_ready"))
    liquidity_waiting = bool(signals.get("liquidity_waiting")) or "WAIT" in _liquidity_status(liquidity)
    case = {
        "ts_utc": row.get("timestamp") or row.get("ts_utc"),
        "setup_type": row.get("setup_type"),
        "decision": row.get("decision"),
        "gate_primary_blocker": row.get("gate_primary_blocker"),
        "present_signals": _string_list(signals.get("present_signals")),
        "near_miss_missing_signals": _string_list(row.get("near_miss_missing_signals")),
        "setup_candidates": candidates,
        "best_setup_candidate": best,
        "liquidity_state": liquidity.get("liquidity_state") or _liquidity_status(liquidity),
        "liquidity_ready": liquidity_ready,
        "liquidity_waiting": liquidity_waiting,
        "agent3_sweep_detected": agent3_sweep,
        "micro_sweep_1m_confirmed": micro_sweep,
        "choch_detected": _bool_any(row.get("choch_detected"), micro.get("choch_detected"), signals.get("choch_detected")),
        "trigger_inside_poi": _bool_any(row.get("trigger_inside_poi"), micro.get("trigger_inside_poi"), signals.get("trigger_inside_poi")),
        "micro_confirmed": _bool_any(row.get("micro_confirmed"), signals.get("micro_confirmed"), micro.get("micro_is_confirmed")),
        "micro_inside_poi": _bool_any(row.get("micro_inside_poi"), signals.get("micro_inside_poi"), micro.get("price_in_agent2_poi")),
        "timing_ready": _bool_any(signals.get("timing_ready"), str(timing.get("readiness_state") or "").upper() == "READY"),
        "in_ote": _bool_any(signals.get("in_ote"), timing.get("in_ote")),
        "poi_type": signals.get("poi_type") or poi.get("poi_type"),
        "trend_aligned_poi": bool(signals.get("trend_aligned_poi")),
        "counter_trend_poi": bool(signals.get("counter_trend_poi")),
        "setup_sweep_evidence": bool(signals.get("setup_sweep_evidence") or row.get("setup_sweep_evidence")),
        "setup_sweep_evidence_source": (
            row.get("setup_sweep_evidence_source")
            or signals.get("setup_sweep_evidence_source")
            or "NONE"
        ),
    }
    case["root_cause_guess"] = _root_cause_guess(case, candidates)
    return case


def _root_cause_guess(case: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    has_tradable = any(_candidate_type(item) in TRADABLE_SETUP_TYPES for item in candidates)
    if case["micro_sweep_1m_confirmed"] and not case["agent3_sweep_detected"] and not case["setup_sweep_evidence"]:
        return "MICRO_SWEEP_EVIDENCE_NOT_VISIBLE_TO_CANDIDATE_MAPPING"
    if case["micro_sweep_1m_confirmed"] and not case["agent3_sweep_detected"] and has_tradable and case["liquidity_waiting"]:
        return "MICRO_SWEEP_CANDIDATE_BLOCKED_BY_AGENT3_LIQUIDITY_WAITING"
    if case["setup_type"] == "POI_REACTION" and not has_tradable:
        return "NO_TRADABLE_CANDIDATE_BY_DESIGN"
    if has_tradable and case["liquidity_waiting"]:
        return "TRADABLE_CANDIDATE_BLOCKED_BY_LIQUIDITY_WAITING"
    return "UNKNOWN"


def _signals(row: dict[str, Any]) -> dict[str, Any]:
    direct = row.get("setup_signal_inventory")
    if isinstance(direct, dict) and direct:
        return direct
    classification = row.get("setup_classification") if isinstance(row.get("setup_classification"), dict) else {}
    evidence = classification.get("evidence") if isinstance(classification.get("evidence"), dict) else {}
    signals = evidence.get("signals") if isinstance(evidence.get("signals"), dict) else {}
    return signals


def _bundle_section(row: dict[str, Any], section: str) -> dict[str, Any]:
    bundle = row.get("p1_evidence_bundle") if isinstance(row.get("p1_evidence_bundle"), dict) else {}
    value = bundle.get(section) if isinstance(bundle.get(section), dict) else {}
    return value


def _timing(row: dict[str, Any]) -> dict[str, Any]:
    bundle = row.get("p1_evidence_bundle") if isinstance(row.get("p1_evidence_bundle"), dict) else {}
    raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
    timing = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}
    return timing


def _candidate_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _candidate_type(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_type") or "UNKNOWN").upper()


def _synergy_true(row: dict[str, Any]) -> bool:
    if row.get("poi_micro_synergy") is True:
        return True
    payload = row.get("poi_micro_synergy_payload") if isinstance(row.get("poi_micro_synergy_payload"), dict) else {}
    return bool(payload.get("synergy"))


def _agent3_sweep_detected(row: dict[str, Any], signals: dict[str, Any], liquidity: dict[str, Any]) -> bool:
    return _bool_any(
        signals.get("sweep_detected"),
        liquidity.get("sweep_detected"),
        liquidity.get("sweep"),
        str(liquidity.get("liquidity_state") or "").upper() == "SWEEP",
        str(liquidity.get("event") or "").upper() == "SWEEP",
    )


def _micro_sweep_confirmed(row: dict[str, Any], signals: dict[str, Any]) -> bool:
    micro = _bundle_section(row, "micro")
    evidence = row.get("micro_evidence") if isinstance(row.get("micro_evidence"), dict) else {}
    return _bool_any(
        row.get("micro_sweep_confirmed"),
        signals.get("micro_sweep_confirmed"),
        row.get("sweep_1m_confirmed"),
        micro.get("sweep_1m_confirmed"),
        evidence.get("sweep_1m_confirmed"),
    )


def _sweep_source(
    row: dict[str, Any],
    signals: dict[str, Any],
    *,
    agent3_sweep: bool,
    micro_sweep: bool,
) -> str:
    explicit = row.get("setup_sweep_evidence_source") or signals.get("setup_sweep_evidence_source")
    if explicit:
        return str(explicit)
    if agent3_sweep:
        return "AGENT3"
    if micro_sweep:
        return "MICRO_CONTRACT"
    return "NONE"


def _liquidity_status(liquidity: dict[str, Any]) -> str:
    return str(
        liquidity.get("readiness_state")
        or liquidity.get("execution_readiness")
        or liquidity.get("liquidity_semantic_status")
        or "UNKNOWN"
    ).upper()


def _summary_int(summary: dict[str, Any], key: str) -> int:
    value = summary.get(key)
    if value is None and isinstance(summary.get("metrics"), dict):
        value = summary["metrics"].get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _bool_any(*values: Any) -> bool:
    return any(bool(value) for value in values)


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
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# P2-E Phase15 Liquidity/Sweep Candidate Diagnosis",
        "",
        f"- total_decisions: {report['total_decisions']}",
        f"- synergy_true_count: {report['synergy_true_count']}",
        f"- dominant_root_cause: {report['dominant_root_cause']}",
        f"- micro_sweep_confirmed_count: {report['micro_sweep_confirmed_count']}",
        f"- agent3_sweep_detected_count: {report['agent3_sweep_detected_count']}",
        f"- micro_sweep_without_agent3_sweep_count: {report['micro_sweep_without_agent3_sweep_count']}",
        f"- tradable_candidate_count: {report['tradable_candidate_count']}",
        f"- enter_eligible_count: {report['enter_eligible_count']}",
        f"- risk_multiplier_positive: {report['risk_multiplier_positive']}",
        f"- enter_count: {report['enter_count']}",
        "",
        "## Synergy True Cases",
    ]
    for case in report.get("synergy_true_cases", []):
        lines.extend(
            [
                "",
                f"- ts_utc: {case.get('ts_utc')}",
                f"  setup_type: {case.get('setup_type')}",
                f"  decision: {case.get('decision')}",
                f"  gate_primary_blocker: {case.get('gate_primary_blocker')}",
                f"  agent3_sweep_detected: {case.get('agent3_sweep_detected')}",
                f"  micro_sweep_1m_confirmed: {case.get('micro_sweep_1m_confirmed')}",
                f"  setup_sweep_evidence_source: {case.get('setup_sweep_evidence_source')}",
                f"  liquidity_state: {case.get('liquidity_state')}",
                f"  root_cause_guess: {case.get('root_cause_guess')}",
            ]
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decisions = _load_decisions(args.decisions)
    summary = _load_json(args.summary)
    report = diagnose_phase15_liquidity_sweep_candidate(decisions, summary, top=args.top)
    output_dir = args.summary.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_json = output_dir / "phase15_diagnosis.json"
    output_md = output_dir / "phase15_diagnosis.md"
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_json": str(output_json),
                "output_md": str(output_md),
                "total_decisions": report["total_decisions"],
                "synergy_true_count": report["synergy_true_count"],
                "dominant_root_cause": report["dominant_root_cause"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
