import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import schedule

from utils.logger import get_logger
from utils.discord_notifier import DiscordNotifier, _notifier_from_config


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

    if report_type == "weekly":
        import sqlite3
        try:
            conn = sqlite3.connect("data/memory.db")
            conn.row_factory = sqlite3.Row
            errs = conn.execute("SELECT pattern_type, frequency FROM error_patterns ORDER BY frequency DESC LIMIT 3").fetchall()
            strats = conn.execute("SELECT strategy_name, trades_count, win_count, avg_rr FROM strategy_performance ORDER BY trades_count DESC LIMIT 3").fetchall()
            agents = conn.execute("SELECT agent_id, SUM(was_correct) as correct, COUNT(*) as total FROM agent_performance WHERE was_correct IS NOT NULL GROUP BY agent_id ORDER BY correct*1.0/total DESC").fetchall()
            conn.close()
            
            err_str = "\n".join([f"- {e['pattern_type']}: {e['frequency']} occurrences" for e in errs]) or "- Aucune erreur"
            strat_str = "\n".join([f"- {s['strategy_name']}: {s['win_count']}/{s['trades_count']} wins, Avg RR: {s['avg_rr']:.2f}" for s in strats]) or "- Aucune stratégie"
            agent_str = "\n".join([f"- {a['agent_id']}: {a['correct']}/{a['total']} correct ({a['correct']/a['total']*100:.0f}%)" for a in agents if a['total'] > 0]) or "- Aucun agent"
            
            recommendation = "Continuer le monitoring avec prudence."
            if errs and errs[0]['frequency'] > 5:
                recommendation = f"⚠️ Pattern d'erreur dominant '{errs[0]['pattern_type']}'. Revue recommandée."
                
            return (
                f"RAPPORT HEBDOMADAIRE\n"
                f"Heure: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC+1\n\n"
                f"📊 STATS AGENTS\n{agent_str}\n\n"
                f"🎯 STRATEGIES TOP\n{strat_str}\n\n"
                f"⚠️ ERREURS FREQUENTES\n{err_str}\n\n"
                f"💡 RECOMMANDATION\n{recommendation}"
            )
        except Exception as e:
            return f"RAPPORT HEBDOMADAIRE\nErreur lors de la génération: {e}"

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
    notifier: DiscordNotifier | None = None,
) -> str:
    notifier = notifier or _notifier_from_config()
    text = build_report(blackboard, report_type)
    report_path = save_report_file(text, report_type)
    channel = "reports"
    if report_type == "weekly":
        await notifier.send_embed(
            title="Rapport hebdomadaire",
            description=text[:4096],
            color=0xD4A843,
            fields=[],
            channel=channel,
        )
    else:
        await notifier.send(text, channel=channel)
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
    notifier: DiscordNotifier | None = None,
    job_factory: Callable[[str], Callable[[], None]] | None = None,
) -> None:
    job_factory = job_factory or _async_job_factory(blackboard, notifier)
    scheduler.every().day.at("22:00", SCHEDULE_TZ).do(job_factory("daily"))
    scheduler.every().friday.at("21:30", SCHEDULE_TZ).do(job_factory("weekly"))
    scheduler.every().friday.at("21:00", SCHEDULE_TZ).do(_monthly_guard(job_factory("monthly")))


async def report_scheduler_loop(blackboard, notifier: DiscordNotifier | None = None) -> None:
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
    notifier: DiscordNotifier,
    at_time: str,
) -> None:
    scheduler.every().day.at(at_time, SCHEDULE_TZ).do(_async_job_factory(blackboard, notifier)("test"))


def _async_job_factory(blackboard, notifier: DiscordNotifier | None = None) -> Callable[[str], Callable[[], None]]:
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
