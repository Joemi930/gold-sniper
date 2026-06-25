import asyncio
import math
from datetime import datetime, timezone
from typing import Sequence

from core.blackboard import BLACKBOARD, BlackBoard
from utils.logger import get_logger
import config

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - optional dependency fallback
    yf = None

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - yfinance SSL fallback optional
    curl_requests = None


DXY_SYMBOL = "DX-Y.NYB"
US10Y_SYMBOL = "^TNX"
GOLD_SYMBOL = f"{config.MT5_SYMBOL}=X"
GOLD_FALLBACK_SYMBOL = "GC=F"
MACRO_UPDATE_INTERVAL_SECONDS = 300
MACRO_CORRELATION_PERIOD = "5d"
MACRO_CORRELATION_INTERVAL = "15m"
MACRO_CORRELATION_WINDOW = 20


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
        if not gold_data:
            gold_data = await loop.run_in_executor(None, self._fetch_ticker, GOLD_FALLBACK_SYMBOL)

        state = analyze_macro_context(dxy_data, us10y_data, gold_data)
        state["last_macro_update"] = datetime.now(timezone.utc)
        state["macro_feed_alive"] = bool(dxy_data and gold_data)
        self.last_update = state["last_macro_update"]
        self.last_error = None
        await self.blackboard.update_market(state)
        return state

    def _fetch_ticker(self, symbol: str) -> list[float]:
        """Recupere les 20 dernieres clotures 15M Yahoo Finance."""
        if yf is None:
            return []
        try:
            prices = self._fetch_ticker_history(symbol)
            if prices:
                return prices
            if curl_requests is not None:
                return self._fetch_ticker_history(symbol, session=self._make_yfinance_ssl_fallback_session())
            return []
        except Exception as exc:
            error = str(exc)
            if "SSL certificate" in error and curl_requests is not None:
                try:
                    return self._fetch_ticker_history(symbol, session=self._make_yfinance_ssl_fallback_session())
                except Exception as retry_exc:
                    self.last_error = f"{symbol}: {retry_exc}"
                    return []
            self.last_error = f"{symbol}: {exc}"
            return []

    @staticmethod
    def _make_yfinance_ssl_fallback_session():
        return curl_requests.Session(impersonate="chrome", verify=False)

    def _fetch_ticker_history(self, symbol: str, session=None) -> list[float]:
        if session is None:
            ticker = yf.Ticker(symbol)
        else:
            ticker = yf.Ticker(symbol, session=session)
        hist = ticker.history(
            period=MACRO_CORRELATION_PERIOD,
            interval=MACRO_CORRELATION_INTERVAL,
        )
        if hist is None or hist.empty or "Close" not in hist:
            return []
        return [float(value) for value in hist["Close"].dropna().tail(MACRO_CORRELATION_WINDOW)]

    async def _publish_fallback_state(self) -> None:
        """Publie un etat neutre si les donnees macro sont indisponibles."""
        await self.blackboard.update_market(
            {
                "dxy_bias": "NEUTRAL",
                "gold_macro_bias": "NEUTRAL",
                "us10y_direction": "NEUTRAL",
                "real_rate_favorable": False,
                "pearson_dxy_gold": 0.0,
                "macro_signal_strength": "FAIBLE",
                "macro_score_bonus": 0,
                "macro_feed_alive": False,
                "macro_correlation_interval": MACRO_CORRELATION_INTERVAL,
                "macro_correlation_window": MACRO_CORRELATION_WINDOW,
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


def calculate_pearson_correlation(series_a: Sequence[float], series_b: Sequence[float]) -> float:
    """Calcule la correlation de Pearson sur les rendements des deux series."""
    values_a = [float(price) for price in series_a if price is not None]
    values_b = [float(price) for price in series_b if price is not None]
    length = min(len(values_a), len(values_b), MACRO_CORRELATION_WINDOW)
    if length < 5:
        return 0.0

    aligned_a = values_a[-length:]
    aligned_b = values_b[-length:]
    returns_a: list[float] = []
    returns_b: list[float] = []
    for prev_a, cur_a, prev_b, cur_b in zip(aligned_a, aligned_a[1:], aligned_b, aligned_b[1:]):
        if prev_a == 0 or prev_b == 0:
            continue
        returns_a.append((cur_a - prev_a) / prev_a)
        returns_b.append((cur_b - prev_b) / prev_b)

    if len(returns_a) < 3 or len(returns_a) != len(returns_b):
        return 0.0

    mean_a = sum(returns_a) / len(returns_a)
    mean_b = sum(returns_b) / len(returns_b)
    covariance = sum((a - mean_a) * (b - mean_b) for a, b in zip(returns_a, returns_b))
    variance_a = sum((a - mean_a) ** 2 for a in returns_a)
    variance_b = sum((b - mean_b) ** 2 for b in returns_b)
    denominator = math.sqrt(variance_a * variance_b)
    if denominator == 0:
        return 0.0

    correlation = covariance / denominator
    return max(-1.0, min(1.0, correlation))


def classify_macro_signal_strength(pearson_dxy_gold: float) -> str:
    """Classe la force du signal macro selon la correlation DXY/Gold."""
    if pearson_dxy_gold < -0.7:
        return "FORT"
    if pearson_dxy_gold <= -0.3:
        return "MODÉRÉ"
    return "FAIBLE"


def macro_score_bonus_for_strength(strength: str) -> int:
    """Retourne le bonus macro associe a la force du signal."""
    if strength == "FORT":
        return 15
    if strength == "MODÉRÉ":
        return 7
    return 0


def analyze_macro_context(
    dxy_prices: Sequence[float],
    us10y_prices: Sequence[float],
    gold_prices: Sequence[float] | None = None,
) -> dict:
    """Analyse la correlation Pearson DXY/{config.MT5_SYMBOL} et retourne le contexte macro."""
    dxy_bias = classify_trend(dxy_prices)
    us10y_trend = classify_trend(us10y_prices)
    gold_trend = classify_trend(gold_prices or [])
    pearson_dxy_gold = calculate_pearson_correlation(dxy_prices, gold_prices or [])
    macro_signal_strength = classify_macro_signal_strength(pearson_dxy_gold)

    if macro_signal_strength == "FAIBLE":
        gold_macro_bias = "NEUTRAL"
    elif dxy_bias == "BEARISH":
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
    macro_score_bonus = macro_score_bonus_for_strength(macro_signal_strength)

    return {
        "dxy_bias": dxy_bias,
        "gold_macro_bias": gold_macro_bias,
        "gold_trend": gold_trend,
        "us10y_direction": us10y_direction,
        "real_rate_favorable": real_rate_favorable,
        "pearson_dxy_gold": round(pearson_dxy_gold, 3),
        "macro_signal_strength": macro_signal_strength,
        "macro_score_bonus": macro_score_bonus,
        "macro_correlation_interval": MACRO_CORRELATION_INTERVAL,
        "macro_correlation_window": MACRO_CORRELATION_WINDOW,
    }


async def run_macro_monitor(blackboard: BlackBoard = BLACKBOARD) -> None:
    """Helper de lancement standalone."""
    await MacroMonitor(blackboard).run()
