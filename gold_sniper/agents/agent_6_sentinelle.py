import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from utils.logger import get_logger
from utils.discord_notifier import send_discord_notification

try:
    import aiohttp
except ImportError:  # pragma: no cover - fallback runtime
    aiohttp = None

try:
    from config import (
        FMP_TOKEN,
        FINNHUB_TOKEN,
        NEWS_HIGH_IMPACT_BLACKOUT_MINUTES,
        NEWS_SCRAPE_INTERVAL_SECONDS,
        NEWS_STEALTH_AFTER_MINUTES,
        MT5_SYMBOL,
    )
except ImportError:
    FMP_TOKEN = ""
    FINNHUB_TOKEN = ""
    NEWS_HIGH_IMPACT_BLACKOUT_MINUTES = 15
    NEWS_SCRAPE_INTERVAL_SECONDS = 60
    NEWS_STEALTH_AFTER_MINUTES = 60
    MT5_SYMBOL = "XAUUSD"

try:
    from scrapers.economic_calendar import EconomicCalendarScraper
except ImportError:
    EconomicCalendarScraper = None


FINNHUB_API_URL = "https://finnhub.io/api/v1/calendar/economic"
FMP_API_URL = "https://financialmodelingprep.com/stable/economic-calendar"
HIGH_IMPACT_KEYWORDS = [
    "NFP",
    "Non-Farm",
    "FOMC",
    "Federal Reserve",
    "CPI",
    "Consumer Price",
    "PPI",
    "Producer Price",
    "GDP",
    "Gross Domestic",
    "Powell",
    "Yellen",
    "Interest Rate",
    "Unemployment",
]
MEDIUM_IMPACT_KEYWORDS = ["Jobless Claims", "Retail Sales", "ISM", "PCE", "ADP", "Trade Balance"]
BLACKOUT_WINDOWS = {
    "HIGH": {"before": NEWS_HIGH_IMPACT_BLACKOUT_MINUTES, "after": NEWS_HIGH_IMPACT_BLACKOUT_MINUTES},
    "MEDIUM": {"before": 15, "after": 10},
    "LOW": {"before": 0, "after": 0},
}
CALENDAR_CACHE_TTL = timedelta(hours=24)
HIGH_IMPACT_FORCE_REFRESH_WINDOW = timedelta(minutes=90)


def _ensure_utc(value: datetime) -> datetime:
    """Retourne un datetime timezone UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_event_time(raw: Any, fallback: datetime) -> datetime:
    """Parse les formats horaires fournis par les calendriers."""
    if isinstance(raw, datetime):
        return _ensure_utc(raw)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str) and raw:
        cleaned = raw.replace("Z", "+00:00")
        try:
            return _ensure_utc(datetime.fromisoformat(cleaned))
        except ValueError:
            pass
    return fallback


def _classify_impact_finnhub(impact: Any, event_name: str) -> str:
    """Classe l'impact Finnhub en LOW/MEDIUM/HIGH."""
    name = event_name.lower()
    if any(keyword.lower() in name for keyword in HIGH_IMPACT_KEYWORDS):
        return "HIGH"
    try:
        impact_num = int(impact)
    except (TypeError, ValueError):
        impact_num = 0
    if impact_num >= 3:
        return "HIGH"
    if impact_num == 2 or any(keyword.lower() in name for keyword in MEDIUM_IMPACT_KEYWORDS):
        return "MEDIUM"
    return "LOW"


def normalize_calendar_event(raw: dict, now: datetime | None = None) -> dict:
    """Normalise un evenement economique en format interne Agent 6."""
    now = now or datetime.now(timezone.utc)
    name = raw.get("name") or raw.get("event") or raw.get("title") or "Unknown"
    event_time = _parse_event_time(
        raw.get("time") or raw.get("time_utc") or raw.get("datetime") or raw.get("date"),
        now,
    )
    impact = str(raw.get("impact") or _classify_impact_finnhub(raw.get("impactLevel"), name)).upper()
    if impact not in {"HIGH", "MEDIUM", "LOW"}:
        impact = _classify_impact_finnhub(raw.get("impact"), name)
    currency = raw.get("currency") or raw.get("country") or ""
    return {
        "name": name,
        "time": event_time,
        "impact": impact,
        "currency": currency,
        "actual": raw.get("actual"),
        "forecast": raw.get("forecast") or raw.get("estimate") or raw.get("consensus"),
        "previous": raw.get("previous"),
    }


def is_gold_relevant_event(event: dict) -> bool:
    f"""Garde les evenements USD ou high-impact pertinents pour {MT5_SYMBOL}."""
    currency = str(event.get("currency", "")).upper()
    name = str(event.get("name", ""))
    return "US" in currency or "USD" in currency or any(keyword.lower() in name.lower() for keyword in HIGH_IMPACT_KEYWORDS)


def check_blackout(current_time: datetime, news_events: list) -> dict:
    """Compatibilite: evalue le blackout sur une liste d'evenements."""
    state = evaluate_calendar_state(news_events, current_time)
    return {
        "blocked": state["blocked"],
        "impact": state["impact_level"],
        "event_name": state["next_event"]["name"] if state["next_event"] else None,
        "reason": state["reason"],
        "resume_at": state["resume_at"],
        "close_open_positions": state["veto"],
        "veto": state["veto"],
        "stealth_mode": state["stealth_mode"],
    }


def evaluate_calendar_state(events: list, now: datetime | None = None, feed_alive: bool = True) -> dict:
    """Evalue veto HIGH 15/15 et stealth 1h apres l'evenement."""
    now = _ensure_utc(now or datetime.now(timezone.utc))
    blocking_event = None
    stealth_event = None
    veto = False
    blocked = False

    for raw_event in events or []:
        event = normalize_calendar_event(raw_event, now)
        event_time = _ensure_utc(event["time"])
        impact = event["impact"]
        window = BLACKOUT_WINDOWS.get(impact, {"before": 0, "after": 0})
        blackout_start = event_time - timedelta(minutes=window["before"])
        blackout_end = event_time + timedelta(minutes=window["after"])
        stealth_end = event_time + timedelta(minutes=NEWS_STEALTH_AFTER_MINUTES)

        if blackout_start <= now <= blackout_end:
            blocked = True
            blocking_event = event
            veto = impact == "HIGH"
            break

        if impact == "HIGH" and event_time < now <= stealth_end:
            stealth_event = event

    active_event = blocking_event or stealth_event
    resume_at = None
    reason = "NEWS_CLEAR" if feed_alive else "NEWS_FEED_FALLBACK_CLEAR"
    impact_level = "NONE"
    stealth_mode = stealth_event is not None

    if blocking_event:
        impact_level = blocking_event["impact"]
        end = _ensure_utc(blocking_event["time"]) + timedelta(minutes=BLACKOUT_WINDOWS[impact_level]["after"])
        resume_at = end.isoformat()
        stealth_mode = blocking_event["impact"] == "HIGH" and now >= _ensure_utc(blocking_event["time"])
        reason = f"NEWS_BLACKOUT_{impact_level} - {blocking_event['name']}"
    elif stealth_event:
        impact_level = "HIGH"
        resume_at = (_ensure_utc(stealth_event["time"]) + timedelta(minutes=NEWS_STEALTH_AFTER_MINUTES)).isoformat()
        reason = f"STEALTH_MODE_AFTER_HIGH - {stealth_event['name']}"

    return {
        "score": 0 if veto else 100,
        "blocked": blocked,
        "veto": veto,
        "stealth_mode": stealth_mode,
        "impact_level": impact_level,
        "next_event": active_event,
        "resume_at": resume_at,
        "feed_alive": feed_alive,
        "reason": reason,
    }


def has_high_impact_event_within(events: list, now: datetime | None = None, window: timedelta = HIGH_IMPACT_FORCE_REFRESH_WINDOW) -> bool:
    """True si le cache contient une news HIGH dans la fenetre donnee."""
    now = _ensure_utc(now or datetime.now(timezone.utc))
    horizon = now + window
    for raw_event in events or []:
        event = normalize_calendar_event(raw_event, now)
        event_time = _ensure_utc(event["time"])
        if event["impact"] == "HIGH" and now <= event_time <= horizon:
            return True
    return False


class AgentSentinelle:
    """Agent calendrier economique Finnhub avec fallback non bloquant."""

    def __init__(
        self,
        blackboard: BlackBoard,
        discord=None,
        finnhub_token: str | None = None,
        fmp_token: str | None = None,
    ):
        self.bb = blackboard
        self.discord = discord
        self.logger = get_logger()
        self.name = "agent_6"
        self.finnhub_token = finnhub_token if finnhub_token is not None else FINNHUB_TOKEN
        self.fmp_token = fmp_token if fmp_token is not None else FMP_TOKEN
        self.calendar_source = "NONE"
        self.events_cache: list[dict] = []
        self.cache_updated_at: datetime | None = None
        self.feed_alive = True
        self.last_error: str | None = None
        self._sent_pre_event_alerts: set[tuple[str, int]] = set()
        self._sent_result_alerts: set[str] = set()

    async def run(self):
        """Demarre les boucles fetch calendrier et surveillance blackout."""
        self.logger.info("Agent 6 (Sentinelle Finnhub) demarre")
        if not hasattr(self, 'tracked_news'):
            self.tracked_news = {}
        await asyncio.gather(
            self._news_scraper_loop(), 
            self._blackout_check_loop(),
            self._news_reaction_tracker_loop()
        )

    async def _news_reaction_tracker_loop(self):
        while not self.bb.kill_event.is_set():
            try:
                now = _ensure_utc(datetime.now(timezone.utc))
                tick = self.bb.read_sync("market_data.current_tick")
                current_price = float(tick.get("bid", 0.0)) if tick else 0.0

                for raw_event in self.events_cache or []:
                    event = normalize_calendar_event(raw_event, now)
                    if event["impact"] != "HIGH" or not is_gold_relevant_event(event):
                        continue
                    
                    event_time = _ensure_utc(event["time"])
                    event_key = self._event_key(event)
                    
                    if current_price > 0:
                        time_diff = (event_time - now).total_seconds()
                        if 0 <= time_diff <= 300:
                            if event_key not in self.tracked_news:
                                self.tracked_news[event_key] = {"price_before": current_price, "event": event}
                    
                    if event_key in self.tracked_news and not self.tracked_news[event_key].get("recorded"):
                        if now >= event_time + timedelta(minutes=NEWS_STEALTH_AFTER_MINUTES) and current_price > 0:
                            price_before = self.tracked_news[event_key]["price_before"]
                            pips_moved = current_price - price_before
                            
                            from data.memory_db import MemoryDB
                            mem = MemoryDB()
                            actual = str(event.get("actual", ""))
                            forecast = str(event.get("forecast", ""))
                            await mem.record_news_reaction(event["name"], "HIGH", actual, forecast, price_before, current_price, pips_moved)
                            
                            self.tracked_news[event_key]["recorded"] = True
                            
                            bias = await mem.get_news_historical_bias(event["name"])
                            if bias is not None:
                                await self.bb.update_dict("market", {"news_historical_bias": bias})
            except Exception as exc:
                self.logger.warning(f"Tracker news erreur: {exc}")
                
            await asyncio.sleep(60)

    async def _news_scraper_loop(self):
        """Rafraichit le calendrier economique sans tuer l'agent si Finnhub tombe."""
        while not self.bb.kill_event.is_set():
            await self.refresh_events()
            await asyncio.sleep(NEWS_SCRAPE_INTERVAL_SECONDS)

    async def _blackout_check_loop(self):
        """Publie l'etat Sentinelle dans le Blackboard."""
        while not self.bb.kill_event.is_set():
            state = await self.check_and_update_blackboard()
            await self.bb.write_agent_result("agent_6", self._state_to_result(state))
            await asyncio.sleep(5)

    async def refresh_events(self, force: bool = False, now: datetime | None = None) -> list:
        """Recupere Finnhub puis fallback local/scraper en cas d'indisponibilite."""
        now = _ensure_utc(now or datetime.now(timezone.utc))
        cache_is_fresh = self.cache_updated_at and (now - self.cache_updated_at) < CALENDAR_CACHE_TTL
        high_soon = has_high_impact_event_within(self.events_cache, now)
        if not force and cache_is_fresh and not high_soon:
            return self.events_cache
        if high_soon:
            self.logger.warning("Agent 6: HIGH impact <90 min detecte dans le cache, refresh Finnhub force")

        errors = []
        for source_name, fetcher in (
            ("FINNHUB", self._fetch_finnhub),
            ("FMP", self._fetch_fmp),
        ):
            try:
                events = await fetcher(now)
                self.events_cache = events
                self.cache_updated_at = now
                self.feed_alive = True
                self.calendar_source = source_name
                self.last_error = None
                return events
            except Exception as exc:
                errors.append(f"{source_name}: {exc}")

        self.feed_alive = False
        self.last_error = "; ".join(errors)
        fallback_events = await self._fallback_calendar(now)
        if fallback_events:
            self.events_cache = fallback_events
            self.cache_updated_at = now
            self.feed_alive = True
            self.calendar_source = "FOREXFACTORY"
            return self.events_cache

        self.calendar_source = "CACHE"
        await self._notify_feed_down_once()
        return self.events_cache

    async def _fetch_finnhub(self, now: datetime) -> list:
        """Fetch depuis Finnhub calendar/economic gratuit."""
        if aiohttp is None:
            raise RuntimeError("aiohttp indisponible")
        if not self.finnhub_token:
            raise RuntimeError("FINNHUB_TOKEN manquant")

        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        params = {"from": today, "to": tomorrow, "token": self.finnhub_token}

        async with aiohttp.ClientSession() as session:
            async with session.get(FINNHUB_API_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    raise RuntimeError(f"Finnhub HTTP {response.status}")
                data = await response.json()

        raw_events = data.get("economicCalendar", data.get("economic", []))
        events = [normalize_calendar_event(event, now) for event in raw_events]
        return [event for event in events if is_gold_relevant_event(event)]

    async def _fetch_fmp(self, now: datetime) -> list:
        """Fetch calendrier economique FMP avant fallback ForexFactory."""
        if aiohttp is None:
            raise RuntimeError("aiohttp indisponible")
        if not self.fmp_token:
            raise RuntimeError("FMP_TOKEN manquant")

        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        params = {"from": today, "to": tomorrow, "apikey": self.fmp_token}

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                FMP_API_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"FMP HTTP {response.status}")
                data = await response.json()

        raw_events = data if isinstance(data, list) else data.get("economicCalendar", data.get("economic", []))
        events = [normalize_calendar_event(event, now) for event in raw_events or []]
        return [event for event in events if is_gold_relevant_event(event)]

    async def _fallback_calendar(self, now: datetime) -> list:
        """Fallback scraper existant si disponible; sinon conserve le cache."""
        if EconomicCalendarScraper is None:
            return self.events_cache
        try:
            scraper = EconomicCalendarScraper()
            raw_events = await scraper.fetch_next_major_events()
            events = [normalize_calendar_event(event, now) for event in raw_events or []]
            return [event for event in events if is_gold_relevant_event(event)]
        except Exception as exc:
            self.last_error = f"{self.last_error}; fallback={exc}"
            return self.events_cache

    async def check_and_update_blackboard(self, now: datetime | None = None) -> dict:
        """Evalue le calendrier et alerte les autres agents via le Blackboard."""
        await self._process_news_alerts(now)
        state = evaluate_calendar_state(self.events_cache, now, feed_alive=self.feed_alive)
        await self._publish_state(state)
        return state

    async def _process_news_alerts(self, now: datetime | None = None) -> None:
        """Envoie les alertes HIGH impact a 90, 30, 15 min, puis le resultat reel."""
        now = _ensure_utc(now or datetime.now(timezone.utc))
        for raw_event in self.events_cache or []:
            event = normalize_calendar_event(raw_event, now)
            if event["impact"] != "HIGH" or not is_gold_relevant_event(event):
                continue

            event_time = _ensure_utc(event["time"])
            minutes_to = int((event_time - now).total_seconds() // 60)
            event_key = self._event_key(event)
            gold_impact = self._expected_gold_impact(event)

            for threshold in (90, 30, 15):
                alert_key = (event_key, threshold)
                if alert_key not in self._sent_pre_event_alerts and 0 <= minutes_to <= threshold:
                    await self._notify_news_alert(event, threshold, gold_impact)
                    self._sent_pre_event_alerts.add(alert_key)

            actual = event.get("actual")
            forecast = event.get("forecast")
            if event_time <= now and actual not in {None, ""} and event_key not in self._sent_result_alerts:
                await self._notify_news_result(event, str(actual), str(forecast or "N/A"))
                self._sent_result_alerts.add(event_key)

    async def _notify_news_alert(self, event: dict, minutes_to: int, gold_impact: str) -> None:
        if self.discord and hasattr(self.discord, "notify_news_alert"):
            await self.discord.notify_news_alert(event["name"], event["impact"], minutes_to, gold_impact)
            return
        await send_discord_notification(
            self.bb,
            (
                f"*NEWS ALERT* - {event['name']}\n"
                f"Impact: {event['impact']} | Dans: {minutes_to} min\n"
                f"Or: {gold_impact}"
            ),
        )

    async def _notify_news_result(self, event: dict, actual: str, forecast: str) -> None:
        gold_impact = self._actual_vs_forecast_gold_impact(event, actual, forecast)
        if self.discord and hasattr(self.discord, "notify_news_result"):
            await self.discord.notify_news_result(event["name"], actual, forecast, gold_impact)
            return
        await send_discord_notification(
            self.bb,
            (
                f"*NEWS RESULT* - {event['name']}\n"
                f"Reel: {actual} | Prevision: {forecast}\n"
                f"Or: {gold_impact}"
            ),
        )

    def _event_key(self, event: dict) -> str:
        return f"{event.get('name')}|{_ensure_utc(event['time']).isoformat()}|{event.get('currency')}"

    def _expected_gold_impact(self, event: dict) -> str:
        name = str(event.get("name", "")).lower()
        if any(key in name for key in ["cpi", "ppi", "pce", "inflation", "interest rate", "fomc", "powell"]):
            return f"USD/rates sensibles: surprise hawkish = pression baissiere sur {MT5_SYMBOL}, dovish = soutien."
        if any(key in name for key in ["nfp", "non-farm", "unemployment", "jobless", "adp"]):
            return "Emploi USD sensible: donnees fortes = USD/rendements haussiers, pression sur l'or."
        if any(key in name for key in ["gdp", "retail sales", "ism"]):
            return f"Croissance USD: surprise forte peut peser sur l'or; faiblesse peut soutenir {MT5_SYMBOL}."
        return f"Volatilite {MT5_SYMBOL} attendue; attendre la reaction post-news."

    def _actual_vs_forecast_gold_impact(self, event: dict, actual: str, forecast: str) -> str:
        actual_num = self._to_float(actual)
        forecast_num = self._to_float(forecast)
        if actual_num is None or forecast_num is None:
            return f"Comparer manuellement la surprise macro et la reaction {MT5_SYMBOL}."
        surprise = actual_num - forecast_num
        name = str(event.get("name", "")).lower()
        if abs(surprise) < 1e-9:
            return f"Conforme au consensus: reaction {MT5_SYMBOL} probablement guidee par le contexte."
        stronger_is_bearish_gold = not any(key in name for key in ["unemployment", "jobless"])
        if surprise > 0:
            return "Surprise au-dessus du consensus: biais initial baissier or." if stronger_is_bearish_gold else "Surprise au-dessus du consensus: biais initial haussier or."
        return "Surprise sous consensus: biais initial haussier or." if stronger_is_bearish_gold else "Surprise sous consensus: biais initial baissier or."

    def _to_float(self, value: Any) -> float | None:
        if value in {None, ""}:
            return None
        cleaned = str(value).replace("%", "").replace("K", "").replace("k", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None

    async def _publish_state(self, state: dict) -> None:
        """Publie le veto, stealth mode et volatility gate."""
        await self.bb.update_agent(
            "agent_6",
            {
                "score": state["score"],
                "blocked": state["blocked"],
                "veto": state["veto"],
                "impact_level": state["impact_level"],
                "next_event": state["next_event"],
                "resume_at": state["resume_at"],
                "feed_alive": state["feed_alive"],
                "stealth_mode": state["stealth_mode"],
                "reason": state["reason"],
                "calendar_source": self.calendar_source,
                "last_error": self.last_error,
            },
        )
        await self.bb.update_dict(
            "risk_management.volatility_gate",
            {
                "allow_trade": not state["veto"],
                "next_news_time": state["next_event"]["time"] if state["next_event"] else None,
                "news_blackout": state["blocked"],
                "stealth_mode": state["stealth_mode"],
                "impact_level": state["impact_level"],
                "reason": state["reason"],
            },
        )

    def _state_to_result(self, state: dict) -> AgentResult:
        """Convertit l'etat Agent 6 en AgentResult."""
        return AgentResult(
            agent_id="agent_6",
            score=state["score"],
            reason=state["reason"],
            direction=None,
            hard_filter_pass=not state["veto"],
            veto=state["veto"],
            payload=state,
        )

    async def _notify_feed_down_once(self) -> None:
        """Notifie Discord si la source calendrier tombe."""
        if not self.discord:
            return
        try:
            await self.discord.notify_news_feed_down()
        except Exception:
            pass


async def fetch_news_calendar() -> list:
    """Compatibilite externe: fetch ponctuel via AgentSentinelle sans blackboard."""
    from core.blackboard import BLACKBOARD

    agent = AgentSentinelle(BLACKBOARD)
    return await agent.refresh_events(force=True)


AgentCalendar = AgentSentinelle
