"""CLI: build candle coverage manifest for Gold Sniper replay data."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
from gold_sniper.data_pipeline.candle_manifest import (
    build_candle_coverage_manifest,
    save_candle_coverage_manifest,
)
from gold_sniper.data_pipeline.timeframe_aggregation import derive_timeframe_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build candle coverage manifest for Gold Sniper replay data.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--derive-missing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.derive_missing:
        _derive_missing_timeframes(args.data_root, args.symbol, args.start, args.end)
    manifest = build_candle_coverage_manifest(
        data_root=args.data_root,
        symbol=args.symbol,
        requested_start_utc=args.start,
        requested_end_utc=args.end,
    )
    save_candle_coverage_manifest(manifest, args.output)
    print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0 if manifest.overall_status == "COVERAGE_OK" else 2


def _derive_missing_timeframes(data_root: Path, symbol: str, start: str, end: str) -> None:
    input_1m = _find_latest(data_root / "1m", f"{symbol}_1m_*.csv")
    if input_1m is None:
        return
    for timeframe in ("5m", "30m", "1H"):
        folder = data_root / timeframe
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"{symbol}_{timeframe}_{start[:10]}_{end[:10]}.csv"
        if output.exists():
            continue
        derive_timeframe_csv(
            input_1m_csv=input_1m,
            output_csv=output,
            timeframe=timeframe,
            start=start,
            end=end,
        )


def _find_latest(folder: Path, pattern: str) -> Path | None:
    if not folder.exists():
        return None
    matches = sorted(folder.glob(pattern))
    return matches[-1] if matches else None


if __name__ == "__main__":
    raise SystemExit(main())
