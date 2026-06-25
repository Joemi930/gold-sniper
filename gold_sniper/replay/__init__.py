"""Offline replay tools for historical candles and simulated trades."""
import sys
from pathlib import Path as _Path

# Ensure the gold_sniper package root is on sys.path so that
# intra-package imports (from replay.xxx) resolve correctly.
_gold_sniper_root = str(_Path(__file__).resolve().parent.parent)
if _gold_sniper_root not in sys.path:
    sys.path.insert(0, _gold_sniper_root)

from replay.historical_data import load_csv_candles
from replay.replay_engine import ReplayEngine
from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager

__all__ = [
    "ReplayEngine",
    "SimulatedTradeConfig",
    "SimulatedTradeManager",
    "load_csv_candles",
]
