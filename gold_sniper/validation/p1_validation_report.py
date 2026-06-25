"""P1 smoke validation report.
This module analyses outputs produced by P1-replay and decides whether the smoke
run is healthy enough to move to the next phase.
It must stay offline-only and broker-free.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json
import math


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class P1SmokeValidationReport:
    run_id: str
    window_start_utc: str
    window_end_utc: str
    success: bool
    status: str
    total_decisions: int
    decision_counts: dict[str, int]
    setup_grade_distribution: dict[str, int]
    veto_breakdown: dict[str, int]
    blocked_stage_breakdown: dict[str, int]
    score_before_veto_avg: float
    score_after_veto_avg: float
    hard_veto_count: int
    replay_invalid_count: int
    evidence_validation_error_count: int
    data_quality: dict[str, Any]
    timezone_news_ok: bool
    distribution_ok: bool
    veto_score_transparency_ok: bool
    determinism_hash: str
    findings: list[ValidationFinding]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["findings"] = [finding.to_dict() for finding in self.findings]
        return payload


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build_validation_report(
    *,
    run_dir: str | Path,
    window_start_utc: str,
    window_end_utc: str,
    min_decisions: int = 100,
    max_top_decision_share: float = 0.98,
    max_replay_invalid_rate: float = 0.02,
    max_evidence_error_rate: float = 0.01,
) -> P1SmokeValidationReport:
    run_path = Path(run_dir)
    summary_path = run_path / "summary.json"
    decisions_path = run_path / "decisions.jsonl"
    findings: list[ValidationFinding] = []

    if not summary_path.exists():
        return _failed_report(
            run_id=run_path.name,
            window_start_utc=window_start_utc,
            window_end_utc=window_end_utc,
            code="SUMMARY_MISSING",
            message=f"summary.json missing in {run_path}",
        )

    summary = load_json(summary_path)
    decisions = load_jsonl(decisions_path)
    p1 = summary.get("p1_replay") or {}
    metadata = summary.get("metadata") or {}
    data_quality = p1.get("data_quality") or metadata.get("data_quality") or {}

    total_decisions = int(p1.get("total_decisions") or len(decisions) or 0)
    decision_counts = _to_int_dict(p1.get("decision_counts") or {})
    setup_grade_distribution = _to_int_dict(p1.get("setup_grade_distribution") or {})
    veto_breakdown = _to_int_dict(p1.get("veto_breakdown") or {})
    blocked_stage_breakdown = _to_int_dict(p1.get("blocked_stage_breakdown") or {})

    score_before = _safe_float(p1.get("score_before_veto_avg"))
    score_after = _safe_float(p1.get("score_after_veto_avg"))
    hard_veto_count = int(p1.get("hard_veto_count") or 0)
    replay_invalid_count = int(p1.get("replay_invalid_count") or 0)
    evidence_error_count = int(p1.get("evidence_validation_error_count") or 0)
    determinism_hash = str(p1.get("determinism_hash") or "")

    # ── distribution ──────────────────────────────────────────────
    distribution_ok = True

    if total_decisions < min_decisions:
        distribution_ok = False
        findings.append(ValidationFinding(
            code="TOTAL_DECISIONS_TOO_LOW",
            severity="BLOCKER",
            message="Smoke replay produced too few decisions for validation.",
            details={"total_decisions": total_decisions, "min_decisions": min_decisions},
        ))

    top_share = _top_share(decision_counts, total_decisions)
    if total_decisions and top_share >= max_top_decision_share:
        distribution_ok = False
        findings.append(ValidationFinding(
            code="DEGENERATE_DECISION_DISTRIBUTION",
            severity="BLOCKER",
            message="One decision type dominates the smoke run.",
            details={"top_share": top_share, "decision_counts": decision_counts},
        ))

    if len(decision_counts) <= 1 and total_decisions > 0:
        distribution_ok = False
        findings.append(ValidationFinding(
            code="SINGLE_DECISION_TYPE_ONLY",
            severity="BLOCKER",
            message="Smoke run has only one decision type.",
            details={"decision_counts": decision_counts},
        ))

    readiness_dist = _to_int_dict(p1.get("readiness_state_distribution") or {})
    if not readiness_dist:
        distribution_ok = False
        findings.append(ValidationFinding(
            code="READINESS_STATE_MISSING",
            severity="BLOCKER",
            message="Replay metrics are missing readiness state distribution.",
        ))
    elif len([key for key, value in readiness_dist.items() if value > 0]) < 2:
        distribution_ok = False
        findings.append(ValidationFinding(
            code="READINESS_DISTRIBUTION_DEGENERATE",
            severity="BLOCKER",
            message="Replay readiness distribution is degenerate.",
            details={"readiness_state_distribution": readiness_dist},
        ))

    if total_decisions > 0 and decision_counts.get("REJECT", 0) == total_decisions:
        distribution_ok = False
        findings.append(ValidationFinding(
            code="DECISION_STILL_ALL_REJECT",
            severity="BLOCKER",
            message="Replay decisions are still 100% REJECT after readiness classification.",
            details={"decision_counts": decision_counts},
        ))

    # ── veto / score transparency ─────────────────────────────────
    veto_score_transparency_ok = True

    if not veto_breakdown:
        veto_score_transparency_ok = False
        findings.append(ValidationFinding(
            code="VETO_BREAKDOWN_MISSING",
            severity="BLOCKER",
            message="Veto breakdown is missing or empty.",
        ))

    if not blocked_stage_breakdown:
        veto_score_transparency_ok = False
        findings.append(ValidationFinding(
            code="BLOCKED_STAGE_BREAKDOWN_MISSING",
            severity="BLOCKER",
            message="Blocked stage breakdown is missing or empty.",
        ))

    if score_before == 0.0 and score_after == 0.0 and total_decisions > 0:
        veto_score_transparency_ok = False
        findings.append(ValidationFinding(
            code="SCORE_TRANSPARENCY_MISSING",
            severity="BLOCKER",
            message="Average scores before/after veto are both zero.",
        ))

    # ── timezone / news ───────────────────────────────────────────
    timezone_news_ok = True

    news_missing = bool(metadata.get("news_calendar_missing"))
    news_empty = bool(metadata.get("news_calendar_empty"))
    news_errors = list(metadata.get("news_calendar_errors") or [])

    if news_missing:
        timezone_news_ok = False
        findings.append(ValidationFinding(
            code="NEWS_CALENDAR_MISSING",
            severity="BLOCKER",
            message="News calendar missing for smoke validation.",
            details={
                "calendar": metadata.get("news_calendar"),
                "loaded_news_events": metadata.get("loaded_news_events"),
                "raw_events_count": metadata.get("news_calendar_raw_events_count"),
                "filtered_events_count": metadata.get("news_calendar_filtered_events_count"),
                "coverage_start_utc": metadata.get("news_calendar_coverage_start_utc"),
                "coverage_end_utc": metadata.get("news_calendar_coverage_end_utc"),
                "source_format": metadata.get("news_calendar_source_format"),
                "errors": news_errors,
            },
        ))
    elif news_empty:
        timezone_news_ok = False
        findings.append(ValidationFinding(
            code="NEWS_CALENDAR_EMPTY",
            severity="BLOCKER",
            message="News calendar file exists but contains zero events for the requested window.",
            details={
                "calendar": metadata.get("news_calendar"),
                "loaded_news_events": metadata.get("loaded_news_events"),
                "raw_events_count": metadata.get("news_calendar_raw_events_count"),
                "filtered_events_count": metadata.get("news_calendar_filtered_events_count"),
                "coverage_start_utc": metadata.get("news_calendar_coverage_start_utc"),
                "coverage_end_utc": metadata.get("news_calendar_coverage_end_utc"),
                "source_format": metadata.get("news_calendar_source_format"),
                "errors": news_errors,
            },
        ))
    elif news_errors:
        timezone_news_ok = False
        findings.append(ValidationFinding(
            code="NEWS_CALENDAR_ERRORS",
            severity="BLOCKER",
            message="News calendar has load errors.",
            details={"errors": news_errors},
        ))

    # ── P2-B data manifest ──────────────────────────────────────────
    data_manifest = metadata.get("data_manifest")
    data_manifest_status = metadata.get("data_manifest_status")

    if not data_manifest:
        timezone_news_ok = False
        findings.append(ValidationFinding(
            code="DATA_MANIFEST_MISSING",
            severity="BLOCKER",
            message="Candle coverage manifest is missing from replay metadata.",
        ))
    elif data_manifest_status == "MISSING":
        timezone_news_ok = False
        findings.append(ValidationFinding(
            code="DATA_COVERAGE_MISSING",
            severity="BLOCKER",
            message="One or more required timeframes are missing from the data root.",
            details={
                "status": data_manifest_status,
                "missing_timeframes": data_manifest.get("missing_timeframes"),
                "partial_timeframes": data_manifest.get("partial_timeframes"),
                "findings": data_manifest.get("findings"),
                "available_start_utc": metadata.get("available_start_utc"),
                "available_end_utc": metadata.get("available_end_utc"),
            },
        ))
    elif data_manifest_status == "PARTIAL":
        timezone_news_ok = False
        findings.append(ValidationFinding(
            code="DATA_COVERAGE_PARTIAL",
            severity="BLOCKER",
            message="One or more timeframes do not fully cover the requested window.",
            details={
                "status": data_manifest_status,
                "missing_timeframes": data_manifest.get("missing_timeframes"),
                "partial_timeframes": data_manifest.get("partial_timeframes"),
                "findings": data_manifest.get("findings"),
                "available_start_utc": metadata.get("available_start_utc"),
                "available_end_utc": metadata.get("available_end_utc"),
            },
        ))

    # ── P2-C faithful execution model ─────────────────────────────
    execution_model = metadata.get("execution_model") or p1.get("execution_model")
    execution_model_required = bool(metadata.get("execution_model_required", True))
    if execution_model_required and not execution_model:
        timezone_news_ok = False
        findings.append(ValidationFinding(
            code="EXECUTION_MODEL_MISSING",
            severity="BLOCKER",
            message="Replay metadata is missing the faithful execution model.",
        ))
    if execution_model:
        profile = execution_model.get("profile") or {}
        spread_points = _safe_float(profile.get("avg_spread_points") or 0)
        slippage_points = _safe_float(execution_model.get("slippage_points") or 0)
        fill_model = execution_model.get("fill_model")
        faithful = bool(metadata.get("p2c_faithful_simulation") or p1.get("p2c_faithful_simulation"))
        validation_errors = list(execution_model.get("validation_errors") or [])
        if spread_points <= 0:
            timezone_news_ok = False
            findings.append(ValidationFinding(
                code="ZERO_COST_EXECUTION_MODEL",
                severity="BLOCKER",
                message="Replay execution model must include a positive spread.",
                details={"spread_points": spread_points},
            ))
        if slippage_points < 0 or not fill_model or validation_errors:
            timezone_news_ok = False
            findings.append(ValidationFinding(
                code="EXECUTION_MODEL_INVALID",
                severity="BLOCKER",
                message="Replay execution model is invalid.",
                details={"slippage_points": slippage_points, "fill_model": fill_model, "validation_errors": validation_errors},
            ))
        if not faithful:
            timezone_news_ok = False
            findings.append(ValidationFinding(
                code="FAITHFUL_SIMULATION_MISSING",
                severity="BLOCKER",
                message="Replay summary does not declare P2-C faithful simulation.",
            ))

    replay_invalid_rate = replay_invalid_count / total_decisions if total_decisions else 1.0
    if replay_invalid_rate > max_replay_invalid_rate:
        timezone_news_ok = False
        findings.append(ValidationFinding(
            code="REPLAY_INVALID_RATE_TOO_HIGH",
            severity="BLOCKER",
            message="Replay invalid decisions exceed tolerance.",
            details={"rate": replay_invalid_rate, "count": replay_invalid_count},
        ))

    evidence_error_rate = evidence_error_count / total_decisions if total_decisions else 1.0
    if evidence_error_rate > max_evidence_error_rate:
        veto_score_transparency_ok = False
        findings.append(ValidationFinding(
            code="EVIDENCE_VALIDATION_ERROR_RATE_TOO_HIGH",
            severity="BLOCKER",
            message="Evidence validation errors exceed tolerance.",
            details={"rate": evidence_error_rate, "count": evidence_error_count},
        ))

    # ── data quality ──────────────────────────────────────────────
    data_quality_ok = _data_quality_ok(data_quality, findings)

    if not determinism_hash:
        findings.append(ValidationFinding(
            code="DETERMINISM_HASH_MISSING",
            severity="BLOCKER",
            message="P1 replay metrics did not produce determinism_hash.",
        ))

    success = (
        distribution_ok
        and veto_score_transparency_ok
        and timezone_news_ok
        and data_quality_ok
        and bool(determinism_hash)
    )

    return P1SmokeValidationReport(
        run_id=str(summary.get("run_id") or run_path.name),
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        success=success,
        status="PASS" if success else "FAIL",
        total_decisions=total_decisions,
        decision_counts=decision_counts,
        setup_grade_distribution=setup_grade_distribution,
        veto_breakdown=veto_breakdown,
        blocked_stage_breakdown=blocked_stage_breakdown,
        score_before_veto_avg=score_before,
        score_after_veto_avg=score_after,
        hard_veto_count=hard_veto_count,
        replay_invalid_count=replay_invalid_count,
        evidence_validation_error_count=evidence_error_count,
        data_quality=data_quality,
        timezone_news_ok=timezone_news_ok,
        distribution_ok=distribution_ok,
        veto_score_transparency_ok=veto_score_transparency_ok,
        determinism_hash=determinism_hash,
        findings=findings,
    )


def save_validation_report(report: P1SmokeValidationReport, output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


# ── helpers ───────────────────────────────────────────────────────

def _failed_report(
    *,
    run_id: str,
    window_start_utc: str,
    window_end_utc: str,
    code: str,
    message: str,
) -> P1SmokeValidationReport:
    finding = ValidationFinding(code=code, severity="BLOCKER", message=message)
    return P1SmokeValidationReport(
        run_id=run_id,
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        success=False,
        status="FAIL",
        total_decisions=0,
        decision_counts={},
        setup_grade_distribution={},
        veto_breakdown={},
        blocked_stage_breakdown={},
        score_before_veto_avg=0.0,
        score_after_veto_avg=0.0,
        hard_veto_count=0,
        replay_invalid_count=0,
        evidence_validation_error_count=0,
        data_quality={},
        timezone_news_ok=False,
        distribution_ok=False,
        veto_score_transparency_ok=False,
        determinism_hash="",
        findings=[finding],
    )


def _replace_findings(report: P1SmokeValidationReport, findings: list[ValidationFinding]) -> P1SmokeValidationReport:
    """Return a new report with findings replaced (used when adapting a _failed_report)."""
    return P1SmokeValidationReport(
        run_id=report.run_id,
        window_start_utc=report.window_start_utc,
        window_end_utc=report.window_end_utc,
        success=report.success,
        status=report.status,
        total_decisions=report.total_decisions,
        decision_counts=report.decision_counts,
        setup_grade_distribution=report.setup_grade_distribution,
        veto_breakdown=report.veto_breakdown,
        blocked_stage_breakdown=report.blocked_stage_breakdown,
        score_before_veto_avg=report.score_before_veto_avg,
        score_after_veto_avg=report.score_after_veto_avg,
        hard_veto_count=report.hard_veto_count,
        replay_invalid_count=report.replay_invalid_count,
        evidence_validation_error_count=report.evidence_validation_error_count,
        data_quality=report.data_quality,
        timezone_news_ok=report.timezone_news_ok,
        distribution_ok=report.distribution_ok,
        veto_score_transparency_ok=report.veto_score_transparency_ok,
        determinism_hash=report.determinism_hash,
        findings=findings,
    )


def _data_quality_ok(data_quality: dict[str, Any], findings: list[ValidationFinding]) -> bool:
    ok = True
    required = ("1m", "5m", "15m", "1H", "4H")
    for timeframe in required:
        report = data_quality.get(timeframe) or {}
        candles = int(report.get("candles") or 0)
        monotonic = bool(report.get("monotonic", False))
        start = report.get("start_utc")
        end = report.get("end_utc")
        checksum = report.get("checksum")
        if candles <= 0:
            ok = False
            findings.append(ValidationFinding(
                code=f"DATA_QUALITY_{timeframe}_EMPTY",
                severity="BLOCKER",
                message=f"{timeframe} candles are missing or empty.",
                details=report,
            ))
        if not monotonic:
            ok = False
            findings.append(ValidationFinding(
                code=f"DATA_QUALITY_{timeframe}_NOT_MONOTONIC",
                severity="BLOCKER",
                message=f"{timeframe} candles are not monotonic.",
                details=report,
            ))
        if not start or not end or not checksum:
            ok = False
            findings.append(ValidationFinding(
                code=f"DATA_QUALITY_{timeframe}_INCOMPLETE",
                severity="BLOCKER",
                message=f"{timeframe} data quality report is incomplete.",
                details=report,
            ))
    return ok


def _top_share(counts: dict[str, int], total: int) -> float:
    if not counts or total <= 0:
        return 1.0
    return max(counts.values()) / total


def _to_int_dict(value: dict[str, Any]) -> dict[str, int]:
    output = {}
    for key, raw in (value or {}).items():
        try:
            output[str(key)] = int(raw)
        except (TypeError, ValueError):
            output[str(key)] = 0
    return output


def _safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number
