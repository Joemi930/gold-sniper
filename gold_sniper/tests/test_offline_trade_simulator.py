from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from gold_sniper.replay.offline_market_structure import Candle
from gold_sniper.replay.offline_trade_simulator import simulate_enter_outcomes


class TestOfflineTradeSimulator(unittest.TestCase):
    def test_no_enter_returns_not_available(self) -> None:
        result = simulate_enter_outcomes([], [], [])
        self.assertEqual(result["simulation_status"], "NOT_AVAILABLE_NO_ENTER_SHADOW")

    def test_enter_without_sl_tp_is_unknown(self) -> None:
        result = simulate_enter_outcomes([{"decision": "ENTER", "timestamp": "2026-04-01T01:00:00Z", "close": 100}], [], [])
        self.assertEqual(result["unknown_outcome"], 1)

    def test_long_2r_win_can_be_simulated(self) -> None:
        base = datetime(2026, 4, 1, 1, tzinfo=timezone.utc)
        candles = [Candle(base + timedelta(minutes=1), "t", 100, 105, 99, 104)]
        result = simulate_enter_outcomes(
            [{"decision": "ENTER", "timestamp": "2026-04-01T01:00:00Z", "close": 100, "poi_low": 98, "poi_high": 101, "direction": "LONG"}],
            candles,
            [],
        )
        self.assertEqual(result["wins"], 1)


if __name__ == "__main__":
    unittest.main()
