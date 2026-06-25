"""Phase 7 full historical replay pack for the unified XAUUSD shadow strategy."""

from __future__ import annotations

import csv
import argparse
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gold_sniper.replay.news_loader import (
    DEFAULT_NEWS_CACHE,
    NewsCache,
    evaluate_news_for_timestamp,
    load_local_news_cache,
    write_news_loading_summary,
)
from gold_sniper.replay.offline_evidence_builder import OfflineEvidenceBuilder
from gold_sniper.strategy.xauusd_killzone_model import evaluate_xauusd_killzone
from gold_sniper.replay.replay_metrics import build_replay_metrics
from gold_sniper.replay.replay_report import (
    write_evidence_reconstruction_summary,
    write_final_opus_dossier,
    write_modifications_summary_for_opus,
    write_phase_7_replay_summary,
)
from gold_sniper.strategy.unified_xauusd_strategy import evaluate_unified_xauusd_strategy


DEFAULT_DATA_ROOT = Path("gold_sniper/data/historical/XAUUSD")
DEFAULT_REPORT_ROOT = Path("reports/replay")


@dataclass(frozen=True)
class HistoricalDatasetProfile:
    symbol: str
    timeframe: str
    path: str
    rows: int
    date_start: str | None
    date_end: str | None
    columns: list[str]
    timezone: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "path": self.path,
            "rows": self.rows,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "columns": self.columns,
            "timezone": self.timezone,
        }


def scan_local_xauusd_datasets(data_root: Path = DEFAULT_DATA_ROOT) -> list[HistoricalDatasetProfile]:
    profiles: list[HistoricalDatasetProfile] = []
    for timeframe in ("1m", "15m", "4H"):
        folder = data_root / timeframe
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.csv")):
            profiles.append(_profile_csv(path, timeframe))
    return profiles


def run_phase_7_replay_pack(
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    news_cache_path: Path = DEFAULT_NEWS_CACHE,
    timeframe: str = "15m",
    max_events: int | None = None,
) -> dict[str, Any]:
    profiles = scan_local_xauusd_datasets(data_root)
    news_cache = load_local_news_cache(news_cache_path)
    selected = next((profile for profile in profiles if profile.timeframe == timeframe), None)
    if selected is None or selected.rows == 0:
        metrics = _blocking_metrics(profiles, timeframe)
        metrics.update(news_cache.summary())
        metrics.update(_news_decision_counts([]))
        _write_outputs(report_root, metrics, [], "FAILED_CLEANLY", news_cache)
        return {"process_status": "FAILED_CLEANLY", "metrics": metrics, "decisions": []}
    if not news_cache.loaded:
        metrics = _news_blocking_metrics(selected, profiles)
        metrics.update(news_cache.summary())
        metrics.update(_news_decision_counts([]))
        _write_outputs(report_root, metrics, [], "FAILED_CLEANLY", news_cache)
        return {"process_status": "FAILED_CLEANLY", "metrics": metrics, "decisions": []}

    rows = _read_csv_rows(Path(selected.path), max_events=max_events)
    evidence_builder = OfflineEvidenceBuilder.from_data_root(data_root)
    decisions = run_replay_on_rows(rows, symbol=selected.symbol, timeframe=selected.timeframe, news_cache=news_cache, evidence_builder=evidence_builder)
    metrics = build_replay_metrics(
        decisions,
        symbol=selected.symbol,
        timeframe=selected.timeframe,
        date_start=selected.date_start,
        date_end=selected.date_end,
        data_profile={"selected": selected.to_dict(), "available": [profile.to_dict() for profile in profiles]},
        replay_candles_1m=evidence_builder.candles_1m,
        replay_candles_15m=evidence_builder.candles_15m,
    )
    metrics.update(news_cache.summary())
    metrics.update(_news_decision_counts(decisions))
    _write_outputs(report_root, metrics, decisions, "TERMINATED_CLEANLY", news_cache)
    return {"process_status": "TERMINATED_CLEANLY", "metrics": metrics, "decisions": decisions}


def run_phase_7_preflight(
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    news_cache_path: Path = DEFAULT_NEWS_CACHE,
    timeframe: str = "15m",
    sample_events: int = 5,
) -> dict[str, Any]:
    profiles = scan_local_xauusd_datasets(data_root)
    selected = next((profile for profile in profiles if profile.timeframe == timeframe), None)
    news_cache = load_local_news_cache(news_cache_path)
    checks: dict[str, Any] = {
        "news_cache_loaded": news_cache.loaded,
        "has_1m": any(profile.timeframe == "1m" and profile.rows > 0 for profile in profiles),
        "has_15m": selected is not None and selected.rows > 0,
        "has_4H": any(profile.timeframe == "4H" and profile.rows > 0 for profile in profiles),
        "sample_events_requested": sample_events,
        "sample_events_evaluated": 0,
        "sessions_known": False,
        "funnel_trace_present": False,
        "scenarios_present": False,
        "metrics_keys_present": False,
    }
    if selected is None or selected.rows == 0 or not news_cache.loaded:
        return {"process_status": "FAILED_CLEANLY", "preflight": checks}
    rows = _read_csv_rows(Path(selected.path), max_events=sample_events)
    evidence_builder = OfflineEvidenceBuilder.from_data_root(data_root)
    decisions = run_replay_on_rows(rows, symbol=selected.symbol, timeframe=selected.timeframe, news_cache=news_cache, evidence_builder=evidence_builder)
    metrics = build_replay_metrics(
        decisions,
        symbol=selected.symbol,
        timeframe=selected.timeframe,
        date_start=selected.date_start,
        date_end=selected.date_end,
        data_profile={"selected": selected.to_dict(), "available": [profile.to_dict() for profile in profiles]},
        replay_candles_1m=evidence_builder.candles_1m,
        replay_candles_15m=evidence_builder.candles_15m,
    )
    required_metrics = {"funnel_exit_stage_counts", "poi_reject_own_count", "micro_reject_inherited_count", "near_miss_count", "phase_8_ready"}
    checks.update(
        {
            "sample_events_evaluated": len(decisions),
            "sessions_known": all(item.get("session") not in {None, "", "UNKNOWN"} for item in decisions),
            "funnel_trace_present": all(isinstance(item.get("funnel_trace"), dict) for item in decisions),
            "scenarios_present": all(item.get("best_scenario") not in {None, ""} for item in decisions),
            "metrics_keys_present": required_metrics.issubset(metrics),
        }
    )
    status = "PREFLIGHT_OK" if all(checks[key] for key in ("news_cache_loaded", "has_1m", "has_15m", "has_4H", "funnel_trace_present", "metrics_keys_present")) else "FAILED_CLEANLY"
    return {"process_status": status, "preflight": checks, "metrics": metrics}


def run_replay_on_rows(
    rows: list[dict[str, Any]],
    *,
    symbol: str = "XAUUSD",
    timeframe: str = "15m",
    news_cache: NewsCache | None = None,
    evidence_builder: OfflineEvidenceBuilder | None = None,
) -> list[dict[str, Any]]:
    source_rows = deepcopy(rows)
    cache = news_cache or load_local_news_cache()
    decisions: list[dict[str, Any]] = []
    for row in source_rows:
        event = build_event_from_row(row, symbol=symbol, timeframe=timeframe, news_cache=cache, evidence_builder=evidence_builder)
        decision = evaluate_unified_xauusd_strategy(event)
        detail = decision.explanation_detail or {}
        funnel_trace = decision.evidence.get("funnel_trace", {}) if isinstance(decision.evidence, dict) else {}
        news_payload = event.get("agents", {}).get("agent_6", {})
        evidence_flags = event.get("evidence_flags", {})
        agent2 = event.get("agents", {}).get("agent_2", {})
        agent7 = event.get("agents", {}).get("agent_7", {})
        decisions.append(
            {
                "timestamp": row.get("time"),
                "month": str(row.get("time", "UNKNOWN"))[:7] if row.get("time") else "UNKNOWN",
                "symbol": symbol,
                "timeframe": timeframe,
                "session": agent7.get("session_label", "UNKNOWN"),
                "session_allowed": agent7.get("session_allowed"),
                "session_quality": agent7.get("session_quality"),
                "setup_type": decision.setup_type,
                "decision": decision.decision,
                "score": decision.score,
                "confidence": decision.confidence,
                "primary_reason": detail.get("primary_reason"),
                "pipeline_stage": detail.get("pipeline_stage"),
                "trade_readiness": detail.get("trade_readiness"),
                "kasper_alignment": detail.get("kasper_alignment"),
                "missing_conditions": list(decision.missing_conditions),
                "warnings": list(decision.warnings),
                "funnel_trace": funnel_trace,
                "funnel_exit_stage": funnel_trace.get("exit_stage"),
                "funnel_exit_reason": funnel_trace.get("exit_reason"),
                "funnel_near_miss": funnel_trace.get("near_miss"),
                "poi_status": funnel_trace.get("poi_status"),
                "micro_status": funnel_trace.get("micro_status"),
                "best_scenario": funnel_trace.get("best_scenario"),
                "best_scenario_status": funnel_trace.get("best_scenario_status"),
                "close": _float_or_none(row.get("close")),
                "news_clear": news_payload.get("news_clear"),
                "news_veto": news_payload.get("news_veto"),
                "pre_news_lockout": news_payload.get("pre_news_lockout"),
                "post_news_stealth": news_payload.get("post_news_stealth"),
                "calendar_status": news_payload.get("calendar_status"),
                "direction": event.get("context", {}).get("direction"),
                "poi_low": agent2.get("poi_low"),
                "poi_high": agent2.get("poi_high"),
                "htf_context_available": bool(event.get("agents", {}).get("agent_1", {}).get("htf_context_available")),
                "dol_available": bool(event.get("agents", {}).get("agent_1", {}).get("dol_available")),
                "liquidity_story_available": bool(event.get("agents", {}).get("agent_3", {}).get("liquidity_story_available")),
                "poi_available": bool(agent2.get("poi_available")),
                "premium_discount_available": event.get("agents", {}).get("agent_4", {}).get("premium_discount") not in (None, "", "UNKNOWN"),
                "ote_available": bool(event.get("agents", {}).get("agent_4", {}).get("ote_available")),
                "micro_available": bool(event.get("agents", {}).get("agent_5", {}).get("micro_available")),
                "micro_trigger": bool(event.get("agents", {}).get("agent_5", {}).get("micro_trigger")),
                "agent_1_quality": evidence_flags.get("agent_1_quality"),
                "agent_2_quality": evidence_flags.get("agent_2_quality"),
                "agent_3_quality": evidence_flags.get("agent_3_quality"),
                "agent_4_quality": evidence_flags.get("agent_4_quality"),
                "agent_5_quality": evidence_flags.get("agent_5_quality"),
            }
        )
    return decisions


def build_event_from_row(
    row: dict[str, Any],
    *,
    symbol: str = "XAUUSD",
    timeframe: str = "15m",
    news_cache: NewsCache | None = None,
    evidence_builder: OfflineEvidenceBuilder | None = None,
) -> dict[str, Any]:
    timestamp = row.get("time")
    killzone = evaluate_xauusd_killzone(timestamp).to_dict()
    session = killzone.get("session", "UNKNOWN")
    cache = news_cache or load_local_news_cache()
    news_payload = evaluate_news_for_timestamp(timestamp, cache)
    agents = {
        "agent_7": {
            "session": session,
            "session_label": session,
            "session_allowed": killzone.get("session_allowed", False),
            "session_quality": killzone.get("session_quality", "BLOCKED"),
            "ny_time": killzone.get("ny_time"),
            "reason": killzone.get("reason"),
        },
    }
    if news_payload.get("calendar_status") != "NEWS_CONTEXT_MISSING":
        agents["agent_6"] = news_payload
    evidence_context: dict[str, Any] = {}
    evidence_flags: dict[str, Any] = {}
    if evidence_builder is not None:
        offline = evidence_builder.build(row)
        for key, value in offline.get("agents", {}).items():
            if isinstance(value, dict):
                agents[key] = value
        evidence_context = offline.get("context", {}) if isinstance(offline.get("context"), dict) else {}
        evidence_flags = offline.get("evidence_flags", {}) if isinstance(offline.get("evidence_flags"), dict) else {}
    event = {
        "context": {
            **evidence_context,
            "timestamp": timestamp,
            "symbol": symbol,
            "timeframe": timeframe,
            "open": _float_or_none(row.get("open")),
            "high": _float_or_none(row.get("high")),
            "low": _float_or_none(row.get("low")),
            "close": _float_or_none(row.get("close")),
            "tick_volume": _float_or_none(row.get("tick_volume")),
            "session": session,
            "session_label": session,
            "session_allowed": killzone.get("session_allowed", False),
            "session_quality": killzone.get("session_quality", "BLOCKED"),
            "ny_time": killzone.get("ny_time"),
        },
        "agents": agents,
        "spread_risk": {"source": "historical_replay_pack", "spread_available": True, "shadow_only": True},
        "risk": {"source": "historical_replay_pack", "risk_model_available": True, "shadow_only": True},
        "evidence_flags": evidence_flags,
    }
    return event


def classify_session(timestamp: str | None) -> str:
    return evaluate_xauusd_killzone(timestamp).session


def write_decisions_csv(path: Path, decisions: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "symbol",
        "timeframe",
        "session",
        "session_allowed",
        "session_quality",
        "setup_type",
        "decision",
        "score",
        "confidence",
        "primary_reason",
        "pipeline_stage",
        "trade_readiness",
        "kasper_alignment",
        "funnel_exit_stage",
        "funnel_exit_reason",
        "funnel_near_miss",
        "poi_status",
        "micro_status",
        "best_scenario",
        "best_scenario_status",
        "close",
        "calendar_status",
        "news_clear",
        "news_veto",
        "pre_news_lockout",
        "post_news_stealth",
        "htf_context_available",
        "dol_available",
        "liquidity_story_available",
        "poi_available",
        "premium_discount_available",
        "ote_available",
        "micro_available",
        "micro_trigger",
        "agent_1_quality",
        "agent_2_quality",
        "agent_3_quality",
        "agent_4_quality",
        "agent_5_quality",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in decisions:
            writer.writerow({field: item.get(field) for field in fields})


def _write_outputs(
    report_root: Path,
    metrics: dict[str, Any],
    decisions: list[dict[str, Any]],
    process_status: str,
    news_cache: NewsCache,
) -> None:
    report_root.mkdir(parents=True, exist_ok=True)
    (report_root / "phase_7_replay_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    write_decisions_csv(report_root / "phase_7_replay_decisions.csv", decisions)
    write_decisions_csv(report_root / "phase_7_replay_trades.csv", [item for item in decisions if item.get("decision") == "ENTER"])
    write_phase_7_replay_summary(report_root / "phase_7_replay_summary.md", metrics, process_status=process_status)
    write_evidence_reconstruction_summary(report_root / "phase_7_evidence_reconstruction_summary.md", metrics, process_status=process_status)
    write_final_opus_dossier(report_root / "phase_7_final_opus_dossier.md", metrics, process_status=process_status)
    write_modifications_summary_for_opus(report_root / "phase_7_modifications_summary_for_opus.md")
    write_news_loading_summary(report_root / "phase_7_news_loading_summary.md", metrics, news_cache, process_status=process_status)


def _profile_csv(path: Path, timeframe: str) -> HistoricalDatasetProfile:
    rows = 0
    first: dict[str, str] | None = None
    last: dict[str, str] | None = None
    columns: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        for row in reader:
            rows += 1
            if first is None:
                first = row
            last = row
    return HistoricalDatasetProfile(
        symbol="XAUUSD",
        timeframe=timeframe,
        path=str(path),
        rows=rows,
        date_start=(first or {}).get("time"),
        date_end=(last or {}).get("time"),
        columns=columns,
        timezone="UTC_Z_SUFFIX" if str((first or {}).get("time", "")).endswith("Z") else "UNKNOWN",
    )


def _read_csv_rows(path: Path, max_events: int | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
            if max_events is not None and len(rows) >= max_events:
                break
    return rows


def _blocking_metrics(profiles: list[HistoricalDatasetProfile], timeframe: str) -> dict[str, Any]:
    return {
        "symbol": "XAUUSD",
        "timeframes_used": [timeframe],
        "date_start": None,
        "date_end": None,
        "total_bars_or_events": 0,
        "total_decisions": 0,
        "ENTER_count": 0,
        "WAIT_count": 0,
        "REJECT_count": 0,
        "ENTER_rate": 0.0,
        "main_blocking_stage_counts": {},
        "main_missing_condition_counts": {"DATASET_NOT_FOUND_OR_EMPTY": 1},
        "decision_by_session": {},
        "decision_by_month": {},
        "decision_by_setup_type": {},
        "ENTER_by_session": {},
        "ENTER_by_setup_type": {},
        "average_score": 0.0,
        "average_confidence": 0.0,
        "trade_simulation": {"simulation_status": "NOT_AVAILABLE_NO_DATA"},
        "data_profile": {"available": [profile.to_dict() for profile in profiles]},
    }


def _news_blocking_metrics(selected: HistoricalDatasetProfile, profiles: list[HistoricalDatasetProfile]) -> dict[str, Any]:
    return {
        "symbol": selected.symbol,
        "timeframes_used": [selected.timeframe],
        "date_start": selected.date_start,
        "date_end": selected.date_end,
        "total_bars_or_events": selected.rows,
        "total_decisions": 0,
        "ENTER_count": 0,
        "WAIT_count": 0,
        "REJECT_count": 0,
        "ENTER_rate": 0.0,
        "main_blocking_stage_counts": {"NEWS": selected.rows},
        "main_missing_condition_counts": {"NEWS_CACHE_FILE_MISSING": 1},
        "decision_by_session": {},
        "decision_by_month": {},
        "decision_by_setup_type": {},
        "ENTER_by_session": {},
        "ENTER_by_setup_type": {},
        "average_score": 0.0,
        "average_confidence": 0.0,
        "trade_simulation": {"simulation_status": "NOT_AVAILABLE_NEWS_CACHE_MISSING"},
        "data_profile": {"selected": selected.to_dict(), "available": [profile.to_dict() for profile in profiles]},
    }


def _news_decision_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "events_with_news_clear": sum(1 for item in decisions if item.get("news_clear") is True),
        "events_with_news_veto": sum(1 for item in decisions if item.get("news_veto") is True),
        "events_with_pre_news_lockout": sum(1 for item in decisions if item.get("pre_news_lockout") is True),
        "events_with_post_news_stealth": sum(1 for item in decisions if item.get("post_news_stealth") is True),
        "events_with_news_context_missing": sum(
            1 for item in decisions if item.get("calendar_status") == "NEWS_CONTEXT_MISSING" or "NEWS_CONTEXT_MISSING" in (item.get("missing_conditions") or [])
        ),
    }


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 7 historical replay pack")
    parser.add_argument("--preflight-only", action="store_true", help="Run a lightweight readiness check without full replay outputs")
    parser.add_argument("--max-events", type=int, default=None, help="Optional cap for non-final diagnostic runs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preflight_only:
        result = run_phase_7_preflight(sample_events=args.max_events or 5)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["process_status"] == "PREFLIGHT_OK" else 1
    result = run_phase_7_replay_pack(max_events=args.max_events)
    print(json.dumps({"process_status": result["process_status"], "metrics": result["metrics"]}, indent=2, ensure_ascii=False))
    return 0 if result["process_status"] == "TERMINATED_CLEANLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
