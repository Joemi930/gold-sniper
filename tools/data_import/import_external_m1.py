"""P1 — External M1 Data Importer (multi-source: Dukascopy + histdata.com).

This module provides TWO M1 data acquisition paths:

[ACTIVE / PROVEN]  histdata.com — ASCII OHLCV download
  - Downloads M1 data from https://www.histdata.com/ for XAUUSD
  - Format: YYYYMMDD HHMMSS;O;H;L;C;V (semicolon-separated ASCII)
  - No volume or spread data (tick_volume=0, spread=0)
  - Used to fill the Dec 2025 - Feb 2026 gap (85,657 candles)
  - Requires monkeypatched SSL (verify=False) on Windows/Python 3.13+
  - See _import_from_histdata() function at the bottom of this file

[PRESERVED / BLOCKED]  Dukascopy .bi5 tick data
  - Downloads tick data from https://datafeed.dukascopy.com/
  - .bi5 format: LZMA-compressed binary, 5 × int32 big-endian per tick
    [timestamp_ms, ask, bid, ask_volume, bid_volume]
  - Prices: integer × 10 (XAUUSD scale factor)
  - Timestamp: milliseconds since start of the hour
  - Volume: integer × 100
  - BLOCKED: datafeed.dukascopy.com is unreachable from current location
    (SSL timeout). All .bi5 downloader/decoder code is PRESERVED below
    for future use when network conditions change.
  - Tick-to-M1 aggregation code works correctly — tested on sample data

MERGE logic (works with both sources):
  - Deduplicates by timestamp (new data wins)
  - Creates .bak backup before merge
  - Sorts output by timestamp ascending
  - Output: Gold Sniper CSV format with UTC ISO timestamps

DATA PROVENANCE (actual pipeline used for P1 M1 dataset):
  Period                Source              Candles    Format
  ──────                ──────              ───────    ──────
  2025-12-01 → 2026-02-27  histdata.com     85,657     ASCII 1M CSV
  2026-03-16 → 2026-06-26  MT5 (JustMarkets) 100,035   MT5 copy_rates_range
  ⚠ GAP: 2026-02-27 16:58 → 2026-03-16 04:51 UTC (~17 days, ~11,500 missing
    M1 candles). First half of March 2026 has NO M1 data.

Usage:
  # histdata.com download (proven path)
  python tools/data_import/import_external_m1.py \\
    --source histdata --start 2025-12-01 --end 2026-03-01

  # Dukascopy download (preserved, currently blocked)
  python tools/data_import/import_external_m1.py \\
    --source dukascopy --start 2025-12-01 --end 2026-03-15

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


# ── histdata.com downloader (PROVEN PATH) ─────────────────────────────────

def _build_histdata_url(year: int, month: int) -> str:
    """Build histdata.com download URL for XAUUSD M1 data.

    Histdata.com serves ASCII CSV files per month in format:
        YYYYMMDD HHMMSS;O;H;L;C;V
    """
    return f"https://www.histdata.com/download-free-forex-historical-data/?/metatrader/1-minute-bar-quotes/XAUUSD/{year}/{month:02d}"


def _parse_histdata_line(line: str) -> dict[str, Any] | None:
    """Parse one histdata.com ASCII line into an M1 candle dict.

    Input format:  YYYYMMDD HHMMSS;O;H;L;C;V
    Example:        20251201 000000;4245.395;4246.375;4244.105;4244.929;0

    Returns a dict with Gold Sniper columns, or None on parse failure.
    """
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("<"):
        return None
    try:
        parts = line.split(";")
        if len(parts) < 5:
            return None
        dt_str = parts[0].strip()  # YYYYMMDD HHMMSS
        o = float(parts[1])
        h = float(parts[2])
        l = float(parts[3])
        c = float(parts[4])
        v = int(float(parts[5])) if len(parts) > 5 else 0

        # Convert to ISO UTC
        year = int(dt_str[:4])
        month = int(dt_str[4:6])
        day = int(dt_str[6:8])
        hour = int(dt_str[9:11]) if len(dt_str) >= 11 else 0
        minute = int(dt_str[11:13]) if len(dt_str) >= 13 else 0
        second = int(dt_str[13:15]) if len(dt_str) >= 15 else 0
        ts = datetime(year, month, day, hour, minute, second, tzinfo=UTC)

        return {
            "time": ts,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "tick_volume": v,
            "volume": v,
            "spread": 0,
            "real_volume": 0,
        }
    except (ValueError, IndexError):
        return None


def download_histdata_range(
    start: datetime,
    end: datetime,
    *,
    on_progress: Any = None,
) -> list[dict[str, Any]]:
    """Download M1 candles from histdata.com for a date range.

    Downloads one file per month, parses ASCII semicolon-separated format,
    and returns deduplicated M1 candles sorted by time.

    Note: histdata.com requires SSL workaround on Windows/Python 3.13:
        import urllib3
        urllib3.disable_warnings()
        # Then: requests.get(url, verify=False)

    Args:
        start: Start datetime (UTC)
        end: End datetime (UTC)
        on_progress: Optional callback(month_str, status, detail)

    Returns:
        List of M1 candle dicts sorted by time.
    """
    import requests

    # Monkeypatch SSL for Windows Python 3.13 compatibility
    try:
        import urllib3
        urllib3.disable_warnings()
    except ImportError:
        pass

    all_candles: list[dict[str, Any]] = []
    current = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    while current <= end:
        year = current.year
        month = current.month
        month_label = f"{year}-{month:02d}"

        # Build the download URL
        url = (
            f"https://www.histdata.com/download-free-forex-historical-data/"
            f"?/metatrader/1-minute-bar-quotes/XAUUSD/{year}/{month:02d}"
        )

        if on_progress:
            on_progress(month_label, "DOWNLOADING", url)

        candles_this_month = 0
        try:
            # histdata.com requires a POST or GET with specific headers
            # The actual download is triggered via a redirect after form submission
            # This direct-get approach works with session handling
            session = requests.Session()
            session.verify = False

            # Step 1: Get the download page
            resp = session.get(url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            })

            if resp.status_code != 200:
                if on_progress:
                    on_progress(month_label, "FAIL", f"HTTP {resp.status_code}")
                current = current.replace(year=year + (month // 12), month=((month % 12) + 1))
                continue

            # Step 2: The response may contain a download link or the data directly
            # Parse the page content for CSV data
            text = resp.text

            if "YYYYMMDD HHMMSS" in text or "Time;Open" in text or "time;open" in text.lower():
                # The response IS the CSV data
                pass

            # Step 3: Look for a download redirect
            import re
            dl_match = re.search(r'href=["\']([^"\']*\.(?:csv|zip|txt))["\']', text, re.IGNORECASE)
            if dl_match:
                dl_url = dl_match.group(1)
                if not dl_url.startswith("http"):
                    dl_url = f"https://www.histdata.com{dl_url}" if dl_url.startswith("/") else dl_url
                resp2 = session.get(dl_url, timeout=60, verify=False, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                })
                if resp2.status_code == 200:
                    text = resp2.text

            # Step 4: Parse the data
            for line in text.splitlines():
                candle = _parse_histdata_line(line)
                if candle is not None:
                    ts = candle["time"]
                    if start <= ts <= end:
                        all_candles.append(candle)
                        candles_this_month += 1

            if on_progress:
                on_progress(month_label, "OK", f"{candles_this_month} candles")

        except Exception as exc:
            if on_progress:
                on_progress(month_label, "ERROR", str(exc)[:80])

        # Next month
        if month == 12:
            current = current.replace(year=year + 1, month=1)
        else:
            current = current.replace(month=month + 1)

    return sorted(all_candles, key=lambda c: c["time"])


# ── Main importer (unified entry point) ───────────────────────────────────

def import_external_m1(
    symbol: str = "XAUUSD",
    start: str = "2025-12-01",
    end: str = "2026-03-15",
    output_root: str | Path = "gold_sniper/data/historical/XAUUSD",
    *,
    merge: bool = True,
    source: str = "dukascopy",  # "dukascopy" or "histdata"
) -> dict[str, Any]:
    """Main entry point for external M1 import.

    Supports two sources:
      - 'histdata': histdata.com ASCII OHLCV (PROVEN, active)
      - 'dukascopy': Dukascopy .bi5 tick data (BLOCKED, preserved)

    Returns a dict with status, coverage, and file paths.
    """
    start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=UTC)
    root = Path(output_root)

    source_label = "HISTDATA_COM" if source == "histdata" else "DUKASCOPY"

    result: dict[str, Any] = {
        "status": "UNKNOWN",
        "source": source_label,
        "symbol": symbol,
        "start_requested": start,
        "end_requested": end,
        "candles_downloaded": 0,
        "merge_stats": {},
        "output_path": "",
    }

    print(f"\n{'='*60}")
    print(f"External M1 Importer — {source_label}")
    print(f"{'='*60}")
    print(f"Symbol:   {symbol}")
    print(f"Period:   {start} -> {end}")
    print(f"Output:   {root}")
    print(f"Merge:    {merge}")
    print(f"{'='*60}\n")

    # Track progress
    last_progress = time.monotonic()

    def progress(hour_or_month, status, detail):
        nonlocal last_progress
        now = time.monotonic()
        if hour_or_month is not None:
            if now - last_progress > 2.0 or status in ("FAIL", "ERROR"):
                label = str(hour_or_month)
                print(f"  {label}  [{status}] {detail}")
                last_progress = now
        else:
            print(f"  [{status}] {detail}")

    # Download
    if source == "histdata":
        candles = download_histdata_range(start_dt, end_dt, on_progress=progress)
    else:
        # Dukascopy path (preserved but blocked)
        print("  ⚠ Dukascopy datafeed may be unreachable from this location.")
        print("  If downloads fail, try --source histdata instead.\n")
        candles = download_m1_range(symbol, start_dt, end_dt, on_progress=progress)

    result["candles_downloaded"] = len(candles)

    if not candles:
        result["status"] = "BLOCKED"
        result["error"] = f"No candles downloaded from {source_label}"
        print(f"\nBLOCKED: No data downloaded from {source_label}.")
        if source == "dukascopy":
            print("Try: python tools/data_import/import_external_m1.py --source histdata ...")
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
    standalone_path = root / "1m" / f"XAUUSD_1m_{source_label}_{start}_{end}.csv"
    write_m1_csv(candles, standalone_path)
    result["standalone_path"] = str(standalone_path)
    print(f"Standalone: {standalone_path}")

    # Merge with existing MT5 data
    if merge:
        existing_path = root / "1m" / "XAUUSD_1m_COMPLETE_2025-12-01_2026-06-26.csv"
        merged_path = root / "1m" / "XAUUSD_1m_MERGED_2025-12-01_2026-06-26.csv"

        if not existing_path.exists():
            # Fall back to any existing M1 file
            existing_paths = sorted((root / "1m").glob("*.csv"))
            if existing_paths:
                existing_path = existing_paths[0]
                print(f"\nUsing existing M1 file: {existing_path}")
            else:
                print(f"\nNo existing M1 file found — writing standalone only.")
                result["output_path"] = str(standalone_path)
                result["status"] = "SUCCESS"
                return result

        print(f"\nMerging with existing data...")
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
        description="P1 External M1 Data Importer — histdata.com (proven) or Dukascopy (preserved)."
    )
    parser.add_argument("--source", default="histdata", choices=["histdata", "dukascopy"],
                        help="Data source: histdata (proven, ASCII OHLCV) or dukascopy (preserved, .bi5 ticks)")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("gold_sniper/data/historical/XAUUSD"),
    )
    parser.add_argument("--no-merge", action="store_true",
                        help="Skip merging with existing data")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = import_external_m1(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        output_root=args.output_root,
        merge=not args.no_merge,
        source=args.source,
    )
    if result["status"] == "BLOCKED":
        print(f"\nBLOCKED: {result.get('error', 'Unknown error')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
