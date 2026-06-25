"""News source adapters for Gold Sniper replay.

Supports FMP (financialmodelingprep), Fed FOMC (static official dates),
and manual fixtures. All adapters are testable without network by design.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import os
from gold_sniper.data_pipeline.news_jsonl import NormalizedNewsEvent, normalize_news_events


@dataclass(frozen=True)
class NewsFetchResult:
    source: str
    ok: bool
    events: list[NormalizedNewsEvent]
    errors: list[str]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["events"] = [event.to_dict() for event in self.events]
        return payload


def fetch_fmp_economic_calendar(
    *,
    start_date: str,
    end_date: str,
    api_key: str | None = None,
    timeout_sec: int = 30,
) -> NewsFetchResult:
    key = api_key or os.environ.get("FMP_API_KEY")
    if not key:
        return NewsFetchResult(
            source="FMP",
            ok=False,
            events=[],
            errors=["FMP_API_KEY_MISSING"],
            metadata={"provider": "FMP", "requires_key": True},
        )
    params = {"from": start_date[:10], "to": end_date[:10], "apikey": key}
    url = "https://financialmodelingprep.com/stable/economic-calendar?" + urlencode(params)
    return _fetch_json_calendar_url(source="FMP", url=url, timeout_sec=timeout_sec)


def build_fomc_static_events(*, start_date: str, end_date: str) -> NewsFetchResult:
    rows = [
        {"time": "2026-01-28T19:00:00Z", "currency": "USD", "impact": "HIGH", "event": "FOMC Statement", "source": "FED"},
        {"time": "2026-03-18T18:00:00Z", "currency": "USD", "impact": "HIGH", "event": "FOMC Statement", "source": "FED"},
        {"time": "2026-04-29T18:00:00Z", "currency": "USD", "impact": "HIGH", "event": "FOMC Statement", "source": "FED"},
        {"time": "2026-06-17T18:00:00Z", "currency": "USD", "impact": "HIGH", "event": "FOMC Statement", "source": "FED"},
    ]
    filtered = _filter_rows_by_date(rows, start_date=start_date, end_date=end_date)
    return NewsFetchResult(
        source="FED",
        ok=True,
        events=normalize_news_events(filtered, source="FED"),
        errors=[],
        metadata={"provider": "Federal Reserve", "mode": "static_official_dates"},
    )


def build_manual_major_us_events_fixture(*, start_date: str, end_date: str) -> NewsFetchResult:
    rows: list[dict[str, Any]] = []
    filtered = _filter_rows_by_date(rows, start_date=start_date, end_date=end_date)
    return NewsFetchResult(
        source="MANUAL_FIXTURE",
        ok=True,
        events=normalize_news_events(filtered, source="MANUAL_FIXTURE"),
        errors=[],
        metadata={"provider": "manual_fixture", "warning": "EMPTY_FIXTURE_NOT_VALID_FOR_PRODUCTION_REPLAY"},
    )


def _fetch_json_calendar_url(*, source: str, url: str, timeout_sec: int) -> NewsFetchResult:
    try:
        request = Request(url, headers={"User-Agent": "GoldSniper-P2B/1.0"})
        with urlopen(request, timeout=timeout_sec) as response:
            body = response.read().decode("utf-8")
        raw = json.loads(body)
        if isinstance(raw, dict) and "Error Message" in raw:
            return NewsFetchResult(source=source, ok=False, events=[], errors=[str(raw["Error Message"])], metadata={})
        rows = raw if isinstance(raw, list) else raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            return NewsFetchResult(source=source, ok=False, events=[], errors=["UNEXPECTED_PROVIDER_PAYLOAD"], metadata={"payload_type": type(raw).__name__})
        events = normalize_news_events(rows, source=source)
        return NewsFetchResult(source=source, ok=True, events=events, errors=[], metadata={"provider_rows": len(rows)})
    except Exception as exc:
        return NewsFetchResult(source=source, ok=False, events=[], errors=[str(exc)], metadata={})


def _filter_rows_by_date(rows: list[dict[str, Any]], *, start_date: str, end_date: str) -> list[dict[str, Any]]:
    from gold_sniper.data_pipeline.news_jsonl import parse_news_time
    start = parse_news_time(start_date)
    end = parse_news_time(end_date)
    out = []
    for row in rows:
        try:
            ts = parse_news_time(row.get("time"))
        except Exception:
            continue
        if start <= ts <= end:
            out.append(row)
    return out
