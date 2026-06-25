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
fake.order_send = lambda request: ORDER_SEND_CALLS.append(request)
fake.positions_get = lambda **kwargs: []
fake.symbol_info_tick = lambda symbol: types.SimpleNamespace(bid=1990.0, ask=1991.0)
sys.modules["MetaTrader5"] = fake

import config
from core.blackboard import BlackBoard
from core.recovery_manager import _close_gap_position


class TestRecoveryGuard(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ORDER_SEND_CALLS.clear()
        self._run_mode = config.RUN_MODE
        self._allow = config.ALLOW_BROKER_WRITES
        config.RUN_MODE = "REPLAY"
        config.ALLOW_BROKER_WRITES = False

    def tearDown(self) -> None:
        config.RUN_MODE = self._run_mode
        config.ALLOW_BROKER_WRITES = self._allow

    async def test_recovery_close_blocks_broker_write_outside_live_authorized(self) -> None:
        position = types.SimpleNamespace(
            ticket=123,
            symbol="XAUUSD",
            volume=0.1,
            type=fake.POSITION_TYPE_BUY,
        )

        result = await _close_gap_position(position, 1990.0, BlackBoard())

        self.assertFalse(result.allowed)
        self.assertEqual(ORDER_SEND_CALLS, [])


if __name__ == "__main__":
    unittest.main()
