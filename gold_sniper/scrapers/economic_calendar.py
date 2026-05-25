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
from datetime import datetime, timezone, timedelta
from utils.logger import get_logger

class EconomicCalendarScraper:
    def __init__(self):
        self.logger = get_logger()
        self.api_url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
        
    async def fetch_next_major_events(self) -> list:
        """
        Récupère les événements économiques à fort impact (High) pour l'USD.
        """
        now = datetime.now(timezone.utc)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        cache_file = "cache_forexfactory.xml"
        
        # 1. Vérifier si un cache récent existe (moins de 6 heures)
        if os.path.exists(cache_file):
            file_age = datetime.now().timestamp() - os.path.getmtime(cache_file)
            if file_age < 6 * 3600:
                self.logger.info("📅 Chargement du calendrier depuis le cache local.")
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    return self._parse_xml(content, now)
                except Exception as e:
                    self.logger.warning(f"Erreur de lecture du cache : {e}")

        # 2. Si pas de cache ou cache obsolète, appeler l'API
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
                async with session.get(self.api_url) as response:
                    if response.status == 200:
                        content = await response.text()
                        
                        # Sauvegarder dans le cache
                        try:
                            with open(cache_file, "w", encoding="utf-8") as f:
                                f.write(content)
                        except Exception as e:
                            self.logger.warning(f"Impossible de sauvegarder le cache : {e}")
                            
                        return self._parse_xml(content, now)
                    elif response.status == 429:
                        self.logger.warning("⚠️ ForexFactory a renvoyé 429 (Rate Limit).")
                        # Fallback sur le cache obsolète si disponible
                        if os.path.exists(cache_file):
                            self.logger.info("📅 Fallback sur l'ancien cache local.")
                            with open(cache_file, "r", encoding="utf-8") as f:
                                return self._parse_xml(f.read(), now)
                        return []
                    else:
                        self.logger.warning(f"ForexFactory a renvoyé le statut {response.status}. Mode simulé utilisé.")
                        return []
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du calendrier ForexFactory: {e}")
            return []

    def _parse_xml(self, content: str, now: datetime) -> list:
        """Parse le contenu XML de ForexFactory."""
        try:
            root = ET.fromstring(content)
            events = []
            
            for event in root.findall('event'):
                country = event.find('country').text
                impact = event.find('impact').text
                
                if country == 'USD' and impact == 'High':
                    date_str = event.find('date').text
                    time_str = event.find('time').text
                    name = event.find('title').text
                    
                    if not time_str or time_str.lower() == 'all day':
                        continue
                    
                    try:
                        dt_str = f"{date_str} {time_str}"
                        local_dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
                        # Approximation EDT to UTC (+4h)
                        utc_time = local_dt.replace(tzinfo=timezone.utc) + timedelta(hours=4)
                        
                        if utc_time > now:
                            events.append({
                                "name": name,
                                "time_utc": utc_time,
                                "volatility": impact
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
