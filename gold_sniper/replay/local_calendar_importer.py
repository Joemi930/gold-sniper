"""Import a local economic calendar JSON file into the Phase 7 news cache."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gold_sniper.replay.news_loader import DEFAULT_NEWS_CACHE


DEFAULT_SOURCE_PATH = Path(r"C:\Users\tetej\Music\Bug bounty\Trading\calendar-event-list.json")
DEFAULT_START = "2026-04-01T01:00:00Z"
DEFAULT_END = "2026-06-05T20:00:00Z"
HIGH_KEYWORDS = ("NFP", "NON-FARM", "NONFARM", "CPI", "FOMC", "PCE", "PPI", "GDP", "UNEMPLOYMENT RATE", "POWELL", "INTEREST RATE")


def load_local_calendar_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"LOCAL_CALENDAR_NOT_FOUND: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("events", "calendar", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError("LOCAL_CALENDAR_UNSUPPORTED_FORMAT")


def normalize_local_calendar_events(
    raw_events: list[dict[str, Any]],
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    normalized: list[dict[str, Any]] = []
    stats = {
        "raw_events": len(raw_events),
        "filtered_out_of_period": 0,
        "filtered_non_usd": 0,
        "invalid_events": 0,
        "high_impact": 0,
        "medium_impact": 0,
        "low_impact": 0,
    }
    for raw in raw_events:
        event_time = _parse_time(_first(raw, ("time", "date", "datetime", "time_utc", "event_time", "Start")))
        if event_time is None:
            stats["invalid_events"] += 1
            continue
        if event_time < start_dt or event_time > end_dt:
            stats["filtered_out_of_period"] += 1
            continue
        currency = str(_first(raw, ("currency", "country", "currencyCode", "Currency")) or "").upper()
        if currency != "USD":
            stats["filtered_non_usd"] += 1
            continue
        title = _first(raw, ("title", "event", "name", "Name"))
        impact = _normalize_impact(_first(raw, ("impact", "importance", "impactLevel", "priority", "Impact")), str(title or ""))
        if impact == "HIGH":
            stats["high_impact"] += 1
        elif impact == "MEDIUM":
            stats["medium_impact"] += 1
        else:
            stats["low_impact"] += 1
        normalized.append(
            {
                "time": event_time.isoformat().replace("+00:00", "Z"),
                "currency": "USD",
                "impact": impact,
                "title": str(title) if title not in (None, "") else None,
                "actual": _first(raw, ("actual", "Actual")),
                "forecast": _first(raw, ("forecast", "Forecast")),
                "previous": _first(raw, ("previous", "Previous")),
                "source_id": _first(raw, ("source_id", "id", "Id")),
            }
        )
    normalized.sort(key=lambda item: item["time"])
    return normalized, stats


def write_news_cache_from_local_calendar(
    source_path: Path = DEFAULT_SOURCE_PATH,
    output_path: Path = DEFAULT_NEWS_CACHE,
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
) -> dict[str, Any]:
    raw = load_local_calendar_file(source_path)
    events, stats = normalize_local_calendar_events(raw, start=start, end=end)
    payload = {
        "source": "local_calendar_event_list",
        "symbol": "XAUUSD",
        "currency_filter": ["USD"],
        "date_start": start,
        "date_end": end,
        "timezone": "UTC",
        "events": events,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "source_path": str(source_path),
        "output_path": str(output_path),
        "cache_generated": True,
        "events_written": len(events),
        **stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import local calendar JSON into Phase 7 replay news cache.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_NEWS_CACHE)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    args = parser.parse_args(argv)
    try:
        summary = write_news_cache_from_local_calendar(args.source, args.output, start=args.start, end=args.end)
    except Exception as exc:
        print(json.dumps({"status": "FAILED_CLEANLY", "error": type(exc).__name__}, indent=2))
        return 1
    print(json.dumps({"status": "TERMINATED_CLEANLY", **summary}, indent=2, ensure_ascii=False))
    return 0


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_impact(value: Any, title: str) -> str:
    text = str(value or "").upper()
    if text in {"HIGH", "MEDIUM", "LOW"}:
        return text
    if text in {"3", "2", "1"}:
        return {"3": "HIGH", "2": "MEDIUM", "1": "LOW"}[text]
    if any(keyword in title.upper() for keyword in HIGH_KEYWORDS):
        return "HIGH"
    return "LOW"


if __name__ == "__main__":
    raise SystemExit(main())
