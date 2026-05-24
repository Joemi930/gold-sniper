from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable

import MetaTrader5 as mt5

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agents.agent_7_chronos import score_agent_7
from agents.base_agent import AgentResult
from config import MT5_PATH, SYMBOL
from core.blackboard import BLACKBOARD, BlackBoard
from core.orchestrator import run_orchestrator
from utils.logger import get_logger


BACKTEST_MONTHS = 6
BACKTEST_DAYS = BACKTEST_MONTHS * 30
CACHE_MAX_AGE = timedelta(hours=24)
BACKTEST_DIR = Path("logs/backtests")
M1_CACHE = BACKTEST_DIR / f"{SYMBOL}_M1_cache.parquet"
BACKTEST_RESULTS = BACKTEST_DIR / "backtest_results.jsonl"
MIN_REPLAY_BARS = 100
MT5_DOWNLOAD_CHUNK_DAYS = 30


class ParquetUnavailable(RuntimeError):
    pass


def _require_pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ParquetUnavailable(
            "pyarrow est requis pour le cache parquet. Installe-le avec: python -m pip install pyarrow"
        ) from exc
    return pa, pq


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _to_utc(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _bar_from_rate(rate) -> dict:
    return {
        "time": _to_utc(rate["time"]),
        "open": float(rate["open"]),
        "high": float(rate["high"]),
        "low": float(rate["low"]),
        "close": float(rate["close"]),
        "tick_volume": int(rate["tick_volume"]),
        "spread": int(rate["spread"]),
        "real_volume": int(rate["real_volume"]),
    }


def _ensure_mt5() -> bool:
    init_kwargs = {}
    if MT5_PATH:
        init_kwargs["path"] = MT5_PATH
    if not mt5.initialize(**init_kwargs):
        return False
    info = mt5.account_info()
    if info is None:
        return False
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        return False
    if not symbol_info.visible:
        mt5.symbol_select(SYMBOL, True)
    return True


def _download_m1_range(start: datetime, end: datetime) -> list[dict]:
    if not _ensure_mt5():
        raise RuntimeError(
            "MT5 non connecté. Ouvre MetaTrader 5, connecte le compte, puis relance le backtest."
        )

    bars = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=MT5_DOWNLOAD_CHUNK_DAYS), end)
        rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, cursor, chunk_end)
        if rates is not None and len(rates) > 0:
            bars.extend(_bar_from_rate(rate) for rate in rates)
        cursor = chunk_end + timedelta(minutes=1)

    if not bars:
        raise RuntimeError(
            f"Aucune bougie {SYMBOL} M1 reçue depuis MT5 pour {start} -> {end} | last_error={mt5.last_error()}"
        )
    return _dedupe_sort(bars)


def _latest_available_m1_time() -> datetime:
    if not _ensure_mt5():
        raise RuntimeError(
            "MT5 non connecté. Ouvre MetaTrader 5, connecte le compte, puis relance le backtest."
        )
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 1)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Impossible de lire la dernière bougie M1 {SYMBOL} | last_error={mt5.last_error()}")
    return _to_utc(rates[0]["time"])


def _dedupe_sort(bars: Iterable[dict]) -> list[dict]:
    indexed = {_to_utc(bar["time"]): {**bar, "time": _to_utc(bar["time"])} for bar in bars}
    return [indexed[key] for key in sorted(indexed)]


def _load_parquet(path: Path) -> list[dict]:
    _, pq = _require_pyarrow()
    table = pq.read_table(path)
    raw = table.to_pydict()
    bars = []
    for idx in range(table.num_rows):
        bars.append(
            {
                "time": _to_utc(raw["time"][idx]),
                "open": float(raw["open"][idx]),
                "high": float(raw["high"][idx]),
                "low": float(raw["low"][idx]),
                "close": float(raw["close"][idx]),
                "tick_volume": int(raw.get("tick_volume", [0] * table.num_rows)[idx] or 0),
                "spread": int(raw.get("spread", [0] * table.num_rows)[idx] or 0),
                "real_volume": int(raw.get("real_volume", [0] * table.num_rows)[idx] or 0),
            }
        )
    return _dedupe_sort(bars)


def _save_parquet(path: Path, bars: list[dict]) -> None:
    pa, pq = _require_pyarrow()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "time": [bar["time"] for bar in bars],
        "open": [bar["open"] for bar in bars],
        "high": [bar["high"] for bar in bars],
        "low": [bar["low"] for bar in bars],
        "close": [bar["close"] for bar in bars],
        "tick_volume": [bar.get("tick_volume", 0) for bar in bars],
        "spread": [bar.get("spread", 0) for bar in bars],
        "real_volume": [bar.get("real_volume", 0) for bar in bars],
    }
    table = pa.Table.from_pydict(payload)
    pq.write_table(table, path)


def _cache_is_recent(path: Path, now: datetime) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return now - modified < CACHE_MAX_AGE


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = alpha * value + (1 - alpha) * ema
    return float(ema)


def _atr(bars: list[dict], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    true_ranges = []
    for idx in range(1, len(bars)):
        high = bars[idx]["high"]
        low = bars[idx]["low"]
        prev_close = bars[idx - 1]["close"]
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    window = true_ranges[-period:]
    return mean(window) if window else 0.0


def _aggregate_window(bars: list[dict]) -> dict:
    return {
        "time": bars[-1]["time"],
        "open": bars[0]["open"],
        "high": max(bar["high"] for bar in bars),
        "low": min(bar["low"] for bar in bars),
        "close": bars[-1]["close"],
        "tick_volume": sum(bar.get("tick_volume", 0) for bar in bars),
        "spread": bars[-1].get("spread", 0),
        "real_volume": sum(bar.get("real_volume", 0) for bar in bars),
    }


class BacktestEngine:
    def __init__(
        self,
        blackboard: BlackBoard = BLACKBOARD,
        cache_path: Path = M1_CACHE,
        results_path: Path = BACKTEST_RESULTS,
    ):
        self.blackboard = blackboard
        self.cache_path = cache_path
        self.results_path = results_path
        self.logger = get_logger()
        self.download_performed = False
        self.cache_loaded = False
        self.cache_created = False

    def load_historical_data(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        if self.cache_path.exists() and _cache_is_recent(self.cache_path, now):
            self.cache_loaded = True
            return _load_parquet(self.cache_path)

        latest_available = _latest_available_m1_time()
        download_end = min(now, latest_available)
        start = download_end - timedelta(days=BACKTEST_DAYS)

        if self.cache_path.exists():
            cached = _load_parquet(self.cache_path)
            self.cache_loaded = True
            if _cache_is_recent(self.cache_path, now):
                return cached

            last_time = cached[-1]["time"] if cached else start
            missing_start = last_time + timedelta(minutes=1)
            if missing_start < download_end:
                missing = _download_m1_range(missing_start, download_end)
                self.download_performed = True
                merged = _dedupe_sort([*cached, *missing])
                _save_parquet(self.cache_path, merged)
                return merged
            return cached

        bars = _download_m1_range(start, download_end)
        self.download_performed = True
        self.cache_created = True
        bars = _dedupe_sort(bars)
        _save_parquet(self.cache_path, bars)
        return bars

    async def run(self, limit_bars: int | None = None, bars: list[dict] | None = None) -> dict:
        BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
        self.results_path.write_text("", encoding="utf-8")

        if bars is None:
            bars = self.load_historical_data()
        bars = _dedupe_sort(bars)

        if len(bars) < MIN_REPLAY_BARS:
            raise RuntimeError(f"Historique insuffisant: {len(bars)} bougies, minimum {MIN_REPLAY_BARS}")

        if limit_bars:
            bars = bars[: max(MIN_REPLAY_BARS, limit_bars)]

        await self._prepare_blackboard()

        m1_window: deque[dict] = deque(maxlen=300)
        candles_15m: list[dict] = []
        candles_4h: list[dict] = []
        decisions = []
        trades = []

        for idx, bar in enumerate(bars):
            m1_window.append(bar)
            await self._feed_bar(bar, m1_window, candles_15m, candles_4h)

            if len(m1_window) < 60:
                continue

            agent_results = self._simulate_agents(list(m1_window), candles_15m, candles_4h, bar)
            for result in agent_results:
                await self.blackboard.write_agent_result(result.agent_id, result)

            decision = await run_orchestrator(agent_results)
            decision_entry = {
                "ts": bar["time"].isoformat(),
                "bar_index": idx,
                "decision": decision.get("decision"),
                "score": decision.get("score"),
                "raw_score": decision.get("raw_score"),
                "direction": decision.get("direction"),
                "regime": decision.get("regime"),
                "session": decision.get("session"),
                "reason": decision.get("reason"),
                "agents": {
                    result.agent_id: {
                        "score": result.score,
                        "hard_filter_pass": result.hard_filter_pass,
                        "direction": result.direction,
                        "reason": result.reason,
                    }
                    for result in agent_results
                },
            }

            if decision.get("decision") == "EXECUTE":
                trade = self._simulate_trade(bars, idx, decision)
                trades.append(trade)
                decision_entry["trade"] = trade

            decisions.append(decision_entry)
            self._append_result(decision_entry)

        summary = self._summary(decisions, trades, len(bars))
        self._print_summary(summary)
        return summary

    async def _prepare_blackboard(self) -> None:
        self.blackboard.reset_pipeline()
        await self.blackboard.update_agent("agent_6", {"veto": False, "blocked": False, "reason": ""})
        await self.blackboard.update_agent("risk_manager", {"veto": False, "trades_today": 0, "reason": ""})
        await self.blackboard.update_market({"regime": "UNKNOWN", "session": "LONDON"})
        await self.blackboard.update_dict("orchestrator", {"last_signal_time": None})

    async def _feed_bar(
        self,
        bar: dict,
        m1_window: deque[dict],
        candles_15m: list[dict],
        candles_4h: list[dict],
    ) -> None:
        spread_points = float(bar.get("spread", 0) or 0)
        await self.blackboard.update_dict(
            "market_data.current_tick",
            {
                "bid": bar["close"],
                "ask": bar["close"],
                "spread_points": spread_points,
                "time": bar["time"],
                "volume": bar.get("tick_volume", 0),
            },
        )
        await self.blackboard.update_market(
            {
                "current_price": bar["close"],
                "bid": bar["close"],
                "ask": bar["close"],
                "spread_points": spread_points,
                "atr_14_1m": _atr(list(m1_window), 14),
            }
        )
        await self.blackboard.write("market_data.atr_14", _atr(list(m1_window), 14))

        self.blackboard.read_sync("market_data.candles.1m").append(bar)
        if len(m1_window) >= 15 and len(m1_window) % 15 == 0:
            candle_15m = _aggregate_window(list(m1_window)[-15:])
            candles_15m.append(candle_15m)
            self.blackboard.read_sync("market_data.candles.15m").append(candle_15m)
        if len(m1_window) >= 240 and len(m1_window) % 240 == 0:
            candle_4h = _aggregate_window(list(m1_window)[-240:])
            candles_4h.append(candle_4h)
            self.blackboard.read_sync("market_data.candles.4H").append(candle_4h)

    def _simulate_agents(
        self,
        m1: list[dict],
        candles_15m: list[dict],
        candles_4h: list[dict],
        bar: dict,
    ) -> list[AgentResult]:
        closes = [b["close"] for b in m1]
        highs = [b["high"] for b in m1]
        lows = [b["low"] for b in m1]
        atr_1m = max(_atr(m1, 14), 0.01)
        ema_fast = _ema(closes[-40:], 12)
        ema_slow = _ema(closes[-80:], 26)
        direction = "LONG" if ema_fast >= ema_slow else "SHORT"
        trend_strength = min(abs(ema_fast - ema_slow) / atr_1m * 20, 20)

        range_high = max(highs[-60:])
        range_low = min(lows[-60:])
        range_size = max(range_high - range_low, 0.01)
        position = (bar["close"] - range_low) / range_size

        a1_score = min(84 + trend_strength, 96)
        a2_score = 88 if 0.25 <= position <= 0.75 else 72
        sweep_long = lows[-1] <= min(lows[-10:]) and bar["close"] > lows[-1] + 0.25 * atr_1m
        sweep_short = highs[-1] >= max(highs[-10:]) and bar["close"] < highs[-1] - 0.25 * atr_1m
        a3_score = 88 if (direction == "LONG" and sweep_long) or (direction == "SHORT" and sweep_short) else 78
        fib_ok = (direction == "LONG" and position <= 0.65) or (direction == "SHORT" and position >= 0.35)
        a4_score = 88 if fib_ok else 68
        body = abs(bar["close"] - bar["open"])
        a5_score = 90 if body >= 0.35 * atr_1m else 82

        session_result = score_agent_7(bar["time"], {"poc": None, "vah": None, "val": None}, bar["close"])
        return [
            AgentResult("agent_1", a1_score, True, direction, f"BT_EMA_TREND_{direction}"),
            AgentResult("agent_2", a2_score, True, direction, f"BT_POI_POSITION={position:.2f}"),
            AgentResult("agent_3", a3_score, True, direction, "BT_SWEEP_SCORE"),
            AgentResult("agent_4", a4_score, True, direction, "BT_FIB_ZONE"),
            AgentResult("agent_5", a5_score, True, direction, "BT_TRIGGER_BODY"),
            AgentResult("agent_6", 100, True, None, "BT_NO_NEWS"),
            session_result,
        ]

    def _simulate_trade(self, bars: list[dict], idx: int, decision: dict) -> dict:
        entry = bars[idx]["close"]
        direction = decision.get("direction") or "LONG"
        atr_value = max(_atr(bars[max(0, idx - 60): idx + 1], 14), 0.1)
        risk = atr_value
        reward = atr_value * 1.5
        sl = entry - risk if direction == "LONG" else entry + risk
        tp = entry + reward if direction == "LONG" else entry - reward
        outcome = {"won": False, "exit": "TIMEOUT"}

        for future in bars[idx + 1: idx + 31]:
            if direction == "LONG":
                if future["low"] <= sl:
                    outcome = {"won": False, "exit": "SL"}
                    break
                if future["high"] >= tp:
                    outcome = {"won": True, "exit": "TP"}
                    break
            else:
                if future["high"] >= sl:
                    outcome = {"won": False, "exit": "SL"}
                    break
                if future["low"] <= tp:
                    outcome = {"won": True, "exit": "TP"}
                    break

        return {
            "entry": round(entry, 3),
            "sl": round(sl, 3),
            "tp": round(tp, 3),
            "direction": direction,
            "score": decision.get("score"),
            **outcome,
        }

    def _append_result(self, entry: dict) -> None:
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.results_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=_json_default) + "\n")

    def _summary(self, decisions: list[dict], trades: list[dict], replayed_bars: int) -> dict:
        execute_count = len(trades)
        wins = sum(1 for trade in trades if trade.get("won"))
        avg_score = mean([trade["score"] for trade in trades]) if trades else 0.0
        return {
            "symbol": SYMBOL,
            "replayed_bars": replayed_bars,
            "execute_signals": execute_count,
            "wins": wins,
            "losses": execute_count - wins,
            "simulated_winrate_pct": round(wins / execute_count * 100, 2) if execute_count else 0.0,
            "avg_execute_score": round(avg_score, 2),
            "results_path": str(self.results_path),
            "cache_path": str(self.cache_path),
            "cache_loaded": self.cache_loaded,
            "cache_created": self.cache_created,
            "download_performed": self.download_performed,
        }

    def _print_summary(self, summary: dict) -> None:
        print("\nBACKTEST SUMMARY")
        print(f"Symbol: {summary['symbol']}")
        print(f"Bougies rejouees: {summary['replayed_bars']}")
        print(f"Signaux EXECUTE: {summary['execute_signals']}")
        print(f"Winrate simule: {summary['simulated_winrate_pct']}%")
        print(f"Score moyen trades: {summary['avg_execute_score']}")
        print(f"Cache charge: {summary['cache_loaded']} | Download MT5: {summary['download_performed']}")
        print(f"Cache: {summary['cache_path']}")
        print(f"Resultats: {summary['results_path']}")


def build_synthetic_bars(count: int = 180) -> list[dict]:
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    bars = []
    price = 2000.0
    for idx in range(count):
        wave = math.sin(idx / 9) * 0.8
        drift = idx * 0.015
        open_price = price
        close = 2000.0 + drift + wave
        high = max(open_price, close) + 0.35
        low = min(open_price, close) - 0.35
        bars.append(
            {
                "time": start + timedelta(minutes=idx),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": 100 + idx,
                "spread": 20,
                "real_volume": 0,
            }
        )
        price = close
    return bars


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Gold Sniper Script 18 Backtesting Engine")
    parser.add_argument("--limit", type=int, default=None, help="Limite le nombre de bougies rejouees")
    parser.add_argument("--synthetic", action="store_true", help="Utilise des bougies synthetiques pour validation locale")
    args = parser.parse_args()

    engine = BacktestEngine()
    bars = build_synthetic_bars(max(args.limit or MIN_REPLAY_BARS, MIN_REPLAY_BARS)) if args.synthetic else None
    await engine.run(limit_bars=args.limit, bars=bars)


if __name__ == "__main__":
    asyncio.run(_main())
