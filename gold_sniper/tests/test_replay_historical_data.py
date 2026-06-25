from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from replay.historical_data import load_csv_candles


class TestReplayHistoricalData(unittest.TestCase):
    def test_csv_loader_sorts_normalizes_and_filters_candles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "xau.csv"
            path.write_text(
                "time,open,high,low,close,tick_volume\n"
                "2024-01-01T00:02:00Z,3,4,2,3.5,30\n"
                "2024-01-01T00:00:00Z,1,2,0.5,1.5,10\n"
                "2024-01-01T00:01:00Z,2,3,1,2.5,20\n",
                encoding="utf-8",
            )

            candles = load_csv_candles(
                path,
                "1m",
                start="2024-01-01T00:01:00Z",
                end="2024-01-01T00:02:00Z",
            )

        self.assertEqual([candle["close"] for candle in candles], [2.5, 3.5])
        self.assertEqual(candles[0]["tick_volume"], 20.0)
        self.assertEqual(candles[0]["time"].tzinfo.utcoffset(candles[0]["time"]).total_seconds(), 0)

    def test_csv_loader_rejects_missing_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("time,open,high,low,close\n2024-01-01T00:00:00Z,1,2,0,1\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_csv_candles(path, "1m")


if __name__ == "__main__":
    unittest.main()
