"""
Commandes opérationnelles Gold Sniper — consomme data/discord_inbox.jsonl
(pc_manager est le seul lecteur gateway Discord).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import config
from utils.discord_commands import LIFECYCLE_COMMANDS, normalize_command
from utils.discord_notifier import (
    COLOR_BLUE,
    COLOR_GOLD,
    COLOR_GREEN,
    COLOR_GREY,
    COLOR_RED,
    DiscordNotifier,
    _notifier_from_config,
)
from utils.logger import get_logger
from utils.report_scheduler import send_scheduled_report

logger = get_logger()

COMMANDS_HELP = {
    "!status": "État complet du système",
    "!pause": "Suspendre les nouveaux trades",
    "!resume": "Reprendre les nouveaux trades",
    "!risk": "Modifier le risque live (ex: !risk 0.5)",
    "!trades": "Positions ouvertes et P&L",
    "!agents": "Scores des 7 agents",
    "!regime": "Régime et stratégie active",
    "!news": "Annonces économiques 24h",
    "!backtest": "Backtest rapide 7 jours",
    "!calibrate": "Calibration des poids agents",
    "!report": "Rapport journalier immédiat",
    "!logs": "Fichier de logs de session",
    "!memory": "Stats mémoire SQLite",
    "!health": "Diagnostic complet",
    "!chart": "Graphique XAUUSD 1M",
    "!help": "Liste des commandes",
}


class DiscordCommander:
    def __init__(
        self,
        blackboard,
        notifier: DiscordNotifier | None = None,
        on_restart: Callable[[], None] | None = None,
    ) -> None:
        self.blackboard = blackboard
        self.notifier = notifier or _notifier_from_config()
        self.on_restart = on_restart
        self._debounce: dict[str, float] = {}
        self._inbox_path = Path(config.DISCORD_INBOX_PATH)
        self._inbox_lock_path = self._inbox_path.with_suffix(".lock")
        self._processed_store = Path("data/discord_inbox_processed.json")
        self._processed_ids: set[str] = self._load_processed_ids()

    async def run_forever(self) -> None:
        self._inbox_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._inbox_path.exists():
            self._inbox_path.touch()
        logger.info("Discord commander démarré (inbox %s)", self._inbox_path)
        while not self.blackboard.kill_event.is_set():
            try:
                await self._drain_inbox()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Discord commander erreur: {exc}")
            await asyncio.sleep(0.75)

    def _load_processed_ids(self) -> set[str]:
        if not self._processed_store.exists():
            return set()
        try:
            data = json.loads(self._processed_store.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(str(x) for x in data[-500:])
        except (json.JSONDecodeError, OSError):
            pass
        return set()

    def _save_processed_ids(self) -> None:
        self._processed_store.parent.mkdir(parents=True, exist_ok=True)
        trimmed = list(self._processed_ids)[-500:]
        self._processed_store.write_text(
            json.dumps(trimmed, ensure_ascii=False),
            encoding="utf-8",
        )

    async def _drain_inbox(self) -> None:
        from utils.inbox_lock import inbox_lock

        if not self._inbox_path.exists():
            return
        try:
            with inbox_lock(self._inbox_lock_path):
                lines = self._inbox_path.read_text(encoding="utf-8").splitlines()
                if not lines:
                    return
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    eid = entry.get("id", "")
                    if eid in self._processed_ids:
                        continue
                    user_id = int(entry.get("user_id", 0))
                    if config.DISCORD_USER_ID and user_id != config.DISCORD_USER_ID:
                        continue
                    content = entry.get("content", "")
                    cmd, args, _normalized = normalize_command(content)
                    if cmd in LIFECYCLE_COMMANDS:
                        self._processed_ids.add(eid)
                        continue
                    if self._is_debounced(cmd):
                        self._processed_ids.add(eid)
                        continue
                    await self.handle_command(cmd, args, raw=content)
                    self._processed_ids.add(eid)
                self._inbox_path.write_text("", encoding="utf-8")
                self._save_processed_ids()
        except TimeoutError as exc:
            logger.warning("Inbox lock: %s", exc)

    def _is_debounced(self, cmd: str) -> bool:
        now = time.monotonic()
        last = self._debounce.get(cmd, 0)
        if now - last < 5.0:
            return True
        self._debounce[cmd] = now
        return False

    async def handle_command(self, cmd: str, args: list[str], raw: str = "") -> None:
        handlers = {
            "status": self._cmd_status,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "risk": self._cmd_risk,
            "trades": self._cmd_trades,
            "agents": self._cmd_agents,
            "regime": self._cmd_regime,
            "backtest": self._cmd_backtest,
            "calibrate": self._cmd_calibrate,
            "report": self._cmd_report,
            "news": self._cmd_news,
            "logs": self._cmd_logs,
            "memory": self._cmd_memory,
            "health": self._cmd_health,
            "chart": self._cmd_chart,
            "help": self._cmd_help,
            "start": self._cmd_bot_online,
        }
        handler = handlers.get(cmd)
        if handler is None:
            if cmd:
                await self._reply_embed("Commande inconnue", f"`{raw}`\nTape `!help`", COLOR_GREY)
            return
        await handler(args)

    async def _reply_embed(
        self,
        title: str,
        description: str,
        color: int,
        fields: list[dict] | None = None,
    ) -> None:
        await self.notifier.send_embed(
            title=title,
            description=description,
            color=color,
            fields=fields or [],
            channel="commands",
        )

    async def _cmd_bot_online(self, args: list[str]) -> None:
        await self._reply_embed(
            "Gold Sniper en ligne",
            "Moteur actif — commandes opérationnelles disponibles.\n`!help` pour la liste.",
            COLOR_GREEN,
        )

    async def _cmd_status(self, args: list[str]) -> None:
        data = self.blackboard.get_all()
        meta = data.get("meta", {})
        market = data.get("market", {})
        orch = data.get("orchestrator", {})
        control = data.get("control", {})
        daily = data.get("daily_stats", {})
        account = meta.get("account_info") or {}
        active_count = len(data.get("active_trades", {}) or {})
        paused = bool(control.get("paused"))
        mode = "PAPER" if not config.LIVE_MODE else "LIVE"
        pnl_day = float(daily.get("realized_pnl", 0) or 0) + float(daily.get("floating_pnl", 0) or 0)
        if mode == "PAPER":
            color = COLOR_GOLD
        elif pnl_day >= 0:
            color = COLOR_GREEN
        else:
            color = COLOR_RED

        cf_url = meta.get("cloudflare_url") or "—"
        from core.mt5_bridge import bridge
        mt5_ok = bridge.connected

        fields = [
            {"name": "Mode", "value": mode, "inline": True},
            {"name": "État", "value": "PAUSE" if paused else "ACTIF", "inline": True},
            {"name": "Session", "value": str(market.get("session", "?")), "inline": True},
            {"name": "Régime", "value": str(market.get("regime", "?")), "inline": True},
            {"name": "Stratégie", "value": str(orch.get("strategy", "?")), "inline": True},
            {"name": "Score", "value": f"{float(orch.get('final_score', 0)):.0f}/100", "inline": True},
            {"name": "Décision", "value": str(orch.get("decision", "N/A")), "inline": True},
            {"name": "Equity", "value": f"{float(account.get('equity', 0)):.2f}$", "inline": True},
            {"name": "P&L jour", "value": f"{pnl_day:+.2f}$", "inline": True},
            {"name": "Trades ouverts", "value": str(active_count), "inline": True},
            {"name": "MT5", "value": "✅" if mt5_ok else "❌", "inline": True},
            {"name": "Cloudflare", "value": "✅" if cf_url.startswith("https://") else "❌", "inline": True},
        ]
        await self._reply_embed("STATUS", meta.get("state", "UNKNOWN"), color, fields)

    async def _cmd_pause(self, args: list[str]) -> None:
        async with self.blackboard._lock:
            control = self.blackboard._data.setdefault("control", {})
            control.update({
                "paused": True,
                "paused_at": datetime.now(timezone.utc).isoformat(),
                "pause_reason": "DISCORD_PAUSE",
            })
            self.blackboard._data["trade_signals"] = {}
            self.blackboard._data.setdefault("orchestrator", {})["pending_signal"] = None
        await self._reply_embed(
            "Pause",
            "⏸️ Nouveaux trades suspendus — positions ouvertes conservées",
            COLOR_GOLD,
        )

    async def _cmd_resume(self, args: list[str]) -> None:
        async with self.blackboard._lock:
            control = self.blackboard._data.setdefault("control", {})
            control.update({
                "paused": False,
                "resumed_at": datetime.now(timezone.utc).isoformat(),
                "pause_reason": None,
                "memory_pause": False,
                "consecutive_losses": 0,
                "risk_manager_pause_reset": True,
            })
        await self._reply_embed("Reprise", "▶️ Reprise active — système en mode trading", COLOR_GREEN)

    async def _cmd_risk(self, args: list[str]) -> None:
        control = self.blackboard.get_all().get("control", {})
        if not args:
            r = control.get("live_risk_pct") or control.get("risk_pct_per_trade", config.RISK_PCT_PER_TRADE)
            await self._reply_embed("Risque", f"Actuel: {r}% — usage: `!risk 0.5`", COLOR_BLUE)
            return
        try:
            new_risk = float(args[0])
            if not 0.1 <= new_risk <= 2.0:
                raise ValueError
        except ValueError:
            await self._reply_embed("Risque", "Valeur invalide (0.1 à 2.0)", COLOR_RED)
            return
        import config as cfg
        cfg.RISK_PCT_PER_TRADE = new_risk
        async with self.blackboard._lock:
            c = self.blackboard._data.setdefault("control", {})
            c["live_risk_pct"] = new_risk
            c["risk_pct_per_trade"] = new_risk
            c["risk_updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._reply_embed("Risque", f"⚙️ Risque modifié : {new_risk}% par trade", COLOR_GREEN)

    async def _cmd_trades(self, args: list[str]) -> None:
        active = self.blackboard.get_all().get("active_trades", {}) or {}
        if not active:
            await self._reply_embed("Trades", "Aucune position ouverte", COLOR_GREY)
            return
        fields = []
        for ticket, trade in active.items():
            fields.append({
                "name": f"#{ticket}",
                "value": (
                    f"{trade.get('type')} entry={trade.get('entry_price')} "
                    f"SL={trade.get('current_sl')} TP1={trade.get('tp1')}"
                )[:1024],
                "inline": False,
            })
        await self._reply_embed("Positions ouvertes", f"{len(active)} trade(s)", COLOR_BLUE, fields)

    async def _cmd_agents(self, args: list[str]) -> None:
        fields = []
        for i in range(1, 8):
            agent_id = f"agent_{i}"
            agent = self.blackboard.get_agent(agent_id)
            score = float(agent.get("score", 0) or 0)
            if score >= 80:
                color_note = "🟢"
            elif score >= 60:
                color_note = "🟠"
            else:
                color_note = "🔴"
            fields.append({
                "name": f"{color_note} {agent_id}",
                "value": (
                    f"Score: {score:.0f}/100 | {agent.get('direction', '—')}\n"
                    f"{str(agent.get('reason', ''))[:80]}"
                ),
                "inline": False,
            })
        await self._reply_embed("Agents", "7 agents + risk", COLOR_GOLD, fields)

    async def _cmd_regime(self, args: list[str]) -> None:
        market = self.blackboard.get_market()
        orch = self.blackboard.get_all().get("orchestrator", {})
        await self._reply_embed(
            "Régime & stratégie",
            f"Session: {market.get('session', '?')}",
            COLOR_GOLD,
            [
                {"name": "Régime", "value": str(market.get("regime", "?")), "inline": True},
                {"name": "Stratégie", "value": str(orch.get("strategy", "?")), "inline": True},
                {"name": "Seuil", "value": str(orch.get("threshold", "?")), "inline": True},
            ],
        )

    async def _cmd_backtest(self, args: list[str]) -> None:
        await self._reply_embed("Backtest", "⏳ Backtest lancé sur 7 jours...", COLOR_BLUE)
        try:
            engine_path = Path("backtesting/backtest_engine.py")
            if not engine_path.exists():
                await self._reply_embed("Backtest", "backtest_engine.py introuvable", COLOR_RED)
                return
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(engine_path),
                "--limit",
                "500",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120.0)
            out_str = stdout.decode("utf-8", errors="replace")[-800:]
            await self._reply_embed("Backtest terminé", out_str or "OK", COLOR_GREEN)
        except asyncio.TimeoutError:
            await self._reply_embed("Backtest", "Timeout (>120s)", COLOR_RED)
        except Exception as exc:
            await self._reply_embed("Backtest", str(exc), COLOR_RED)

    async def _cmd_calibrate(self, args: list[str]) -> None:
        import sqlite3
        db_path = Path("data/memory.db")
        count = 0
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute("SELECT COUNT(*) FROM trades").fetchone()
                count = int(row[0]) if row else 0
            finally:
                conn.close()
        if count < 50 and (not args or args[0].lower() != "confirm"):
            await self._reply_embed(
                "Calibration",
                f"❌ Calibration impossible — {count}/50 trades disponibles",
                COLOR_RED,
            )
            return
        if args and args[0].lower() == "confirm":
            try:
                from utils.weight_calibrator import recalibrate_weights
                weights = recalibrate_weights()
                await self._reply_embed("Calibration", f"Poids appliqués: {weights}", COLOR_GREEN)
            except Exception as exc:
                await self._reply_embed("Calibration", str(exc), COLOR_RED)
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "utils/weight_calibrator.py",
                "--dry-run",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path(".").resolve()),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            text = (stdout or stderr).decode("utf-8", errors="replace")[-1500:]
            await self._reply_embed(
                "Calibration (dry-run)",
                text + "\n\nTape `!calibrate confirm` pour appliquer",
                COLOR_GOLD,
            )
        except Exception as exc:
            try:
                from utils.weight_calibrator import recalibrate_weights
                weights = recalibrate_weights()
                await self._reply_embed(
                    "Calibration",
                    f"Poids proposés: {weights}\n`!calibrate confirm` pour appliquer",
                    COLOR_GOLD,
                )
            except Exception as exc2:
                await self._reply_embed("Calibration", str(exc2), COLOR_RED)

    async def _cmd_report(self, args: list[str]) -> None:
        await send_scheduled_report(self.blackboard, "daily", self.notifier)
        await self._reply_embed("Rapport", "Rapport journalier envoyé dans #reports", COLOR_GREEN)

    async def _cmd_news(self, args: list[str]) -> None:
        try:
            from agents.agent_6_sentinelle import fetch_news_calendar, is_gold_relevant_event, _ensure_utc
            events = await fetch_news_calendar()
            now = _ensure_utc(datetime.now(timezone.utc))
            horizon = now + timedelta(hours=24)
            upcoming = [
                e for e in events
                if e["impact"] in {"HIGH", "MEDIUM", "LOW"}
                and now <= _ensure_utc(e["time"]) <= horizon
                and is_gold_relevant_event(e)
            ]
            if not upcoming:
                await self._reply_embed("News", "Aucune annonce pertinente 24h", COLOR_GREY)
                return
            fields = []
            high_count = 0
            for e in upcoming[:15]:
                imp = e["impact"]
                emoji = "🔴" if imp == "HIGH" else ("🟡" if imp == "MEDIUM" else "🟢")
                if imp == "HIGH":
                    high_count += 1
                t = _ensure_utc(e["time"]).strftime("%H:%M UTC")
                fields.append({
                    "name": f"{emoji} {e['name'][:40]}",
                    "value": f"{e['currency']} — {t}",
                    "inline": False,
                })
            desc = f"{high_count} événement(s) HIGH sur 24h" if high_count else "Calendrier 24h"
            await self._reply_embed("News 24h", desc, COLOR_GOLD, fields)
        except Exception as exc:
            await self._reply_embed("News", str(exc), COLOR_RED)

    async def _cmd_logs(self, args: list[str]) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        paths = [
            Path(f"logs/gold_sniper_{today}.jsonl"),
            Path("logs/decision_log.jsonl"),
        ]
        sent_names: list[str] = []
        for path in paths:
            if path.exists() and path.stat().st_size > 0:
                if await self.notifier.send_document(
                    path, caption=f"Logs {path.name}", channel="reports"
                ):
                    sent_names.append(path.name)
        if sent_names:
            await self._reply_embed(
                "Logs",
                f"Fichier(s) envoyé(s) dans #reports : {', '.join(sent_names)}",
                COLOR_GREEN,
            )
        else:
            await self._reply_embed("Logs", "Aucun fichier de log trouvé", COLOR_GREY)

    async def _cmd_memory(self, args: list[str]) -> None:
        import sqlite3
        db = Path("data/memory.db")
        if not db.exists():
            await self._reply_embed("Memory", "memory.db absent", COLOR_GREY)
            return
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            total = conn.execute("SELECT COUNT(*) c FROM trades").fetchone()["c"]
            wins = conn.execute(
                "SELECT COUNT(*) c FROM trades WHERE pnl > 0"
            ).fetchone()["c"]
            winrate = (wins / total * 100) if total else 0
            errs = conn.execute(
                "SELECT pattern_type, frequency FROM error_patterns ORDER BY frequency DESC LIMIT 3"
            ).fetchall()
            cal = conn.execute(
                "SELECT MAX(calibrated_at) t FROM calibration_history"
            ).fetchone()
            last_cal = cal["t"] if cal and cal["t"] else "Jamais"
        except sqlite3.Error:
            total, winrate, errs, last_cal = 0, 0, [], "N/A"
        finally:
            conn.close()
        err_lines = "\n".join(f"• {e['pattern_type']}: {e['frequency']}" for e in errs) or "—"
        await self._reply_embed(
            "Mémoire SQLite",
            f"{total} trades | Winrate global {winrate:.0f}%",
            COLOR_BLUE,
            [
                {"name": "Top erreurs", "value": err_lines[:1024], "inline": False},
                {"name": "Dernière calibration", "value": str(last_cal), "inline": False},
            ],
        )

    async def _cmd_health(self, args: list[str]) -> None:
        from core.mt5_bridge import bridge
        data = self.blackboard.get_all()
        meta = data.get("meta", {})
        now = datetime.now(timezone.utc)
        stale_agents = []
        for i in range(1, 8):
            agent = self.blackboard.get_agent(f"agent_{i}")
            ts = agent.get("timestamp") or agent.get("updated_at")
            if ts:
                try:
                    if isinstance(ts, str):
                        at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    else:
                        at = ts
                    if (now - at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at).total_seconds() > 300:
                        stale_agents.append(f"agent_{i}")
                except Exception:
                    stale_agents.append(f"agent_{i}")
            else:
                stale_agents.append(f"agent_{i}")

        last_candle = data.get("market_data", {}).get("last_candle_time", "?")
        cf = meta.get("cloudflare_url", "—")
        drive = data.get("drive_sync", {}).get("last_success", "?")
        boot = meta.get("boot_time", "?")
        from utils.system_metrics import format_ram_cpu_health

        ram_cpu = format_ram_cpu_health()

        await self._reply_embed(
            "Health check",
            "Diagnostic système",
            COLOR_BLUE,
            [
                {"name": "MT5", "value": "✅" if bridge.connected else "❌", "inline": True},
                {"name": "Agents stale (>5m)", "value": ", ".join(stale_agents) or "aucun", "inline": False},
                {"name": "Dernière bougie", "value": str(last_candle), "inline": True},
                {"name": "Cloudflare", "value": "✅" if str(cf).startswith("https://") else "❌", "inline": True},
                {"name": "Drive sync", "value": str(drive), "inline": True},
                {"name": "RAM / CPU", "value": ram_cpu, "inline": True},
                {"name": "Boot", "value": str(boot), "inline": False},
            ],
        )

    async def _cmd_chart(self, args: list[str]) -> None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            await self._reply_embed("Chart", "matplotlib requis", COLOR_RED)
            return
        candle_store = (
            self.blackboard.get_all()
            .get("market_data", {})
            .get("candles", {})
        )
        candles = candle_store.get("1m") or candle_store.get("15m") or []
        tf_label = "1M" if candle_store.get("1m") else "15M"
        if not candles:
            await self._reply_embed(
                "Chart",
                "Pas de bougies en cache (attendre le warmup ou `!start`)",
                COLOR_GREY,
            )
            return
        recent = list(candles)[-50:]
        closes = [float(c.get("close", c.get("c", 0))) for c in recent]
        if not closes:
            await self._reply_embed("Chart", "Données invalides", COLOR_RED)
            return
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(closes, color="#D4A843", linewidth=1.5)
        ax.set_title(f"{config.MT5_SYMBOL} — 50 dernieres bougies {tf_label}")
        ax.grid(True, alpha=0.3)
        out = Path("data/chart_tmp.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=100, bbox_inches="tight")
        plt.close(fig)
        await self.notifier.send_document(
            out, caption=f"Chart {config.MT5_SYMBOL} {tf_label}", channel="commands"
        )
        await self._reply_embed("Chart", f"Graphique {tf_label} envoye.", COLOR_GREEN)
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass

    async def _cmd_help(self, args: list[str]) -> None:
        fields = [
            {"name": cmd, "value": desc, "inline": False}
            for cmd, desc in COMMANDS_HELP.items()
        ]
        fields.append({
            "name": "PC Manager",
            "value": "`!start` `!kill` `!restart` `!pc_status`",
            "inline": False,
        })
        await self._reply_embed("Aide", "Commandes Gold Sniper", COLOR_GOLD, fields)


async def discord_command_loop(
    blackboard,
    on_restart: Callable[[], None] | None = None,
) -> None:
    commander = DiscordCommander(blackboard, on_restart=on_restart)
    await commander.run_forever()
