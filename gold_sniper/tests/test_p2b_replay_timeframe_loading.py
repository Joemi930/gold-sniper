from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from core.blackboard import BlackBoard
from gold_sniper.replay.replay_engine import ReplayEngine
from gold_sniper.replay.run_replay import _load_replay_timeframes


class TestP2bReplayTimeframeLoading(unittest.TestCase):
    def test_loader_derives_5m_and_1h_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_csv(root, "1m", [
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:01:00Z",
                "2026-06-01T00:05:00Z",
            ])
            _write_csv(root, "15m", ["2026-06-01T00:00:00Z"])
            _write_csv(root, "4H", ["2026-06-01T00:00:00Z"])

            loaded, sources, derived, missing = _load_replay_timeframes(
                root,
                symbol="XAUUSD",
                start="2026-06-01T00:00:00Z",
                end="2026-06-01T00:05:00Z",
            )

        self.assertEqual(len(loaded["1m"]), 3)
        self.assertIn("5m", derived)
        self.assertIn("1H", derived)
        self.assertEqual(sources["5m"], "DERIVED_FROM_1M")
        self.assertEqual(missing, [])

    def test_engine_injects_external_timeframes(self) -> None:
        board = BlackBoard()
        m1 = [_candle("2026-06-01T00:00:00Z")]
        external = {
            "5m": [_candle("2026-06-01T00:00:00Z")],
            "15m": [_candle("2026-06-01T00:00:00Z")],
            "1H": [_candle("2026-06-01T00:00:00Z")],
            "4H": [_candle("2026-06-01T00:00:00Z")],
        }
        engine = ReplayEngine(
            board,
            m1,
            output_root=Path(tempfile.gettempdir()) / "p2b_replay_timeframe_test",
            run_id="unit",
            candles_by_timeframe=external,
        )

        asyncio.run(engine._prepare_blackboard())
        asyncio.run(engine._inject_candle(m1[0], 0))

        for timeframe in ("5m", "15m", "1H", "4H"):
            self.assertEqual(len(board.read_sync(f"market_data.candles.{timeframe}")), 1)


def _write_csv(root: Path, timeframe: str, times: list[str]) -> None:
    folder = root / timeframe
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["time,open,high,low,close,tick_volume"]
    for index, ts in enumerate(times):
        base = 2400 + index
        lines.append(f"{ts},{base},{base + 1},{base - 1},{base + 0.5},100")
    (folder / f"XAUUSD_{timeframe}_test.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _candle(ts: str) -> dict:
    return {
        "time": ts,
        "open": 2400.0,
        "high": 2401.0,
        "low": 2399.0,
        "close": 2400.5,
        "volume": 100.0,
        "tick_volume": 100.0,
    }


if __name__ == "__main__":
    unittest.main()
