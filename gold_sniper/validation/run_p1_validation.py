"""CLI for P1 smoke validation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from gold_sniper.validation.p1_smoke_validator import (
    DEFAULT_RUN_ID,
    DEFAULT_SMOKE_END_UTC,
    DEFAULT_SMOKE_START_UTC,
    run_smoke_validation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "historical" / "XAUUSD"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "replay_runs"
DEFAULT_REPORT_OUTPUT = PROJECT_ROOT / "data" / "validation_reports" / f"{DEFAULT_RUN_ID}.json"
DEFAULT_NEWS = PROJECT_ROOT / "data" / "historical" / "news" / "economic_calendar_2026-06-07_2026-06-17.jsonl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run P1 10-day smoke validation.")
    parser.add_argument("--start", default=DEFAULT_SMOKE_START_UTC)
    parser.add_argument("--end", default=DEFAULT_SMOKE_END_UTC)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--news-calendar", type=Path, default=DEFAULT_NEWS)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_smoke_validation(
        data_root=args.data_root,
        news_calendar=args.news_calendar,
        output_root=args.output_root,
        report_output=args.report_output,
        run_id=args.run_id,
        start_utc=args.start,
        end_utc=args.end,
        execute=not args.dry_run,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0 if report.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
