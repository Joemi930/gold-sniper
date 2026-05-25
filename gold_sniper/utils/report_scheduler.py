import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import schedule

from utils.logger import get_logger
from utils.telegram_notifier import TelegramNotifier, _notifier_from_config


UTC_PLUS_1 = ZoneInfo("Africa/Kinshasa")
SCHEDULE_TZ = "Africa/Kinshasa"
REPORTS_DIR = Path("logs/reports")


def is_last_friday(now: datetime | None = None) -> bool:
    now = (now or datetime.now(UTC_PLUS_1)).astimezone(UTC_PLUS_1)
    if now.weekday() != 4:
        return False
    return (now + timedelta(days=7)).month != now.month


def build_report(blackboard, report_type: str, now: datetime | None = None) -> str:
    now = (now or datetime.now(UTC_PLUS_1)).astimezone(UTC_PLUS_1)
    data = blackboard.get_all()
    daily = data.get("daily_stats", {})
    meta = data.get("meta", {})
    market = data.get("market", {})
    orch = data.get("orchestrator", {})
    active = data.get("active_trades", {}) or {}
    closed = data.get("positions", {}).get("closed_today", []) or []
    realized = float(daily.get("realized_pnl", 0.0) or 0.0)
    floating = float(daily.get("floating_pnl", 0.0) or 0.0)
    total = realized + floating
    wins = sum(1 for trade in closed if float(trade.get("pnl", 0.0) or 0.0) > 0)
    losses = sum(1 for trade in closed if float(trade.get("pnl", 0.0) or 0.0) < 0)
    winrate = (wins / len(closed) * 100.0) if closed else 0.0

    title = {
        "daily": "RAPPORT JOURNALIER",
        "weekly": "RAPPORT HEBDOMADAIRE",
        "monthly": "RAPPORT MENSUEL",
        "test": "RAPPORT TEST",
    }.get(report_type, f"RAPPORT {report_type.upper()}")

    return (
        f"{title}\n"
        f"Heure: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC+1\n"
        f"Session: {market.get('session', 'UNKNOWN')} | Regime: {market.get('regime', 'UNKNOWN')}\n"
        f"Strategie: {orch.get('strategy', 'N/A')} | Decision: {orch.get('decision', 'N/A')}\n"
        f"Trades ouverts jour: {meta.get('daily_trade_count', 0)} | Positions actives: {len(active)}\n"
        f"Trades fermes: {daily.get('trades_closed', len(closed))} | Wins: {wins} | Losses: {losses} | Winrate: {winrate:.0f}%\n"
        f"PnL realise: {realized:+.2f} | PnL flottant: {floating:+.2f} | Total: {total:+.2f}"
    )


async def send_scheduled_report(
    blackboard,
    report_type: str,
    notifier: TelegramNotifier | None = None,
) -> str:
    notifier = notifier or _notifier_from_config()
    text = build_report(blackboard, report_type)
    report_path = save_report_file(text, report_type)
    await notifier.send(text, parse_mode=None)
    await blackboard.update_dict("orchestrator", {
        "last_scheduled_report": report_type,
        "last_scheduled_report_at": datetime.now(timezone.utc).isoformat(),
        "last_scheduled_report_path": str(report_path),
    })
    return text


def save_report_file(text: str, report_type: str, now: datetime | None = None) -> Path:
    now = (now or datetime.now(UTC_PLUS_1)).astimezone(UTC_PLUS_1)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report_type}_report_{now.strftime('%Y-%m-%d_%H%M%S')}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def install_report_jobs(
    scheduler,
    blackboard,
    notifier: TelegramNotifier | None = None,
    job_factory: Callable[[str], Callable[[], None]] | None = None,
) -> None:
    job_factory = job_factory or _async_job_factory(blackboard, notifier)
    scheduler.every().day.at("22:00", SCHEDULE_TZ).do(job_factory("daily"))
    scheduler.every().friday.at("21:30", SCHEDULE_TZ).do(job_factory("weekly"))
    scheduler.every().friday.at("21:00", SCHEDULE_TZ).do(_monthly_guard(job_factory("monthly")))


async def report_scheduler_loop(blackboard, notifier: TelegramNotifier | None = None) -> None:
    logger = get_logger()
    scheduler = schedule.Scheduler()
    install_report_jobs(scheduler, blackboard, notifier)
    logger.info("Report scheduler demarre: daily 22:00, weekly vendredi 21:30, monthly dernier vendredi 21:00 UTC+1")
    while not blackboard.kill_event.is_set():
        try:
            scheduler.run_pending()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Report scheduler erreur: {exc}")
        await asyncio.sleep(1.0)


def schedule_test_report(
    scheduler,
    blackboard,
    notifier: TelegramNotifier,
    at_time: str,
) -> None:
    scheduler.every().day.at(at_time, SCHEDULE_TZ).do(_async_job_factory(blackboard, notifier)("test"))


def _async_job_factory(blackboard, notifier: TelegramNotifier | None = None) -> Callable[[str], Callable[[], None]]:
    def build(report_type: str) -> Callable[[], None]:
        def run() -> None:
            asyncio.create_task(send_scheduled_report(blackboard, report_type, notifier))
        return run
    return build


def _monthly_guard(job: Callable[[], None]) -> Callable[[], None]:
    def guarded() -> None:
        if is_last_friday():
            job()
    return guarded
