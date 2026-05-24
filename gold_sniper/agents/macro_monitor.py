import asyncio
from datetime import datetime, timezone
from typing import Sequence

from core.blackboard import BLACKBOARD, BlackBoard
from utils.logger import get_logger

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - optional dependency fallback
    yf = None


DXY_SYMBOL = "DX-Y.NYB"
US10Y_SYMBOL = "^TNX"
GOLD_SYMBOL = "GC=F"
MACRO_UPDATE_INTERVAL_SECONDS = 300


class MacroMonitor:
    """Moniteur macro DXY / US10Y pour le Gold Sniper."""

    def __init__(self, blackboard: BlackBoard = BLACKBOARD):
        self.blackboard = blackboard
        self.logger = get_logger()
        self.last_update: datetime | None = None
        self.last_error: str | None = None

    async def run(self) -> None:
        """Boucle principale compatible engine."""
        await self.run_forever(MACRO_UPDATE_INTERVAL_SECONDS)

    async def run_forever(self, interval_seconds: int = MACRO_UPDATE_INTERVAL_SECONDS) -> None:
        """Met a jour le contexte macro toutes les 5 minutes."""
        while not self.blackboard.kill_event.is_set():
            try:
                await self._update_macro_data()
            except Exception as exc:
                self.last_error = str(exc)
                self.logger.warning(f"MacroMonitor erreur: {exc}")
                await self._publish_fallback_state()
            await asyncio.sleep(interval_seconds)

    async def _update_macro_data(self) -> dict:
        """Recupere DXY, US10Y, Gold et publie les biais macro."""
        loop = asyncio.get_running_loop()
        dxy_data = await loop.run_in_executor(None, self._fetch_ticker, DXY_SYMBOL)
        us10y_data = await loop.run_in_executor(None, self._fetch_ticker, US10Y_SYMBOL)
        gold_data = await loop.run_in_executor(None, self._fetch_ticker, GOLD_SYMBOL)

        state = analyze_macro_context(dxy_data, us10y_data, gold_data)
        state["last_macro_update"] = datetime.now(timezone.utc)
        state["macro_feed_alive"] = bool(dxy_data or us10y_data or gold_data)
        self.last_update = state["last_macro_update"]
        self.last_error = None
        await self.blackboard.update_market(state)
        return state

    def _fetch_ticker(self, symbol: str) -> list[float]:
        """Recupere les 20 dernieres clotures horaires Yahoo Finance."""
        if yf is None:
            return []
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d", interval="1h")
            if hist is None or hist.empty or "Close" not in hist:
                return []
            return [float(value) for value in hist["Close"].dropna().tail(20)]
        except Exception as exc:
            self.last_error = f"{symbol}: {exc}"
            return []

    async def _publish_fallback_state(self) -> None:
        """Publie un etat neutre si les donnees macro sont indisponibles."""
        await self.blackboard.update_market(
            {
                "dxy_bias": "NEUTRAL",
                "gold_macro_bias": "NEUTRAL",
                "us10y_direction": "NEUTRAL",
                "real_rate_favorable": False,
                "macro_score_bonus": 0,
                "macro_feed_alive": False,
                "last_macro_update": datetime.now(timezone.utc),
            }
        )

    @staticmethod
    def _classify_trend(prices: Sequence[float]) -> str:
        """Expose la classification pour les tests et compatibilite."""
        return classify_trend(prices)


def classify_trend(prices: Sequence[float]) -> str:
    """Classe une tendance simple via moyenne rapide vs moyenne lente."""
    values = [float(price) for price in prices if price is not None]
    if len(values) < 5:
        return "NEUTRAL"

    fast = sum(values[-5:]) / 5
    slow = sum(values) / len(values)

    if fast > slow * 1.001:
        return "BULLISH"
    if fast < slow * 0.999:
        return "BEARISH"
    return "NEUTRAL"


def analyze_macro_context(
    dxy_prices: Sequence[float],
    us10y_prices: Sequence[float],
    gold_prices: Sequence[float] | None = None,
) -> dict:
    """Analyse la correlation DXY/Gold/US10Y et retourne le contexte macro."""
    dxy_bias = classify_trend(dxy_prices)
    us10y_trend = classify_trend(us10y_prices)
    gold_trend = classify_trend(gold_prices or [])

    if dxy_bias == "BEARISH":
        gold_macro_bias = "BULLISH"
    elif dxy_bias == "BULLISH":
        gold_macro_bias = "BEARISH"
    else:
        gold_macro_bias = "NEUTRAL"

    if us10y_trend == "BEARISH":
        us10y_direction = "DOWN"
    elif us10y_trend == "BULLISH":
        us10y_direction = "UP"
    else:
        us10y_direction = "NEUTRAL"

    real_rate_favorable = us10y_direction == "DOWN"
    macro_score_bonus = 0
    if gold_macro_bias == "BULLISH":
        macro_score_bonus += 10
    elif gold_macro_bias == "BEARISH":
        macro_score_bonus -= 10
    if real_rate_favorable:
        macro_score_bonus += 5

    return {
        "dxy_bias": dxy_bias,
        "gold_macro_bias": gold_macro_bias,
        "gold_trend": gold_trend,
        "us10y_direction": us10y_direction,
        "real_rate_favorable": real_rate_favorable,
        "macro_score_bonus": macro_score_bonus,
    }


async def run_macro_monitor(blackboard: BlackBoard = BLACKBOARD) -> None:
    """Helper de lancement standalone."""
    await MacroMonitor(blackboard).run()
