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
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agents.agent_1_meteo import classify_market_structure, detect_swings, score_agent_1
from agents.agent_2_cartographe import detect_fvg, detect_order_blocks, score_agent_2
from agents.agent_3_liquidite import (
    check_asian_range,
    detect_equal_levels,
    detect_inducement,
    detect_liquidity_event,
    score_agent_3,
)
from agents.agent_4_fibonacci import calculate_ote_levels, score_fibonacci_ote
from agents.agent_5_microscope import analyze_amd_sequence
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


def _ohlc_arrays(candles: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.array([c["open"] for c in candles], dtype=float),
        np.array([c["high"] for c in candles], dtype=float),
        np.array([c["low"] for c in candles], dtype=float),
        np.array([c["close"] for c in candles], dtype=float),
    )


def _to_agent_result(agent_id: str, reason: str, hard_filter_pass: bool = False, direction: str | None = None, score: float = 0.0) -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        score=score,
        hard_filter_pass=hard_filter_pass,
        direction=direction,
        reason=reason,
        payload={"backtest_real_agent": True},
    )


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

        m1_window: deque[dict] = deque(maxlen=3000)
        candles_15m: list[dict] = []
        candles_4h: list[dict] = []
        decisions = []
        trades = []

        for idx, bar in enumerate(bars):
            m1_window.append(bar)
            await self._feed_bar(bar, idx, m1_window, candles_15m, candles_4h)

            if len(m1_window) < 60:
                continue

            agent_results = await self._run_real_agents(list(m1_window), candles_15m, candles_4h, bar)
            for result in agent_results:
                await self.blackboard.write_agent_result(result.agent_id, result)

            decision = await run_orchestrator(agent_results, self.blackboard)
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
        async with self.blackboard._lock:
            for candle_deque in self.blackboard._data.get("market_data", {}).get("candles", {}).values():
                candle_deque.clear()
            self.blackboard._data["trade_signals"] = {}
        await self.blackboard.update_agent("agent_6", {"veto": False, "blocked": False, "reason": ""})
        await self.blackboard.update_agent("risk_manager", {"veto": False, "trades_today": 0, "reason": ""})
        await self.blackboard.update_market({"regime": "UNKNOWN", "session": "LONDON"})
        await self.blackboard.update_dict("orchestrator", {"last_signal_time": None})

    async def _feed_bar(
        self,
        bar: dict,
        bar_index: int,
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
        replay_count = bar_index + 1
        if len(m1_window) >= 15 and replay_count % 15 == 0:
            candle_15m = _aggregate_window(list(m1_window)[-15:])
            candles_15m.append(candle_15m)
            self.blackboard.read_sync("market_data.candles.15m").append(candle_15m)
        if len(m1_window) >= 240 and replay_count % 240 == 0:
            candle_4h = _aggregate_window(list(m1_window)[-240:])
            candles_4h.append(candle_4h)
            self.blackboard.read_sync("market_data.candles.4H").append(candle_4h)

    async def _run_real_agents(
        self,
        m1: list[dict],
        candles_15m: list[dict],
        candles_4h: list[dict],
        bar: dict,
    ) -> list[AgentResult]:
        atr_1m = max(_atr(m1, 14), 0.01)
        atr_15m = max(_atr(candles_15m[-50:], 14), atr_1m)
        session_result = score_agent_7(bar["time"], {"poc": None, "vah": None, "val": None}, bar["close"])

        if len(candles_15m) < 10 or len(candles_4h) < 10:
            return [
                _to_agent_result("agent_1", "BT_REAL_AGENT1_INSUFFICIENT_HTF_DATA"),
                _to_agent_result("agent_2", "BT_REAL_AGENT2_WAITING_AGENT1"),
                _to_agent_result("agent_3", "BT_REAL_AGENT3_WAITING_AGENT2", hard_filter_pass=True, score=30),
                _to_agent_result("agent_4", "BT_REAL_AGENT4_WAITING_AGENT2"),
                _to_agent_result("agent_5", "BT_REAL_AGENT5_WAITING_POI"),
                AgentResult("agent_6", 100, True, None, "BT_NO_NEWS", payload={"backtest_real_agent": True}),
                session_result,
            ]

        open_4h, high_4h, low_4h, close_4h = _ohlc_arrays(candles_4h)
        open_15m, high_15m, low_15m, close_15m = _ohlc_arrays(candles_15m)

        swings_4h = detect_swings(high_4h, low_4h, close_4h, n=5, atr_14=atr_15m)
        swings_15m = detect_swings(high_15m, low_15m, close_15m, n=3, atr_14=atr_15m)
        structure_4h = classify_market_structure(swings_4h, close_4h)
        structure_15m = classify_market_structure(swings_15m, close_15m)
        a1 = score_agent_1(structure_4h, structure_15m)
        await self.blackboard.update_agent("agent_1", {
            "score": a1.score,
            "direction": a1.direction,
            "reason": a1.reason,
            "hard_filter_pass": a1.hard_filter_pass,
        })
        await self.blackboard.write_agent_result("agent_1", a1)

        direction = a1.direction
        if not direction or a1.score == 0:
            return [
                a1,
                _to_agent_result("agent_2", "BT_REAL_AGENT2_WAITING_VALID_AGENT1"),
                _to_agent_result("agent_3", "BT_REAL_AGENT3_WAITING_AGENT2", hard_filter_pass=True, score=30),
                _to_agent_result("agent_4", "BT_REAL_AGENT4_WAITING_AGENT2"),
                _to_agent_result("agent_5", "BT_REAL_AGENT5_WAITING_POI"),
                AgentResult("agent_6", 100, True, None, "BT_NO_NEWS", payload={"backtest_real_agent": True}),
                session_result,
            ]

        liquidity_pools = dict(self.blackboard.get_all().get("market_analysis", {}).get("liquidity_pools", {}) or {})
        htf_bias = (a1.payload or {}).get("structure_4h") or direction
        fvgs = detect_fvg(high_15m, low_15m, atr_15m, direction)
        obs = detect_order_blocks(
            high_15m,
            low_15m,
            open_15m,
            close_15m,
            swings_15m["swing_highs"],
            swings_15m["swing_lows"],
            atr_15m,
            direction,
            htf_bias=htf_bias,
            liquidity_pools=liquidity_pools,
        )
        a2 = score_agent_2(obs[0] if obs else None, fvgs[0] if fvgs else None, bar["close"], atr_15m, self.blackboard)
        payload2 = a2.payload or {}
        await self.blackboard.update_agent("agent_2", {
            "score": a2.score,
            "direction": a2.direction,
            "active_ob": payload2.get("active_ob"),
            "active_fvg": payload2.get("active_fvg"),
            "poi_zone": payload2.get("poi_zone"),
            "ob_score": payload2.get("ob_score", 0),
            "zone_is_fresh": payload2.get("zone_is_fresh", False),
            "reason": a2.reason,
            "hard_filter_pass": a2.hard_filter_pass,
        })
        await self.blackboard.write_agent_result("agent_2", a2)
        await self.blackboard.update_dict("market_analysis.zones", {"order_blocks": obs, "fvgs": fvgs})

        eq_levels = detect_equal_levels(swings_15m["swing_highs"], swings_15m["swing_lows"], high_15m, low_15m, atr_15m)
        await self.blackboard.update_dict("market_analysis.liquidity_pools", eq_levels)
        eqh_level = eq_levels["eqh"][0]["level"] if eq_levels["eqh"] else 0.0
        eql_level = eq_levels["eql"][0]["level"] if eq_levels["eql"] else 0.0
        liquidity_event = detect_liquidity_event(high_15m, low_15m, close_15m, eqh_level, eql_level, atr_15m, direction)
        asian_range = check_asian_range(m1, atr_1m)
        ohlcv_15m = np.column_stack([open_15m, high_15m, low_15m, close_15m])
        swing_lows_idx = [item["index"] for item in swings_15m["swing_lows"]]
        swing_highs_idx = [item["index"] for item in swings_15m["swing_highs"]]
        major_low = min((item["price"] for item in swings_15m["swing_lows"]), default=None)
        major_high = max((item["price"] for item in swings_15m["swing_highs"]), default=None)
        idm = detect_inducement(ohlcv_15m, swing_lows_idx, major_low, direction, atr_15m, swing_highs_idx, major_high)
        a3 = score_agent_3(liquidity_event, asian_range, direction, idm)
        await self.blackboard.update_agent("agent_3", {
            "score": a3.score,
            "direction": a3.direction,
            "eqh_levels": eq_levels["eqh"],
            "eql_levels": eq_levels["eql"],
            "reason": a3.reason,
            "hard_filter_pass": a3.hard_filter_pass,
        })
        await self.blackboard.write_agent_result("agent_3", a3)

        if swings_15m["swing_highs"] and swings_15m["swing_lows"]:
            last_high = swings_15m["swing_highs"][-1]["price"]
            last_low = swings_15m["swing_lows"][-1]["price"]
            if last_high <= last_low:
                last_high, last_low = float(max(high_15m)), float(min(low_15m))
            fib_levels = calculate_ote_levels(last_low, last_high, direction)
            a4 = score_fibonacci_ote(bar["close"], fib_levels, direction, self.blackboard.get_market().get("dxy_bias", "NEUTRAL"))
        else:
            a4 = _to_agent_result("agent_4", "BT_REAL_AGENT4_NO_SWINGS", direction=direction)
        payload4 = a4.payload or {}
        await self.blackboard.update_agent("agent_4", {
            "score": a4.score,
            "direction": a4.direction,
            "swing_used": {"low_price": payload4.get("levels", {}).get("swing_low"), "high_price": payload4.get("levels", {}).get("swing_high")},
            "in_ote": payload4.get("in_ote", False),
            "price_in_ote": payload4.get("price_in_ote", False),
            "reason": a4.reason,
            "hard_filter_pass": a4.hard_filter_pass,
        })
        await self.blackboard.write_agent_result("agent_4", a4)

        a5 = analyze_amd_sequence(
            m1[-120:],
            direction=direction,
            poi_zone=payload2.get("poi_zone"),
            atr_1m=atr_1m,
            in_ote=bool(payload4.get("in_ote", False)),
            a4_data=self.blackboard.get_agent("agent_4"),
        )
        payload5 = a5.payload or {}
        await self.blackboard.update_agent("agent_5", {
            "score": a5.score,
            "direction": a5.direction,
            "choch_detected": payload5.get("choch_detected", False),
            "sweep_1m_confirmed": payload5.get("sweep_1m_confirmed", False),
            "amd_phase": payload5.get("amd_phase", 0),
            "entry_price": payload5.get("entry"),
            "sl_price": payload5.get("sl"),
            "tp1_price": payload5.get("tp1"),
            "tp2_price": payload5.get("tp2"),
            "reason": a5.reason,
            "hard_filter_pass": a5.hard_filter_pass,
        })
        await self.blackboard.write_agent_result("agent_5", a5)

        return [
            a1,
            a2,
            a3,
            a4,
            a5,
            AgentResult("agent_6", 100, True, None, "BT_NO_NEWS", payload={"backtest_real_agent": True}),
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
