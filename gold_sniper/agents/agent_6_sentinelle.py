import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from utils.logger import get_logger

try:
    import aiohttp
except ImportError:  # pragma: no cover - fallback runtime
    aiohttp = None

try:
    from config import (
        FINNHUB_TOKEN,
        NEWS_HIGH_IMPACT_BLACKOUT_MINUTES,
        NEWS_SCRAPE_INTERVAL_SECONDS,
        NEWS_STEALTH_AFTER_MINUTES,
    )
except ImportError:
    FINNHUB_TOKEN = ""
    NEWS_HIGH_IMPACT_BLACKOUT_MINUTES = 15
    NEWS_SCRAPE_INTERVAL_SECONDS = 60
    NEWS_STEALTH_AFTER_MINUTES = 60

try:
    from scrapers.economic_calendar import EconomicCalendarScraper
except ImportError:
    EconomicCalendarScraper = None


FINNHUB_API_URL = "https://finnhub.io/api/v1/calendar/economic"
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
    event_time = _parse_event_time(raw.get("time") or raw.get("time_utc") or raw.get("datetime"), now)
    impact = str(raw.get("impact") or _classify_impact_finnhub(raw.get("impactLevel"), name)).upper()
    if impact not in {"HIGH", "MEDIUM", "LOW"}:
        impact = _classify_impact_finnhub(raw.get("impact"), name)
    currency = raw.get("currency") or raw.get("country") or ""
    return {"name": name, "time": event_time, "impact": impact, "currency": currency}


def is_gold_relevant_event(event: dict) -> bool:
    """Garde les evenements USD ou high-impact pertinents pour XAUUSD."""
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


class AgentSentinelle:
    """Agent calendrier economique Finnhub avec fallback non bloquant."""

    def __init__(self, blackboard: BlackBoard, telegram=None, finnhub_token: str | None = None):
        self.bb = blackboard
        self.telegram = telegram
        self.logger = get_logger()
        self.name = "agent_6"
        self.finnhub_token = finnhub_token if finnhub_token is not None else FINNHUB_TOKEN
        self.events_cache: list[dict] = []
        self.cache_updated_at: datetime | None = None
        self.feed_alive = True
        self.last_error: str | None = None

    async def run(self):
        """Demarre les boucles fetch calendrier et surveillance blackout."""
        self.logger.info("Agent 6 (Sentinelle Finnhub) demarre")
        await asyncio.gather(self._news_scraper_loop(), self._blackout_check_loop())

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
        if not force and self.cache_updated_at and (now - self.cache_updated_at).total_seconds() < 600:
            return self.events_cache

        try:
            events = await self._fetch_finnhub(now)
            self.events_cache = events
            self.cache_updated_at = now
            self.feed_alive = True
            self.last_error = None
            return events
        except Exception as exc:
            self.feed_alive = False
            self.last_error = str(exc)
            fallback_events = await self._fallback_calendar(now)
            if fallback_events:
                self.events_cache = fallback_events
                self.cache_updated_at = now
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
        state = evaluate_calendar_state(self.events_cache, now, feed_alive=self.feed_alive)
        await self._publish_state(state)
        return state

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
        """Notifie Telegram si la source calendrier tombe."""
        if not self.telegram:
            return
        try:
            await self.telegram.notify_news_feed_down()
        except Exception:
            pass


async def fetch_news_calendar() -> list:
    """Compatibilite externe: fetch ponctuel via AgentSentinelle sans blackboard."""
    from core.blackboard import BLACKBOARD

    agent = AgentSentinelle(BLACKBOARD)
    return await agent.refresh_events(force=True)


AgentCalendar = AgentSentinelle
