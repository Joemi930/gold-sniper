"""Canonical Gold Sniper news JSONL format.

Format attendu par ligne :
{"time":"2026-06-12T12:30:00+00:00","currency":"USD","impact":"HIGH","event":"CPI YoY",...}
"""

from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

IMPACT_ALIASES = {
    "HIGH": "HIGH",
    "3": "HIGH",
    "RED": "HIGH",
    "MEDIUM": "MEDIUM",
    "2": "MEDIUM",
    "ORANGE": "MEDIUM",
    "LOW": "LOW",
    "1": "LOW",
    "YELLOW": "LOW",
    "NONE": "NONE",
}


@dataclass(frozen=True)
class NormalizedNewsEvent:
    time: str
    currency: str
    impact: str
    event: str
    source: str
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    provider_event_id: str | None = None
    country: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload.get("raw") is None:
            payload.pop("raw", None)
        return payload


def normalize_news_event(raw: dict[str, Any], *, source: str) -> NormalizedNewsEvent:
    if not isinstance(raw, dict):
        raise ValueError("News event must be a dict")
    time_value = (
        raw.get("time")
        or raw.get("time_utc")
        or raw.get("datetime")
        or raw.get("date")
        or raw.get("release_time")
    )
    ts = parse_news_time(time_value)
    currency = str(raw.get("currency") or raw.get("country") or "USD").upper()
    if currency in {"UNITED STATES", "US", "USA"}:
        currency = "USD"
    event_name = str(raw.get("event") or raw.get("name") or raw.get("title") or "").strip()
    if not event_name:
        raise ValueError("News event missing event/name/title")
    impact = normalize_impact(raw.get("impact") or raw.get("importance") or raw.get("priority") or "UNKNOWN")
    return NormalizedNewsEvent(
        time=ts.isoformat(),
        currency=currency,
        impact=impact,
        event=event_name,
        source=source,
        actual=_to_optional_str(raw.get("actual")),
        forecast=_to_optional_str(raw.get("forecast")),
        previous=_to_optional_str(raw.get("previous")),
        provider_event_id=_to_optional_str(raw.get("id") or raw.get("event_id")),
        country=_to_optional_str(raw.get("country")),
        raw=dict(raw),
    )


def normalize_news_events(rows: list[dict[str, Any]], *, source: str) -> list[NormalizedNewsEvent]:
    events = [normalize_news_event(row, source=source) for row in rows]
    events.sort(key=lambda item: item.time)
    deduped: dict[tuple[str, str, str], NormalizedNewsEvent] = {}
    for event in events:
        key = (event.time, event.currency, event.event)
        deduped[key] = event
    return list(deduped.values())


def write_news_jsonl(events: list[NormalizedNewsEvent], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event.to_dict(), ensure_ascii=False, default=str) for event in events]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_news_jsonl(path: str | Path) -> list[NormalizedNewsEvent]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return normalize_news_events(rows, source="JSONL")


def parse_news_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("News event missing time")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_impact(value: Any) -> str:
    raw = str(value or "UNKNOWN").strip().upper()
    return IMPACT_ALIASES.get(raw, raw if raw in {"HIGH", "MEDIUM", "LOW", "NONE"} else "UNKNOWN")


def _to_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
