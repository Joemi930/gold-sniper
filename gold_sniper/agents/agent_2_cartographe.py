import asyncio
from typing import Any

import numpy as np

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from utils.logger import get_logger


OB_MIN_SCORE = 60.0
OB_FACTOR_WEIGHTS = {
    "freshness": 20.0,
    "impulse": 20.0,
    "htf_alignment": 20.0,
    "fvg_in_zone": 20.0,
    "liquidity_confluence": 20.0,
}


def _arrays_to_ohlcv(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Assemble les arrays OHLC en matrice numpy."""
    return np.column_stack([open_, high, low, close])


def _grade(score: float) -> str:
    """Convertit un score OB en grade lisible."""
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "E"


def _bias_matches_direction(htf_bias: str | None, direction: str) -> bool:
    """Verifie que le biais HTF confirme la direction du trade."""
    if not htf_bias:
        return False
    normalized = str(htf_bias).upper()
    if direction == "LONG":
        return normalized in {"LONG", "BULLISH", "BUY"}
    return normalized in {"SHORT", "BEARISH", "SELL"}


def _extract_liquidity_levels(liquidity_pools: dict | None, direction: str) -> list[float]:
    """Extrait les niveaux de liquidite pertinents pour un OB."""
    if not liquidity_pools:
        return []

    keys = ["eql", "ssl", "swing_lows"] if direction == "LONG" else ["eqh", "bsl", "swing_highs"]
    levels: list[float] = []
    for key in keys:
        for item in liquidity_pools.get(key, []) or []:
            if isinstance(item, dict):
                level = item.get("level", item.get("price"))
            else:
                level = item
            if level is not None:
                levels.append(float(level))
    return levels


def _zone_retouched_after_creation(ohlcv: np.ndarray, ob_idx: int, bottom: float, top: float) -> bool:
    """Detecte si le prix est revenu mitiguer la zone apres sa creation."""
    for candle in ohlcv[ob_idx + 2 :]:
        high = float(candle[1])
        low = float(candle[2])
        if high >= bottom and low <= top:
            return True
    return False


def _fvg_created_from_ob(ohlcv: np.ndarray, ob_idx: int, direction: str) -> bool:
    """Detecte une FVG creee par l'impulsion sortie de l'OB."""
    if ob_idx + 2 >= len(ohlcv):
        return False
    high = ohlcv[:, 1]
    low = ohlcv[:, 2]
    if direction == "LONG":
        return bool(low[ob_idx + 2] > high[ob_idx])
    return bool(high[ob_idx + 2] < low[ob_idx])


def _liquidity_confluence(
    ohlcv: np.ndarray,
    ob_idx: int,
    bottom: float,
    top: float,
    atr_14: float,
    direction: str,
    liquidity_pools: dict | None,
) -> bool:
    """Verifie si l'OB est colle a un niveau de liquidite."""
    tolerance = max(0.25 * atr_14, 0.0001)
    levels = _extract_liquidity_levels(liquidity_pools, direction)
    if levels:
        if direction == "LONG":
            return any(bottom - tolerance <= level <= top + tolerance for level in levels)
        return any(bottom - tolerance <= level <= top + tolerance for level in levels)

    lookback = ohlcv[max(0, ob_idx - 8) : ob_idx]
    if len(lookback) == 0:
        return False
    if direction == "LONG":
        return float(ohlcv[ob_idx][2]) <= float(np.min(lookback[:, 2])) + tolerance
    return float(ohlcv[ob_idx][1]) >= float(np.max(lookback[:, 1])) - tolerance


def score_order_block(
    ohlcv: np.ndarray,
    ob_idx: int,
    atr_14: float,
    direction: str,
    htf_bias: str | None = None,
    liquidity_pools: dict | None = None,
) -> dict:
    """
    Score un Order Block sur 5 facteurs independants.

    Facteurs: fraicheur, impulsion, alignement HTF, FVG dans la zone,
    confluence liquidite. Un score < 60 rend l'OB inutilisable.
    """
    i = ob_idx
    if i + 2 >= len(ohlcv) or direction not in {"LONG", "SHORT"}:
        return {"score": 0.0, "factors": {}, "valid": False, "grade": "E"}

    atr = max(float(atr_14 or 0.0), 0.0001)
    o, h, l, c = 0, 1, 2, 3
    bottom = float(ohlcv[i][l])
    top = float(ohlcv[i][h])
    factors: dict[str, float] = {}

    is_fresh = not _zone_retouched_after_creation(ohlcv, i, bottom, top)
    factors["freshness"] = OB_FACTOR_WEIGHTS["freshness"] if is_fresh else 0.0

    impulse_body = abs(float(ohlcv[i + 1][c] - ohlcv[i + 1][o]))
    impulse_direction_ok = (
        (direction == "LONG" and ohlcv[i + 1][c] > ohlcv[i + 1][o])
        or (direction == "SHORT" and ohlcv[i + 1][c] < ohlcv[i + 1][o])
    )
    impulse_ratio = impulse_body / atr if impulse_direction_ok else 0.0
    factors["impulse"] = min(impulse_ratio / 2.0, 1.0) * OB_FACTOR_WEIGHTS["impulse"]

    factors["htf_alignment"] = OB_FACTOR_WEIGHTS["htf_alignment"] if _bias_matches_direction(htf_bias, direction) else 0.0
    factors["fvg_in_zone"] = OB_FACTOR_WEIGHTS["fvg_in_zone"] if _fvg_created_from_ob(ohlcv, i, direction) else 0.0
    factors["liquidity_confluence"] = (
        OB_FACTOR_WEIGHTS["liquidity_confluence"]
        if _liquidity_confluence(ohlcv, i, bottom, top, atr, direction, liquidity_pools)
        else 0.0
    )

    score = round(min(sum(factors.values()), 100.0), 1)
    return {
        "score": score,
        "factors": {key: round(value, 1) for key, value in factors.items()},
        "valid": score >= OB_MIN_SCORE,
        "grade": _grade(score),
        "fresh": is_fresh,
        "impulse_ratio": round(impulse_ratio, 2),
    }


def detect_order_blocks(
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray,
    close: np.ndarray,
    swing_highs: list,
    swing_lows: list,
    atr_14: float,
    direction: str,
    htf_bias: str | None = None,
    liquidity_pools: dict | None = None,
) -> list:
    """Detecte et garde uniquement les OB dont le score institutionnel passe."""
    obs = []
    length = len(close)
    ohlcv = _arrays_to_ohlcv(open_, high, low, close)
    liquidity_context = dict(liquidity_pools or {})
    liquidity_context.setdefault("swing_highs", swing_highs)
    liquidity_context.setdefault("swing_lows", swing_lows)

    for i in range(2, length - 2):
        if direction == "LONG":
            if close[i] >= open_[i]:
                continue

            post_body = abs(close[i + 1] - open_[i + 1])
            if not (close[i + 1] > open_[i + 1] and post_body >= 1.5 * atr_14):
                continue

            recent_shs = [s for s in swing_highs if s["index"] < i]
            if not recent_shs:
                continue
            last_sh_price = recent_shs[-1]["price"]
            if not (close[i + 1] > last_sh_price or (len(close) > i + 2 and close[i + 2] > last_sh_price)):
                continue

            ob_zone = {
                "type": "BULLISH",
                "top": float(high[i]),
                "bottom": float(low[i]),
                "entry_zone_top": float(open_[i]),
                "entry_zone_bottom": float(low[i]),
                "candle_index": i,
                "age": length - 1 - i,
            }

        elif direction == "SHORT":
            if close[i] <= open_[i]:
                continue

            post_body = abs(close[i + 1] - open_[i + 1])
            if not (close[i + 1] < open_[i + 1] and post_body >= 1.5 * atr_14):
                continue

            recent_sls = [s for s in swing_lows if s["index"] < i]
            if not recent_sls:
                continue
            last_sl_price = recent_sls[-1]["price"]
            if not (close[i + 1] < last_sl_price or (len(close) > i + 2 and close[i + 2] < last_sl_price)):
                continue

            ob_zone = {
                "type": "BEARISH",
                "top": float(high[i]),
                "bottom": float(low[i]),
                "entry_zone_top": float(high[i]),
                "entry_zone_bottom": float(close[i]),
                "candle_index": i,
                "age": length - 1 - i,
            }
        else:
            continue

        score_details = score_order_block(ohlcv, i, atr_14, direction, htf_bias, liquidity_context)
        ob_zone.update(
            {
                "ob_score": score_details["score"],
                "score": score_details["score"],
                "score_factors": score_details["factors"],
                "grade": score_details["grade"],
                "fresh": score_details.get("fresh", False),
                "valid": score_details["valid"],
            }
        )
        if score_details["valid"]:
            obs.append(ob_zone)

    obs.sort(key=lambda x: x["ob_score"], reverse=True)
    return obs[:3]


def detect_fvg(high: np.ndarray, low: np.ndarray, atr_14: float, direction: str) -> list:
    """Detecte les FVG fraiches dans la direction du biais."""
    fvgs = []
    length = len(high)
    min_size = 0.1 * atr_14

    for i in range(2, length):
        if direction == "LONG":
            fvg_size = low[i] - high[i - 2]
            if fvg_size >= min_size:
                fvg_top = float(low[i])
                fvg_bottom = float(high[i - 2])
                equilibrium = (fvg_top + fvg_bottom) / 2
                mitigated = any(low[j] <= equilibrium for j in range(i + 1, length))
                fvgs.append(
                    {
                        "type": "BULLISH",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "equilibrium": equilibrium,
                        "size": float(fvg_size),
                        "size_ratio": float(fvg_size / atr_14),
                        "fresh": not mitigated,
                        "candle_index": i,
                    }
                )

        elif direction == "SHORT":
            fvg_size = low[i - 2] - high[i]
            if fvg_size >= min_size:
                fvg_top = float(low[i - 2])
                fvg_bottom = float(high[i])
                equilibrium = (fvg_top + fvg_bottom) / 2
                mitigated = any(high[j] >= equilibrium for j in range(i + 1, length))
                fvgs.append(
                    {
                        "type": "BEARISH",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "equilibrium": equilibrium,
                        "size": float(fvg_size),
                        "size_ratio": float(fvg_size / atr_14),
                        "fresh": not mitigated,
                        "candle_index": i,
                    }
                )

    fresh_fvgs = [f for f in fvgs if f["fresh"]]
    fresh_fvgs.sort(key=lambda x: x["candle_index"], reverse=True)
    return fresh_fvgs[:3]


def detect_breaker_blocks(ob_zones: list, current_ohlcv: np.ndarray) -> list:
    """Transforme les OB invalides en breaker blocks."""
    breakers = []
    if len(current_ohlcv) == 0:
        return breakers
    last_close = float(current_ohlcv[-1][3])

    for ob in ob_zones:
        invalidated = (
            ob.get("type") == "BULLISH" and last_close < float(ob["bottom"])
        ) or (
            ob.get("type") == "BEARISH" and last_close > float(ob["top"])
        )
        if invalidated:
            breakers.append(
                {
                    "level": ob["top"] if ob["type"] == "BULLISH" else ob["bottom"],
                    "type": "BEARISH_BREAKER" if ob["type"] == "BULLISH" else "BULLISH_BREAKER",
                    "strength": ob.get("ob_score", ob.get("score", 0)),
                    "origin_ob": ob,
                }
            )
    return breakers


def _result_direction(zone_type: str | None) -> str | None:
    """Mappe le type de zone vers LONG/SHORT."""
    if zone_type == "BULLISH":
        return "LONG"
    if zone_type == "BEARISH":
        return "SHORT"
    return None


def score_agent_2(
    best_ob: dict | None,
    best_fvg: dict | None,
    current_price: float,
    atr_14: float,
    blackboard: BlackBoard,
) -> AgentResult:
    """Construit le resultat Agent 2 depuis le meilleur OB score."""
    if not best_ob:
        return AgentResult(
            agent_id="agent_2",
            score=0,
            reason="NO_VALID_OB_SCORE_GE_60",
            direction=None,
            hard_filter_pass=False,
        )

    ob_score = float(best_ob.get("ob_score", 0.0))
    if ob_score < OB_MIN_SCORE or not best_ob.get("valid", False):
        return AgentResult(
            agent_id="agent_2",
            score=0,
            reason=f"WEAK_OB_REJECTED score={ob_score:.1f}<60 factors={best_ob.get('score_factors', {})}",
            direction=_result_direction(best_ob.get("type")),
            hard_filter_pass=False,
            payload={"ob_score": ob_score, "score_factors": best_ob.get("score_factors", {})},
        )

    zone_is_fresh = bool(best_ob.get("fresh", False))
    if not zone_is_fresh:
        return AgentResult(
            agent_id="agent_2",
            score=0,
            reason="ZONE_ALREADY_MITIGATED",
            direction=_result_direction(best_ob.get("type")),
            hard_filter_pass=False,
            payload={"ob_score": ob_score, "score_factors": best_ob.get("score_factors", {})},
        )

    price_in_zone = float(best_ob["bottom"]) <= current_price <= float(best_ob["top"])
    fvg_confluence = bool(
        best_fvg
        and float(best_ob["bottom"]) <= float(best_fvg["top"])
        and float(best_fvg["bottom"]) <= float(best_ob["top"])
    )

    if price_in_zone:
        asyncio.create_task(
            blackboard.notify_price_in_poi(
                {
                    "zone": best_ob,
                    "score_agent2": ob_score,
                    "current_price": current_price,
                }
            )
        )

    return AgentResult(
        agent_id="agent_2",
        score=ob_score,
        reason=f"OB_5_FACTORS score={ob_score:.1f} grade={best_ob.get('grade')} factors={best_ob.get('score_factors')}",
        direction=_result_direction(best_ob.get("type")),
        hard_filter_pass=True,
        payload={
            "zone_type": best_ob.get("type"),
            "zone_top": best_ob.get("top"),
            "zone_bottom": best_ob.get("bottom"),
            "poi_zone": best_ob,
            "active_ob": best_ob,
            "active_fvg": best_fvg,
            "ob_score": ob_score,
            "score_factors": best_ob.get("score_factors", {}),
            "grade": best_ob.get("grade"),
            "fvg_confluence": fvg_confluence,
            "price_in_zone": price_in_zone,
            "zone_is_fresh": zone_is_fresh,
            "zone_age_15m_candles": best_ob.get("age", 0),
        },
    )


class AgentCartographe:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_2"
        self._known_zones: list[dict[str, Any]] = []

    async def run(self):
        """Boucle principale Agent 2."""
        self.logger.info("Agent 2 (Cartographe V2 OB 5 facteurs) demarre")
        while not self.bb.kill_event.is_set():
            try:
                try:
                    await asyncio.wait_for(self.bb._events["new_candle_15m"].wait(), timeout=15.0)
                    self.bb._events["new_candle_15m"].clear()
                except asyncio.TimeoutError:
                    pass

                agent1_result = await self.bb.wait_for_agent("agent_1", timeout=2.0)
                if not agent1_result or agent1_result.score == 0 or not agent1_result.direction:
                    result = AgentResult(
                        agent_id="agent_2",
                        score=0,
                        reason="WAITING_ON_AGENT1_FAIL",
                        direction=None,
                        hard_filter_pass=False,
                    )
                    await self.bb.write_agent_result("agent_2", result)
                    await self.bb.update_dict(f"agents.{self.name}", {"order_blocks": []})
                    continue

                direction = agent1_result.direction
                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14")
                current_tick = self.bb.read_sync("market_data.current_tick")

                if len(candles_15m) < 10 or not atr_14 or not current_tick:
                    await asyncio.sleep(2)
                    continue

                high = np.array([c["high"] for c in candles_15m], dtype=float)
                low = np.array([c["low"] for c in candles_15m], dtype=float)
                open_ = np.array([c["open"] for c in candles_15m], dtype=float)
                close = np.array([c["close"] for c in candles_15m], dtype=float)

                from agents.agent_1_meteo import detect_swings

                swings = detect_swings(high, low, close, n=3, atr_14=atr_14)
                agent1_meta = agent1_result.payload or {}
                htf_bias = agent1_meta.get("structure_4h") or direction
                liquidity_pools = dict(self.bb.get_all().get("market_analysis", {}).get("liquidity_pools", {}) or {})

                fvgs = detect_fvg(high, low, atr_14, direction)
                obs = detect_order_blocks(
                    high,
                    low,
                    open_,
                    close,
                    swings["swing_highs"],
                    swings["swing_lows"],
                    atr_14,
                    direction,
                    htf_bias=htf_bias,
                    liquidity_pools=liquidity_pools,
                )

                ohlcv = _arrays_to_ohlcv(open_, high, low, close)
                breakers = detect_breaker_blocks(self._known_zones, ohlcv)
                self._known_zones = [*obs, *[z for z in self._known_zones if z not in obs]][:20]

                best_ob = obs[0] if obs else None
                best_fvg = fvgs[0] if fvgs else None

                bid = float(current_tick.get("bid", 0.0))
                ask = float(current_tick.get("ask", 0.0))
                current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(close[-1])

                result = score_agent_2(best_ob, best_fvg, current_price, atr_14, self.bb)
                await self.bb.write_agent_result("agent_2", result)

                payload = result.payload or {}
                await self.bb.update_agent(
                    self.name,
                    {
                        "score": result.score,
                        "direction": result.direction,
                        "active_ob": payload.get("active_ob"),
                        "active_fvg": payload.get("active_fvg"),
                        "breaker_blocks": breakers,
                        "poi_zone": payload.get("poi_zone"),
                        "ob_score": payload.get("ob_score", 0),
                        "zone_is_fresh": payload.get("zone_is_fresh", False),
                        "reason": result.reason,
                        "hard_filter_pass": result.hard_filter_pass,
                    },
                )

                ui_zones = [*obs]
                if best_fvg:
                    ui_zones.append(best_fvg)

                await self.bb.update_dict(f"agents.{self.name}", {"order_blocks": ui_zones, "fvgs": fvgs})
                await self.bb.update_dict("market_analysis.zones", {"order_blocks": ui_zones, "fvgs": fvgs, "breaker_blocks": breakers})

            except Exception as exc:
                self.logger.error(f"Erreur dans Agent 2 (Cartographe V2): {exc}")
                await asyncio.sleep(5)


AgentCartographer = AgentCartographe
