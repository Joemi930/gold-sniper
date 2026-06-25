from __future__ import annotations

import os
import sys
import types
import unittest

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

ORDER_SEND_CALLS: list[dict] = []

fake = types.SimpleNamespace()
fake.ORDER_TYPE_BUY = 0
fake.ORDER_TYPE_SELL = 1
fake.POSITION_TYPE_BUY = 0
fake.TRADE_ACTION_DEAL = 1
fake.ORDER_TIME_GTC = 0
fake.ORDER_FILLING_IOC = 0
fake.TRADE_RETCODE_DONE = 10009
fake.symbol_info_tick = lambda symbol: types.SimpleNamespace(bid=1990.0, ask=1991.0)
fake.order_send = lambda request: ORDER_SEND_CALLS.append(request)
sys.modules["MetaTrader5"] = fake

import config
from core.blackboard import BlackBoard
from utils.emergency_shutdown import _close_open_positions


class TestEmergencyGuard(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ORDER_SEND_CALLS.clear()
        self._run_mode = config.RUN_MODE
        self._allow = config.ALLOW_BROKER_WRITES
        config.RUN_MODE = "PAPER"
        config.ALLOW_BROKER_WRITES = False

        position = types.SimpleNamespace(
            ticket=456,
            magic=240115,
            symbol="XAUUSD",
            volume=0.1,
            type=fake.POSITION_TYPE_BUY,
        )
        fake.positions_get = lambda **kwargs: [position]

    def tearDown(self) -> None:
        config.RUN_MODE = self._run_mode
        config.ALLOW_BROKER_WRITES = self._allow

    async def test_emergency_close_blocks_broker_write_outside_live_authorized(self) -> None:
        board = BlackBoard()
        board._data["active_trades"] = {
            "456": {
                "ticket": 456,
                "type": "BUY",
                "entry_price": 2000.0,
            }
        }
        board._data["positions"]["open_positions"] = [
            {"ticket": 456, "symbol": "XAUUSD"}
        ]

        summary = await _close_open_positions(board)

        self.assertEqual(summary["seen"], 1)
        self.assertEqual(summary["closed"], 0)
        self.assertEqual(ORDER_SEND_CALLS, [])
        self.assertIn("456", board.read_sync("active_trades"))
        self.assertEqual(
            board.read_sync("positions.open_positions"),
            [{"ticket": 456, "symbol": "XAUUSD"}],
        )


if __name__ == "__main__":
    unittest.main()
