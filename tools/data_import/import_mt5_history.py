"""P3 — MT5 Historical Data Import (Read-Only).

Imports XAUUSD historical candles from MetaTrader5 terminal.
Writes CSV + Parquet + manifest + gaps report.

Allowed MT5 APIs (read-only):
  - initialize, shutdown, last_error, copy_rates_range

Forbidden MT5 APIs:
  - order_send, order_check, positions_get, orders_get, trading writes

Required timeframes: M1 (source of truth), M5, M15, M30, H1, H4, D1

Usage:
  python tools/data_import/import_mt5_history.py \
    --start 2026-01-01 --end 2026-06-30 \
    --output-root gold_sniper/data/historical/XAUUSD
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TIMEFRAMES: dict[str, str] = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1H": "TIMEFRAME_H1",
    "4H": "TIMEFRAME_H4",
    "1D": "TIMEFRAME_D1",
}

EXPECTED_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
}

CSV_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_rates(rates: list[dict[str, Any]], timeframe: str) -> dict[str, Any]:
    """Validate monotonic time, no duplicates, expected intervals, detect gaps."""
    expected_sec = EXPECTED_INTERVAL_SECONDS.get(timeframe, 60)
    gaps: list[dict[str, Any]] = []
    duplicates = 0
    time_order_issues = 0
    prev_ts: datetime | None = None

    for i, row in enumerate(rates):
        ts = parse_utc(row["time"])
        if prev_ts is not None:
            diff_sec = (ts - prev_ts).total_seconds()
            if diff_sec <= 0:
                time_order_issues += 1
            elif diff_sec > expected_sec * 2:
                gaps.append({
                    "index": i,
                    "prev_time": prev_ts.isoformat(),
                    "time": ts.isoformat(),
                    "diff_seconds": diff_sec,
                    "missing_bars_est": int(diff_sec / expected_sec) - 1,
                })
        prev_ts = ts

    # Check duplicate times
    times = [row["time"] for row in rates]
    duplicates = len(times) - len(set(times))

    return {
        "timeframe": timeframe,
        "bars_count": len(rates),
        "duplicates": duplicates,
        "time_order_issues": time_order_issues,
        "gaps_count": len(gaps),
        "gaps": gaps[:50],  # limit to first 50
        "coverage_start_utc": rates[0]["time"] if rates else None,
        "coverage_end_utc": rates[-1]["time"] if rates else None,
        "expected_interval_sec": expected_sec,
        "valid": duplicates == 0 and time_order_issues == 0,
    }


def build_output_path(
    output_root: str | Path, symbol: str, timeframe: str, start: datetime, end: datetime
) -> dict[str, Path]:
    root = Path(output_root)
    tf_dir = root / timeframe
    tf_dir.mkdir(parents=True, exist_ok=True)
    base = f"{symbol}_{timeframe}_{start:%Y-%m-%d}_{end:%Y-%m-%d}"
    return {
        "csv": tf_dir / f"{base}.csv",
        "parquet": tf_dir / f"{base}.parquet",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write Parquet file if pandas/pyarrow available, else skip."""
    try:
        import pandas as pd
        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"])
        df.to_parquet(path, index=False)
        return True
    except ImportError:
        return False


def write_manifest(
    output_root: Path,
    symbol: str,
    start: datetime,
    end: datetime,
    broker: str,
    results: dict[str, dict[str, Any]],
    coverage_by_tf: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    manifest = {
        "symbol": symbol,
        "source": "MetaTrader5 read-only import",
        "broker": broker,
        "imported_at_utc": datetime.now(timezone.utc).isoformat(),
        "coverage": {
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(),
        },
        "timeframes": {},
        "total_bars": sum(r.get("bars_count", 0) for r in coverage_by_tf.values()),
        "total_gaps": sum(r.get("gaps_count", 0) for r in coverage_by_tf.values()),
    }
    for tf, info in coverage_by_tf.items():
        manifest["timeframes"][tf] = {
            "bars_count": info["bars_count"],
            "duplicates": info["duplicates"],
            "gaps_count": info["gaps_count"],
            "coverage_start_utc": info["coverage_start_utc"],
            "coverage_end_utc": info["coverage_end_utc"],
            "valid": info["valid"],
            "files": {
                "csv": str(results.get(tf, {}).get("csv_path", "")),
                "parquet": str(results.get(tf, {}).get("parquet_path", "")),
            },
        }

    manifest_path = output_root / "manifest.json"
    with manifest_path.open("w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def write_gaps_report(
    output_root: Path, coverage_by_tf: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    gaps_report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_gaps": sum(r.get("gaps_count", 0) for r in coverage_by_tf.values()),
        "by_timeframe": {
            tf: {
                "gaps_count": info["gaps_count"],
                "gaps": info["gaps"],
            }
            for tf, info in coverage_by_tf.items()
        },
    }
    gaps_path = output_root / "gaps_report.json"
    with gaps_path.open("w") as f:
        json.dump(gaps_report, f, indent=2)
    return gaps_report


def import_history(
    *,
    symbol: str = "XAUUSD",
    mt5_symbol: str = "XAUUSD",
    start: str | datetime,
    end: str | datetime,
    output_root: str | Path,
    terminal_path: str | None = None,
    chunk_days: int = 14,
    mt5_module: Any = None,
    broker_name: str = "MetaTrader5",
) -> dict[str, Any]:
    """Main import routine — read-only, no order_send, no trading writes."""
    mt5 = mt5_module or _import_mt5()
    start_dt = parse_utc(start)
    end_dt = parse_utc(end)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    initialized = False
    try:
        initialized = bool(
            mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
        )
        if not initialized:
            error = mt5.last_error()
            return {"status": "BLOCKED", "error": f"MT5 initialize failed: {error}"}

        if not mt5.symbol_select(mt5_symbol, True):
            error = mt5.last_error()
            return {"status": "BLOCKED", "error": f"MT5 symbol_select failed for {mt5_symbol}: {error}"}

        results: dict[str, dict[str, Any]] = {}
        coverage_by_tf: dict[str, dict[str, Any]] = {}

        for timeframe, mt5_attr_name in TIMEFRAMES.items():
            mt5_timeframe = getattr(mt5, mt5_attr_name)
            rates = _copy_rates_range_chunked(
                mt5, mt5_symbol, mt5_timeframe, start_dt, end_dt, chunk_days=max(1, int(chunk_days))
            )
            rows = _rates_to_rows(rates)
            validation = validate_rates(rows, timeframe)
            coverage_by_tf[timeframe] = validation

            paths = build_output_path(output_root, symbol, timeframe, start_dt, end_dt)
            write_csv(paths["csv"], rows)
            parquet_ok = write_parquet(paths["parquet"], rows)

            results[timeframe] = {
                "csv_path": str(paths["csv"]),
                "parquet_path": str(paths["parquet"]) if parquet_ok else None,
                "rows": len(rows),
                "start_utc": rows[0]["time"] if rows else None,
                "end_utc": rows[-1]["time"] if rows else None,
                "validation": validation,
            }

        manifest = write_manifest(output_root, symbol, start_dt, end_dt, broker_name, results, coverage_by_tf)
        gaps_report = write_gaps_report(output_root, coverage_by_tf)

        return {
            "status": "SUCCESS",
            "symbol": symbol,
            "output_root": str(output_root),
            "timeframes_imported": len(results),
            "total_bars": manifest["total_bars"],
            "total_gaps": manifest["total_gaps"],
            "manifest_path": str(output_root / "manifest.json"),
            "gaps_report_path": str(output_root / "gaps_report.json"),
            "results": results,
        }
    finally:
        if initialized:
            mt5.shutdown()


def _copy_rates_range_chunked(
    mt5: Any, symbol: str, timeframe: Any, start: datetime, end: datetime, *, chunk_days: int
) -> list[Any]:
    """Chunked copy_rates_range to handle large date ranges."""
    output_by_time: dict[int, Any] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        rates = mt5.copy_rates_range(symbol, timeframe, cursor, chunk_end)
        if rates is None:
            raise RuntimeError(f"MT5 copy_rates_range failed: {mt5.last_error()}")
        for rate in rates:
            output_by_time[int(_rate_get(rate, "time"))] = rate
        cursor = chunk_end + timedelta(seconds=1)
    return [output_by_time[key] for key in sorted(output_by_time)]


def _rates_to_rows(rates: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rate in rates:
        ts = datetime.fromtimestamp(int(_rate_get(rate, "time")), tz=timezone.utc)
        rows.append({
            "time": ts.isoformat().replace("+00:00", "Z"),
            "open": float(_rate_get(rate, "open")),
            "high": float(_rate_get(rate, "high")),
            "low": float(_rate_get(rate, "low")),
            "close": float(_rate_get(rate, "close")),
            "tick_volume": int(_rate_get(rate, "tick_volume", 0) or 0),
            "spread": int(_rate_get(rate, "spread", 0) or 0),
            "real_volume": int(_rate_get(rate, "real_volume", 0) or 0),
        })
    return rows


def _rate_get(rate: Any, key: str, default: Any = None) -> Any:
    if hasattr(rate, "get"):
        return rate.get(key, default)
    names = getattr(getattr(rate, "dtype", None), "names", None)
    if names and key in names:
        return rate[key]
    try:
        return rate[key]
    except Exception:
        return default


def _import_mt5() -> Any:
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed") from exc
    return mt5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P3 Read-Only MT5 Historical Data Import for Gold Sniper."
    )
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--mt5-symbol", default=None)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--terminal-path", default=None)
    parser.add_argument("--chunk-days", type=int, default=14)
    parser.add_argument("--broker-name", default="MetaTrader5")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = import_history(
        symbol=args.symbol,
        mt5_symbol=args.mt5_symbol or args.symbol,
        start=args.start,
        end=args.end,
        output_root=args.output_root,
        terminal_path=args.terminal_path,
        chunk_days=args.chunk_days,
        broker_name=args.broker_name,
    )
    if result["status"] == "BLOCKED":
        print(f"BLOCKED: {result['error']}")
        return 1
    print(f"Imported {result['total_bars']} bars across {result['timeframes_imported']} timeframes")
    print(f"Manifest: {result['manifest_path']}")
    print(f"Gaps: {result['gaps_report_path']}")
    for tf, info in result.get("results", {}).items():
        print(f"  {tf}: {info['rows']} rows → {info['csv_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
