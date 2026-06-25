"""CLI: import historical economic news into Gold Sniper JSONL format."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
from gold_sniper.data_pipeline.news_jsonl import write_news_jsonl
from gold_sniper.data_pipeline.news_sources import (
    build_fomc_static_events,
    fetch_fmp_economic_calendar,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import historical economic news into Gold Sniper JSONL.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", action="append", choices=("fmp", "fed_fomc"), default=[])
    parser.add_argument("--allow-partial", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sources = args.source or ["fed_fomc"]
    results = []
    events = []
    for source in sources:
        if source == "fmp":
            result = fetch_fmp_economic_calendar(start_date=args.start, end_date=args.end)
        elif source == "fed_fomc":
            result = build_fomc_static_events(start_date=args.start, end_date=args.end)
        else:
            raise ValueError(f"Unsupported source: {source}")
        results.append(result)
        if result.ok:
            events.extend(result.events)

    # deduplicate by canonical key
    by_key = {}
    for event in events:
        by_key[(event.time, event.currency, event.event)] = event
    final_events = sorted(by_key.values(), key=lambda item: item.time)
    write_news_jsonl(final_events, args.output)

    report = {
        "output": str(args.output),
        "events_written": len(final_events),
        "sources": [r.to_dict() for r in results],
        "ok": bool(final_events) or args.allow_partial,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not final_events and not args.allow_partial:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
