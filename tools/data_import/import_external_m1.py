"""P1 — External M1 Data Importer (Dukascopy fallback).

Downloads XAUUSD M1 tick data from Dukascopy's historical data feed,
decodes .bi5 binary format, aggregates ticks into M1 candles, and
merges with existing MT5 data without duplicates.

Dukascopy .bi5 format:
  - LZMA compressed binary
  - Each row: 5 × int32 big-endian (20 bytes)
    [timestamp_ms, ask, bid, ask_volume, bid_volume]
  - Prices: integer × 10 (XAUUSD scale factor)
  - Timestamp: milliseconds since start of the hour
  - Volume: integer × 100

Usage:
  python tools/data_import/import_external_m1.py \\
    --start 2025-12-01 --end 2026-03-15 \\
    --output-root gold_sniper/data/historical/XAUUSD

P1-clean: no broker writes, no live trading, offline data only.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import lzma
import os
import struct
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

# ── Constants ────────────────────────────────────────────────────────────────

DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
XAUUSD_SCALE = 10  # Dukascopy stores XAUUSD price × 10
VOLUME_SCALE = 100  # Volume × 100
BYTES_PER_TICK = 20  # 5 × int32
HOURS_PER_REQUEST = 1  # One .bi5 file per hour
MAX_RETRIES = 3
RETRY_DELAY = 2.0  # seconds between retries
REQUEST_DELAY = 0.5  # polite delay between requests

CSV_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "volume", "spread", "real_volume"]

UTC = timezone.utc


# ── Dukascopy .bi5 downloader ───────────────────────────────────────────────

class Bi5DownloadError(Exception):
    """Raised when a .bi5 file cannot be downloaded or decoded."""


def _build_bi5_url(symbol: str, dt: datetime) -> str:
    """Build the Dukascopy datafeed URL for a specific hour.

    Example:
      https://datafeed.dukascopy.com/datafeed/XAUUSD/2025/12/01/00h_ticks.bi5
    """
    return (
        f"{DUKASCOPY_BASE_URL}/{symbol}/"
        f"{dt.year}/{dt.month:02d}/{dt.day:02d}/"
        f"{dt.hour:02d}h_ticks.bi5"
    )


def _download_bi5(url: str) -> bytes:
    """Download a .bi5 file with retries."""
    req = Request(url, headers={"User-Agent": "GoldSniper-P1/3.2"})
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    raise Bi5DownloadError(f"Failed to download {url}: {last_error}")


def _decode_bi5(data: bytes) -> list[dict[str, Any]]:
    """Decode a .bi5 file into a list of tick dicts.

    Returns ticks sorted by timestamp with fields:
      time (datetime), ask (float), bid (float), ask_volume (float), bid_volume (float)
    """
    if not data:
        return []

    # Decompress LZMA
    try:
        decompressed = lzma.decompress(data)
    except Exception as exc:
        raise Bi5DownloadError(f"LZMA decompression failed: {exc}")

    if len(decompressed) % BYTES_PER_TICK != 0:
        raise Bi5DownloadError(
            f"Invalid .bi5 data: length {len(decompressed)} not divisible by {BYTES_PER_TICK}"
        )

    ticks = []
    num_ticks = len(decompressed) // BYTES_PER_TICK

    for i in range(num_ticks):
        offset = i * BYTES_PER_TICK
        chunk = decompressed[offset:offset + BYTES_PER_TICK]
        ts_ms, ask_raw, bid_raw, ask_vol_raw, bid_vol_raw = struct.unpack(">iiiii", chunk)

        ticks.append({
            "time": ts_ms,  # will be resolved when we know the hour
            "ask": ask_raw / XAUUSD_SCALE,
            "bid": bid_raw / XAUUSD_SCALE,
            "ask_volume": ask_vol_raw / VOLUME_SCALE if ask_vol_raw >= 0 else 0.0,
            "bid_volume": bid_vol_raw / VOLUME_SCALE if bid_vol_raw >= 0 else 0.0,
        })

    return ticks


def _resolve_tick_times(ticks: list[dict[str, Any]], hour_start: datetime) -> list[dict[str, Any]]:
    """Convert millisecond offsets to absolute UTC datetimes."""
    base_ms = hour_start.timestamp()
    for tick in ticks:
        tick["time"] = datetime.fromtimestamp(base_ms + tick["time"] / 1000.0, tz=UTC)
    return ticks


# ── Tick-to-M1 aggregation ──────────────────────────────────────────────────

def _aggregate_ticks_to_m1(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate tick data into 1-minute OHLCV candles.

    Uses midpoint price (bid+ask)/2 for OHLC.  Spread is ask-bid in points.
    """
    if not ticks:
        return []

    # Sort ticks by time
    sorted_ticks = sorted(ticks, key=lambda t: t["time"])

    candles: dict[datetime, dict[str, Any]] = {}

    for tick in sorted_ticks:
        # Floor to minute
        minute = tick["time"].replace(second=0, microsecond=0)
        mid = (tick["ask"] + tick["bid"]) / 2.0

        if minute not in candles:
            candles[minute] = {
                "time": minute,
                "open": mid,
                "high": mid,
                "low": mid,
                "close": mid,
                "tick_volume": 0,
                "volume": 0,
                "spread": 0,
                "real_volume": 0,
                "spread_sum": 0.0,
                "spread_count": 0,
            }

        c = candles[minute]
        c["high"] = max(c["high"], mid)
        c["low"] = min(c["low"], mid)
        c["close"] = mid
        c["tick_volume"] += 1
        c["volume"] += int(tick.get("ask_volume", 0) + tick.get("bid_volume", 0))
        c["real_volume"] += int(tick.get("ask_volume", 0) + tick.get("bid_volume", 0))
        c["spread_sum"] += tick["ask"] - tick["bid"]
        c["spread_count"] += 1

    # Finalize candles
    result = []
    for minute in sorted(candles):
        c = candles[minute]
        if c["spread_count"] > 0:
            c["spread"] = int(round(c["spread_sum"] / c["spread_count"] * 10000))  # spread in points
        else:
            c["spread"] = 0
        # Clean up temp fields
        del c["spread_sum"]
        del c["spread_count"]
        result.append(c)

    return result


# ── Download + aggregate for a date range ───────────────────────────────────

def download_m1_range(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    on_progress: Any = None,
) -> list[dict[str, Any]]:
    """Download and aggregate M1 candles from Dukascopy for a date range.

    Args:
        symbol: Dukascopy symbol (e.g., "XAUUSD")
        start: Start datetime (UTC)
        end: End datetime (UTC)
        on_progress: Optional callback(hour_dt, status, detail)

    Returns:
        List of M1 candle dicts sorted by time.
    """
    all_ticks: list[dict[str, Any]] = []
    current = start.replace(minute=0, second=0, microsecond=0)
    total_hours = int((end - start).total_seconds() / 3600)
    downloaded = 0
    failed = 0
    empty = 0

    while current < end:
        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(hours=1)
            continue

        url = _build_bi5_url(symbol, current)
        try:
            raw = _download_bi5(url)
            if raw:
                ticks = _decode_bi5(raw)
                if ticks:
                    ticks = _resolve_tick_times(ticks, current)
                    all_ticks.extend(ticks)
                    if on_progress:
                        on_progress(current, "OK", f"{len(ticks)} ticks")
                else:
                    empty += 1
                    if on_progress:
                        on_progress(current, "EMPTY", "0 ticks")
            else:
                empty += 1
        except Bi5DownloadError as exc:
            failed += 1
            if on_progress:
                on_progress(current, "FAIL", str(exc)[:80])
        except Exception as exc:
            failed += 1
            if on_progress:
                on_progress(current, "ERROR", str(exc)[:80])

        downloaded += 1
        if downloaded % 100 == 0 and on_progress:
            on_progress(None, "PROGRESS", f"{downloaded}/{total_hours} hours, {failed} failed, {empty} empty")

        current += timedelta(hours=1)
        time.sleep(REQUEST_DELAY)  # Be polite to Dukascopy servers

    if on_progress:
        on_progress(None, "SUMMARY", f"Downloaded {downloaded} hours, {failed} failed, {empty} empty, {len(all_ticks)} ticks total")

    # Aggregate to M1
    if on_progress:
        on_progress(None, "AGGREGATE", f"Aggregating {len(all_ticks)} ticks to M1...")
    candles = _aggregate_ticks_to_m1(all_ticks)

    return candles


# ── CSV I/O ─────────────────────────────────────────────────────────────────

def write_m1_csv(candles: list[dict[str, Any]], path: Path) -> int:
    """Write M1 candles to CSV. Returns number of rows written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for c in candles:
            ts = c["time"]
            if isinstance(ts, datetime):
                ts = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            writer.writerow([
                ts,
                c["open"], c["high"], c["low"], c["close"],
                c.get("tick_volume", 0),
                c.get("volume", 0),
                c.get("spread", 0),
                c.get("real_volume", 0),
            ])
    return len(candles)


def read_csv_candles(path: Path) -> list[dict[str, Any]]:
    """Read Gold Sniper CSV candles."""
    candles = []
    if not path.exists():
        return candles
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append({
                "time": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "tick_volume": int(row.get("tick_volume", 0) or 0),
                "volume": int(row.get("volume", 0) or 0),
                "spread": int(row.get("spread", 0) or 0),
                "real_volume": int(row.get("real_volume", 0) or 0),
            })
    return candles


# ── Merge ───────────────────────────────────────────────────────────────────

def merge_m1_files(
    existing_path: Path,
    new_candles: list[dict[str, Any]],
    output_path: Path,
    *,
    backup: bool = True,
) -> dict[str, Any]:
    """Merge new M1 candles with existing CSV, deduplicating by timestamp.

    Args:
        existing_path: Path to existing MT5 M1 CSV
        new_candles: New candles to merge
        output_path: Where to write the merged result
        backup: If True, create a .bak copy of the existing file

    Returns:
        Dict with merge statistics.
    """
    stats = {
        "existing_count": 0,
        "new_count": len(new_candles),
        "merged_count": 0,
        "duplicates_removed": 0,
        "backup_created": False,
    }

    # Read existing
    existing = read_csv_candles(existing_path) if existing_path.exists() else []
    stats["existing_count"] = len(existing)

    # Backup
    if backup and existing_path.exists():
        backup_path = existing_path.with_suffix(".csv.bak")
        existing_path.rename(backup_path)
        stats["backup_created"] = True

    # Merge: build dict by timestamp, new data overwrites existing
    merged: dict[str, dict[str, Any]] = {}
    for c in existing:
        ts = c["time"]
        if isinstance(ts, datetime):
            ts = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        merged[ts] = c

    for c in new_candles:
        ts = c["time"]
        if isinstance(ts, datetime):
            ts = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        if ts in merged:
            stats["duplicates_removed"] += 1
        merged[ts] = c  # new data wins

    # Sort and write
    sorted_candles = [merged[ts] for ts in sorted(merged)]
    stats["merged_count"] = len(sorted_candles)
    write_m1_csv(sorted_candles, output_path)

    return stats


# ── Main importer ───────────────────────────────────────────────────────────

def import_external_m1(
    symbol: str = "XAUUSD",
    start: str = "2025-12-01",
    end: str = "2026-03-15",
    output_root: str | Path = "gold_sniper/data/historical/XAUUSD",
    *,
    merge: bool = True,
) -> dict[str, Any]:
    """Main entry point for external M1 import.

    Returns a dict with status, coverage, and file paths.
    """
    start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=UTC)
    root = Path(output_root)

    result: dict[str, Any] = {
        "status": "UNKNOWN",
        "source": "DUKASCOPY",
        "symbol": symbol,
        "start_requested": start,
        "end_requested": end,
        "candles_downloaded": 0,
        "merge_stats": {},
        "output_path": "",
    }

    print(f"\n{'='*60}")
    print(f"External M1 Importer — Dukascopy Data Feed")
    print(f"{'='*60}")
    print(f"Symbol:   {symbol}")
    print(f"Period:   {start} -> {end}")
    print(f"Output:   {root}")
    print(f"Merge:    {merge}")
    print(f"{'='*60}\n")

    # Track progress
    last_progress = time.monotonic()

    def progress(hour_dt, status, detail):
        nonlocal last_progress
        now = time.monotonic()
        if hour_dt is not None:
            if now - last_progress > 2.0 or status in ("FAIL", "ERROR"):
                print(f"  {hour_dt.strftime('%Y-%m-%d %H:00')} UTC  [{status}] {detail}")
                last_progress = now
        else:
            print(f"  [{status}] {detail}")

    # Download
    candles = download_m1_range(symbol, start_dt, end_dt, on_progress=progress)
    result["candles_downloaded"] = len(candles)

    if not candles:
        result["status"] = "BLOCKED"
        result["error"] = "No candles downloaded"
        print("\nBLOCKED: No data downloaded from Dukascopy.")
        return result

    # Coverage
    first_ts = candles[0]["time"]
    last_ts = candles[-1]["time"]
    if isinstance(first_ts, datetime):
        first_ts = first_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(last_ts, datetime):
        last_ts = last_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    result["coverage_start"] = first_ts
    result["coverage_end"] = last_ts

    print(f"\nDownloaded {len(candles)} M1 candles: {first_ts} -> {last_ts}")

    # Write standalone file
    standalone_path = root / "1m" / f"XAUUSD_1m_DUKASCOPY_{start}_{end}.csv"
    write_m1_csv(candles, standalone_path)
    result["standalone_path"] = str(standalone_path)
    print(f"Standalone: {standalone_path}")

    # Merge with existing MT5 data
    if merge:
        existing_path = root / "1m" / "XAUUSD_1m_2025-12-01_2026-06-01.csv"
        merged_path = root / "1m" / "XAUUSD_1m_MERGED_2025-12-01_2026-06-01.csv"

        print(f"\nMerging with existing MT5 data...")
        print(f"  Existing: {existing_path}")
        print(f"  Output:   {merged_path}")

        merge_stats = merge_m1_files(existing_path, candles, merged_path, backup=True)
        result["merge_stats"] = merge_stats
        result["output_path"] = str(merged_path)

        print(f"  Existing candles:  {merge_stats['existing_count']:,}")
        print(f"  New candles:       {merge_stats['new_count']:,}")
        print(f"  Merged total:      {merge_stats['merged_count']:,}")
        print(f"  Duplicates removed:{merge_stats['duplicates_removed']:,}")
        print(f"  Backup created:    {merge_stats['backup_created']}")
    else:
        result["output_path"] = str(standalone_path)

    result["status"] = "SUCCESS"
    print(f"\n{'='*60}")
    print(f"Import complete: {result['status']}")
    print(f"{'='*60}")

    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P1 External M1 Data Importer — Dukascopy historical data feed."
    )
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("gold_sniper/data/historical/XAUUSD"),
    )
    parser.add_argument("--no-merge", action="store_true",
                        help="Skip merging with existing MT5 data")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = import_external_m1(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        output_root=args.output_root,
        merge=not args.no_merge,
    )
    if result["status"] == "BLOCKED":
        print(f"\nBLOCKED: {result.get('error', 'Unknown error')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
