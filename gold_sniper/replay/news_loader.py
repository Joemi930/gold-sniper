"""Local news cache loader for Phase 7 historical replay.

The loader is intentionally offline-only. It never contacts external APIs and
does not read environment files. A missing cache means the replay must keep
NEWS_CONTEXT_MISSING instead of inventing a clear calendar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_NEWS_CACHE = Path("gold_sniper/data/news/XAUUSD_USD_news_2026-04-01_2026-06-05.json")
PRE_NEWS_LOCKOUT_MINUTES = 15
POST_NEWS_HARD_MINUTES = 15
POST_NEWS_STEALTH_MINUTES = 60


@dataclass(frozen=True)
class NewsCache:
    source: str
    symbol: str
    currency_filter: list[str]
    date_start: str | None
    date_end: str | None
    timezone: str
    events: list[dict[str, Any]]
    path: str | None
    loaded: bool
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        usd_events = [event for event in self.events if _currency(event) == "USD"]
        high_impact = [event for event in usd_events if _impact(event) == "HIGH"]
        medium_impact = [event for event in usd_events if _impact(event) == "MEDIUM"]
        low_impact = [event for event in usd_events if _impact(event) == "LOW"]
        return {
            "news_source": self.source if self.loaded else "LOCAL_NEWS_CACHE_MISSING",
            "news_loaded_count": len(self.events),
            "usd_news_count": len(usd_events),
            "high_impact_news_count": len(high_impact),
            "medium_impact_news_count": len(medium_impact),
            "low_impact_news_count": len(low_impact),
            "news_cache_path": self.path,
            "news_cache_loaded": self.loaded,
            "news_cache_error": self.error,
            "pre_news_lockout_minutes": PRE_NEWS_LOCKOUT_MINUTES,
            "post_news_hard_minutes": POST_NEWS_HARD_MINUTES,
            "post_news_stealth_minutes": POST_NEWS_STEALTH_MINUTES,
        }


def load_local_news_cache(path: str | Path = DEFAULT_NEWS_CACHE) -> NewsCache:
    cache_path = Path(path)
    if not cache_path.exists():
        return NewsCache(
            source="LOCAL_NEWS_CACHE_MISSING",
            symbol="XAUUSD",
            currency_filter=["USD"],
            date_start=None,
            date_end=None,
            timezone="UTC",
            events=[],
            path=str(cache_path),
            loaded=False,
            error="NEWS_CACHE_FILE_MISSING",
        )
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return NewsCache(
            source="LOCAL_NEWS_CACHE_INVALID",
            symbol="XAUUSD",
            currency_filter=["USD"],
            date_start=None,
            date_end=None,
            timezone="UTC",
            events=[],
            path=str(cache_path),
            loaded=False,
            error=f"NEWS_CACHE_INVALID: {exc}",
        )
    if not isinstance(payload, dict):
        return NewsCache(
            source="LOCAL_NEWS_CACHE_INVALID",
            symbol="XAUUSD",
            currency_filter=["USD"],
            date_start=None,
            date_end=None,
            timezone="UTC",
            events=[],
            path=str(cache_path),
            loaded=False,
            error="NEWS_CACHE_ROOT_NOT_OBJECT",
        )

    raw_events = payload.get("events")
    events = [_normalize_event(event) for event in raw_events if isinstance(event, dict)] if isinstance(raw_events, list) else []
    events = [event for event in events if event is not None and _currency(event) == "USD"]
    events.sort(key=lambda item: item["time"])
    return NewsCache(
        source=str(payload.get("source") or "local_news_cache"),
        symbol=str(payload.get("symbol") or "XAUUSD"),
        currency_filter=[str(item).upper() for item in payload.get("currency_filter", ["USD"])],
        date_start=payload.get("date_start"),
        date_end=payload.get("date_end"),
        timezone=str(payload.get("timezone") or "UTC"),
        events=events,
        path=str(cache_path),
        loaded=True,
    )


def evaluate_news_for_timestamp(timestamp: str | datetime | None, cache: NewsCache) -> dict[str, Any]:
    if not cache.loaded:
        return {
            "calendar_status": "NEWS_CONTEXT_MISSING",
            "news_clear": None,
            "news_veto": False,
            "high_impact_news": False,
            "pre_news_lockout": False,
            "post_news_stealth": False,
            "news_normalized": False,
            "nearest_news": None,
            "news_source": cache.source,
        }

    current_time = _parse_time(timestamp)
    if current_time is None:
        return {
            "calendar_status": "NEWS_TIMESTAMP_INVALID",
            "news_clear": None,
            "news_veto": False,
            "high_impact_news": False,
            "pre_news_lockout": False,
            "post_news_stealth": False,
            "news_normalized": False,
            "nearest_news": None,
            "news_source": cache.source,
        }

    nearest = _nearest_event(current_time, cache.events)
    high_events = [event for event in cache.events if _impact(event) == "HIGH"]
    for event in high_events:
        event_time = event["time"]
        pre_start = event_time - timedelta(minutes=PRE_NEWS_LOCKOUT_MINUTES)
        hard_end = event_time + timedelta(minutes=POST_NEWS_HARD_MINUTES)
        stealth_end = event_time + timedelta(minutes=POST_NEWS_STEALTH_MINUTES)
        if pre_start <= current_time < event_time:
            return _blocked_payload("PRE_NEWS_LOCKOUT", event, cache.source, pre_news=True)
        if event_time <= current_time <= hard_end:
            return _blocked_payload("NEWS_VETO_HIGH_IMPACT", event, cache.source, veto=True, stealth=True)
        if hard_end < current_time <= stealth_end:
            return _blocked_payload("POST_NEWS_STEALTH", event, cache.source, stealth=True)

    return {
        "calendar_status": "NEWS_CLEAR",
        "news_clear": True,
        "news_veto": False,
        "high_impact_news": False,
        "pre_news_lockout": False,
        "post_news_stealth": False,
        "news_normalized": True,
        "nearest_news": _public_event(nearest) if nearest else None,
        "news_source": cache.source,
    }


def write_news_loading_summary(path: Path, metrics: dict[str, Any], cache: NewsCache, *, process_status: str) -> None:
    lines = [
        "# Phase 7 News Loading Summary",
        "",
        "## Periode demandee",
        f"- Debut: {metrics.get('date_start')}",
        f"- Fin: {metrics.get('date_end')}",
        "",
        "## Source API locale utilisee",
        f"- Source: {cache.source}",
        f"- Cache: {cache.path}",
        f"- Charge: {cache.loaded}",
        "",
        "## Nombre de news chargees",
        f"- Total: {metrics.get('news_loaded_count', 0)}",
        "",
        "## Nombre de news USD",
        f"- USD: {metrics.get('usd_news_count', 0)}",
        "",
        "## Nombre de high impact",
        f"- HIGH: {metrics.get('high_impact_news_count', 0)}",
        f"- MEDIUM: {metrics.get('medium_impact_news_count', 0)}",
        f"- LOW: {metrics.get('low_impact_news_count', 0)}",
        "",
        "## Fenetres pre-news/post-news appliquees",
        f"- Pre-news: {PRE_NEWS_LOCKOUT_MINUTES} minutes",
        f"- Post-news hard: {POST_NEWS_HARD_MINUTES} minutes",
        f"- Post-news stealth: {POST_NEWS_STEALTH_MINUTES} minutes",
        "",
        "## Nombre d'evenements replay affectes par news veto",
        f"- news_clear: {metrics.get('events_with_news_clear', 0)}",
        f"- news_veto: {metrics.get('events_with_news_veto', 0)}",
        f"- pre_news_lockout: {metrics.get('events_with_pre_news_lockout', 0)}",
        f"- post_news_stealth: {metrics.get('events_with_post_news_stealth', 0)}",
        f"- news_context_missing: {metrics.get('events_with_news_context_missing', 0)}",
        "",
        "## Erreurs ou limites",
        f"- Erreur: {cache.error or 'Aucune'}",
        f"- replay_process_status: {process_status}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _blocked_payload(
    status: str,
    event: dict[str, Any],
    source: str,
    *,
    veto: bool = False,
    pre_news: bool = False,
    stealth: bool = False,
) -> dict[str, Any]:
    return {
        "calendar_status": status,
        "news_clear": False,
        "news_veto": bool(veto or pre_news),
        "high_impact_news": True,
        "pre_news_lockout": pre_news,
        "post_news_stealth": stealth,
        "news_normalized": False,
        "nearest_news": _public_event(event),
        "news_source": source,
    }


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    event_time = _parse_time(raw.get("time") or raw.get("time_utc") or raw.get("datetime") or raw.get("date"))
    if event_time is None:
        return None
    return {
        "time": event_time,
        "currency": _currency(raw),
        "impact": _impact(raw),
        "title": raw.get("title") or raw.get("event") or raw.get("name"),
        "actual": raw.get("actual"),
        "forecast": raw.get("forecast"),
        "previous": raw.get("previous"),
        "source_id": raw.get("source_id") or raw.get("id"),
    }


def _nearest_event(current_time: datetime, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    return min(events, key=lambda event: abs((event["time"] - current_time).total_seconds()))


def _public_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not event:
        return None
    return {
        "time": event["time"].isoformat().replace("+00:00", "Z"),
        "currency": event.get("currency"),
        "impact": event.get("impact"),
        "title": event.get("title"),
        "actual": event.get("actual"),
        "forecast": event.get("forecast"),
        "previous": event.get("previous"),
        "source_id": event.get("source_id"),
    }


def _parse_time(value: str | datetime | Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _currency(event: dict[str, Any]) -> str:
    return str(event.get("currency") or event.get("country") or "").upper()


def _impact(event: dict[str, Any]) -> str:
    return str(event.get("impact") or "").upper()
