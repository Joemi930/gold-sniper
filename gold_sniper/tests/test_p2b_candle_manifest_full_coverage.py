from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from gold_sniper.data_pipeline.candle_manifest import build_candle_coverage_manifest


class TestP2bCandleManifestFullCoverage(unittest.TestCase):
    def test_manifest_derives_missing_5m_and_1h_from_1m(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tf(root, "1m", ["2026-06-01T00:00:00Z"])
            _write_tf(root, "15m", ["2026-06-01T00:00:00Z"])
            _write_tf(root, "4H", ["2026-06-01T00:00:00Z"])

            manifest = build_candle_coverage_manifest(
                data_root=root,
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-01T00:00:00Z",
            )

        self.assertEqual(manifest.overall_status, "COVERAGE_OK")
        self.assertEqual(manifest.timeframes["5m"].source, "DERIVED_FROM_1M")
        self.assertEqual(manifest.timeframes["1H"].source, "DERIVED_FROM_1M")
        self.assertIn("5m", manifest.generated_timeframes)
        self.assertIn("1H", manifest.generated_timeframes)
        self.assertTrue(manifest.timeframes["5m"].checksum)

    def test_manifest_missing_required_when_1m_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tf(root, "15m", ["2026-06-01T00:00:00Z"])
            _write_tf(root, "4H", ["2026-06-01T00:00:00Z"])

            manifest = build_candle_coverage_manifest(
                data_root=root,
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-01T00:00:00Z",
            )

        self.assertEqual(manifest.overall_status, "MISSING")
        self.assertIn("1m", manifest.missing_timeframes)
        self.assertIn("5m", manifest.missing_timeframes)
        self.assertIn("1H", manifest.missing_timeframes)

    def test_unexpected_gap_blocks_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tf(root, "1m", ["2026-06-01T12:00:00Z", "2026-06-01T12:03:00Z"])

            manifest = build_candle_coverage_manifest(
                data_root=root,
                requested_start_utc="2026-06-01T12:00:00Z",
                requested_end_utc="2026-06-01T12:03:00Z",
                required_timeframes=("1m",),
                optional_timeframes=(),
            )

        self.assertEqual(manifest.overall_status, "PARTIAL")
        self.assertEqual(manifest.timeframes["1m"].unexpected_gap_count, 1)

    def test_weekend_gap_does_not_block_by_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_tf(root, "1m", ["2026-06-05T21:00:00Z", "2026-06-08T01:00:00Z"])

            manifest = build_candle_coverage_manifest(
                data_root=root,
                requested_start_utc="2026-06-05T21:00:00Z",
                requested_end_utc="2026-06-08T01:00:00Z",
                required_timeframes=("1m",),
                optional_timeframes=(),
            )

        self.assertEqual(manifest.overall_status, "COVERAGE_OK")
        self.assertEqual(manifest.timeframes["1m"].weekend_gap_count, 1)
        self.assertEqual(manifest.timeframes["1m"].unexpected_gap_count, 0)


def _write_tf(root: Path, timeframe: str, times: list[str]) -> None:
    folder = root / timeframe
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"XAUUSD_{timeframe}_test.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "open", "high", "low", "close", "tick_volume"])
        writer.writeheader()
        for index, ts in enumerate(times):
            base = 2400 + index
            writer.writerow({
                "time": ts,
                "open": base,
                "high": base + 1,
                "low": base - 1,
                "close": base + 0.5,
                "tick_volume": 100,
            })


if __name__ == "__main__":
    unittest.main()
