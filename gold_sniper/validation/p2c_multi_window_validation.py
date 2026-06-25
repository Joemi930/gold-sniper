"""Offline P2-C multi-window validation runner.

This module is diagnostic-only. It runs replay validation windows from local CSV
and calendar files, never broker/live/paper integrations.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gold_sniper.data_pipeline.candle_manifest import build_candle_coverage_manifest
from gold_sniper.replay.economic_calendar import load_calendar_result
from gold_sniper.replay.historical_data import parse_timestamp
from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "gold_sniper" / "data" / "replay_runs"


@dataclass(frozen=True)
class WindowSpec:
    name: str
    start: str
    end: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "start": self.start, "end": self.end}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline P2-C multi-window validation.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--news-calendar", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--custom-windows", type=Path)
    parser.add_argument("--coverage-check-only", action="store_true")
    parser.add_argument("--allow-partial-window", action="store_true")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "gold_sniper" / "data" / "validation_reports" / "P2C_MULTI_WINDOW_SUMMARY.json")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def run_multi_window_validation(args: argparse.Namespace) -> dict[str, Any]:
    coverage = build_coverage_status(
        data_root=args.data_root,
        news_calendar=args.news_calendar,
        start=args.start,
        end=args.end,
    )
    windows = load_windows(args)
    payload: dict[str, Any] = {
        "status": coverage["status"],
        "coverage": coverage,
        "coverage_check_only": bool(args.coverage_check_only),
        "allow_partial_window": bool(args.allow_partial_window),
        "windows_requested": [window.to_dict() for window in windows],
        "windows": [],
        "performance_summary": build_p2c_performance_summary(windows=[]),
        "diagnostic": coverage.get("diagnostic"),
    }

    if args.coverage_check_only:
        _write_json(args.output, payload)
        return payload

    if coverage["status"] != "COVERAGE_OK" and not args.allow_partial_window:
        payload["status"] = "PARTIAL_DATA_COVERAGE"
        payload["diagnostic"] = "DATA_COVERAGE_PARTIAL"
        _write_json(args.output, payload)
        return payload

    window_results: list[dict[str, Any]] = []
    for window in windows:
        window_coverage = build_coverage_status(
            data_root=args.data_root,
            news_calendar=args.news_calendar,
            start=window.start,
            end=window.end,
        )
        if window_coverage["status"] != "COVERAGE_OK" and not args.allow_partial_window:
            window_results.append({
                "name": window.name,
                "start": window.start,
                "end": window.end,
                "status": "PARTIAL_DATA_COVERAGE",
                "coverage": window_coverage,
                "window_days": _window_days(window.start, window.end),
            })
            continue
        window_results.append(_run_window(args, window, window_coverage))

    performance = build_p2c_performance_summary(windows=window_results)
    payload.update({
        "status": "PASS" if all(str(item.get("status")) == "PASS" for item in window_results) else "PARTIAL",
        "windows": window_results,
        "performance_summary": performance,
        "diagnostic": performance.get("diagnostic"),
    })
    _write_json(args.output, payload)
    return payload


def build_coverage_status(*, data_root: Path, news_calendar: Path, start: str, end: str) -> dict[str, Any]:
    manifest = build_candle_coverage_manifest(
        data_root=data_root,
        symbol="XAUUSD",
        requested_start_utc=start,
        requested_end_utc=end,
    )
    calendar = load_calendar_result(news_calendar, start=start, end=end)
    news_ok = not calendar.missing and not calendar.empty and not calendar.errors
    data_ok = manifest.overall_status == "COVERAGE_OK"
    status = "COVERAGE_OK" if data_ok and news_ok else "PARTIAL_DATA_COVERAGE"
    diagnostic = None
    if not data_ok:
        diagnostic = "DATA_COVERAGE_PARTIAL"
    elif not news_ok:
        diagnostic = "NEWS_COVERAGE_PARTIAL"
    m1 = manifest.timeframes.get("1m")
    return {
        "status": status,
        "diagnostic": diagnostic,
        "data_manifest_status": manifest.overall_status,
        "missing_timeframes": list(manifest.missing_timeframes),
        "partial_timeframes": list(manifest.partial_timeframes),
        "available_start_utc": manifest.available_start_utc,
        "available_end_utc": manifest.available_end_utc,
        "m1_start_utc": m1.start_utc if m1 else None,
        "m1_end_utc": m1.end_utc if m1 else None,
        "news_calendar_missing": calendar.missing,
        "news_calendar_empty": calendar.empty,
        "news_calendar_errors": list(calendar.errors),
        "loaded_news_events": len(calendar.events),
        "news_coverage_start_utc": calendar.coverage_start_utc,
        "news_coverage_end_utc": calendar.coverage_end_utc,
        "manifest": manifest.to_dict(),
    }


def load_windows(args: argparse.Namespace) -> list[WindowSpec]:
    if args.custom_windows:
        raw = json.loads(args.custom_windows.read_text(encoding="utf-8"))
        return [
            WindowSpec(
                name=str(item.get("name") or f"custom_{index + 1}"),
                start=str(item["start"]),
                end=str(item["end"]),
            )
            for index, item in enumerate(raw or [])
        ]
    return generate_rolling_windows(
        start=args.start,
        end=args.end,
        window_days=int(args.window_days),
        step_days=int(args.step_days),
    )


def generate_rolling_windows(*, start: str, end: str, window_days: int, step_days: int) -> list[WindowSpec]:
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    if window_days <= 0 or step_days <= 0:
        raise ValueError("window_days and step_days must be positive")
    windows: list[WindowSpec] = []
    cursor = start_dt
    index = 1
    while cursor <= end_dt:
        window_end = min(cursor + timedelta(days=window_days) - timedelta(seconds=1), end_dt)
        windows.append(WindowSpec(
            name=f"window_{index:02d}_{cursor.date()}_{window_end.date()}",
            start=cursor.isoformat().replace("+00:00", "Z"),
            end=window_end.isoformat().replace("+00:00", "Z"),
        ))
        cursor += timedelta(days=step_days)
        index += 1
    return windows


def _run_window(args: argparse.Namespace, window: WindowSpec, coverage: dict[str, Any]) -> dict[str, Any]:
    run_id = f"P2C_WINDOW_{_safe_name(window.name)}"
    report_output = args.output.parent / f"{run_id}.json"
    command = [
        sys.executable,
        "-m",
        "gold_sniper.validation.run_p1_validation",
        "--run-id",
        run_id,
        "--start",
        window.start,
        "--end",
        window.end,
        "--data-root",
        str(args.data_root),
        "--news-calendar",
        str(args.news_calendar),
        "--output-root",
        str(args.output_root),
        "--report-output",
        str(report_output),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "gold_sniper")
    completed = subprocess.run(command, cwd=PROJECT_ROOT, env=env, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return {
            "name": window.name,
            "start": window.start,
            "end": window.end,
            "status": "FAIL",
            "run_id": run_id,
            "window_days": _window_days(window.start, window.end),
            "coverage": coverage,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }
    summary_path = Path(args.output_root) / run_id / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    p1 = summary.get("p1_replay") or {}
    performance = summary.get("performance_summary") or summary.get("p2c_performance_summary") or {}
    return {
        "name": window.name,
        "start": window.start,
        "end": window.end,
        "status": "PASS",
        "run_id": run_id,
        "window_days": _window_days(window.start, window.end),
        "coverage": coverage,
        "total_decisions": int(p1.get("total_decisions") or 0),
        "decision_counts": p1.get("decision_counts") or {},
        "setup_grade_distribution": p1.get("setup_grade_distribution") or {},
        "performance_summary": performance,
        "determinism_hash": p1.get("determinism_hash"),
    }


def _window_days(start: str, end: str) -> float:
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    return round(((end_dt - start_dt).total_seconds() + 1) / 86400.0, 6)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value)).strip("_")[:80] or "window"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_multi_window_validation(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload.get("status") in {"PASS", "COVERAGE_OK", "PARTIAL_DATA_COVERAGE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
