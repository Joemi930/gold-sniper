from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "export_mt5_historical_candles.py"


class FakeMT5:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240

    def __init__(self) -> None:
        self.shutdown_called = False

    def initialize(self, path=None) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def symbol_select(self, symbol, enable) -> bool:
        return symbol == "XAUUSDm" and enable is True

    def copy_rates_range(self, symbol, timeframe, start, end):
        return [
            {
                "time": int(start.timestamp()),
                "open": 2400.0,
                "high": 2401.0,
                "low": 2399.0,
                "close": 2400.5,
                "tick_volume": 100,
                "spread": 20,
                "real_volume": 0,
            }
        ]

    def last_error(self):
        return (0, "OK")


class TestP2bMt5ExportSchema(unittest.TestCase):
    def test_export_writes_expected_csv_schema_without_real_mt5(self) -> None:
        module = _load_tool()
        fake = FakeMT5()
        with tempfile.TemporaryDirectory() as tmp:
            result = module.export_all_timeframes(
                symbol="XAUUSD",
                mt5_symbol="XAUUSDm",
                start="2026-01-01T00:00:00Z",
                end="2026-06-18T23:59:59Z",
                output_root=tmp,
                mt5_module=fake,
            )
            sample = Path(result["1m"]["path"])
            text = sample.read_text(encoding="utf-8").splitlines()

        self.assertTrue(fake.shutdown_called)
        self.assertEqual(set(result), {"1m", "5m", "15m", "1H", "4H"})
        self.assertEqual(text[0], "time,open,high,low,close,tick_volume,spread,real_volume")
        self.assertIn("2026-01-01T00:00:00Z,2400.0,2401.0,2399.0,2400.5,100,20,0", text[1])


def _load_tool():
    spec = importlib.util.spec_from_file_location("export_mt5_historical_candles", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load export tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
