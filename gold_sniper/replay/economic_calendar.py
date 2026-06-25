"""Offline economic calendar loader for replay mode."""
from __future__ import annotations

import json
from collections import Counter
from csv import DictReader
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
CSV_TIME_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
)


def load_economic_calendar_jsonl(
    path: str | Path,
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
) -> list[dict[str, Any]]:
    calendar_path = Path(path)
    if not calendar_path.exists():
        return []

    start_dt = _parse_time(start) if start else None
    end_dt = _parse_time(end) if end else None
    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(calendar_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid economic calendar JSONL line {line_number}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid economic calendar JSONL line {line_number}: expected object")
        event = dict(raw)
        _validate_event(event, line_number)
        event_time = _parse_time(event.get("time") or event.get("time_utc") or event.get("datetime") or event.get("date"))
        if start_dt and event_time < start_dt:
            continue
        if end_dt and event_time > end_dt:
            continue
        event["time"] = event_time
        if "currency" not in event and "country" in event:
            event["currency"] = event["country"]
        if "name" not in event and "event" in event:
            event["name"] = event["event"]
        if "event" not in event and "name" in event:
            event["event"] = event["name"]
        event["impact"] = str(event["impact"]).upper()
        events.append(event)
    events.sort(key=lambda item: item["time"])
    return events


def load_economic_calendar_csv(
    path: str | Path,
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    timezone_name: str = "UTC",
) -> list[dict[str, Any]]:
    calendar_path = Path(path)
    if not calendar_path.exists():
        return []
    start_dt = _parse_time(start) if start else None
    end_dt = _parse_time(end) if end else None
    events: list[dict[str, Any]] = []
    with calendar_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = DictReader(handle)
        expected = ["Id", "Start", "Name", "Impact", "Currency"]
        if reader.fieldnames != expected:
            raise ValueError(f"Invalid CSV calendar columns: {reader.fieldnames!r}")
        for line_number, row in enumerate(reader, start=2):
            try:
                event_time = _parse_csv_calendar_time(row.get("Start"), timezone_name=timezone_name)
                impact = _normalize_impact(row.get("Impact"))
                name = str(row.get("Name") or "").strip()
                currency = str(row.get("Currency") or "").strip().upper()
                event_id = str(row.get("Id") or "").strip()
                if not event_id or not name or not currency:
                    raise ValueError("missing required calendar value")
            except Exception as exc:
                raise ValueError(f"Invalid CSV calendar line {line_number}: {exc}") from exc
            if start_dt and event_time < start_dt:
                continue
            if end_dt and event_time > end_dt:
                continue
            events.append({
                "id": event_id,
                "time": event_time,
                "time_utc": event_time.isoformat(),
                "event": name,
                "name": name,
                "impact": impact,
                "currency": currency,
                "source": "CSV_CALENDAR_EVENT_LIST",
            })
    events.sort(key=lambda item: item["time"])
    return events


def _parse_time(value: str | datetime | Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        raise ValueError("Missing economic calendar event time")
    text = str(value).strip()
    if not text:
        raise ValueError("Missing economic calendar event time")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_csv_calendar_time(value: str | Any, timezone_name: str = "UTC") -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Missing CSV calendar Start")
    tz = ZoneInfo(timezone_name)
    for fmt in CSV_TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _normalize_impact(value: Any) -> str:
    impact = str(value or "").strip().upper()
    if impact in {"HIGH", "MEDIUM", "LOW", "NONE"}:
        return impact
    if impact in {"HOLIDAY", "NON_ECONOMIC"}:
        return "NONE"
    raise ValueError(f"Unsupported impact: {value!r}")


def _validate_event(event: dict[str, Any], line_number: int) -> None:
    required_groups = {
        "time": ("time", "time_utc", "datetime", "date"),
        "currency": ("currency", "country"),
        "impact": ("impact",),
        "event": ("event", "name", "title"),
    }
    for label, keys in required_groups.items():
        if not any(str(event.get(key) or "").strip() for key in keys):
            raise ValueError(f"Invalid economic calendar JSONL line {line_number}: missing {label}")


# ── P1-replay calendar enhancements ──


@dataclass(frozen=True)
class CalendarLoadResult:
    events: list[dict[str, Any]]
    source: str
    missing: bool
    errors: list[str]
    empty: bool = False
    coverage_start_utc: str | None = None
    coverage_end_utc: str | None = None
    raw_events_count: int = 0
    loaded_events_count: int = 0
    filtered_events_count: int = 0
    source_format: str = "JSONL"
    duplicate_id_count: int = 0
    duplicate_key_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "source": self.source,
            "missing": self.missing,
            "errors": list(self.errors),
            "empty": self.empty,
            "coverage_start_utc": self.coverage_start_utc,
            "coverage_end_utc": self.coverage_end_utc,
            "raw_events_count": self.raw_events_count,
            "loaded_events_count": self.loaded_events_count,
            "filtered_events_count": self.filtered_events_count,
            "source_format": self.source_format,
            "duplicate_id_count": self.duplicate_id_count,
            "duplicate_key_count": self.duplicate_key_count,
        }


@dataclass(frozen=True)
class NewsContext:
    status: str
    impact_level: str
    news_clear: bool
    blocked: bool
    replay_invalid: bool
    reason: str
    minutes_to_news: int | None
    minutes_since_news: int | None
    event_name: str | None
    event_ts_utc: str | None
    event_ts_ny: str | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_calendar_result(
    path: str | Path,
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    source: str = "REPLAY_JSONL",
) -> CalendarLoadResult:
    calendar_path = Path(path)
    if not calendar_path.exists():
        return CalendarLoadResult(
            events=[], source=source, missing=True, errors=["CALENDAR_FILE_MISSING"],
            empty=True, coverage_start_utc=None, coverage_end_utc=None,
            source_format=calendar_path.suffix.lower().lstrip(".").upper() or "UNKNOWN",
        )
    try:
        source_format = "CSV" if calendar_path.suffix.lower() == ".csv" else "JSONL"
        raw_events = (
            load_economic_calendar_csv(calendar_path, timezone_name="UTC")
            if source_format == "CSV"
            else load_economic_calendar_jsonl(calendar_path)
        )
        events = (
            load_economic_calendar_csv(calendar_path, start=start, end=end, timezone_name="UTC")
            if source_format == "CSV"
            else load_economic_calendar_jsonl(calendar_path, start=start, end=end)
        )
        coverage_start = None
        coverage_end = None
        if raw_events:
            times = sorted(e.get("time") for e in raw_events if e.get("time") is not None)
            if times:
                coverage_start = times[0].isoformat() if hasattr(times[0], "isoformat") else str(times[0])
                coverage_end = times[-1].isoformat() if hasattr(times[-1], "isoformat") else str(times[-1])
        duplicate_id_count, duplicate_key_count = _duplicate_counts(raw_events)
        return CalendarLoadResult(
            events=events, source=source, missing=False, errors=[],
            empty=len(events) == 0,
            coverage_start_utc=coverage_start,
            coverage_end_utc=coverage_end,
            raw_events_count=len(raw_events),
            loaded_events_count=len(events),
            filtered_events_count=max(0, len(raw_events) - len(events)),
            source_format=source_format,
            duplicate_id_count=duplicate_id_count,
            duplicate_key_count=duplicate_key_count,
        )
    except Exception as exc:
        return CalendarLoadResult(
            events=[], source=source, missing=True, errors=[str(exc)],
            empty=True, coverage_start_utc=None, coverage_end_utc=None,
            source_format=calendar_path.suffix.lower().lstrip(".").upper() or "UNKNOWN",
        )


def news_context_at(
    events: list[dict[str, Any]],
    current_ts: datetime,
    *,
    source: str = "REPLAY_JSONL",
    calendar_missing: bool = False,
    pre_minutes: int = 30,
    post_minutes: int = 15,
    stealth_minutes: int = 30,
) -> NewsContext:
    now = _parse_time(current_ts) if not isinstance(current_ts, datetime) else current_ts
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if calendar_missing:
        return NewsContext(
            status="MISSING", impact_level="UNKNOWN", news_clear=False,
            blocked=False, replay_invalid=True, reason="NEWS_CALENDAR_MISSING",
            minutes_to_news=None, minutes_since_news=None,
            event_name=None, event_ts_utc=None, event_ts_ny=None, source=source,
        )

    if not events:
        return NewsContext(
            status="EMPTY", impact_level="UNKNOWN", news_clear=False,
            blocked=False, replay_invalid=True, reason="NEWS_CALENDAR_EMPTY",
            minutes_to_news=None, minutes_since_news=None,
            event_name=None, event_ts_utc=None, event_ts_ny=None, source=source,
        )

    nearest = None
    nearest_delta_abs = None
    for event in events:
        event_time = event.get("time")
        if not isinstance(event_time, datetime):
            event_time = _parse_time(event_time)
        delta_minutes = int((event_time - now).total_seconds() // 60)
        distance = abs(delta_minutes)
        if nearest_delta_abs is None or distance < nearest_delta_abs:
            nearest = event
            nearest_delta_abs = distance

    if not nearest:
        return NewsContext(
            status="CLEAR", impact_level="NONE", news_clear=True,
            blocked=False, replay_invalid=False, reason="NO_NEWS_IN_DATASET",
            minutes_to_news=None, minutes_since_news=None,
            event_name=None, event_ts_utc=None, event_ts_ny=None, source=source,
        )

    event_time = nearest.get("time")
    if not isinstance(event_time, datetime):
        event_time = _parse_time(event_time)
    delta_minutes = int((event_time - now).total_seconds() // 60)
    impact = str(nearest.get("impact") or "UNKNOWN").upper()
    name = str(nearest.get("event") or nearest.get("name") or nearest.get("title") or "UNKNOWN")
    in_pre = 0 <= delta_minutes <= pre_minutes
    in_post = -post_minutes <= delta_minutes < 0
    in_stealth = -stealth_minutes <= delta_minutes < -post_minutes
    blocked = impact == "HIGH" and (in_pre or in_post)
    stealth = impact == "HIGH" and in_stealth

    if blocked:
        status = "BLOCKED"
        reason = "NEWS_HIGH_IMPACT_WINDOW"
    elif stealth:
        status = "STEALTH"
        reason = "NEWS_POST_EVENT_STEALTH"
    else:
        status = "CLEAR"
        reason = "NEWS_CLEAR"

    return NewsContext(
        status=status, impact_level=impact,
        news_clear=status == "CLEAR",
        blocked=blocked, replay_invalid=False,
        reason=reason,
        minutes_to_news=delta_minutes if delta_minutes >= 0 else None,
        minutes_since_news=abs(delta_minutes) if delta_minutes < 0 else None,
        event_name=name,
        event_ts_utc=event_time.isoformat(),
        event_ts_ny=event_time.astimezone(NY_TZ).isoformat(),
        source=source,
    )


def _duplicate_counts(events: list[dict[str, Any]]) -> tuple[int, int]:
    ids = [str(event.get("id") or "").strip() for event in events if str(event.get("id") or "").strip()]
    keys = [
        (
            event.get("time").isoformat() if hasattr(event.get("time"), "isoformat") else str(event.get("time")),
            str(event.get("name") or event.get("event") or "").strip(),
            str(event.get("currency") or "").strip(),
        )
        for event in events
    ]
    duplicate_id_count = sum(value - 1 for value in Counter(ids).values() if value > 1)
    duplicate_key_count = sum(value - 1 for value in Counter(keys).values() if value > 1)
    return duplicate_id_count, duplicate_key_count
