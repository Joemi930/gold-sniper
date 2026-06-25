"""Offline candle coverage manifest for replay data governance."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from gold_sniper.data_pipeline.timeframe_aggregation import aggregate_candles
from gold_sniper.replay.historical_data import (
    TIMEFRAME_SECONDS,
    build_data_quality_report,
    classify_time_gap,
    load_csv_candles,
    parse_timestamp,
)

REQUIRED_BASE_TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")
OPTIONAL_DERIVED_TIMEFRAMES = ("30m",)


@dataclass(frozen=True)
class TimeframeCoverage:
    timeframe: str
    path: str | None
    exists: bool
    candles: int
    start_utc: str | None
    end_utc: str | None
    gaps: int
    duplicates_removed: int = 0
    monotonic: bool = False
    checksum: str | None = None
    source: str = "CSV"
    coverage_status: str = "MISSING"
    missing_reason: str | None = None
    raw_gap_count: int = 0
    session_gap_count: int = 0
    weekend_gap_count: int = 0
    unexpected_gap_count: int = 0
    largest_gap_seconds: int = 0
    largest_gap_start_utc: str | None = None
    largest_gap_end_utc: str | None = None
    checksum_algorithm: str | None = "sha256"
    derived_from: str | None = None
    source_rows: int = 0
    normalized_rows: int = 0
    generated_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandleCoverageManifest:
    symbol: str
    requested_start_utc: str
    requested_end_utc: str
    data_root: str
    required_timeframes: list[str]
    optional_timeframes: list[str]
    timeframes: dict[str, TimeframeCoverage]
    overall_status: str
    available_start_utc: str | None
    available_end_utc: str | None
    missing_timeframes: list[str] = field(default_factory=list)
    partial_timeframes: list[str] = field(default_factory=list)
    generated_timeframes: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timeframes"] = {key: value.to_dict() for key, value in self.timeframes.items()}
        return payload


def build_candle_coverage_manifest(
    *,
    data_root: str | Path,
    symbol: str = "XAUUSD",
    requested_start_utc: str,
    requested_end_utc: str,
    required_timeframes: tuple[str, ...] = REQUIRED_BASE_TIMEFRAMES,
    optional_timeframes: tuple[str, ...] = OPTIONAL_DERIVED_TIMEFRAMES,
) -> CandleCoverageManifest:
    root = Path(data_root)
    requested_start = parse_timestamp(requested_start_utc)
    requested_end = parse_timestamp(requested_end_utc)
    timeframes: dict[str, TimeframeCoverage] = {}
    findings: list[dict[str, Any]] = []
    all_timeframes = list(dict.fromkeys([*required_timeframes, *optional_timeframes]))

    m1_path = _find_timeframe_csv(root, symbol, "1m", requested_start=requested_start, requested_end=requested_end)
    m1_window_candles = (
        load_csv_candles(m1_path, "1m", start=requested_start, end=requested_end)
        if m1_path and m1_path.exists() else []
    )

    for timeframe in all_timeframes:
        csv_path = _find_timeframe_csv(root, symbol, timeframe, requested_start=requested_start, requested_end=requested_end)
        derived_candles = None
        derived_from = None
        if csv_path is None and timeframe in {"5m", "15m", "1H", "4H"} and m1_window_candles:
            derived_candles = aggregate_candles(m1_window_candles, target_timeframe=timeframe)
            derived_from = str(m1_path)

        coverage = _build_timeframe_coverage(
            csv_path=csv_path,
            derived_candles=derived_candles,
            derived_from=derived_from,
            derived_source_rows=len(m1_window_candles) if derived_candles is not None else 0,
            timeframe=timeframe,
            symbol=symbol,
            requested_start=requested_start,
            requested_end=requested_end,
        )
        timeframes[timeframe] = coverage
        if coverage.coverage_status != "COVERAGE_OK":
            findings.append({
                "code": f"{timeframe}_COVERAGE_{coverage.coverage_status}",
                "timeframe": timeframe,
                "reason": coverage.missing_reason,
                "unexpected_gap_count": coverage.unexpected_gap_count,
                "available_start_utc": coverage.start_utc,
                "available_end_utc": coverage.end_utc,
            })

    missing_required = [
        timeframe for timeframe in required_timeframes
        if timeframes[timeframe].coverage_status == "MISSING"
    ]
    partial_required = [
        timeframe for timeframe in required_timeframes
        if timeframes[timeframe].coverage_status == "PARTIAL"
    ]
    starts = [
        parse_timestamp(coverage.start_utc)
        for coverage in timeframes.values()
        if coverage.start_utc and coverage.coverage_status != "MISSING"
    ]
    ends = [
        parse_timestamp(coverage.end_utc)
        for coverage in timeframes.values()
        if coverage.end_utc and coverage.coverage_status != "MISSING"
    ]

    if missing_required:
        overall = "MISSING"
    elif partial_required:
        overall = "PARTIAL"
    else:
        overall = "COVERAGE_OK"

    return CandleCoverageManifest(
        symbol=symbol,
        requested_start_utc=requested_start.isoformat(),
        requested_end_utc=requested_end.isoformat(),
        data_root=str(root),
        required_timeframes=list(required_timeframes),
        optional_timeframes=list(optional_timeframes),
        timeframes=timeframes,
        overall_status=overall,
        available_start_utc=max(starts).isoformat() if starts else None,
        available_end_utc=min(ends).isoformat() if ends else None,
        missing_timeframes=missing_required,
        partial_timeframes=partial_required,
        generated_timeframes=[
            timeframe for timeframe, coverage in timeframes.items()
            if coverage.source == "DERIVED_FROM_1M"
        ],
        findings=findings,
    )


def save_candle_coverage_manifest(manifest: CandleCoverageManifest, output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _find_timeframe_csv(
    root: Path,
    symbol: str,
    timeframe: str,
    *,
    requested_start: datetime | None = None,
    requested_end: datetime | None = None,
) -> Path | None:
    folder = root / timeframe
    if not folder.exists():
        return None
    candidates = sorted(folder.glob(f"{symbol}_{timeframe}_*.csv"))
    if not candidates:
        candidates = sorted(folder.glob("*.csv"))
    if not candidates:
        return None
    if requested_start is not None and requested_end is not None:
        ranked = [
            _coverage_rank(path, timeframe, requested_start=requested_start, requested_end=requested_end)
            for path in candidates
        ]
        ranked = [item for item in ranked if item is not None]
        if ranked:
            ranked.sort(reverse=True)
            return ranked[0][3]
    return candidates[-1]


def _coverage_rank(
    path: Path,
    timeframe: str,
    *,
    requested_start: datetime,
    requested_end: datetime,
) -> tuple[int, float, float, Path] | None:
    try:
        candles = load_csv_candles(path, timeframe)
    except Exception:
        return None
    if not candles:
        return None
    start = parse_timestamp(candles[0]["time"])
    end = parse_timestamp(candles[-1]["time"])
    covers = int(start <= requested_start and end >= requested_end)
    overlap_start = max(start, requested_start)
    overlap_end = min(end, requested_end)
    overlap = max(0.0, (overlap_end - overlap_start).total_seconds())
    end_score = end.timestamp()
    return covers, overlap, end_score, path


def _build_timeframe_coverage(
    *,
    csv_path: Path | None,
    derived_candles: list[dict[str, Any]] | None,
    derived_from: str | None,
    derived_source_rows: int,
    timeframe: str,
    symbol: str,
    requested_start: datetime,
    requested_end: datetime,
) -> TimeframeCoverage:
    if derived_candles is not None:
        quality = build_data_quality_report(
            derived_candles,
            symbol=symbol,
            timeframe=timeframe,
            source="DERIVED_FROM_1M",
        )
        return _coverage_from_quality(
            quality=quality,
            timeframe=timeframe,
            path=None,
            exists=True,
            source="DERIVED_FROM_1M",
            derived_from=derived_from,
            source_rows=derived_source_rows,
            generated_rows=len(derived_candles),
            requested_start=requested_start,
            requested_end=requested_end,
        )

    if csv_path is None or not csv_path.exists():
        return TimeframeCoverage(
            timeframe=timeframe,
            path=str(csv_path) if csv_path else None,
            exists=False,
            candles=0,
            start_utc=None,
            end_utc=None,
            gaps=0,
            duplicates_removed=0,
            monotonic=False,
            checksum=None,
            source="CSV",
            coverage_status="MISSING",
            missing_reason="CSV_FILE_MISSING",
            checksum_algorithm=None,
        )

    candles = load_csv_candles(csv_path, timeframe, start=requested_start, end=requested_end)
    quality = build_data_quality_report(candles, symbol=symbol, timeframe=timeframe, source=str(csv_path))
    return _coverage_from_quality(
        quality=quality,
        timeframe=timeframe,
        path=str(csv_path),
        exists=True,
        source="CSV",
        derived_from=None,
        source_rows=len(candles),
        generated_rows=0,
        requested_start=requested_start,
        requested_end=requested_end,
    )


def _coverage_from_quality(
    *,
    quality: Any,
    timeframe: str,
    path: str | None,
    exists: bool,
    source: str,
    derived_from: str | None,
    source_rows: int,
    generated_rows: int,
    requested_start: datetime,
    requested_end: datetime,
) -> TimeframeCoverage:
    start = parse_timestamp(quality.start_utc) if quality.start_utc else None
    end = parse_timestamp(quality.end_utc) if quality.end_utc else None

    if quality.candles <= 0:
        status = "MISSING"
        reason = "CSV_EMPTY"
    elif not quality.monotonic:
        status = "PARTIAL"
        reason = "TIMESTAMP_NOT_MONOTONIC"
    elif start is None or end is None:
        status = "MISSING"
        reason = "NO_TIME_RANGE"
    elif not _start_covers_request(start, requested_start, timeframe) or not _end_covers_request(end, requested_end, timeframe):
        status = "PARTIAL"
        reason = "REQUESTED_WINDOW_NOT_FULLY_COVERED"
    elif int(getattr(quality, "unexpected_gap_count", 0) or 0) > 0:
        status = "PARTIAL"
        reason = "UNEXPECTED_GAPS_PRESENT"
    else:
        status = "COVERAGE_OK"
        reason = None

    return TimeframeCoverage(
        timeframe=timeframe,
        path=path,
        exists=exists,
        candles=quality.candles,
        start_utc=quality.start_utc,
        end_utc=quality.end_utc,
        gaps=quality.gaps,
        duplicates_removed=quality.duplicates_removed,
        monotonic=quality.monotonic,
        checksum=quality.checksum,
        source=source,
        coverage_status=status,
        missing_reason=reason,
        raw_gap_count=quality.raw_gap_count,
        session_gap_count=quality.session_gap_count,
        weekend_gap_count=quality.weekend_gap_count,
        unexpected_gap_count=quality.unexpected_gap_count,
        largest_gap_seconds=quality.largest_gap_seconds,
        largest_gap_start_utc=quality.largest_gap_start_utc,
        largest_gap_end_utc=quality.largest_gap_end_utc,
        checksum_algorithm="sha256",
        derived_from=derived_from,
        source_rows=source_rows,
        normalized_rows=quality.candles,
        generated_rows=generated_rows,
    )


def _effective_end(end: datetime, timeframe: str) -> datetime:
    from datetime import timedelta

    return end + timedelta(seconds=TIMEFRAME_SECONDS[timeframe] - 1)


def _start_covers_request(start: datetime, requested_start: datetime, timeframe: str) -> bool:
    if start <= requested_start:
        return True
    if (start - requested_start).total_seconds() <= TIMEFRAME_SECONDS[timeframe]:
        return True
    gap_type = classify_time_gap(requested_start, start, TIMEFRAME_SECONDS[timeframe])
    return gap_type in {"SESSION_GAP", "WEEKEND_GAP"}


def _end_covers_request(end: datetime, requested_end: datetime, timeframe: str) -> bool:
    effective_end = _effective_end(end, timeframe)
    if effective_end >= requested_end:
        return True
    gap_type = classify_time_gap(effective_end, requested_end, TIMEFRAME_SECONDS[timeframe])
    return gap_type in {"SESSION_GAP", "WEEKEND_GAP"}
