import asyncio
from datetime import datetime, timezone

import numpy as np

from core.blackboard import BLACKBOARD, BlackBoard
from utils.logger import get_logger


REGIME_UPDATE_INTERVAL_SECONDS = 60


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Calcule une EMA simple sur un tableau de prix."""
    if len(data) == 0:
        return np.array([])
    if len(data) < period:
        return np.array(data, dtype=float)
    alpha = 2 / (period + 1)
    ema = [float(data[0])]
    for price in data[1:]:
        ema.append(alpha * float(price) + (1 - alpha) * ema[-1])
    return np.array(ema)


def calculate_atr_from_ohlcv(ohlcv: np.ndarray, period: int = 14) -> float:
    """Calcule un ATR approximatif depuis OHLCV."""
    if len(ohlcv) < 2:
        return 0.0
    highs = ohlcv[:, 1]
    lows = ohlcv[:, 2]
    closes = ohlcv[:, 3]
    true_ranges = []
    for i in range(1, len(ohlcv)):
        true_ranges.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    if not true_ranges:
        return 0.0
    return float(np.mean(true_ranges[-period:]))


def detect_market_regime(ohlcv_4h: np.ndarray, atr_14: float, atr_baseline: float) -> dict:
    """
    Detecte TRENDING, RANGING, HIGH_VOLATILITY ou ACCUMULATION.

    ohlcv_4h: matrice [open, high, low, close, volume?].
    """
    if ohlcv_4h is None or len(ohlcv_4h) < 20:
        return {"regime": "UNKNOWN", "confidence": 0.0, "description": "Donnees insuffisantes"}

    ohlcv = np.asarray(ohlcv_4h, dtype=float)
    highs = ohlcv[:, 1]
    lows = ohlcv[:, 2]
    closes = ohlcv[:, 3]

    if atr_baseline > 0 and atr_14 > 1.8 * atr_baseline:
        ratio = atr_14 / atr_baseline
        return {
            "regime": "HIGH_VOLATILITY",
            "confidence": round(min((ratio - 1.8) / 0.5, 1.0), 3),
            "atr_ratio": round(ratio, 3),
            "description": f"ATR {atr_14:.2f} = {ratio:.1f}x normal",
        }

    ema10 = _ema(closes, 10)
    ema20 = _ema(closes, 20)
    if len(ema10) >= 5 and len(ema20) >= 5 and ema20[-1] != 0 and ema10[-5] != 0:
        ema_spread = (ema10[-1] - ema20[-1]) / ema20[-1]
        ema_slope = (ema10[-1] - ema10[-5]) / ema10[-5]
        if abs(ema_spread) > 0.002 and abs(ema_slope) > 0.001:
            direction = "UP" if ema_spread > 0 else "DOWN"
            return {
                "regime": "TRENDING",
                "direction": direction,
                "confidence": round(min(abs(ema_spread) / 0.005, 1.0), 3),
                "description": f"Tendance {direction} - EMA spread={ema_spread:.3%}",
            }

    recent_range = float(np.max(highs[-10:]) - np.min(lows[-10:]))
    if atr_baseline > 0 and recent_range < 0.5 * atr_baseline:
        confidence = 1.0 - recent_range / (0.5 * atr_baseline)
        return {
            "regime": "ACCUMULATION",
            "confidence": round(max(0.0, min(confidence, 1.0)), 3),
            "range_size": round(recent_range, 3),
            "description": f"Accumulation - range={recent_range:.2f} < 0.5x ATR",
        }

    range_high = float(np.max(highs[-20:]))
    range_low = float(np.min(lows[-20:]))
    return {
        "regime": "RANGING",
        "confidence": 0.6,
        "range_high": range_high,
        "range_low": range_low,
        "description": f"Range [{range_low:.2f} - {range_high:.2f}]",
    }


class RegimeDetector:
    """Detecteur de regime de marche base sur les bougies 4H."""

    def __init__(self, blackboard: BlackBoard = BLACKBOARD):
        self.blackboard = blackboard
        self.logger = get_logger()
        self.last_regime = "UNKNOWN"

    async def run(self) -> None:
        """Boucle principale compatible engine."""
        while not self.blackboard.kill_event.is_set():
            try:
                await self.update_regime()
            except Exception as exc:
                self.logger.warning(f"RegimeDetector erreur: {exc}")
            await asyncio.sleep(REGIME_UPDATE_INTERVAL_SECONDS)

    async def update_regime(self) -> dict:
        """Lit les bougies 4H, detecte le regime et publie dans market.regime."""
        candles = list(self.blackboard.read_sync("market_data.candles.4H") or [])
        if len(candles) < 20:
            result = {"regime": "UNKNOWN", "confidence": 0.0, "description": "Donnees 4H insuffisantes"}
            await self._publish(result)
            return result

        ohlcv = np.array(
            [
                [
                    float(c["open"]),
                    float(c["high"]),
                    float(c["low"]),
                    float(c["close"]),
                    float(c.get("volume", 0.0)),
                ]
                for c in candles
            ],
            dtype=float,
        )
        atr_14 = self.blackboard.read_sync("market.atr_14_4h") or self.blackboard.read_sync("market_data.atr_14")
        atr_14 = float(atr_14 or calculate_atr_from_ohlcv(ohlcv, 14))
        atr_baseline = float(calculate_atr_from_ohlcv(ohlcv, min(50, max(14, len(ohlcv) - 1))))

        result = detect_market_regime(ohlcv, atr_14, atr_baseline)
        await self._publish(result)
        return result

    async def _publish(self, result: dict) -> None:
        """Publie le regime dans le Blackboard."""
        self.last_regime = result.get("regime", "UNKNOWN")
        await self.blackboard.update_market(
            {
                "regime": self.last_regime,
                "regime_confidence": result.get("confidence", 0.0),
                "regime_description": result.get("description", ""),
                "regime_updated_at": datetime.now(timezone.utc),
            }
        )


async def run_regime_detector(blackboard: BlackBoard = BLACKBOARD) -> None:
    """Helper standalone."""
    await RegimeDetector(blackboard).run()
