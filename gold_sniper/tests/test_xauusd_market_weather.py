from __future__ import annotations

import unittest

from gold_sniper.strategy.xauusd_market_weather import evaluate_xauusd_market_weather


def _candles(closes: list[float]) -> list[dict]:
    return [
        {"open": close - 0.3, "high": close + 0.8, "low": close - 0.8, "close": close, "tick_volume": 100}
        for close in closes
    ]


class TestXauusdMarketWeather(unittest.TestCase):
    def test_ema200_m15_bullish(self) -> None:
        result = evaluate_xauusd_market_weather(_candles([1900 + i for i in range(220)]))
        self.assertEqual(result.ema_200_m15_bias, "BULLISH")

    def test_ema200_m15_bearish(self) -> None:
        result = evaluate_xauusd_market_weather(_candles([2200 - i for i in range(220)]))
        self.assertEqual(result.ema_200_m15_bias, "BEARISH")

    def test_ema200_m15_chop_waits(self) -> None:
        closes = [2000 + (4 if i % 2 else -4) for i in range(220)]
        result = evaluate_xauusd_market_weather(_candles(closes))
        self.assertEqual(result.ema_200_m15_bias, "CHOP")
        self.assertEqual(result.trade_permission, "WAIT")


if __name__ == "__main__":
    unittest.main()
