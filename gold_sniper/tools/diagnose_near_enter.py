from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


NEAR_MISS_DECISIONS = {"WATCH_ONLY", "WAIT_FOR_TRIGGER"}
DEFAULT_SETUP_TYPES = {
    "UNKNOWN",
    "POI_REACTION",
    "SWEEP_REVERSAL",
    "CONTINUATION_LIGHT",
    "REVERSAL_LIGHT",
    "OTE_PULLBACK",
}
GRADE_RANK = {"D": 0, "C": 1, "B": 2, "A": 3, "A_PLUS": 4}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose top near-enter decisions from decisions.jsonl.")
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--include-unknown", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-poi-reaction", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--include-setup-types",
        default=None,
        help="Comma-separated setup types to audit, for example UNKNOWN,POI_REACTION,SWEEP_REVERSAL.",
    )
    parser.add_argument("--min-grade", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_types = _parse_setup_types(args.include_setup_types) or set(DEFAULT_SETUP_TYPES)
    if not args.include_unknown:
        setup_types.discard("UNKNOWN")
    if not args.include_poi_reaction:
        setup_types.discard("POI_REACTION")
    if not setup_types:
        setup_types = set(DEFAULT_SETUP_TYPES)

    decisions = _load_decisions(args.decisions)
    top = diagnose_near_miss(
        decisions,
        top=args.top,
        setup_types=setup_types,
        min_grade=args.min_grade,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "total_decisions_scanned": top["total_decisions_scanned"],
        "near_miss_count": top["near_miss_count"],
        "near_miss_scanned_count": top["near_miss_scanned_count"],
        "near_miss_selected_top_count": top["near_miss_selected_top_count"],
    }, ensure_ascii=False))
    return 0


def diagnose_near_miss(
    decisions: list[dict[str, Any]],
    *,
    top: int = 50,
    setup_types: set[str] | None = None,
    min_grade: str | None = None,
) -> dict[str, Any]:
    setup_filter = setup_types or set(DEFAULT_SETUP_TYPES)
    min_grade_rank = _grade_rank(min_grade) if min_grade else None
    scored: list[tuple[float, dict[str, Any]]] = []
    scored_by_setup: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    candidate_type_distribution: Counter[str] = Counter()
    main_missing_signal_distribution: Counter[str] = Counter()
    main_blocker_distribution: Counter[str] = Counter()
    setup_type_distribution_scanned: Counter[str] = Counter()
    near_miss_with_candidates_count = 0

    for decision in decisions:
        setup_type = str(decision.get("setup_type") or "UNKNOWN")
        if setup_type not in setup_filter:
            continue
        if str(decision.get("decision") or "UNKNOWN") not in NEAR_MISS_DECISIONS:
            continue
        if min_grade_rank is not None and _grade_rank(decision.get("setup_grade")) < min_grade_rank:
            continue

        signals = _signals(decision)
        candidates = decision.get("setup_candidates") or []
        if not _has_useful_signal(decision, signals, candidates):
            continue

        score = near_miss_score(decision, signals, candidates)
        row = _compact_row(decision, score)
        scored.append((score, row))
        scored_by_setup[setup_type].append((score, row))
        setup_type_distribution_scanned[setup_type] += 1
        if candidates:
            near_miss_with_candidates_count += 1

        for candidate in candidates if isinstance(candidates, list) else []:
            if isinstance(candidate, dict):
                candidate_type_distribution[str(candidate.get("candidate_type") or "UNKNOWN")] += 1
        for missing in row["signals_missing"]:
            main_missing_signal_distribution[str(missing)] += 1
        blockers = row["enter_eligibility_blockers"] or [row["enter_eligibility_reason"]]
        for blocker in blockers:
            main_blocker_distribution[str(blocker or "UNKNOWN")] += 1

    scored.sort(key=lambda item: item[0], reverse=True)
    top_rows = _ranked_rows(scored, max(int(top), 0))
    top_by_setup_type = {
        setup_type: _ranked_rows(rows, 10)
        for setup_type, rows in sorted(scored_by_setup.items())
    }

    return {
        "total_decisions_scanned": len(decisions),
        "near_miss_scanned_count": len(scored),
        "near_miss_with_candidates_count": near_miss_with_candidates_count,
        "near_miss_selected_top_count": len(top_rows),
        "near_miss_count": len(top_rows),
        "top_overall": top_rows,
        "top": top_rows,
        "top_by_setup_type": top_by_setup_type,
        "setup_type_distribution_scanned": dict(setup_type_distribution_scanned.most_common()),
        "near_miss_by_current_setup_type": dict(setup_type_distribution_scanned.most_common()),
        "candidate_type_distribution": dict(candidate_type_distribution.most_common()),
        "near_miss_candidate_type_distribution": dict(candidate_type_distribution.most_common()),
        "main_missing_signal_distribution": dict(main_missing_signal_distribution.most_common(25)),
        "main_blocker_distribution": dict(main_blocker_distribution.most_common(25)),
    }


def near_miss_score(
    decision: dict[str, Any],
    signals: dict[str, Any] | None = None,
    candidates: list[Any] | None = None,
) -> float:
    signals = signals if isinstance(signals, dict) else _signals(decision)
    candidates = candidates if isinstance(candidates, list) else decision.get("setup_candidates") or []
    score = 0.0
    if decision.get("setup_type") in {"UNKNOWN", "POI_REACTION"}:
        score += 10.0
    if decision.get("decision") in NEAR_MISS_DECISIONS:
        score += 10.0
    grade = decision.get("setup_grade")
    if grade == "B":
        score += 8.0
    elif grade == "C":
        score += 5.0
    if signals.get("poi_present"):
        score += 5.0
    if signals.get("trend_aligned_poi") or signals.get("counter_trend_poi"):
        score += 5.0
    if signals.get("micro_waiting") or signals.get("micro_partial"):
        score += 5.0
    if signals.get("liquidity_waiting") or signals.get("sweep_detected"):
        score += 5.0
    if signals.get("in_ote") or signals.get("timing_ready"):
        score += 5.0
    confidences = [
        _float(candidate.get("confidence"))
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    if confidences:
        score += max(confidences) * 10.0
    return round(score, 4)


def _compact_row(decision: dict[str, Any], score: float) -> dict[str, Any]:
    signals = _signals(decision)
    candidates = decision.get("setup_candidates") or []
    best_candidate = _best_candidate(decision)
    signals_present = _signals_present(decision, signals)
    signals_missing = _signals_missing(decision, signals, best_candidate)
    candidate_types = [
        str(candidate.get("candidate_type") or "UNKNOWN")
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    blockers = [
        str(blocker)
        for blocker in (decision.get("enter_eligibility_blockers") or [])
    ]
    return {
        "rank": 0,
        "near_miss_score": score,
        "decision_id": decision.get("decision_id") or decision.get("id") or decision.get("bar_index"),
        "timestamp": decision.get("timestamp"),
        "decision": decision.get("decision"),
        "setup_type": decision.get("setup_type"),
        "setup_grade": decision.get("setup_grade"),
        "readiness_state": decision.get("readiness_state"),
        "enter_eligible": bool(decision.get("enter_eligible")),
        "enter_eligibility_reason": decision.get("enter_eligibility_reason"),
        "enter_eligibility_blockers": blockers,
        "risk_reason": decision.get("risk_reason"),
        "signals_present": signals_present,
        "signals_missing": signals_missing,
        "best_candidate": best_candidate,
        "candidate_types": candidate_types,
        "architecte_comment": _architecte_comment(decision, signals_missing, blockers, best_candidate),
    }


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _parse_setup_types(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        item.strip().upper()
        for item in value.split(",")
        if item.strip()
    }


def _ranked_rows(scored: list[tuple[float, dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    rows = [dict(row) for _, row in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    return rows


def _signals(decision: dict[str, Any]) -> dict[str, Any]:
    signals = decision.get("setup_signal_inventory")
    if isinstance(signals, dict) and signals:
        return signals
    return _signals_from_bundle(decision.get("p1_evidence_bundle") or {})


def _signals_from_bundle(bundle: Any) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        return {}
    context = bundle.get("context") if isinstance(bundle.get("context"), dict) else {}
    poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
    liquidity = bundle.get("liquidity") if isinstance(bundle.get("liquidity"), dict) else {}
    micro = bundle.get("micro") if isinstance(bundle.get("micro"), dict) else {}
    raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
    timing = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}
    poi_present = bool(poi.get("selected_poi") or poi.get("price_bounds") or poi.get("poi_available"))
    micro_state = str(micro.get("readiness_state") or micro.get("execution_readiness") or "").upper()
    liquidity_state = str(liquidity.get("readiness_state") or liquidity.get("execution_readiness") or "").upper()
    micro_partial = bool(
        micro.get("reclaim_confirmed")
        or micro.get("trigger_inside_poi")
        or micro.get("displacement_present")
        or micro.get("retest_confirmed")
    )
    return {
        "poi_present": poi_present,
        "trend_aligned_poi": bool(poi.get("aligned_with_context")),
        "counter_trend_poi": bool(poi.get("opposes_htf_dol")),
        "micro_waiting": "WAIT" in micro_state,
        "micro_partial": micro_partial,
        "liquidity_waiting": "WAIT" in liquidity_state,
        "sweep_detected": bool(liquidity.get("sweep_detected")),
        "in_ote": bool(context.get("in_ote") or timing.get("in_ote")),
        "timing_ready": str(timing.get("readiness_state") or "").upper() == "READY",
        "present_signals": _fallback_present_signals(
            poi_present=poi_present,
            micro_waiting="WAIT" in micro_state,
            micro_partial=micro_partial,
            liquidity_waiting="WAIT" in liquidity_state,
            sweep_detected=bool(liquidity.get("sweep_detected")),
            in_ote=bool(context.get("in_ote") or timing.get("in_ote")),
        ),
        "missing_core": [],
    }


def _fallback_present_signals(**flags: bool) -> list[str]:
    mapping = {
        "poi_present": "POI_PRESENT",
        "micro_waiting": "MICRO_WAITING",
        "micro_partial": "MICRO_PARTIAL",
        "liquidity_waiting": "LIQUIDITY_WAITING",
        "sweep_detected": "SWEEP_DETECTED",
        "in_ote": "IN_OTE",
    }
    return [label for key, label in mapping.items() if flags.get(key)]


def _has_useful_signal(
    decision: dict[str, Any],
    signals: dict[str, Any],
    candidates: Any,
) -> bool:
    if candidates:
        return True
    if decision.get("setup_grade") in {"B", "C"}:
        return True
    risk_preview = decision.get("risk_preview") or {}
    if isinstance(risk_preview, dict) and _float(risk_preview.get("risk_pct")) > 0.0:
        return True
    return any(
        bool(signals.get(key))
        for key in (
            "poi_present",
            "direction_known",
            "in_ote",
            "micro_waiting",
            "micro_partial",
            "liquidity_waiting",
            "sweep_detected",
            "trend_aligned_poi",
            "counter_trend_poi",
            "timing_ready",
        )
    )


def _best_candidate(decision: dict[str, Any]) -> dict[str, Any]:
    best = decision.get("best_setup_candidate")
    if isinstance(best, dict) and best:
        return best
    candidates = decision.get("setup_candidates") or []
    if not isinstance(candidates, list):
        return {}
    return max(
        (candidate for candidate in candidates if isinstance(candidate, dict)),
        key=lambda candidate: _float(candidate.get("confidence")),
        default={},
    )


def _signals_present(decision: dict[str, Any], signals: dict[str, Any]) -> list[str]:
    direct = decision.get("near_miss_present_signals")
    if isinstance(direct, list) and direct:
        return [str(item) for item in direct]
    present = signals.get("present_signals")
    return [str(item) for item in present] if isinstance(present, list) else []


def _signals_missing(
    decision: dict[str, Any],
    signals: dict[str, Any],
    best_candidate: dict[str, Any],
) -> list[str]:
    direct = decision.get("near_miss_missing_signals")
    if isinstance(direct, list) and direct:
        return [str(item) for item in direct]
    if isinstance(best_candidate.get("missing"), list):
        return [str(item) for item in best_candidate.get("missing") or []]
    missing_core = signals.get("missing_core")
    return [str(item) for item in missing_core] if isinstance(missing_core, list) else []


def _architecte_comment(
    decision: dict[str, Any],
    signals_missing: list[str],
    blockers: list[str],
    best_candidate: dict[str, Any],
) -> str:
    if decision.get("enter_eligible"):
        return "Near-miss devenu eligible; verification PDE/risk requise."
    if blockers:
        return f"Bloque par {blockers[0]}."
    if signals_missing:
        return f"Preuve manquante principale: {signals_missing[0]}."
    if best_candidate:
        return "Candidat detecte mais conditions ENTER non reunies."
    return "Near-miss conserve pour audit; preuve utile partielle seulement."


def _grade_rank(value: Any) -> int:
    return GRADE_RANK.get(str(value or "D").upper().replace("+", "_PLUS"), 0)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    raise SystemExit(main())
