"""P1 smoke validation orchestration.
This module prepares commands and validates outputs for a 10-day P1 replay smoke.
It does not download data and it does not create fake data.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import subprocess
import sys
from gold_sniper.validation.p1_validation_report import (
    P1SmokeValidationReport,
    ValidationFinding,
    build_validation_report,
    save_validation_report,
    _failed_report,
    _replace_findings,
)


DEFAULT_SMOKE_START_UTC = "2026-06-07T00:00:00Z"
DEFAULT_SMOKE_END_UTC = "2026-06-17T00:00:00Z"
DEFAULT_RUN_ID = "P1_VALIDATION_SMOKE_2026_06_07_2026_06_17"


@dataclass(frozen=True)
class SmokeInputStatus:
    csv_ok: bool
    news_ok: bool
    missing_files: list[str]
    data_root: str
    news_calendar: str

    @property
    def ok(self) -> bool:
        return self.csv_ok

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_smoke_inputs(
    *,
    data_root: str | Path,
    news_calendar: str | Path,
) -> SmokeInputStatus:
    root = Path(data_root)
    news = Path(news_calendar)
    required = [
        root / "1m",
        root / "15m",
        root / "4H",
    ]
    missing: list[str] = []
    csv_ok = True
    for path in required:
        if not path.exists():
            csv_ok = False
            missing.append(str(path))
            continue
        csv_files = list(path.glob("*.csv"))
        if not csv_files:
            csv_ok = False
            missing.append(f"{path}/*.csv")
    news_ok = news.exists()
    if not news_ok:
        missing.append(str(news))
    return SmokeInputStatus(
        csv_ok=csv_ok,
        news_ok=news_ok,
        missing_files=missing,
        data_root=str(root),
        news_calendar=str(news),
    )


def build_smoke_command(
    *,
    run_id: str,
    start_utc: str,
    end_utc: str,
    data_root: str | Path,
    output_root: str | Path,
    news_calendar: str | Path,
    python_executable: str | None = None,
) -> list[str]:
    python_bin = python_executable or sys.executable
    return [
        python_bin,
        "-m",
        "gold_sniper.replay.run_replay",
        "--run-id",
        run_id,
        "--start",
        start_utc,
        "--end",
        end_utc,
        "--data-root",
        str(data_root),
        "--output-root",
        str(output_root),
        "--news-calendar",
        str(news_calendar),
        "--initial-equity",
        "100",
    ]


def run_smoke_validation(
    *,
    data_root: str | Path,
    news_calendar: str | Path,
    output_root: str | Path,
    report_output: str | Path,
    run_id: str = DEFAULT_RUN_ID,
    start_utc: str = DEFAULT_SMOKE_START_UTC,
    end_utc: str = DEFAULT_SMOKE_END_UTC,
    execute: bool = True,
) -> P1SmokeValidationReport:
    input_status = validate_smoke_inputs(data_root=data_root, news_calendar=news_calendar)
    if not input_status.ok:
        report = build_missing_data_report(
            run_id=run_id,
            start_utc=start_utc,
            end_utc=end_utc,
            input_status=input_status,
        )
        save_validation_report(report, report_output)
        return report

    command = build_smoke_command(
        run_id=run_id,
        start_utc=start_utc,
        end_utc=end_utc,
        data_root=data_root,
        output_root=output_root,
        news_calendar=news_calendar,
    )

    if execute:
        completed = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[2],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            report = build_command_failed_report(
                run_id=run_id,
                start_utc=start_utc,
                end_utc=end_utc,
                command=command,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            save_validation_report(report, report_output)
            return report

    run_dir = Path(output_root) / run_id
    report = build_validation_report(
        run_dir=run_dir,
        window_start_utc=start_utc,
        window_end_utc=end_utc,
    )
    save_validation_report(report, report_output)
    return report


def build_missing_data_report(
    *,
    run_id: str,
    start_utc: str,
    end_utc: str,
    input_status: SmokeInputStatus,
) -> P1SmokeValidationReport:
    report = _failed_report(
        run_id=run_id,
        window_start_utc=start_utc,
        window_end_utc=end_utc,
        code="DATA_MISSING",
        message="Required real smoke data is missing.",
    )
    finding = ValidationFinding(
        code="DATA_MISSING_FILES",
        severity="BLOCKER",
        message="One or more required candle/news files are missing.",
        details=input_status.to_dict(),
    )
    return _replace_findings(report, [finding])


def build_command_failed_report(
    *,
    run_id: str,
    start_utc: str,
    end_utc: str,
    command: list[str],
    returncode: int,
    stdout: str,
    stderr: str,
) -> P1SmokeValidationReport:
    report = _failed_report(
        run_id=run_id,
        window_start_utc=start_utc,
        window_end_utc=end_utc,
        code="SMOKE_COMMAND_FAILED",
        message="run_replay smoke command failed.",
    )
    finding = ValidationFinding(
        code="SMOKE_COMMAND_FAILED_DETAILS",
        severity="BLOCKER",
        message="Replay command returned non-zero exit code.",
        details={
            "command": command,
            "returncode": returncode,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        },
    )
    return _replace_findings(report, [finding])
