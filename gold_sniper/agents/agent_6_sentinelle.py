import asyncio
from datetime import datetime, timedelta, timezone

from core.blackboard import BlackBoard
from core.agent_result import AgentResult
from utils.logger import get_logger

# Fallback on scraper if available
try:
    from scrapers.economic_calendar import EconomicCalendarScraper
except ImportError:
    EconomicCalendarScraper = None

BLACKOUT_WINDOWS = {
    'HIGH':   {'before': 60, 'after': 30},
    'MEDIUM': {'before': 15, 'after': 10},
    'LOW':    {'before': 0,  'after': 0},
}

HIGH_IMPACT_KEYWORDS = [
    "NFP", "Non-Farm Payroll", "FOMC", "Federal Reserve",
    "CPI", "Consumer Price Index", "PPI",
    "GDP", "Gross Domestic Product", "Powell", "Fed Chair",
    "Interest Rate Decision"
]

MEDIUM_IMPACT_KEYWORDS = [
    "Jobless Claims", "Retail Sales", "ISM",
    "PCE", "ADP", "Trade Balance"
]

async def fetch_news_calendar() -> list:
    if not EconomicCalendarScraper:
        return []
        
    try:
        scraper = EconomicCalendarScraper()
        events = await scraper.fetch_next_major_events()
        return _classify_news_impact(events)
    except Exception as e:
        return None

def _classify_news_impact(raw_events: list) -> list:
    classified = []
    for event in raw_events:
        name = event.get("name", "")
        if any(kw.lower() in name.lower() for kw in HIGH_IMPACT_KEYWORDS):
            impact = "HIGH"
        elif any(kw.lower() in name.lower() for kw in MEDIUM_IMPACT_KEYWORDS):
            impact = "MEDIUM"
        else:
            impact = "LOW"
        
        classified.append({
            "name": name,
            "impact": impact,
            "time": event["time_utc"] if "time_utc" in event else event["time"],
        })
    return classified

def check_blackout(current_time: datetime, news_events: list) -> dict:
    if not news_events:
        return {"blocked": False, "reason": "NEWS_CLEAR"}
        
    for event in news_events:
        impact = event["impact"]
        window = BLACKOUT_WINDOWS.get(impact, {'before': 0, 'after': 0})
        
        if window['before'] == 0 and window['after'] == 0:
            continue
            
        blackout_start = event["time"] - timedelta(minutes=window["before"])
        blackout_end   = event["time"] + timedelta(minutes=window["after"])
        
        if blackout_start <= current_time <= blackout_end:
            return {
                "blocked": True,
                "impact": impact,
                "event_name": event["name"],
                "reason": f"NEWS_BLACKOUT_{impact}",
                "resume_at": blackout_end.isoformat(),
                "close_open_positions": impact == "HIGH",
            }
    
    return {"blocked": False, "reason": "NEWS_CLEAR"}

class AgentSentinelle:
    HOSTILE_MODE_DELAY = 5 * 60
    
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_6_sentinelle"
        self.news_events = []
        self.last_successful_fetch = datetime.utcnow().replace(tzinfo=timezone.utc)
        self.hostile_mode = False
    
    async def run(self):
        self.logger.info("▶️  Agent 6 (Sentinelle V2) démarré")
        await asyncio.gather(
            self._news_scraper_loop(),
            self._blackout_check_loop(),
        )
    
    async def _news_scraper_loop(self):
        while not self.bb.kill_event.is_set():
            events = await fetch_news_calendar()
            
            if events is not None:
                self.news_events = events
                self.last_successful_fetch = datetime.now(timezone.utc)
                self.hostile_mode = False
            else:
                if self.last_successful_fetch:
                    elapsed = (datetime.now(timezone.utc) - self.last_successful_fetch).total_seconds()
                    if elapsed > self.HOSTILE_MODE_DELAY:
                        self.hostile_mode = True
            
            await asyncio.sleep(60)
    
    async def _blackout_check_loop(self):
        while not self.bb.kill_event.is_set():
            current_time = datetime.now(timezone.utc)
            
            if self.hostile_mode:
                result = AgentResult(
                    agent_id="agent_6", score=0,
                    reason="ASSUME_HOSTILE_NEWS_FEED_DOWN",
                    direction=None, is_hard_filter=False
                )
                allow_trade = False
                next_event = None
            else:
                blackout = check_blackout(current_time, self.news_events)
                
                if blackout["blocked"]:
                    score = 0
                    reason = blackout["reason"]
                    allow_trade = False
                else:
                    score = 100
                    reason = "NEWS_CLEAR"
                    allow_trade = True
                
                result = AgentResult(
                    agent_id="agent_6", score=score,
                    reason=reason, direction=None,
                    is_hard_filter=False,
                    metadata=blackout
                )
                
                # find next event
                future_events = [e for e in self.news_events if e["time"] > current_time]
                next_event = future_events[0] if future_events else None
            
            await self.bb.write_agent_result("agent_6", result)
            
            await self.bb.update_dict(f"agents.{self.name}", {
                "is_clear": allow_trade,
                "next_red_event": {
                    "name": next_event['name'],
                    "time_utc": next_event['time'],
                } if next_event else None,
                "blackout_active": not allow_trade,
            })
            
            await self.bb.update_dict("risk_management.volatility_gate", {
                "allow_trade": allow_trade,
                "next_news_time": next_event['time'] if next_event else None,
            })
            
            await asyncio.sleep(5)
