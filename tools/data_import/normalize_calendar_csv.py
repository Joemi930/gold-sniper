"""P3 — News Calendar CSV Normalizer.

Reads calendar-event-list.csv and produces:
  - gold_sniper/data/historical/news/calendar_events_2026.jsonl
  - gold_sniper/data/historical/news/calendar_events_manifest.json

Rules:
  - Timezone: assumed UTC (input has no explicit TZ)
  - Impact normalized: HIGH / MEDIUM / LOW / NONE
  - Currency: uppercase
  - XAUUSD audit: USD HIGH/MEDIUM are priority
  - Deduplicate by (id) and by (time_utc, currency, impact, name)
  - Keep all currencies for traceability
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_COLUMNS = ["Id", "Start", "Name", "Impact", "Currency"]

VALID_IMPACTS = {"HIGH", "MEDIUM", "LOW", "NONE"}


def normalize_impact(raw: str) -> str:
    """Normalize impact to HIGH/MEDIUM/LOW/NONE."""
    value = str(raw or "NONE").strip().upper()
    if value in VALID_IMPACTS:
        return value
    if value == "HOLIDAY":
        return "NONE"
    return "NONE"


def parse_date_utc(raw: str) -> str:
    """Parse DD/MM/YYYY HH:MM:SS as UTC, return ISO 8601."""
    # Try multiple formats
    formats = [
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(str(raw).strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {raw}")


def normalize_row(row: dict[str, str], source: str = "calendar-event-list.csv") -> dict[str, Any]:
    """Normalize a single CSV row to JSONL event."""
    return {
        "id": str(row.get("Id", "")).strip(),
        "time_utc": parse_date_utc(row.get("Start", "")),
        "event": str(row.get("Name", "")).strip(),
        "name": str(row.get("Name", "")).strip(),
        "impact": normalize_impact(row.get("Impact", "NONE")),
        "currency": str(row.get("Currency", "")).strip().upper(),
        "source": source,
    }


def deduplicate(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """Deduplicate by id, then by (time_utc, currency, impact, name)."""
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    dup_by_id = 0
    dup_by_key = 0

    for event in events:
        eid = event["id"]
        if eid and eid in seen_ids:
            dup_by_id += 1
            continue
        seen_ids.add(eid)

        key = (event["time_utc"], event["currency"], event["impact"], event["name"])
        if key in seen_keys:
            dup_by_key += 1
            continue
        seen_keys.add(key)

        deduped.append(event)

    return deduped, dup_by_id, dup_by_key


def normalize_calendar(
    csv_path: str | Path,
    output_dir: str | Path,
    *,
    timezone_assumption: str = "UTC",
) -> dict[str, Any]:
    """Main normalization routine."""
    csv_path = Path(csv_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read CSV
    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if set(reader.fieldnames or []) != set(EXPECTED_COLUMNS):
            return {
                "status": "BLOCKED",
                "error": f"Unexpected columns: {reader.fieldnames}. Expected: {EXPECTED_COLUMNS}",
            }
        rows = list(reader)

    if not rows:
        return {"status": "BLOCKED", "error": "CSV file is empty"}

    # Normalize
    events = [normalize_row(row) for row in rows]

    # Deduplicate
    events, dup_id, dup_key = deduplicate(events)

    # Sort by time
    events.sort(key=lambda e: e["time_utc"])

    # Compute coverage
    coverage_start = events[0]["time_utc"] if events else None
    coverage_end = events[-1]["time_utc"] if events else None

    # Count by currency/impact
    usd_high_medium = [
        e for e in events
        if e["currency"] == "USD" and e["impact"] in ("HIGH", "MEDIUM")
    ]
    usd_all = [e for e in events if e["currency"] == "USD"]

    # Determine output filename from coverage
    start_clean = (coverage_start or "unknown").replace(":", "").replace("-", "")[:8]
    end_clean = (coverage_end or "unknown").replace(":", "").replace("-", "")[:8]
    jsonl_name = f"calendar_events_{start_clean}_{end_clean}.jsonl"

    # Write JSONL
    jsonl_path = output_dir / jsonl_name
    with jsonl_path.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # Write manifest
    manifest = {
        "source": str(csv_path),
        "source_rows": len(rows),
        "events_count": len(events),
        "duplicates_by_id": dup_id,
        "duplicates_by_key": dup_key,
        "timezone_assumption": timezone_assumption,
        "coverage_start_utc": coverage_start,
        "coverage_end_utc": coverage_end,
        "usd_total": len(usd_all),
        "usd_high_medium": len(usd_high_medium),
        "impacts": {
            impact: len([e for e in events if e["impact"] == impact])
            for impact in sorted(VALID_IMPACTS)
        },
        "currencies": sorted(set(e["currency"] for e in events)),
        "files": {
            "jsonl": str(jsonl_path),
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    manifest_path = output_dir / "calendar_events_manifest.json"
    with manifest_path.open("w") as f:
        json.dump(manifest, f, indent=2)

    return {
        "status": "SUCCESS",
        "events_count": len(events),
        "usd_high_medium": len(usd_high_medium),
        "usd_total": len(usd_all),
        "coverage_start_utc": coverage_start,
        "coverage_end_utc": coverage_end,
        "duplicates_by_id": dup_id,
        "duplicates_by_key": dup_key,
        "jsonl_path": str(jsonl_path),
        "manifest_path": str(manifest_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P3 News Calendar CSV Normalizer for Gold Sniper."
    )
    parser.add_argument("--input", type=Path, default="calendar-event-list.csv")
    parser.add_argument(
        "--output-dir", type=Path,
        default="gold_sniper/data/historical/news",
    )
    parser.add_argument("--timezone-assumption", default="UTC")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = normalize_calendar(
        csv_path=args.input,
        output_dir=args.output_dir,
        timezone_assumption=args.timezone_assumption,
    )
    if result["status"] == "BLOCKED":
        print(f"BLOCKED: {result['error']}")
        return 1
    print(f"Normalized {result['events_count']} events")
    print(f"USD HIGH/MEDIUM: {result['usd_high_medium']}")
    print(f"USD total: {result['usd_total']}")
    print(f"Coverage: {result['coverage_start_utc']} -> {result['coverage_end_utc']}")
    print(f"Duplicates: {result['duplicates_by_id']} by ID, {result['duplicates_by_key']} by key")
    print(f"JSONL: {result['jsonl_path']}")
    print(f"Manifest: {result['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
