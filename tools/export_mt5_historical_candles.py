"""Export MT5 historical candles to offline CSV files.

This tool is intentionally isolated under tools/. It must not be imported by
the replay, strategy, validation, or agent runtime.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TIMEFRAMES = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "1H": "TIMEFRAME_H1",
    "4H": "TIMEFRAME_H4",
}


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_output_path(output_root: str | Path, logical_symbol: str, timeframe: str, start: datetime, end: datetime) -> Path:
    root = Path(output_root)
    return root / timeframe / f"{logical_symbol}_{timeframe}_{start:%Y-%m-%d}_{end:%Y-%m-%d}.csv"


def export_all_timeframes(
    *,
    symbol: str,
    mt5_symbol: str,
    start: str | datetime,
    end: str | datetime,
    output_root: str | Path,
    terminal_path: str | None = None,
    chunk_days: int = 14,
    mt5_module: Any | None = None,
) -> dict[str, dict[str, Any]]:
    mt5 = mt5_module or _import_mt5()
    start_dt = parse_utc(start)
    end_dt = parse_utc(end)
    initialized = False
    try:
        initialized = bool(mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize())
        if not initialized:
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(mt5_symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed for {mt5_symbol}: {mt5.last_error()}")

        results: dict[str, dict[str, Any]] = {}
        for timeframe, mt5_attr in TIMEFRAMES.items():
            mt5_timeframe = getattr(mt5, mt5_attr)
            rates = _copy_rates_range_chunked(
                mt5,
                mt5_symbol,
                mt5_timeframe,
                start_dt,
                end_dt,
                chunk_days=max(1, int(chunk_days)),
            )
            output_path = build_output_path(output_root, symbol, timeframe, start_dt, end_dt)
            rows = _rates_to_rows(rates)
            write_csv(output_path, rows)
            results[timeframe] = {
                "path": str(output_path),
                "rows": len(rows),
                "start_utc": rows[0]["time"] if rows else None,
                "end_utc": rows[-1]["time"] if rows else None,
            }
        return results
    finally:
        if initialized:
            mt5.shutdown()


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _copy_rates_range_chunked(
    mt5: Any,
    symbol: str,
    timeframe: Any,
    start: datetime,
    end: datetime,
    *,
    chunk_days: int,
) -> list[Any]:
    output_by_time: dict[int, Any] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        rates = mt5.copy_rates_range(symbol, timeframe, cursor, chunk_end)
        if rates is None:
            raise RuntimeError(f"MT5 copy_rates_range failed for {symbol}: {mt5.last_error()}")
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
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed in this Python environment") from exc
    return mt5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export offline MT5 candles to Gold Sniper CSV files.")
    parser.add_argument("--symbol", default="XAUUSD", help="Logical symbol used in output filenames.")
    parser.add_argument("--mt5-symbol", default=None, help="Broker symbol in MT5, e.g. XAUUSDm.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--terminal-path", default=None, help="Optional terminal64.exe path.")
    parser.add_argument("--chunk-days", type=int, default=14, help="Days per MT5 copy_rates_range request.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = export_all_timeframes(
        symbol=args.symbol,
        mt5_symbol=args.mt5_symbol or args.symbol,
        start=args.start,
        end=args.end,
        output_root=args.output_root,
        terminal_path=args.terminal_path,
        chunk_days=args.chunk_days,
    )
    for timeframe, result in results.items():
        print(f"{timeframe}: {result['rows']} rows -> {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
