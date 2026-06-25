"""P3-E — Bisect News Index for fast calendar lookups during replay.

Replaces linear scan of all news events with O(log n) bisect lookup.
Pre-indexes events by time for is_news_near() queries.

Usage:
    index = NewsIndex.from_jsonl("path/to/calendar.jsonl")
    active = index.news_active_at(candle_time, window_minutes=15)
"""

from __future__ import annotations

import bisect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class NewsIndex:
    """Bisect-indexed economic calendar for O(log n) time-range queries."""

    def __init__(self, events: list[dict[str, Any]] | None = None):
        events = events or []
        # Sort by time
        self._events: list[dict[str, Any]] = sorted(
            events, key=lambda e: _parse_time(e.get("time_utc") or e.get("time") or "1970-01-01T00:00:00+00:00")
        )
        # Pre-extract timestamps as float seconds for fast bisect
        self._timestamps: list[float] = [
            _parse_time(e.get("time_utc") or e.get("time") or "1970-01-01T00:00:00+00:00").timestamp()
            for e in self._events
        ]
        self._high_impact_indices: list[int] = [
            i for i, e in enumerate(self._events)
            if str(e.get("impact", "")).upper() in ("HIGH", "MEDIUM")
        ]

    @classmethod
    def from_jsonl(cls, path: str | Path) -> NewsIndex:
        """Load news index from P3-normalized JSONL file."""
        path = Path(path)
        if not path.exists():
            return cls([])
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return cls(events)

    @classmethod
    def from_csv(cls, path: str | Path) -> NewsIndex:
        """Load news index from CSV file (legacy format)."""
        import csv
        path = Path(path)
        if not path.exists():
            return cls([])
        events = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                events.append({
                    "id": row.get("Id", ""),
                    "time_utc": _parse_time(row.get("Start", "1970-01-01")).isoformat(),
                    "name": row.get("Name", ""),
                    "impact": str(row.get("Impact", "NONE")).upper(),
                    "currency": str(row.get("Currency", "")).upper(),
                })
        return cls(events)

    @property
    def count(self) -> int:
        return len(self._events)

    @property
    def high_impact_count(self) -> int:
        return len(self._high_impact_indices)

    def news_active_at(
        self,
        candle_time: str | datetime,
        *,
        window_minutes: int = 15,
        currencies: list[str] | None = None,
        min_impact: str = "MEDIUM",
    ) -> list[dict[str, Any]]:
        """Return active news events within +/- window_minutes of candle_time.

        Uses bisect for O(log n) time-range lookup.
        """
        if not self._events or not self._timestamps:
            return []

        ct = _parse_time(candle_time)
        ct_ts = ct.timestamp()
        window_sec = window_minutes * 60.0

        lo_ts = ct_ts - window_sec
        hi_ts = ct_ts + window_sec

        # Bisect to find range [lo_idx, hi_idx)
        lo_idx = bisect.bisect_left(self._timestamps, lo_ts)
        hi_idx = bisect.bisect_right(self._timestamps, hi_ts)

        impact_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
        min_rank = impact_rank.get(min_impact.upper(), 2)

        results = []
        for idx in range(lo_idx, hi_idx):
            event = self._events[idx]
            impact = str(event.get("impact", "NONE")).upper()
            currency = str(event.get("currency", "")).upper()

            if impact_rank.get(impact, 0) < min_rank:
                continue
            if currencies and currency not in currencies:
                continue

            results.append(event)

        return results

    def any_high_impact_near(
        self,
        candle_time: str | datetime,
        *,
        window_minutes: int = 15,
        currencies: list[str] | None = None,
    ) -> bool:
        """Fast boolean check: is any HIGH/MEDIUM news near this candle?"""
        return len(self.news_active_at(
            candle_time, window_minutes=window_minutes,
            currencies=currencies, min_impact="MEDIUM",
        )) > 0

    def high_impact_in_range(
        self,
        start: str | datetime,
        end: str | datetime,
        *,
        currencies: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return all HIGH/MEDIUM events in a time range."""
        start_ts = _parse_time(start).timestamp()
        end_ts = _parse_time(end).timestamp()

        lo_idx = bisect.bisect_left(self._timestamps, start_ts)
        hi_idx = bisect.bisect_right(self._timestamps, end_ts)

        results = []
        for idx in range(lo_idx, hi_idx):
            event = self._events[idx]
            impact = str(event.get("impact", "NONE")).upper()
            if impact not in ("HIGH", "MEDIUM"):
                continue
            currency = str(event.get("currency", "")).upper()
            if currencies and currency not in currencies:
                continue
            results.append(event)

        return results

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.count,
            "high_impact_events": self.high_impact_count,
            "time_range_start": (
                self._events[0].get("time_utc") if self._events else None
            ),
            "time_range_end": (
                self._events[-1].get("time_utc") if self._events else None
            ),
        }
