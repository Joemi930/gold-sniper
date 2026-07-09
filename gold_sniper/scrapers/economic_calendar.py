# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — ECONOMIC CALENDAR SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════
#
# Interroge le flux XML de ForexFactory pour récupérer les annonces
# économiques majeures et alimenter l'Agent 6 (Sentinelle).
#
# ═══════════════════════════════════════════════════════════════════════════════

import aiohttp
import asyncio
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from utils.logger import get_logger

class EconomicCalendarScraper:
    def __init__(self):
        self.logger = get_logger()
        self.api_url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
        self.cache_file = Path(__file__).resolve().parents[1] / "data" / "cache_forexfactory.xml"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.last_fetch_ok = False
        self.last_http_status: int | None = None
        self.last_error: str | None = None
        
    async def fetch_next_major_events(self) -> list:
        """
        Récupère les événements économiques à fort impact (High) pour l'USD.
        """
        now = datetime.now(timezone.utc)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        cache_file = self.cache_file

        # 1. Vérifier si un cache récent existe (moins de 6 heures)
        if cache_file.exists():
            file_age = datetime.now().timestamp() - cache_file.stat().st_mtime
            if file_age < 6 * 3600:
                self.logger.info("📅 Chargement du calendrier depuis le cache local.")
                try:
                    content = cache_file.read_text(encoding="utf-8")
                    events = self._parse_xml(content, now)
                    self.last_fetch_ok = True
                    self.last_http_status = 200
                    self.last_error = None
                    return events
                except Exception as e:
                    self.last_error = f"cache_read_error: {e}"
                    self.logger.warning(f"Erreur de lecture du cache : {e}")

        # 2. Si pas de cache ou cache obsolète, appeler l'API
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
                async with session.get(self.api_url) as response:
                    self.last_http_status = response.status
                    if response.status == 200:
                        content = await response.text()

                        try:
                            cache_file.write_text(content, encoding="utf-8")
                        except Exception as e:
                            self.logger.warning(f"Impossible de sauvegarder le cache : {e}")

                        events = self._parse_xml(content, now)
                        self.last_fetch_ok = True
                        self.last_error = None
                        return events
                    if response.status == 429:
                        self.logger.warning("⚠️ ForexFactory a renvoyé 429 (Rate Limit).")
                        if cache_file.exists():
                            self.logger.info("📅 Fallback sur l'ancien cache local.")
                            events = self._parse_xml(cache_file.read_text(encoding="utf-8"), now)
                            self.last_fetch_ok = True
                            self.last_error = "HTTP 429; stale cache used"
                            return events
                    self.last_fetch_ok = False
                    self.last_error = f"HTTP {response.status}"
                    self.logger.warning(f"ForexFactory a renvoyé le statut {response.status}.")
                    return []
        except Exception as e:
            self.last_fetch_ok = False
            self.last_error = f"{type(e).__name__}: {e}"
            self.logger.error(f"Erreur lors de la récupération du calendrier ForexFactory: {e}")
            return []

    def _parse_xml(self, content: str, now: datetime) -> list:
        """Parse le contenu XML de ForexFactory."""
        try:
            root = ET.fromstring(content)
            events = []
            
            ny_tz = ZoneInfo("America/New_York")
            for event in root.findall("event"):
                country = (event.findtext("country") or "").strip().upper()
                impact = (event.findtext("impact") or "").strip().upper()

                if country == "USD" and impact == "HIGH":
                    date_str = (event.findtext("date") or "").strip()
                    time_str = (event.findtext("time") or "").strip()
                    name = (event.findtext("title") or "Unknown").strip()

                    if not time_str or time_str.lower() in {"all day", "tentative"}:
                        continue

                    try:
                        dt_str = f"{date_str} {time_str}"
                        local_dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p").replace(tzinfo=ny_tz)
                        utc_time = local_dt.astimezone(timezone.utc)

                        if utc_time > now:
                            events.append({
                                "name": name,
                                "time": utc_time,
                                "time_utc": utc_time,
                                "impact": "HIGH",
                                "currency": "USD",
                                "actual": event.findtext("actual"),
                                "forecast": event.findtext("forecast"),
                                "previous": event.findtext("previous"),
                                "source": "FOREXFACTORY",
                            })
                    except Exception as e:
                        self.logger.warning(f"Erreur de parsing de l'heure {date_str} {time_str}: {e}")
            
            events.sort(key=lambda x: x["time_utc"])
            return events
        except Exception as e:
            self.logger.error(f"Erreur de parsing XML global: {e}")
            return []

if __name__ == "__main__":
    # Test unitaire rapide
    async def test():
        scraper = EconomicCalendarScraper()
        events = await scraper.fetch_next_major_events()
        for e in events:
            print(f"{e['time_utc']} - {e['name']} ({e['volatility']})")
    asyncio.run(test())
