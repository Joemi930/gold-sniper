from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from gold_sniper.strategy.poi_readiness_contract import evaluate_poi_contract


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit POI contract contradictions in decisions.jsonl.")
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decisions = _load_decisions(args.decisions)
    report = diagnose_poi_contract(decisions, top=args.top)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "total_decisions_scanned": report["total_decisions_scanned"],
        "poi_suspect_count": report["poi_suspect_count"],
    }, ensure_ascii=False))
    return 0


def diagnose_poi_contract(decisions: list[dict[str, Any]], *, top: int = 50) -> dict[str, Any]:
    status_distribution: Counter[str] = Counter()
    contradiction_distribution: Counter[str] = Counter()
    score_source_distribution: Counter[str] = Counter()
    failure_class_distribution: Counter[str] = Counter()
    semantic_status_distribution: Counter[str] = Counter()
    origin_distribution: Counter[str] = Counter()
    suspects: list[dict[str, Any]] = []

    for decision in decisions:
        poi = _poi_payload(decision)
        selected = poi.get("selected_poi") if isinstance(poi.get("selected_poi"), dict) else {}
        contract = evaluate_poi_contract(poi, selected_poi=selected)
        contract_dict = contract.to_dict()
        quality = contract.quality
        semantic_status = str(poi.get("poi_semantic_status") or contract.semantic_status_raw or "UNKNOWN")
        failure_class = str(poi.get("poi_failure_class") or contract.failure_class or "NONE")
        origin = _quality_origin(quality.score_source, quality.final_poi_quality_score, quality.score_is_computed)

        status_distribution[contract.status.value] += 1
        score_source_distribution[quality.score_source] += 1
        failure_class_distribution[failure_class] += 1
        semantic_status_distribution[semantic_status] += 1
        origin_distribution[origin] += 1
        for contradiction in contract.contradictions:
            contradiction_distribution[str(contradiction)] += 1

        if not _is_poi_suspect(decision, poi, contract_dict):
            continue

        suspects.append(_compact_suspect(decision, poi, contract_dict, origin))

    suspects.sort(key=_suspect_rank, reverse=True)
    return {
        "total_decisions_scanned": len(decisions),
        "poi_suspect_count": len(suspects),
        "poi_contract_status_distribution": dict(status_distribution.most_common()),
        "poi_contradiction_distribution": dict(contradiction_distribution.most_common()),
        "poi_quality_score_source_distribution": dict(score_source_distribution.most_common()),
        "poi_quality_origin_distribution": dict(origin_distribution.most_common()),
        "poi_failure_class_distribution": dict(failure_class_distribution.most_common()),
        "poi_semantic_status_distribution": dict(semantic_status_distribution.most_common()),
        "top_suspects": suspects[: max(int(top), 0)],
    }


def _compact_suspect(
    decision: dict[str, Any],
    poi: dict[str, Any],
    contract: dict[str, Any],
    origin: str,
) -> dict[str, Any]:
    quality = contract.get("quality") if isinstance(contract.get("quality"), dict) else {}
    selected = poi.get("selected_poi") if isinstance(poi.get("selected_poi"), dict) else {}
    return {
        "timestamp": decision.get("timestamp"),
        "setup_type": decision.get("setup_type"),
        "decision": decision.get("decision"),
        "setup_grade": decision.get("setup_grade"),
        "poi_semantic_status": poi.get("poi_semantic_status") or contract.get("semantic_status_raw"),
        "poi_failure_class": poi.get("poi_failure_class") or contract.get("failure_class"),
        "poi_quality_score": quality.get("final_poi_quality_score"),
        "poi_contract_status": contract.get("status"),
        "poi_contract_reason": contract.get("reason"),
        "contradictions": list(contract.get("contradictions") or []),
        "quality_breakdown": quality,
        "source_field": quality.get("score_source"),
        "quality_origin": origin,
        "selected_poi_score": selected.get("score"),
        "mitigation_pct": poi.get("mitigation_pct") or selected.get("mitigation_pct"),
        "lifecycle_state": poi.get("lifecycle_state") or selected.get("lifecycle_state"),
        "lifecycle_normalized": poi.get("lifecycle_normalized") or selected.get("lifecycle_normalized"),
        "architecte_comment": _architecte_comment(contract, origin),
    }


def _is_poi_suspect(
    decision: dict[str, Any],
    poi: dict[str, Any],
    contract: dict[str, Any],
) -> bool:
    semantic = str(poi.get("poi_semantic_status") or contract.get("semantic_status_raw") or "").upper()
    failure = str(poi.get("poi_failure_class") or contract.get("failure_class") or "").upper()
    quality = contract.get("quality") if isinstance(contract.get("quality"), dict) else {}
    final_score = _float_or_none(quality.get("final_poi_quality_score"))
    signals = decision.get("setup_signal_inventory") or {}
    readiness_watch_with_bounds = (
        isinstance(signals, dict)
        and signals.get("price_bounds_present") is True
        and str(decision.get("poi_execution_readiness") or "").upper() == "WATCH_ONLY"
    )
    return bool(
        contract.get("contradictions")
        or ("PRESENT" in semantic and ("EXECUTABLE" in semantic or "READY" in semantic) and final_score == 0.0)
        or ("PRESENT" in semantic and ("EXECUTABLE" in semantic or "READY" in semantic) and final_score is None)
        or "REJECTED" in failure
        or readiness_watch_with_bounds
    )


def _quality_origin(score_source: str, final_score: Any, score_is_computed: bool) -> str:
    score = _float_or_none(final_score)
    if score_source == "DEFAULT_OR_LEGACY_ZERO":
        return "DEFAULT_ZERO_OR_LEGACY"
    if score_source == "MISSING":
        return "QUALITY_NOT_COMPUTED"
    if score == 0.0 and score_is_computed:
        return "COMPUTED_ZERO"
    return "QUALITY_AVAILABLE"


def _architecte_comment(contract: dict[str, Any], origin: str) -> str:
    contradictions = set(contract.get("contradictions") or [])
    if "EXECUTABLE_WITH_ZERO_QUALITY" in contradictions and "EXECUTABLE_WITH_REJECTED_FAILURE_CLASS" in contradictions:
        return "Executable semantic conflicts with zero-quality rejected POI; contract downgrades it for audit."
    if contract.get("status") == "POI_TOO_WEAK":
        return "POI is observable but not ready because quality/failure evidence is weak."
    if origin == "QUALITY_NOT_COMPUTED":
        return "POI bounds exist but no computed quality source was found."
    return "POI contract suspect retained for line-by-line audit."


def _suspect_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    contradictions = len(row.get("contradictions") or [])
    is_sweep = 1 if row.get("setup_type") == "SWEEP_REVERSAL" else 0
    grade_rank = {"A_PLUS": 4, "A": 3, "B": 2, "C": 1}.get(str(row.get("setup_grade")), 0)
    return contradictions, is_sweep, grade_rank


def _poi_payload(decision: dict[str, Any]) -> dict[str, Any]:
    bundle = decision.get("p1_evidence_bundle") or {}
    if isinstance(bundle, dict) and isinstance(bundle.get("poi"), dict):
        return bundle["poi"]
    poi = decision.get("poi")
    return poi if isinstance(poi, dict) else {}


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
