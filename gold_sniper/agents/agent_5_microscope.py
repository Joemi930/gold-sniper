import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

from agents.base_agent import AgentResult
from config import AGENT_DASHBOARD_PULSE_SEC
from core.blackboard import BLACKBOARD, BlackBoard
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger


AMD_ACCUMULATION_WINDOW = 10
AMD_SWEEP_ATR_MIN = 0.05
AMD_CHOCH_BODY_MIN = 0.5
AMD_MAX_CHOCH_DELAY = 5


class AMDPhase(Enum):
    IDLE = "IDLE"
    ACCUMULATION_DETECTED = "ACCUMULATION"
    MANIPULATION_DETECTED = "MANIPULATION"
    DISTRIBUTION_CONFIRMED = "DISTRIBUTION"


@dataclass
class AMDState:
    phase: AMDPhase = AMDPhase.IDLE
    accumulation_high: float = 0.0
    accumulation_low: float = 0.0
    accumulation_start_index: int = 0
    sweep_index: int = -1
    sweep_price: float = 0.0
    choch_index: int = -1
    last_swing_high_1m: float = 0.0
    last_swing_low_1m: float = 0.0


def _candle_value(candle, key: str, index: int) -> float:
    """Lit une valeur OHLC depuis un dict ou une sequence."""
    if isinstance(candle, dict):
        return float(candle[key])
    return float(candle[index])


def _normalize_candles(candles: Sequence) -> list[dict]:
    """Normalise les bougies 1M au format dict open/high/low/close/volume."""
    normalized = []
    for candle in candles:
        if isinstance(candle, dict):
            volume = float(candle.get("volume", 0.0))
        elif len(candle) > 4:
            volume = _candle_value(candle, "volume", 4)
        else:
            volume = 0.0

        normalized.append(
            {
                "open": _candle_value(candle, "open", 0),
                "high": _candle_value(candle, "high", 1),
                "low": _candle_value(candle, "low", 2),
                "close": _candle_value(candle, "close", 3),
                "volume": volume,
            }
        )
    return normalized


def _price_in_zone(price: float, zone: dict | None) -> bool:
    """Verifie si le prix est dans la zone POI."""
    if not zone:
        return False
    bottom = zone.get("entry_zone_bottom", zone.get("bottom"))
    top = zone.get("entry_zone_top", zone.get("top"))
    if bottom is None or top is None:
        return False
    return float(bottom) <= price <= float(top)


def _find_last_swing_high(candles: Sequence, n: int = 2) -> float | None:
    """Retourne le dernier micro swing high confirme."""
    ohlcv = _normalize_candles(candles)
    if len(ohlcv) < 2 * n + 1:
        return None
    for i in range(len(ohlcv) - n - 1, n - 1, -1):
        high = ohlcv[i]["high"]
        if all(high > ohlcv[i - k]["high"] for k in range(1, n + 1)) and all(
            high > ohlcv[i + k]["high"] for k in range(1, n + 1)
        ):
            return high
    return None


def _find_last_swing_low(candles: Sequence, n: int = 2) -> float | None:
    """Retourne le dernier micro swing low confirme."""
    ohlcv = _normalize_candles(candles)
    if len(ohlcv) < 2 * n + 1:
        return None
    for i in range(len(ohlcv) - n - 1, n - 1, -1):
        low = ohlcv[i]["low"]
        if all(low < ohlcv[i - k]["low"] for k in range(1, n + 1)) and all(
            low < ohlcv[i + k]["low"] for k in range(1, n + 1)
        ):
            return low
    return None


def _calculate_levels(direction: str, entry: float, atr: float, a4_data: dict | None = None) -> tuple[float, float, float, float]:
    """Calcule entry, SL, TP1 et TP2 depuis l'ATR et le contexte Fibonacci."""
    a4_data = a4_data or {}
    sl_buffer = max(float(atr), 0.01)
    if direction == "LONG":
        sl = entry - sl_buffer
        tp1 = entry + sl_buffer * 1.5
        tp2 = entry + sl_buffer * 2.5
        swing = a4_data.get("swing_used") or {}
        if isinstance(swing, dict):
            tp2 = max(tp2, float(swing.get("high_price", tp2)))
    else:
        sl = entry + sl_buffer
        tp1 = entry - sl_buffer * 1.5
        tp2 = entry - sl_buffer * 2.5
        swing = a4_data.get("swing_used") or {}
        if isinstance(swing, dict):
            tp2 = min(tp2, float(swing.get("low_price", tp2)))
    return entry, sl, tp1, tp2


def _reject(agent_id: str, reason: str, direction: str | None = None, payload: dict | None = None) -> AgentResult:
    """Construit un rejet standard AgentResult."""
    return AgentResult(
        agent_id=agent_id,
        score=0,
        hard_filter_pass=False,
        direction=direction,
        reason=reason,
        payload=payload or {},
    )


def _detect_accumulation(candles_1m: list[dict], poi_zone: dict, atr_1m: float) -> Optional[dict]:
    """Detecte une accumulation recente dans le POI."""
    if len(candles_1m) < AMD_ACCUMULATION_WINDOW:
        return None

    recent = candles_1m[-AMD_ACCUMULATION_WINDOW:]
    acc_high = max(c["high"] for c in recent)
    acc_low = min(c["low"] for c in recent)
    acc_range = acc_high - acc_low
    range_ok = acc_range <= 2.0 * atr_1m
    zone_ok = all(_price_in_zone(c["close"], poi_zone) for c in recent)
    if not range_ok or not zone_ok:
        return None
    return {
        "high": acc_high,
        "low": acc_low,
        "range": acc_range,
        "start_index": max(0, len(candles_1m) - AMD_ACCUMULATION_WINDOW),
    }


def detect_amd_sequence(
    candles_1m: Sequence,
    direction: str,
    poi_zone: dict,
    atr_14: float,
    amd_state: AMDState,
) -> tuple[AMDState, dict]:
    """Avance la sequence AMD stateful: accumulation, sweep, puis CHoCH post-sweep."""
    candles = _normalize_candles(candles_1m)
    if not candles or direction not in {"LONG", "SHORT"}:
        return amd_state, {"phase": amd_state.phase.value, "choch_detected": False, "sweep_detected": False, "amd_complete": False}

    atr = max(float(atr_14 or 0.0), 0.01)
    current_index = len(candles) - 1
    current = candles[-1]
    min_sweep_depth = AMD_SWEEP_ATR_MIN * atr

    if amd_state.phase == AMDPhase.IDLE:
        accum = _detect_accumulation(candles, poi_zone, atr)
        if accum:
            amd_state.phase = AMDPhase.ACCUMULATION_DETECTED
            amd_state.accumulation_high = accum["high"]
            amd_state.accumulation_low = accum["low"]
            amd_state.accumulation_start_index = accum["start_index"]

    if amd_state.phase == AMDPhase.ACCUMULATION_DETECTED:
        if direction == "LONG":
            sweep_depth = amd_state.accumulation_low - current["low"]
            is_sweep = current["low"] < amd_state.accumulation_low and current["close"] > amd_state.accumulation_low and sweep_depth >= min_sweep_depth
        else:
            sweep_depth = current["high"] - amd_state.accumulation_high
            is_sweep = current["high"] > amd_state.accumulation_high and current["close"] < amd_state.accumulation_high and sweep_depth >= min_sweep_depth

        if is_sweep:
            history_before_sweep = candles[:current_index]
            amd_state.phase = AMDPhase.MANIPULATION_DETECTED
            amd_state.sweep_price = current["low"] if direction == "LONG" else current["high"]
            amd_state.sweep_index = current_index
            amd_state.last_swing_high_1m = _find_last_swing_high(history_before_sweep) or amd_state.accumulation_high
            amd_state.last_swing_low_1m = _find_last_swing_low(history_before_sweep) or amd_state.accumulation_low

    if amd_state.phase == AMDPhase.MANIPULATION_DETECTED:
        candles_after_sweep = current_index - amd_state.sweep_index
        body = abs(current["close"] - current["open"])
        delay_ok = 0 < candles_after_sweep <= AMD_MAX_CHOCH_DELAY

        if candles_after_sweep > AMD_MAX_CHOCH_DELAY:
            amd_state = AMDState()
        elif delay_ok:
            if direction == "LONG":
                choch_confirmed = current["close"] > amd_state.last_swing_high_1m and body >= AMD_CHOCH_BODY_MIN * atr
            else:
                choch_confirmed = current["close"] < amd_state.last_swing_low_1m and body >= AMD_CHOCH_BODY_MIN * atr

            if choch_confirmed:
                amd_state.phase = AMDPhase.DISTRIBUTION_CONFIRMED
                amd_state.choch_index = current_index

    sweep_detected = amd_state.sweep_index >= 0 and amd_state.phase in {
        AMDPhase.MANIPULATION_DETECTED,
        AMDPhase.DISTRIBUTION_CONFIRMED,
    }
    return amd_state, {
        "phase": amd_state.phase.value,
        "choch_detected": sweep_detected and amd_state.phase == AMDPhase.DISTRIBUTION_CONFIRMED,
        "sweep_detected": sweep_detected,
        "amd_complete": sweep_detected and amd_state.phase == AMDPhase.DISTRIBUTION_CONFIRMED,
        "sweep_index": amd_state.sweep_index,
        "choch_index": amd_state.choch_index,
        "candles_since_sweep": current_index - amd_state.sweep_index if amd_state.sweep_index >= 0 else 0,
    }


def analyze_amd_sequence(
    candles_1m: Sequence,
    direction: str,
    poi_zone: dict | None,
    atr_1m: float,
    in_ote: bool = False,
    a4_data: dict | None = None,
) -> AgentResult:
    """Analyse complete AMD en mode batch pour validation et orchestration."""
    agent_id = "agent_5"
    candles = _normalize_candles(candles_1m)
    atr = max(float(atr_1m or 0.0), 0.01)

    if direction not in {"LONG", "SHORT"}:
        return _reject(agent_id, "NO_DIRECTION_FROM_AGENT_1")
    if len(candles) < AMD_ACCUMULATION_WINDOW + AMD_MAX_CHOCH_DELAY:
        return _reject(agent_id, "NOT_ENOUGH_1M_CANDLES", direction)

    current_price = candles[-1]["close"]
    price_in_poi = _price_in_zone(current_price, poi_zone) or in_ote
    if not price_in_poi:
        return _reject(agent_id, "PRICE_OUTSIDE_POI_OTE - CHoCH ignore", direction)

    acc_window = candles[-AMD_ACCUMULATION_WINDOW - AMD_MAX_CHOCH_DELAY : -AMD_MAX_CHOCH_DELAY]
    acc_high = max(c["high"] for c in acc_window)
    acc_low = min(c["low"] for c in acc_window)
    acc_range = acc_high - acc_low
    if acc_range > 2.0 * atr:
        return _reject(agent_id, f"NO_ACCUMULATION - range={acc_range:.2f} > 2xATR", direction)

    recent_start = max(0, len(candles) - 10)
    recent = candles[recent_start:]
    sweep_detected = False
    sweep_idx: int | None = None
    sweep_price: float | None = None

    for offset, candle in enumerate(recent):
        if direction == "LONG":
            sweep_depth = acc_low - candle["low"]
            is_sweep = candle["low"] < acc_low and candle["close"] > acc_low and sweep_depth >= AMD_SWEEP_ATR_MIN * atr
            candidate_price = candle["low"]
        else:
            sweep_depth = candle["high"] - acc_high
            is_sweep = candle["high"] > acc_high and candle["close"] < acc_high and sweep_depth >= AMD_SWEEP_ATR_MIN * atr
            candidate_price = candle["high"]
        if is_sweep:
            sweep_detected = True
            sweep_idx = recent_start + offset
            sweep_price = candidate_price
            break

    if not sweep_detected or sweep_idx is None:
        return AgentResult(
            agent_id=agent_id,
            score=30,
            hard_filter_pass=False,
            direction=direction,
            reason="CHoCH_SANS_SWEEP - risque de fausse cassure",
            payload={"amd_phase": 1, "sweep_1m_confirmed": False, "choch_detected": False},
        )

    last_swing_high = _find_last_swing_high(candles[:sweep_idx]) or acc_high
    last_swing_low = _find_last_swing_low(candles[:sweep_idx]) or acc_low
    post_sweep = candles[sweep_idx + 1 : sweep_idx + AMD_MAX_CHOCH_DELAY + 1]
    choch_detected = False
    choch_price: float | None = None

    for candle in post_sweep:
        body = abs(candle["close"] - candle["open"])
        if direction == "LONG" and candle["close"] > last_swing_high and body >= AMD_CHOCH_BODY_MIN * atr:
            choch_detected = True
            choch_price = candle["close"]
            break
        if direction == "SHORT" and candle["close"] < last_swing_low and body >= AMD_CHOCH_BODY_MIN * atr:
            choch_detected = True
            choch_price = candle["close"]
            break

    if not choch_detected or choch_price is None:
        return AgentResult(
            agent_id=agent_id,
            score=50,
            hard_filter_pass=False,
            direction=direction,
            reason="SWEEP_PRESENT_MAIS_PAS_DE_CHOCH - attendre",
            payload={"amd_phase": 2, "sweep_1m_confirmed": True, "choch_detected": False, "sweep_price": sweep_price},
        )

    entry, sl, tp1, tp2 = _calculate_levels(direction, choch_price, atr, a4_data)
    return AgentResult(
        agent_id=agent_id,
        score=85,
        hard_filter_pass=True,
        direction=direction,
        reason=f"AMD_COMPLET - Accumulation -> Sweep -> CHoCH @ {choch_price:.2f}",
        payload={
            "amd_phase": 3,
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "sweep_index": sweep_idx,
            "sweep_price": sweep_price,
            "choch_price": choch_price,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
        },
    )


def score_agent_5(signal: dict, current_close: float, choch_displacement: float, atr_14: float, direction: str) -> AgentResult:
    """Score l'Agent 5 en refusant tout CHoCH sans sweep prealable."""
    if signal.get("choch_detected") and not signal.get("sweep_detected"):
        return AgentResult(
            agent_id="agent_5",
            score=30,
            reason="CHoCH_SANS_SWEEP - risque de fausse cassure",
            direction=direction,
            hard_filter_pass=False,
            payload={"amd_phase": 1, "sweep_1m_confirmed": False, "choch_detected": False},
        )

    if not signal.get("choch_detected"):
        phase = signal.get("phase", "IDLE")
        return AgentResult(
            agent_id="agent_5",
            score=0,
            reason=f"NO_CHOCH_YET_PHASE={phase}",
            direction=direction,
            hard_filter_pass=False,
            payload={"phase": phase, "sweep_1m_confirmed": bool(signal.get("sweep_detected"))},
        )

    displacement_ratio = choch_displacement / atr_14 if atr_14 > 0 else 0
    quality_bonus = min(displacement_ratio / 0.5, 1.0) * 10
    total = min(85 + quality_bonus, 100)

    return AgentResult(
        agent_id="agent_5",
        score=round(total, 1),
        reason=f"AMD_COMPLET - Accumulation -> Sweep -> CHoCH DISP={displacement_ratio:.2f}",
        direction=direction,
        hard_filter_pass=True,
        payload={
            "amd_complete": True,
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "displacement_ratio": displacement_ratio,
            "phase": signal.get("phase"),
        },
    )


async def run_agent_5(ohlcv_1m: Sequence, blackboard: BlackBoard = BLACKBOARD) -> AgentResult:
    """Execution ponctuelle Agent 5 conforme au Script 03."""
    a1_data = blackboard.get_agent("agent_1")
    a2_data = blackboard.get_agent("agent_2")
    a4_data = blackboard.get_agent("agent_4")
    market_data = blackboard.get_market()

    result = analyze_amd_sequence(
        ohlcv_1m,
        direction=a1_data.get("direction"),
        poi_zone=a2_data.get("poi_zone"),
        atr_1m=market_data.get("atr_14_1m") or market_data.get("atr_14") or 1.0,
        in_ote=bool(a4_data.get("in_ote", False)),
        a4_data=a4_data,
    )
    await _publish_agent_5_result(blackboard, result)
    return result


async def _publish_agent_5_result(blackboard: BlackBoard, result: AgentResult) -> None:
    """Publie le resultat Agent 5 dans les slots Blackboard attendus."""
    payload = result.payload or {}
    await blackboard.update_agent(
        "agent_5",
        {
            "score": result.score,
            "direction": result.direction,
            "choch_detected": bool(payload.get("choch_detected", result.score >= 85)),
            "choch_price": payload.get("choch_price"),
            "price_in_poi": result.reason != "PRICE_OUTSIDE_POI_OTE - CHoCH ignore",
            "sweep_1m_confirmed": bool(payload.get("sweep_1m_confirmed", False)),
            "amd_phase": payload.get("amd_phase", 0),
            "entry_price": payload.get("entry"),
            "sl_price": payload.get("sl"),
            "tp1_price": payload.get("tp1"),
            "tp2_price": payload.get("tp2"),
            "reason": result.reason,
            "hard_filter_pass": result.hard_filter_pass,
        },
    )
    await blackboard.publish_agent_dashboard("agent_5", result, min_interval_sec=0)


class AgentMicroscope:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_5"
        self.amd_state = AMDState()
        self.active = False
        self.current_poi = None

    async def run(self):
        """Demarre les boucles Agent 5."""
        self.logger.info("Agent 5 (Microscope V2 AMD) demarre")
        await asyncio.gather(
            self._wait_for_poi_activation(),
            self._tick_monitoring_loop(),
        )

    async def _wait_for_poi_activation(self):
        """Reveille l'Agent 5 quand le prix entre dans un POI."""
        while not self.bb.kill_event.is_set():
            try:
                await asyncio.wait_for(self.bb._events["price_in_poi"].wait(), timeout=1.0)
                self.bb._events["price_in_poi"].clear()
                await self.bb.update_dict(f"agents.{self.name}", {"state": "AWAKE"})

                poi_data = self.bb.read_sync("meta.active_poi")
                if poi_data:
                    self.current_poi = poi_data.get("zone", poi_data)
                    self.amd_state = AMDState()
                    self.active = True
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                self.logger.error(f"Erreur wait_for_poi_activation Agent 5: {exc}")
                await asyncio.sleep(2)

    async def _tick_monitoring_loop(self):
        """Surveille les bougies 1M et publie uniquement une AMD complete."""
        while not self.bb.kill_event.is_set():
            await asyncio.sleep(0.05)

            if not self.active or not self.current_poi:
                await self.bb.update_dict(f"agents.{self.name}", {"state": "SLEEPING"})
                await self.bb.publish_agent_dashboard(
                    "agent_5",
                    idle_result(
                        "agent_5",
                        reason="IDLE_SLEEPING",
                        payload={"phase": AMDPhase.IDLE.value},
                    ),
                    min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                    trigger_orchestrator=False,
                )
                continue

            try:
                candles_1m = list(self.bb.read_sync("market_data.candles.1m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14") or self.bb.read_sync("market.atr_14_1m") or 1.0
                tick = self.bb.read_sync("market_data.current_tick")
                agent1_result = self.bb.read_sync("agent_results.agent_1")
                direction = agent1_result.direction if agent1_result else self.bb.get_agent("agent_1").get("direction")

                if not candles_1m or not tick or not direction:
                    await self.bb.publish_agent_dashboard(
                        "agent_5",
                        idle_result("agent_5", reason="WAITING_TICK_DATA"),
                        min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                        trigger_orchestrator=False,
                    )
                    continue

                current_price = (float(tick["bid"]) + float(tick["ask"])) / 2
                if not _price_in_zone(current_price, self.current_poi):
                    self.active = False
                    await self.bb.update_dict(f"agents.{self.name}", {"state": "SLEEPING"})
                    await self.bb.publish_agent_dashboard(
                        "agent_5",
                        idle_result(
                            "agent_5",
                            reason="IDLE_PRICE_OUTSIDE_POI",
                            payload={"phase": AMDPhase.IDLE.value},
                        ),
                        min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                        trigger_orchestrator=False,
                    )
                    continue

                self.amd_state, signal = detect_amd_sequence(candles_1m, direction, self.current_poi, atr_14, self.amd_state)
                await self.bb.update_dict(f"agents.{self.name}", {"state": f"ACTIVE ({self.amd_state.phase.value})"})

                if signal["choch_detected"]:
                    closes = [_candle_value(c, "close", 3) for c in candles_1m]
                    choch_displacement = abs(closes[-1] - closes[-2]) if len(closes) >= 2 else 0.0
                    result = score_agent_5(signal, current_price, choch_displacement, atr_14, direction)
                    await _publish_agent_5_result(self.bb, result)

                    if result.hard_filter_pass:
                        self.active = False
                        await self.bb.update_dict(f"agents.{self.name}", {"state": "SIGNAL_SENT"})
                else:
                    prev = self.bb.read_sync("agent_results.agent_5")
                    pulse_score = float(prev.score) if prev else 0.0
                    await self.bb.publish_agent_dashboard(
                        "agent_5",
                        idle_result(
                            "agent_5",
                            reason="ACTIVE_MONITORING",
                            score=pulse_score,
                            direction=direction,
                            payload={"phase": self.amd_state.phase.value},
                        ),
                        min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                        trigger_orchestrator=False,
                    )
            except Exception as exc:
                self.logger.error(f"Erreur _tick_monitoring_loop Agent 5: {exc}")
                await asyncio.sleep(2)
