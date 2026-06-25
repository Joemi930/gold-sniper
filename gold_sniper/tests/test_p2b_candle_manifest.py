"""P2-B Candle Manifest tests — coverage detection, gaps, checksums."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from gold_sniper.data_pipeline.candle_manifest import (
    CandleCoverageManifest,
    TimeframeCoverage,
    build_candle_coverage_manifest,
)


class TestP2bCandleManifest(unittest.TestCase):
    def _write_csv(self, folder: Path, name: str, rows: list[dict], tf: str = "1m") -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        fieldnames = ["time", "open", "high", "low", "close", "tick_volume"]
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in rows:
                w.writerow(row)
        return path

    def test_manifest_coverage_ok(self):
        """All required timeframes cover the full window → COVERAGE_OK."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tf in ("1m", "15m", "4H"):
                tf_dir = root / tf
                tf_dir.mkdir()
                self._write_csv(tf_dir, f"XAUUSD_{tf}_2026-06-01_2026-06-03.csv", [
                    {"time": "2026-06-01T00:00:00Z", "open": "2400.0", "high": "2401.0", "low": "2399.0", "close": "2400.5", "tick_volume": "100"},
                ], tf)
            manifest = build_candle_coverage_manifest(
                data_root=root, symbol="XAUUSD",
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-01T00:00:00Z",
            )
            self.assertEqual(manifest.overall_status, "COVERAGE_OK")
            self.assertIn("1m", manifest.timeframes)
            self.assertEqual(manifest.timeframes["1m"].coverage_status, "COVERAGE_OK")

    def test_manifest_partial_when_window_incomplete(self):
        """End time before requested → PARTIAL."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tf in ("1m", "15m", "4H"):
                tf_dir = root / tf
                tf_dir.mkdir()
                self._write_csv(tf_dir, f"XAUUSD_{tf}_2026-06-01_2026-06-02.csv", [
                    {"time": "2026-06-01T00:00:00Z", "open": "2400.0", "high": "2401.0", "low": "2399.0", "close": "2400.5", "tick_volume": "100"},
                    {"time": "2026-06-02T23:59:00Z", "open": "2410.0", "high": "2411.0", "low": "2409.0", "close": "2410.5", "tick_volume": "100"},
                ], tf)
            manifest = build_candle_coverage_manifest(
                data_root=root, symbol="XAUUSD",
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-05T23:59:59Z",
            )
            self.assertEqual(manifest.overall_status, "PARTIAL")

    def test_manifest_missing_required_timeframe(self):
        """Missing a required timeframe → MISSING."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tf in ("15m",):  # no 1m source, so missing required cannot be derived
                tf_dir = root / tf
                tf_dir.mkdir()
                self._write_csv(tf_dir, f"XAUUSD_{tf}_2026-06-01_2026-06-03.csv", [
                    {"time": "2026-06-01T00:00:00Z", "open": "2400.0", "high": "2401.0", "low": "2399.0", "close": "2400.5", "tick_volume": "100"},
                ], tf)
            manifest = build_candle_coverage_manifest(
                data_root=root, symbol="XAUUSD",
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-01T00:00:00Z",
            )
            self.assertEqual(manifest.overall_status, "MISSING")
            self.assertIn("1m", manifest.missing_timeframes)
            self.assertIn("5m", manifest.missing_timeframes)

    def test_manifest_gaps_counted(self):
        """Gaps are reported in quality report."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tf_dir = root / "1m"
            tf_dir.mkdir()
            self._write_csv(tf_dir, "XAUUSD_1m_2026-06-01_2026-06-03.csv", [
                {"time": "2026-06-01T00:00:00Z", "open": "2400.0", "high": "2401.0", "low": "2399.0", "close": "2400.5", "tick_volume": "100"},
                {"time": "2026-06-01T00:03:00Z", "open": "2400.5", "high": "2401.5", "low": "2400.0", "close": "2401.0", "tick_volume": "100"},
            ], "1m")
            for tf in ("15m", "4H"):
                tf_dir2 = root / tf
                tf_dir2.mkdir()
                self._write_csv(tf_dir2, f"XAUUSD_{tf}_2026-06-01_2026-06-03.csv", [
                    {"time": "2026-06-01T00:00:00Z", "open": "2400.0", "high": "2401.0", "low": "2399.0", "close": "2400.5", "tick_volume": "100"},
                ], tf)
            manifest = build_candle_coverage_manifest(
                data_root=root, symbol="XAUUSD",
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-01T00:03:00Z",
            )
            self.assertGreater(manifest.timeframes["1m"].gaps, 0)

    def test_manifest_checksum_present(self):
        """Checksum is present when data exists."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tf in ("1m", "15m", "4H"):
                tf_dir = root / tf
                tf_dir.mkdir()
                self._write_csv(tf_dir, f"XAUUSD_{tf}_2026-06-01_2026-06-03.csv", [
                    {"time": "2026-06-01T00:00:00Z", "open": "2400.0", "high": "2401.0", "low": "2399.0", "close": "2400.5", "tick_volume": "100"},
                ], tf)
            manifest = build_candle_coverage_manifest(
                data_root=root, symbol="XAUUSD",
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-03T23:59:59Z",
            )
            self.assertIsNotNone(manifest.timeframes["1m"].checksum)
            self.assertTrue(len(manifest.timeframes["1m"].checksum) > 0)

    def test_manifest_to_dict(self):
        """Manifest serializes cleanly."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tf in ("1m", "15m", "4H"):
                tf_dir = root / tf
                tf_dir.mkdir()
                self._write_csv(tf_dir, f"XAUUSD_{tf}_2026-06-01_2026-06-03.csv", [
                    {"time": "2026-06-01T00:00:00Z", "open": "2400.0", "high": "2401.0", "low": "2399.0", "close": "2400.5", "tick_volume": "100"},
                ], tf)
            manifest = build_candle_coverage_manifest(
                data_root=root, symbol="XAUUSD",
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-01T00:00:00Z",
            )
            d = manifest.to_dict()
            self.assertIn("timeframes", d)
            self.assertIn("1m", d["timeframes"])
            self.assertEqual(d["overall_status"], "COVERAGE_OK")

    def test_manifest_optional_timeframes_checked(self):
        """Optional timeframes are checked but don't affect overall status."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tf in ("1m", "15m", "4H"):
                tf_dir = root / tf
                tf_dir.mkdir()
                self._write_csv(tf_dir, f"XAUUSD_{tf}_2026-06-01_2026-06-03.csv", [
                    {"time": "2026-06-01T00:00:00Z", "open": "2400.0", "high": "2401.0", "low": "2399.0", "close": "2400.5", "tick_volume": "100"},
                ], tf)
            manifest = build_candle_coverage_manifest(
                data_root=root, symbol="XAUUSD",
                requested_start_utc="2026-06-01T00:00:00Z",
                requested_end_utc="2026-06-01T00:00:00Z",
            )
            # 5m and 1H are now required, but derived from 1m; 30m remains optional.
            self.assertEqual(manifest.overall_status, "COVERAGE_OK")
            self.assertIn("5m", manifest.timeframes)


if __name__ == "__main__":
    unittest.main()
