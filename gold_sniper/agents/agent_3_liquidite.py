import asyncio
from datetime import datetime
from typing import Sequence

import numpy as np

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger


MIN_SWEEP_DEPTH_ATR = 0.05
EQ_TOLERANCE_ATR = 0.15


def _to_ohlcv(open_: np.ndarray | None, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """Construit une matrice OHLC minimale."""
    if open_ is None:
        open_ = closes.copy()
    return np.column_stack([open_, highs, lows, closes])


def detect_equal_levels(
    swing_highs: list,
    swing_lows: list,
    highs: np.ndarray,
    lows: np.ndarray,
    atr_14: float,
    tolerance_k: float = EQ_TOLERANCE_ATR,
) -> dict:
    """Detecte les clusters EQH/EQL par tolerance ATR."""
    tolerance = tolerance_k * atr_14
    eqh_clusters = []
    eql_clusters = []

    for i, sh1 in enumerate(swing_highs):
        for sh2 in swing_highs[i + 1 :]:
            if abs(sh1["price"] - sh2["price"]) <= tolerance:
                level = max(float(sh1["price"]), float(sh2["price"]))
                eqh_clusters.append(
                    {
                        "level": level,
                        "bsl_zone_top": level + 0.1 * atr_14,
                        "bsl_zone_bottom": level,
                        "strength": abs(sh2["index"] - sh1["index"]),
                        "idx_1": sh1["index"],
                        "idx_2": sh2["index"],
                        "swept": False,
                        "broken": False,
                    }
                )

    for i, sl1 in enumerate(swing_lows):
        for sl2 in swing_lows[i + 1 :]:
            if abs(sl1["price"] - sl2["price"]) <= tolerance:
                level = min(float(sl1["price"]), float(sl2["price"]))
                eql_clusters.append(
                    {
                        "level": level,
                        "ssl_zone_bottom": level - 0.1 * atr_14,
                        "ssl_zone_top": level,
                        "strength": abs(sl2["index"] - sl1["index"]),
                        "idx_1": sl1["index"],
                        "idx_2": sl2["index"],
                        "swept": False,
                        "broken": False,
                    }
                )

    eqh_clusters.sort(key=lambda x: x["strength"], reverse=True)
    eql_clusters.sort(key=lambda x: x["strength"], reverse=True)
    return {"eqh": eqh_clusters[:5], "eql": eql_clusters[:5]}


def detect_eqh_eql(
    highs: Sequence[float],
    lows: Sequence[float],
    swing_highs_idx: list[int],
    swing_lows_idx: list[int],
    atr_14: float,
    tolerance_k: float = EQ_TOLERANCE_ATR,
) -> dict:
    """Interface conforme au rapport Script 05."""
    swing_highs = [{"index": idx, "price": float(highs[idx])} for idx in swing_highs_idx]
    swing_lows = [{"index": idx, "price": float(lows[idx])} for idx in swing_lows_idx]
    return detect_equal_levels(swing_highs, swing_lows, np.array(highs), np.array(lows), atr_14, tolerance_k)


def classify_liquidity_event(
    candle_high: float,
    candle_low: float,
    candle_close: float,
    eqh_level: float | None = None,
    eql_level: float | None = None,
    atr_14: float = 1.0,
) -> dict:
    """Classe l'evenement en SWEEP, BREAK, APPROACH ou NONE."""
    min_sweep_depth = MIN_SWEEP_DEPTH_ATR * max(float(atr_14 or 0.0), 0.0001)
    result = {
        "event": "NONE",
        "side": None,
        "sweep_depth": 0.0,
        "sweep_depth_ratio": 0.0,
        "tradeable": False,
        "direction": None,
    }

    if eqh_level:
        eqh = float(eqh_level)
        if candle_high > eqh and candle_close < eqh:
            sweep_depth = candle_high - eqh
            if sweep_depth >= min_sweep_depth:
                result.update(
                    {
                        "event": "SWEEP",
                        "side": "BSL",
                        "sweep_depth": sweep_depth,
                        "sweep_depth_ratio": sweep_depth / atr_14 if atr_14 > 0 else 0.0,
                        "tradeable": True,
                        "direction": "SHORT",
                        "level": eqh,
                    }
                )
        elif candle_close > eqh:
            result.update({"event": "BREAK", "side": "BSL", "tradeable": False, "direction": "LONG", "level": eqh})
        elif eqh - min_sweep_depth <= candle_high <= eqh:
            result.update({"event": "APPROACH", "side": "BSL", "level": eqh})

    if eql_level:
        eql = float(eql_level)
        if candle_low < eql and candle_close > eql:
            sweep_depth = eql - candle_low
            if sweep_depth >= min_sweep_depth:
                result.update(
                    {
                        "event": "SWEEP",
                        "side": "SSL",
                        "sweep_depth": sweep_depth,
                        "sweep_depth_ratio": sweep_depth / atr_14 if atr_14 > 0 else 0.0,
                        "tradeable": True,
                        "direction": "LONG",
                        "level": eql,
                    }
                )
        elif candle_close < eql:
            result.update({"event": "BREAK", "side": "SSL", "tradeable": False, "direction": "SHORT", "level": eql})
        elif eql <= candle_low <= eql + min_sweep_depth and result["event"] == "NONE":
            result.update({"event": "APPROACH", "side": "SSL", "level": eql})

    return result


def detect_liquidity_event(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    eqh_level: float,
    eql_level: float,
    atr_14: float,
    expected_direction: str,
) -> dict:
    """Cherche le dernier sweep ou break pertinent sur les 10 dernieres bougies."""
    length = len(closes)
    lookback = min(10, length)
    relevant_eqh = eqh_level if expected_direction == "SHORT" else None
    relevant_eql = eql_level if expected_direction == "LONG" else None

    for i in range(length - 1, length - lookback - 1, -1):
        event = classify_liquidity_event(
            float(highs[i]),
            float(lows[i]),
            float(closes[i]),
            eqh_level=relevant_eqh,
            eql_level=relevant_eql,
            atr_14=atr_14,
        )
        if event["event"] in {"SWEEP", "BREAK"}:
            event["candle_index"] = i
            event["age"] = length - 1 - i
            return event

    return {"event": "NONE", "detected": False, "tradeable": False, "direction": expected_direction}


def detect_sweep(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    eqh_level: float,
    eql_level: float,
    atr_14: float,
    direction: str,
) -> dict:
    """Compatibilite: retourne seulement les sweeps, jamais les breaks."""
    event = detect_liquidity_event(highs, lows, closes, eqh_level, eql_level, atr_14, direction)
    if event.get("event") != "SWEEP":
        return {"detected": False, "event": event.get("event", "NONE")}
    return {
        "detected": True,
        "event": "SWEEP",
        "type": f"SWEEP_{event['side']}",
        "level_swept": event.get("level"),
        "sweep_depth": event.get("sweep_depth", 0.0),
        "sweep_depth_ratio": event.get("sweep_depth_ratio", 0.0),
        "candle_index": event.get("candle_index"),
        "age": event.get("age", 0),
        "direction": event.get("direction"),
    }


def check_asian_range(candles_1m: list, atr_14: float) -> dict:
    """Detecte la range asiatique sur les bougies 1M."""
    asian_candles = []
    for candle in candles_1m:
        if "time" not in candle:
            continue
        raw_time = candle["time"]
        if isinstance(raw_time, datetime):
            hour = raw_time.hour
        else:
            hour = datetime.fromtimestamp(raw_time).hour
        if hour >= 22 or hour < 7:
            asian_candles.append(candle)

    if len(asian_candles) < 30:
        return {"valid": False, "reason": "NOT_ENOUGH_ASIAN_CANDLES"}

    asian_high = max(float(c["high"]) for c in asian_candles)
    asian_low = min(float(c["low"]) for c in asian_candles)
    asian_range = asian_high - asian_low
    range_valid = asian_range >= 0.3 * atr_14
    return {
        "valid": range_valid,
        "high": asian_high,
        "low": asian_low,
        "range": asian_range,
        "mid": (asian_high + asian_low) / 2,
        "count": len(asian_candles),
    }


def detect_inducement(
    ohlcv: np.ndarray,
    swing_lows_idx: list[int],
    major_swing_low: float | None,
    direction: str,
    atr_14: float,
    swing_highs_idx: list[int] | None = None,
    major_swing_high: float | None = None,
) -> dict:
    """Detecte l'IDM et si cet inducement a ete sweepe."""
    if len(ohlcv) == 0:
        return {"detected": False, "swept": False}

    if direction == "LONG":
        if major_swing_low is None:
            return {"detected": False, "swept": False}
        candidates = [(idx, float(ohlcv[idx][2])) for idx in swing_lows_idx if float(ohlcv[idx][2]) > major_swing_low]
        if not candidates:
            return {"detected": False, "swept": False}
        idm_idx, idm_level = max(candidates, key=lambda item: item[0])
        post_idm = ohlcv[idm_idx + 1 :]
        swept = any(float(candle[2]) < idm_level and float(candle[3]) > idm_level for candle in post_idm)
        return {"detected": True, "level": idm_level, "swept": swept, "idx": idm_idx, "type": "SSL_IDM"}

    if major_swing_high is None or not swing_highs_idx:
        return {"detected": False, "swept": False}
    candidates = [(idx, float(ohlcv[idx][1])) for idx in swing_highs_idx if float(ohlcv[idx][1]) < major_swing_high]
    if not candidates:
        return {"detected": False, "swept": False}
    idm_idx, idm_level = max(candidates, key=lambda item: item[0])
    post_idm = ohlcv[idm_idx + 1 :]
    swept = any(float(candle[1]) > idm_level and float(candle[3]) < idm_level for candle in post_idm)
    return {"detected": True, "level": idm_level, "swept": swept, "idx": idm_idx, "type": "BSL_IDM"}


def score_agent_3(liquidity_event: dict, asian_range: dict, direction: str, idm: dict | None = None) -> AgentResult:
    """Score Agent 3 en distinguant sweep tradeable et break invalidant."""
    event = liquidity_event.get("event", "NONE")
    idm = idm or {"detected": False, "swept": False}

    if event == "BREAK":
        return AgentResult(
            agent_id="agent_3",
            score=0,
            reason=f"BREAK_{liquidity_event.get('side')} - niveau casse, signal annule",
            direction=liquidity_event.get("direction"),
            hard_filter_pass=False,
            payload={
                "event": "BREAK",
                "side": liquidity_event.get("side"),
                "level": liquidity_event.get("level"),
                "asian_range": asian_range,
                "idm": idm,
            },
        )

    if event != "SWEEP" or not liquidity_event.get("tradeable"):
        return AgentResult(
            agent_id="agent_3",
            score=30,
            reason=f"NO_SWEEP_DETECTED event={event}",
            direction=direction,
            hard_filter_pass=True,
            payload={"event": event, "asian_range": asian_range, "idm": idm},
        )

    confirmed_direction = liquidity_event.get("direction")
    if confirmed_direction != direction:
        return AgentResult(
            agent_id="agent_3",
            score=20,
            reason=f"SWEEP_DIRECTION_CONFLICT {confirmed_direction} vs {direction}",
            direction=confirmed_direction,
            hard_filter_pass=False,
            payload={"event": "SWEEP", "asian_range": asian_range, "idm": idm},
        )

    sweep_depth_ratio = liquidity_event.get("sweep_depth_ratio", 0.0)
    sweep_quality = min(sweep_depth_ratio / 0.3, 1.0) * 55
    sweep_age = liquidity_event.get("age", 99)
    freshness_bonus = 15 if sweep_age <= 3 else (8 if sweep_age <= 6 else 0)
    asian_bonus = 15 if asian_range.get("valid") else 0
    idm_bonus = 15 if idm.get("swept") else 0
    total = min(sweep_quality + freshness_bonus + asian_bonus + idm_bonus, 100)

    return AgentResult(
        agent_id="agent_3",
        score=round(total, 1),
        reason=f"SWEEP_{liquidity_event.get('side')}_CONFIRMED depth={sweep_depth_ratio:.2f} age={sweep_age}",
        direction=confirmed_direction,
        hard_filter_pass=True,
        payload={
            "event": "SWEEP",
            "sweep_type": f"SWEEP_{liquidity_event.get('side')}",
            "level_swept": liquidity_event.get("level"),
            "sweep_depth_ratio": sweep_depth_ratio,
            "sweep_age_candles": sweep_age,
            "asian_range": asian_range,
            "asian_range_valid": asian_range.get("valid", False),
            "idm": idm,
            "idm_detected": idm.get("detected", False),
            "idm_swept": idm.get("swept", False),
        },
    )


class AgentLiquidite:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_3"

    async def run(self):
        """Boucle principale Agent 3."""
        self.logger.info("Agent 3 (Liquidite V2 Sweep vs Break) demarre")
        while not self.bb.kill_event.is_set():
            try:
                try:
                    await asyncio.wait_for(self.bb._events["new_candle_15m"].wait(), timeout=15.0)
                    self.bb._events["new_candle_15m"].clear()
                except asyncio.TimeoutError:
                    pass

                agent2_result = await self.bb.wait_for_agent("agent_2", timeout=2.0)
                agent1_result = self.bb.read_sync("agent_results.agent_1")
                if not agent1_result or not agent2_result or agent2_result.score == 0:
                    result = AgentResult(
                        agent_id="agent_3",
                        score=30,
                        reason="WAITING_ON_AGENT2_FAIL",
                        direction=None,
                        hard_filter_pass=True,
                    )
                    await self.bb.publish_agent_dashboard(
                        "agent_3", result, min_interval_sec=0, trigger_orchestrator=False
                    )
                    await self.bb.update_dict(f"agents.{self.name}", {"equal_highs": [], "equal_lows": []})
                    continue

                direction = agent1_result.direction
                if not direction:
                    await self.bb.publish_agent_dashboard(
                        "agent_3",
                        idle_result("agent_3", reason="WAITING_NO_DIRECTION", score=30),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    continue

                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                candles_1m = list(self.bb.read_sync("market_data.candles.1m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14")
                if len(candles_15m) < 10 or not atr_14:
                    await self.bb.publish_agent_dashboard(
                        "agent_3",
                        idle_result("agent_3", reason="WAITING_INSUFFICIENT_DATA", score=30),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    await asyncio.sleep(2)
                    continue

                high = np.array([c["high"] for c in candles_15m], dtype=float)
                low = np.array([c["low"] for c in candles_15m], dtype=float)
                open_ = np.array([c["open"] for c in candles_15m], dtype=float)
                close = np.array([c["close"] for c in candles_15m], dtype=float)

                from agents.agent_1_meteo import detect_swings

                loop = asyncio.get_running_loop()
                swings = await loop.run_in_executor(
                    None,
                    lambda: detect_swings(high, low, close, n=3, atr_14=atr_14),
                )
                eq_levels = detect_equal_levels(swings["swing_highs"], swings["swing_lows"], high, low, atr_14)
                eqh_level = eq_levels["eqh"][0]["level"] if eq_levels["eqh"] else 0.0
                eql_level = eq_levels["eql"][0]["level"] if eq_levels["eql"] else 0.0

                event = detect_liquidity_event(high, low, close, eqh_level, eql_level, atr_14, direction)
                asian_range = check_asian_range(candles_1m, atr_14)

                ohlcv = _to_ohlcv(open_, high, low, close)
                swing_lows_idx = [item["index"] for item in swings["swing_lows"]]
                swing_highs_idx = [item["index"] for item in swings["swing_highs"]]
                major_low = min((item["price"] for item in swings["swing_lows"]), default=None)
                major_high = max((item["price"] for item in swings["swing_highs"]), default=None)
                idm = detect_inducement(ohlcv, swing_lows_idx, major_low, direction, atr_14, swing_highs_idx, major_high)

                result = score_agent_3(event, asian_range, direction, idm)
                payload = result.payload or {}

                await self.bb.update_agent(
                    self.name,
                    {
                        "score": result.score,
                        "direction": result.direction,
                        "eqh_levels": eq_levels["eqh"],
                        "eql_levels": eq_levels["eql"],
                        "sweep_detected": payload.get("event") == "SWEEP",
                        "break_detected": payload.get("event") == "BREAK",
                        "sweep_side": payload.get("sweep_type"),
                        "sweep_depth_ratio": payload.get("sweep_depth_ratio", 0.0),
                        "asian_range": asian_range,
                        "idm_detected": payload.get("idm_detected", False),
                        "idm_swept": payload.get("idm_swept", False),
                        "reason": result.reason,
                        "hard_filter_pass": result.hard_filter_pass,
                    },
                )
                await self.bb.publish_agent_dashboard(
                    "agent_3", result, min_interval_sec=0
                )

            except Exception as exc:
                self.logger.error(f"Erreur dans Agent 3 (Liquidite V2): {exc}")
                from config import AGENT_DASHBOARD_PULSE_SEC

                await self.bb.publish_agent_dashboard(
                    "agent_3",
                    idle_result(
                        "agent_3",
                        reason=f"ERROR: {exc}",
                        score=30,
                        hard_filter_pass=False,
                    ),
                    min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                    trigger_orchestrator=False,
                )
                await asyncio.sleep(5)


AgentLiquidity = AgentLiquidite
