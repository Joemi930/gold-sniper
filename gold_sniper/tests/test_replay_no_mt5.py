from __future__ import annotations

from datetime import datetime, timezone
import importlib
import sys
import tempfile
import unittest
from pathlib import Path


class _BlockMT5:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "MetaTrader5":
            raise ImportError("MetaTrader5 blocked for replay no-MT5 test")
        return None


class TestReplayNoMT5(unittest.IsolatedAsyncioTestCase):
    async def test_replay_runs_without_metatrader5_importable(self) -> None:
        blocker = _BlockMT5()
        previous_mt5 = sys.modules.pop("MetaTrader5", None)
        for name in list(sys.modules):
            if name.startswith("replay."):
                sys.modules.pop(name, None)
        sys.meta_path.insert(0, blocker)
        try:
            module = importlib.import_module("replay.replay_engine")
            from core.blackboard import BlackBoard

            candle = {
                "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1.0,
                "tick_volume": 1.0,
            }
            with tempfile.TemporaryDirectory() as tmp:
                summary = await module.ReplayEngine(BlackBoard(), [candle], output_root=tmp, run_id="no_mt5").run()
            self.assertEqual(summary["candles"], 1)
        finally:
            sys.meta_path.remove(blocker)
            for name in list(sys.modules):
                if name.startswith("replay."):
                    sys.modules.pop(name, None)
            if previous_mt5 is not None:
                sys.modules["MetaTrader5"] = previous_mt5

    def test_order_send_stays_in_broker_gateway_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        needle = "mt5." + "order_send"
        matches = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*.py")
            if needle in path.read_text(encoding="utf-8", errors="ignore")
        ]
        self.assertEqual(matches, ["execution/broker_gateway.py"])


if __name__ == "__main__":
    unittest.main()
