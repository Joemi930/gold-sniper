"""Offline historical candle loading for replay mode."""
from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


TIMEFRAME_ALIASES = {
    "M1": "1m", "1M": "1m", "1m": "1m",
    "M5": "5m", "5M": "5m", "5m": "5m",
    "M15": "15m", "15M": "15m", "15m": "15m",
    "M30": "30m", "30M": "30m", "30m": "30m",
    "H1": "1H", "1H": "1H", "1h": "1H",
    "H4": "4H", "4H": "4H", "4h": "4H",
}
SUPPORTED_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1H", "4H"}
REQUIRED_COLUMNS = {"time", "open", "high", "low", "close"}

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
}


@dataclass(frozen=True)
class DataQualityReport:
    symbol: str
    timeframe: str
    candles: int
    start_utc: str | None
    end_utc: str | None
    gaps: int
    raw_gap_count: int
    session_gap_count: int
    weekend_gap_count: int
    unexpected_gap_count: int
    largest_gap_seconds: int
    largest_gap_start_utc: str | None
    largest_gap_end_utc: str | None
    duplicates_removed: int
    monotonic: bool
    checksum: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_timeframe(timeframe: str) -> str:
    normalized = TIMEFRAME_ALIASES.get(str(timeframe).strip())
    if normalized not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe. Expected one of: {', '.join(sorted(SUPPORTED_TIMEFRAMES))}")
    return normalized


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("Missing candle timestamp")
        if raw.isdigit():
            parsed = datetime.fromtimestamp(int(raw), tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_csv_candles(
    path: str | Path,
    timeframe: str = "1m",
    *,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
) -> list[dict[str, Any]]:
    """Load and normalize local CSV candles without MT5 or network access."""
    normalize_timeframe(timeframe)
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Historical CSV not found: {csv_path}")

    start_dt = parse_timestamp(start) if start is not None else None
    end_dt = parse_timestamp(end) if end is not None else None

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Historical CSV has no header")
        columns = {_canonical_column(name) for name in reader.fieldnames}
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"Historical CSV missing columns: {', '.join(sorted(missing))}")
        if "volume" not in columns and "tick_volume" not in columns:
            raise ValueError("Historical CSV missing volume or tick_volume column")

        candles = [_normalize_row(row) for row in reader]

    filtered = []
    for candle in candles:
        if start_dt is not None and candle["time"] < start_dt:
            continue
        if end_dt is not None and candle["time"] > end_dt:
            continue
        filtered.append(candle)

    return _dedupe_sort(filtered)


def load_timeframe_csvs(paths: dict[str, str | Path]) -> dict[str, list[dict[str, Any]]]:
    loaded: dict[str, list[dict[str, Any]]] = {}
    for timeframe, path in paths.items():
        normalized = normalize_timeframe(timeframe)
        loaded[normalized] = load_csv_candles(path, normalized)
    return loaded


def build_data_quality_report(
    candles: list[dict[str, Any]],
    *,
    symbol: str = "XAUUSD",
    timeframe: str = "1m",
    source: str = "CSV",
) -> DataQualityReport:
    normalized_tf = normalize_timeframe(timeframe)
    expected_seconds = TIMEFRAME_SECONDS[normalized_tf]
    ordered = _dedupe_sort(candles)
    times = [item["time"] for item in ordered]
    gaps = 0
    session_gap_count = 0
    weekend_gap_count = 0
    unexpected_gap_count = 0
    largest_gap_seconds = 0
    largest_gap_start = None
    largest_gap_end = None
    for previous, current in zip(times, times[1:]):
        delta = int((current - previous).total_seconds())
        if delta > expected_seconds:
            gaps += 1
            if delta > largest_gap_seconds:
                largest_gap_seconds = delta
                largest_gap_start = previous
                largest_gap_end = current
            gap_type = classify_time_gap(previous, current, expected_seconds)
            if gap_type == "WEEKEND_GAP":
                weekend_gap_count += 1
            elif gap_type == "SESSION_GAP":
                session_gap_count += 1
            else:
                unexpected_gap_count += 1
    raw = "|".join(
        f"{c['time'].isoformat()}:{c['open']}:{c['high']}:{c['low']}:{c['close']}:{c.get('tick_volume', c.get('volume', 0))}"
        for c in ordered
    )
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return DataQualityReport(
        symbol=symbol,
        timeframe=normalized_tf,
        candles=len(ordered),
        start_utc=times[0].isoformat() if times else None,
        end_utc=times[-1].isoformat() if times else None,
        gaps=gaps,
        raw_gap_count=gaps,
        session_gap_count=session_gap_count,
        weekend_gap_count=weekend_gap_count,
        unexpected_gap_count=unexpected_gap_count,
        largest_gap_seconds=largest_gap_seconds,
        largest_gap_start_utc=largest_gap_start.isoformat() if largest_gap_start else None,
        largest_gap_end_utc=largest_gap_end.isoformat() if largest_gap_end else None,
        duplicates_removed=max(0, len(candles) - len(ordered)),
        monotonic=all(a < b for a, b in zip(times, times[1:])),
        checksum=checksum,
        source=source,
    )


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {_canonical_column(key): value for key, value in row.items()}
    volume = normalized.get("tick_volume") if normalized.get("tick_volume") not in (None, "") else normalized.get("volume")
    candle = {
        "time": parse_timestamp(normalized.get("time")),
        "open": _float(normalized.get("open"), "open"),
        "high": _float(normalized.get("high"), "high"),
        "low": _float(normalized.get("low"), "low"),
        "close": _float(normalized.get("close"), "close"),
        "volume": _float(volume, "volume"),
        "tick_volume": _float(volume, "tick_volume"),
    }
    _validate_ohlc(candle)
    return candle


def classify_time_gap(previous: datetime, current: datetime, expected_seconds: int) -> str:
    previous = parse_timestamp(previous)
    current = parse_timestamp(current)
    delta = int((current - previous).total_seconds())
    if delta <= expected_seconds:
        return "NONE"
    if _gap_crosses_weekend(previous, current):
        return "WEEKEND_GAP"
    if delta <= 4 * 3600 and (
        previous.hour in {20, 21, 22, 23}
        or current.hour in {0, 1, 21, 22, 23}
    ):
        return "SESSION_GAP"
    return "UNEXPECTED_GAP"


def _gap_crosses_weekend(previous: datetime, current: datetime) -> bool:
    day = previous.date()
    end_day = current.date()
    while day <= end_day:
        if day.weekday() >= 5:
            return True
        day += timedelta(days=1)
    return False


def _validate_ohlc(candle: dict[str, Any], *, source: str | None = None) -> None:
    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])
    if h < max(o, c) or l > min(o, c) or h < l:
        raise ValueError(f"Invalid OHLC candle: {source or ''} {candle}")


def _canonical_column(name: Any) -> str:
    key = str(name or "").strip().lower()
    aliases = {
        "timestamp": "time",
        "datetime": "time",
        "date": "time",
        "tickvolume": "tick_volume",
        "tick volume": "tick_volume",
    }
    return aliases.get(key, key)


def _dedupe_sort(candles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time: dict[datetime, dict[str, Any]] = {}
    for candle in candles:
        by_time[candle["time"]] = candle
    return [by_time[key] for key in sorted(by_time)]


def _float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric candle value for {name}: {value!r}") from exc
