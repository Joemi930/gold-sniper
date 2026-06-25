"""Fetch and normalize economic news for Phase 7 replay cache generation.

This module performs network calls only when executed explicitly. It keeps API
credentials out of logs, reports, cache files, and tests.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

from gold_sniper.replay.news_loader import DEFAULT_NEWS_CACHE


FMP_API_URL = "https://financialmodelingprep.com/stable/economic-calendar"
FINNHUB_API_URL = "https://finnhub.io/api/v1/calendar/economic"
DEFAULT_START_ISO = "2026-04-01T01:00:00Z"
DEFAULT_END_ISO = "2026-06-05T20:00:00Z"
HIGH_IMPACT_KEYWORDS = (
    "non-farm",
    "nonfarm",
    "nfp",
    "cpi",
    "fomc",
    "federal funds",
    "interest rate",
    "unemployment rate",
    "pce",
    "ppi",
    "gdp",
    "powell",
)


def load_api_credentials(env_path: str | Path = ".env") -> dict[str, str]:
    values = dict(os.environ)
    path = Path(env_path)
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in values:
                values[key] = value
    return {
        "fmp": _first_non_empty(
            values,
            ("FMP_API_KEY", "FINANCIALMODELINGPREP_API_KEY", "FINANCIAL_MODELING_PREP_API_KEY", "FMP_TOKEN"),
        ),
        "finnhub": _first_non_empty(values, ("FINNHUB_TOKEN",)),
    }


def fetch_fmp_economic_calendar(from_date: str, to_date: str, api_key: str | None = None) -> list[dict[str, Any]]:
    key = api_key if api_key is not None else load_api_credentials().get("fmp")
    if not key:
        raise RuntimeError("FMP_API_KEY_MISSING")
    params = {"from": from_date, "to": to_date, "apikey": key}
    payload = _http_get_json(FMP_API_URL, params)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        raw = payload.get("economicCalendar") or payload.get("economic") or payload.get("data") or []
        return raw if isinstance(raw, list) else []
    return []


def fetch_finnhub_economic_calendar(from_date: str, to_date: str, api_key: str | None = None) -> list[dict[str, Any]]:
    key = api_key if api_key is not None else load_api_credentials().get("finnhub")
    if not key:
        raise RuntimeError("FINNHUB_TOKEN_MISSING")
    params = {"from": from_date, "to": to_date, "token": key}
    payload = _http_get_json(FINNHUB_API_URL, params)
    if isinstance(payload, dict):
        raw = payload.get("economicCalendar") or payload.get("economic") or []
        return raw if isinstance(raw, list) else []
    return []


def fetch_with_fallback(from_date: str, to_date: str, credentials: dict[str, str] | None = None) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
    creds = credentials or load_api_credentials()
    request_counts = {"fmp": 0, "finnhub": 0}
    errors: list[str] = []
    try:
        request_counts["fmp"] += 1
        events = fetch_fmp_economic_calendar(from_date, to_date, creds.get("fmp"))
        if events:
            return "financialmodelingprep", events, request_counts
    except Exception as exc:
        errors.append(f"FMP:{type(exc).__name__}")
    try:
        request_counts["finnhub"] += 1
        events = fetch_finnhub_economic_calendar(from_date, to_date, creds.get("finnhub"))
        if events:
            return "finnhub", events, request_counts
    except Exception as exc:
        errors.append(f"FINNHUB:{type(exc).__name__}")
    raise RuntimeError("NEWS_API_EMPTY_OR_FAILED " + ";".join(errors))


def normalize_news_events(raw_events: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        event_time = _parse_event_time(raw)
        currency = _event_currency(raw)
        if event_time is None or currency != "USD":
            continue
        title = _event_title(raw)
        normalized.append(
            {
                "time": event_time.isoformat().replace("+00:00", "Z"),
                "currency": currency,
                "impact": _event_impact(raw, title),
                "title": title,
                "actual": raw.get("actual"),
                "forecast": raw.get("forecast"),
                "previous": raw.get("previous"),
                "source_id": raw.get("id") or raw.get("source_id"),
            }
        )
    normalized.sort(key=lambda item: item["time"])
    return normalized


def write_news_cache(
    events: list[dict[str, Any]],
    path: Path = DEFAULT_NEWS_CACHE,
    *,
    source: str,
    symbol: str = "XAUUSD",
    date_start: str = DEFAULT_START_ISO,
    date_end: str = DEFAULT_END_ISO,
) -> dict[str, Any]:
    payload = {
        "source": source,
        "symbol": symbol,
        "currency_filter": ["USD"],
        "date_start": date_start,
        "date_end": date_end,
        "timezone": "UTC",
        "events": events,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    high = [event for event in events if str(event.get("impact", "")).upper() == "HIGH"]
    return {
        "source": source,
        "path": str(path),
        "total_events": len(events),
        "usd_events": len(events),
        "high_impact_events": len(high),
    }


def generate_news_cache(
    from_date: str,
    to_date: str,
    *,
    symbol: str = "XAUUSD",
    output: Path = DEFAULT_NEWS_CACHE,
) -> dict[str, Any]:
    source, raw_events, request_counts = fetch_with_fallback(from_date, to_date)
    normalized = normalize_news_events(raw_events, source)
    summary = write_news_cache(
        normalized,
        output,
        source=source,
        symbol=symbol,
        date_start=f"{from_date}T01:00:00Z" if from_date == "2026-04-01" else f"{from_date}T00:00:00Z",
        date_end=f"{to_date}T20:00:00Z" if to_date == "2026-06-05" else f"{to_date}T23:59:59Z",
    )
    summary["fmp_requests"] = request_counts["fmp"]
    summary["finnhub_requests"] = request_counts["finnhub"]
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate local USD news cache for Phase 7 replay.")
    parser.add_argument("--from", dest="from_date", default="2026-04-01")
    parser.add_argument("--to", dest="to_date", default="2026-06-05")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--output", type=Path, default=DEFAULT_NEWS_CACHE)
    args = parser.parse_args(argv)

    try:
        summary = generate_news_cache(args.from_date, args.to_date, symbol=args.symbol, output=args.output)
    except Exception as exc:
        print(json.dumps({"status": "FAILED_CLEANLY", "error": type(exc).__name__}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "status": "TERMINATED_CLEANLY",
                "source": summary["source"],
                "total_events": summary["total_events"],
                "usd_events": summary["usd_events"],
                "high_impact_events": summary["high_impact_events"],
                "cache_path": summary["path"],
                "fmp_requests": summary["fmp_requests"],
                "finnhub_requests": summary["finnhub_requests"],
            },
            indent=2,
        )
    )
    return 0


def _http_get_json(url: str, params: dict[str, str]) -> Any:
    query = parse.urlencode(params)
    req = request.Request(f"{url}?{query}", headers={"Accept": "application/json", "User-Agent": "GoldSniperReplay/1.0"})
    context = ssl._create_unverified_context()
    with request.urlopen(req, timeout=20, context=context) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _first_non_empty(values: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = values.get(key)
        if value:
            return value
    return ""


def _parse_event_time(raw: dict[str, Any]) -> datetime | None:
    value = raw.get("time") or raw.get("date") or raw.get("datetime") or raw.get("time_utc")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _event_currency(raw: dict[str, Any]) -> str:
    return str(raw.get("currency") or raw.get("country") or "").upper()


def _event_title(raw: dict[str, Any]) -> str | None:
    title = raw.get("title") or raw.get("event") or raw.get("name")
    return str(title) if title not in (None, "") else None


def _event_impact(raw: dict[str, Any], title: str | None) -> str:
    raw_impact = str(raw.get("impact") or raw.get("impactLevel") or "").upper()
    if raw_impact in {"HIGH", "MEDIUM", "LOW"}:
        return raw_impact
    if raw_impact in {"3", "2", "1"}:
        return {"3": "HIGH", "2": "MEDIUM", "1": "LOW"}[raw_impact]
    text = (title or "").lower()
    if any(keyword in text for keyword in HIGH_IMPACT_KEYWORDS):
        return "HIGH"
    return "LOW"


if __name__ == "__main__":
    raise SystemExit(main())
