from __future__ import annotations

import importlib
import os
import sys
import unittest

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")


class _BlockMT5:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "MetaTrader5":
            raise ImportError("MetaTrader5 blocked for no-MT5 backtest test")
        return None


class TestBacktestNoMT5(unittest.TestCase):
    def test_backtest_module_imports_without_mt5_and_does_not_initialize(self) -> None:
        blocker = _BlockMT5()
        previous_mt5 = sys.modules.pop("MetaTrader5", None)
        previous_runtime = sys.modules.pop("execution.mt5_runtime", None)
        sys.modules.pop("backtesting.backtest_engine", None)
        sys.meta_path.insert(0, blocker)
        try:
            module = importlib.import_module("backtesting.backtest_engine")
            self.assertIsNone(module.mt5)
            self.assertFalse(module._ensure_mt5())
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.pop("backtesting.backtest_engine", None)
            sys.modules.pop("execution.mt5_runtime", None)
            if previous_mt5 is not None:
                sys.modules["MetaTrader5"] = previous_mt5
            if previous_runtime is not None:
                sys.modules["execution.mt5_runtime"] = previous_runtime


if __name__ == "__main__":
    unittest.main()
