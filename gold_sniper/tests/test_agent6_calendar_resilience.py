from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from gold_sniper.agents.agent_6_sentinelle import AgentSentinelle
from gold_sniper.scrapers.economic_calendar import EconomicCalendarScraper


class _DiscordCounter:
    def __init__(self) -> None:
        self.feed_down_calls = 0

    async def notify_news_feed_down(self) -> None:
        self.feed_down_calls += 1


class Agent6CalendarResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_successful_fallback_is_not_feed_down(self) -> None:
        discord = _DiscordCounter()
        agent = AgentSentinelle(object(), discord=discord, finnhub_token="x", fmp_token="y")
        agent._fetch_finnhub = AsyncMock(side_effect=RuntimeError("Finnhub HTTP 403"))
        agent._fetch_fmp = AsyncMock(side_effect=RuntimeError("FMP HTTP 401"))
        agent._fallback_calendar = AsyncMock(return_value=([], True))

        events = await agent.refresh_events(force=True)

        self.assertEqual(events, [])
        self.assertTrue(agent.feed_alive)
        self.assertEqual(agent.calendar_source, "FOREXFACTORY")
        self.assertIsNone(agent.last_error)
        self.assertEqual(discord.feed_down_calls, 0)

    async def test_feed_down_alert_is_sent_once_per_incident(self) -> None:
        discord = _DiscordCounter()
        agent = AgentSentinelle(object(), discord=discord)

        await agent._notify_feed_down_once()
        await agent._notify_feed_down_once()

        self.assertEqual(discord.feed_down_calls, 1)

    async def test_auth_failures_enter_provider_cooldown(self) -> None:
        now = datetime(2026, 7, 9, 4, 0, tzinfo=timezone.utc)
        agent = AgentSentinelle(object(), finnhub_token="x", fmp_token="y")
        agent._fetch_finnhub = AsyncMock(side_effect=RuntimeError("Finnhub HTTP 403"))
        agent._fetch_fmp = AsyncMock(side_effect=RuntimeError("FMP HTTP 401"))
        agent._fallback_calendar = AsyncMock(return_value=([], True))

        await agent.refresh_events(force=True, now=now)

        self.assertGreater(agent._source_retry_after["FINNHUB"], now)
        self.assertGreater(agent._source_retry_after["FMP"], now)


class ForexFactoryScraperTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_cache_with_no_future_events_is_still_alive(self) -> None:
        xml = """<weeklyevents><event><title>FOMC Meeting Minutes</title><country>USD</country><date>07-08-2026</date><time>6:00pm</time><impact>High</impact></event></weeklyevents>"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "calendar.xml"
            cache.write_text(xml, encoding="utf-8")
            os.utime(cache, None)
            scraper = EconomicCalendarScraper()
            scraper.cache_file = cache

            events = await scraper.fetch_next_major_events()

            self.assertEqual(events, [])
            self.assertTrue(scraper.last_fetch_ok)
            self.assertEqual(scraper.last_http_status, 200)

    def test_forexfactory_time_uses_new_york_dst(self) -> None:
        xml = """<weeklyevents><event><title>CPI</title><country>USD</country><date>07-09-2026</date><time>8:30am</time><impact>High</impact><forecast>0.3%</forecast></event></weeklyevents>"""
        scraper = EconomicCalendarScraper()
        now = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)

        events = scraper._parse_xml(xml, now)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["time"], datetime(2026, 7, 9, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(events[0]["impact"], "HIGH")
        self.assertEqual(events[0]["currency"], "USD")
