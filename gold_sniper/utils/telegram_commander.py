import asyncio
import ssl
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import aiohttp

from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from utils.emergency_shutdown import emergency_shutdown
from utils.logger import get_logger
from utils.telegram_notifier import TelegramNotifier, _notifier_from_config


TELEGRAM_UPDATES_API = "https://api.telegram.org/bot{token}/getUpdates"

COMMANDS = {
    "/start": "Demarrage et presentation du bot",
    "/status": "Etat complet du systeme",
    "/pause": "Suspendre les nouveaux trades",
    "/resume": "Reprendre les nouveaux trades",
    "/restart": "Redemarrage moteur sans couper MT5",
    "/kill": "Arret d'urgence + fermeture positions",
    "/risk": "Modifier le risque live, ex: /risk 0.5",
    "/trades": "Positions ouvertes et PnL",
    "/agents": "Scores et etats des agents",
    "/regime": "Regime marche et strategie active",
    "/backtest": "Backtest rapide",
    "/calibrate": "Calibration des poids",
    "/news": "Prochaines news eco (24h)",
    "/logs": "Telecharger les logs de la session",
    "/help": "Liste des commandes",
}


class TelegramCommander:
    """Telecommande Telegram complete du moteur Gold Sniper."""

    def __init__(
        self,
        blackboard,
        notifier: TelegramNotifier | None = None,
        on_kill: Callable[[], None] | None = None,
        on_restart: Callable[[], None] | None = None,
    ) -> None:
        self.blackboard = blackboard
        self.notifier = notifier or _notifier_from_config()
        self.on_kill = on_kill
        self.on_restart = on_restart
        self.logger = get_logger()
        self._last_update_id: int | None = None
        # Fence anti-/kill persistant : update_id connu au boot, ignoré à la lecture
        self._boot_update_id: int = -1

    async def _fetch_boot_offset(self, session: aiohttp.ClientSession, url: str) -> None:
        """
        Récupère le dernier update_id connu AVANT de commencer à écouter.
        Tous les messages dont update_id <= _boot_update_id seront ignorés,
        ce qui empêche la ré-exécution de commandes obsolètes (/kill, etc.)
        envoyées pendant que le bot était éteint.
        """
        try:
            async with session.get(url, params={"limit": 100}) as resp:
                payload = await resp.json(content_type=None)
            updates = payload.get("result", [])
            if updates:
                self._boot_update_id = max(int(u.get("update_id", 0)) for u in updates)
                # Positionner l'offset pour que Telegram ne renvoie plus ces anciens messages
                self._last_update_id = self._boot_update_id
                self.logger.info(
                    f"Telegram commander — fence boot activée : "
                    f"update_id <= {self._boot_update_id} ignorés."
                )
            else:
                self.logger.info("Telegram commander — aucun message en attente au boot.")
        except Exception as exc:
            self.logger.warning(f"Telegram commander — impossible de lire la fence boot : {exc}")

    async def run_forever(self) -> None:
        notifications = self.blackboard.read_sync("notifications") if self.blackboard else {}
        token = (notifications or {}).get("telegram_bot_token") or TELEGRAM_TOKEN
        chat_id = (notifications or {}).get("telegram_chat_id") or TELEGRAM_CHAT_ID

        if not token or not chat_id:
            self.logger.warning("Telegram commander desactive: token/chat_id manquant.")
            return

        url = TELEGRAM_UPDATES_API.format(token=token)
        timeout = aiohttp.ClientTimeout(total=35)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_build_ssl_context()), timeout=timeout) as session:
            # ── ÉTAPE 1 : Flush les anciens messages avant d'écouter ───────────
            await self._fetch_boot_offset(session, url)

            self.logger.info("Telegram commander demarre.")
            while not self.blackboard.kill_event.is_set():
                try:
                    params: dict[str, Any] = {"timeout": 25, "allowed_updates": '["message","edited_message"]'}
                    if self._last_update_id is not None:
                        params["offset"] = self._last_update_id + 1
                    async with session.get(url, params=params) as response:
                        payload = await response.json(content_type=None)
                    for update in payload.get("result", []):
                        uid = int(update.get("update_id", 0))
                        self._last_update_id = uid
                        # Fence : ignorer tout message antérieur au boot
                        if uid <= self._boot_update_id:
                            continue
                        await self.handle_update(update, authorized_chat_id=chat_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.logger.warning(f"Telegram commander erreur non bloquante: {exc}")
                    await asyncio.sleep(5.0)

    async def handle_update(self, update: dict[str, Any], authorized_chat_id: str | None = None) -> str | None:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip()
        if not text:
            return None

        expected_chat = authorized_chat_id or TELEGRAM_CHAT_ID
        if expected_chat and str(chat.get("id")) != str(expected_chat):
            return None

        parts = text.split()
        command = parts[0].split("@", maxsplit=1)[0].lower()
        args = parts[1:]
        handlers = {
            "/start": self._cmd_start,
            "/status": self._cmd_status,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/restart": self._cmd_restart,
            "/kill": self._cmd_kill,
            "/risk": self._cmd_risk,
            "/trades": self._cmd_trades,
            "/agents": self._cmd_agents,
            "/regime": self._cmd_regime,
            "/calibrate": self._cmd_calibrate,
            "/report": self._cmd_report,
            "/news": self._cmd_news,
            "/logs": self._cmd_logs,
            "/help": self._cmd_help,
        }

        handler = handlers.get(command)
        if handler is None:
            if command.startswith("/"):
                return await self._send(f"Commande inconnue: {command}\nTaper /help")
            return None
        return await handler(args)

    async def _cmd_start(self, args: list[str]) -> str:
        """
        Commande /start — repond independamment de l'etat du moteur.
        Sert a verifier que le bot est actif et a decouvrir les commandes.
        """
        text = (
            "GOLD SNIPER V3 EN LIGNE\n"
            "Bot de trading algorithmique sur XAUUSD (JustMarkets-Demo3).\n"
            "\n"
            "Commandes disponibles :\n"
            "/status  — Etat du systeme\n"
            "/trades  — Positions ouvertes\n"
            "/agents  — Scores agents\n"
            "/news    — Actualites economiques 24h\n"
            "/report  — Rapport journalier\n"
            "/pause   — Suspendre les trades\n"
            "/resume  — Reprendre les trades\n"
            "/help    — Toutes les commandes"
        )
        return await self._send(text)

    async def _cmd_status(self, args: list[str]) -> str:
        data = self.blackboard.get_all()
        meta = data.get("meta", {})
        market = data.get("market", {})
        orch = data.get("orchestrator", {})
        control = data.get("control", {})
        daily = data.get("daily_stats", {})
        active_count = len(data.get("active_trades", {}) or {})
        paused = bool(control.get("paused"))
        text = (
            "STATUS GOLD SNIPER\n"
            f"Etat: {meta.get('state', 'UNKNOWN')} | Mode: {'PAUSE' if paused else 'ACTIF'}\n"
            f"Session: {market.get('session', 'UNKNOWN')} | Regime: {market.get('regime', 'UNKNOWN')}\n"
            f"Decision: {orch.get('decision', 'N/A')} | Score: {orch.get('final_score', 0)}\n"
            f"Strategie: {orch.get('strategy', 'N/A')}\n"
            f"Trades ouverts: {active_count} | Fermes jour: {daily.get('trades_closed', 0)}\n"
            f"Risque live: {control.get('risk_pct_per_trade', 'config')}%"
        )
        return await self._send(text)

    async def _cmd_pause(self, args: list[str]) -> str:
        async with self.blackboard._lock:
            control = self.blackboard._data.setdefault("control", {})
            control.update({
                "paused": True,
                "paused_at": datetime.now(timezone.utc).isoformat(),
                "pause_reason": "TELEGRAM_PAUSE",
            })
            self.blackboard._data["trade_signals"] = {}
            self.blackboard._data.setdefault("orchestrator", {})["pending_signal"] = None
        return await self._send(
            "PAUSE ACTIVEE\n"
            "Nouveaux trades suspendus. Positions ouvertes conservees et toujours gerees."
        )

    async def _cmd_resume(self, args: list[str]) -> str:
        async with self.blackboard._lock:
            control = self.blackboard._data.setdefault("control", {})
            control.update({
                "paused": False,
                "resumed_at": datetime.now(timezone.utc).isoformat(),
                "pause_reason": None,
                "memory_pause": False,
                "memory_resumed_at": datetime.now(timezone.utc).isoformat(),
                "risk_manager_pause_reset": True,
            })
        return await self._send("REPRISE ACTIVEE\nNouveaux trades autorises.")

    async def _cmd_restart(self, args: list[str]) -> str:
        async with self.blackboard._lock:
            meta = self.blackboard._data.setdefault("meta", {})
            meta["restart_requested"] = True
            meta["restart_requested_at"] = datetime.now(timezone.utc).isoformat()
            meta["state"] = "RESTART_REQUESTED"
            self.blackboard._data["trade_signals"] = {}
        if self.on_restart:
            self.on_restart()
        return await self._send(
            "RESTART MOTEUR DEMANDE\n"
            "MT5 n'est pas coupe. Positions conservees pour recovery/gestion."
        )

    async def _cmd_kill(self, args: list[str]) -> str:
        await self._send("KILL SWITCH ACTIVE\nFermeture positions + arret urgence en cours.")
        await emergency_shutdown(self.blackboard, reason="TELEGRAM_KILL")
        if self.on_kill:
            self.on_kill()
        return "KILL SWITCH ACTIVE"

    async def _cmd_risk(self, args: list[str]) -> str:
        if not args:
            control = self.blackboard.get_all().get("control", {})
            return await self._send(f"Usage: /risk 0.5\nRisque actuel: {control.get('risk_pct_per_trade', 'config')}%")
        try:
            new_risk = float(args[0])
            if not 0.1 <= new_risk <= 3.0:
                raise ValueError
        except ValueError:
            return await self._send("Valeur invalide. Exemple: /risk 0.5 (entre 0.1 et 3.0)")

        import config
        config.RISK_PCT_PER_TRADE = new_risk
        async with self.blackboard._lock:
            self.blackboard._data.setdefault("control", {})["risk_pct_per_trade"] = new_risk
            self.blackboard._data["control"]["risk_updated_at"] = datetime.now(timezone.utc).isoformat()
        return await self._send(f"RISQUE LIVE MODIFIE: {new_risk:.2f}% par trade")

    async def _cmd_trades(self, args: list[str]) -> str:
        active = self.blackboard.get_all().get("active_trades", {}) or {}
        if not active:
            return await self._send("Aucune position Gold Sniper suivie actuellement.")
        lines = ["POSITIONS SUIVIES"]
        for ticket, trade in active.items():
            lines.append(
                f"#{ticket} {trade.get('type')} entry={trade.get('entry_price')} "
                f"SL={trade.get('current_sl')} TP1={trade.get('tp1')} TP2={trade.get('tp2')}"
            )
        return await self._send("\n".join(lines))

    async def _cmd_agents(self, args: list[str]) -> str:
        lines = ["AGENTS"]
        for i in range(1, 8):
            agent_id = f"agent_{i}"
            agent = self.blackboard.get_agent(agent_id)
            lines.append(
                f"{agent_id}: score={agent.get('score', 0)} veto={agent.get('veto', False)} "
                f"hf={agent.get('hard_filter_pass', True)} reason={str(agent.get('reason', ''))[:60]}"
            )
        risk = self.blackboard.get_agent("risk_manager")
        lines.append(f"risk_manager: veto={risk.get('veto', False)} reason={risk.get('reason', '')}")
        return await self._send("\n".join(lines))

    async def _cmd_regime(self, args: list[str]) -> str:
        market = self.blackboard.get_market()
        orch = self.blackboard.get_all().get("orchestrator", {})
        return await self._send(
            "REGIME & STRATEGIE\n"
            f"Regime: {market.get('regime', 'UNKNOWN')}\n"
            f"Session: {market.get('session', 'UNKNOWN')}\n"
            f"Strategie: {orch.get('strategy', 'N/A')}\n"
            f"DXY: {market.get('dxy_bias', 'N/A')} | US10Y: {market.get('us10y_direction', 'N/A')}"
        )

    async def _cmd_backtest(self, args: list[str]) -> str:
        try:
            await self._send("⏳ Lancement du backtest rapide (synthétique 180 bougies)...")
            import sys
            import subprocess
            from pathlib import Path
            engine_path = Path("backtesting/backtest_engine.py")
            if engine_path.exists():
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, str(engine_path), "--synthetic", "--limit", "180",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45.0)
                out_str = stdout.decode("utf-8", errors="replace")
                
                path = Path("logs/backtests/backtest_results.jsonl")
                res_text = ""
                if path.exists() and path.stat().st_size > 0:
                    last = path.read_text(encoding="utf-8").strip().splitlines()[-1]
                    res_text = f"\n\nJSON:\n{last[:300]}"
                
                text = f"✅ BACKTEST TERMINÉ\n\n{out_str[-800:]}{res_text}"
            else:
                text = "Fichier backtest_engine.py introuvable."
        except asyncio.TimeoutError:
            text = "⏳ BACKTEST TIMEOUT (Annulé car > 45s)"
        except Exception as exc:
            text = f"❌ BACKTEST ECHEC: {exc}"
        return await self._send(text)

    async def _cmd_calibrate(self, args: list[str]) -> str:
        try:
            from utils.weight_calibrator import recalibrate_weights
            weights = recalibrate_weights()
            text = f"CALIBRATION TERMINEE\nPoids: {weights}" if weights else "CALIBRATION IMPOSSIBLE: historique insuffisant."
        except Exception as exc:
            text = f"CALIBRATION ECHEC: {exc}"
        return await self._send(text)

    async def _cmd_report(self, args: list[str]) -> str:
        daily = self.blackboard.get_all().get("daily_stats", {})
        meta = self.blackboard.get_all().get("meta", {})
        pnl = float(daily.get("realized_pnl", 0.0) or 0.0) + float(daily.get("floating_pnl", 0.0) or 0.0)
        text = (
            "RAPPORT JOURNALIER\n"
            f"Trades ouverts aujourd'hui: {meta.get('daily_trade_count', 0)}\n"
            f"Trades fermes: {daily.get('trades_closed', 0)}\n"
            f"PnL realise: {float(daily.get('realized_pnl', 0.0) or 0.0):+.2f}\n"
            f"PnL flottant: {float(daily.get('floating_pnl', 0.0) or 0.0):+.2f}\n"
            f"PnL total: {pnl:+.2f}"
        )
        return await self._send(text)

    async def _cmd_news(self, args: list[str]) -> str:
        try:
            from agents.agent_6_sentinelle import fetch_news_calendar, is_gold_relevant_event, _ensure_utc
            events = await fetch_news_calendar()
            now = _ensure_utc(datetime.now(timezone.utc))
            horizon = now + timedelta(hours=24)
            upcoming = [
                e for e in events 
                if e["impact"] in {"HIGH", "MEDIUM"} 
                and now <= _ensure_utc(e["time"]) <= horizon
                and is_gold_relevant_event(e)
            ]
            if not upcoming:
                return await self._send("📅 Aucune annonce HIGH/MEDIUM pour l'or dans les prochaines 24h.")
            
            lines = ["📰 ANNONCES ECONOMIQUES — 24H", "──────────────────────"]
            high_count = 0
            for e in upcoming:
                time_str = _ensure_utc(e["time"]).strftime("%Hh%M UTC")
                impact_emoji = "🔴" if e["impact"] == "HIGH" else "🟠"
                if e["impact"] == "HIGH":
                    high_count += 1
                
                impact_or = "HAUSSIER si < prevision" if e["currency"] == "USD" else "INCONNU"
                prev = str(e.get("previous", "N/A"))
                fcst = str(e.get("forecast", "N/A"))
                
                lines.append(f"{impact_emoji} {e['name']} ({e['currency']}) — {time_str}")
                lines.append(f"   Impact or : {impact_or}")
                lines.append(f"   Prevision : {fcst} | Precedent : {prev}")
                lines.append("──────────────────────")
                
            if high_count > 0:
                lines.append(f"⚠️ {high_count} evenements HIGH IMPACT aujourd'hui")
                    
            return await self._send("\n".join(lines))
        except Exception as exc:
            return await self._send(f"❌ Erreur lors de la récupération des news: {exc}")

    async def _cmd_logs(self, args: list[str]) -> str:
        """Envoie le log de decision ou le fichier log actuel."""
        try:
            from pathlib import Path
            import os
            # Chercher le fichier log d'aujourd'hui
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            possible_logs = [
                Path(f"logs/gold_sniper_{today_str}.jsonl"),
                Path("logs/decision_log.jsonl")
            ]
            
            sent = False
            for path in possible_logs:
                if path.exists() and path.stat().st_size > 0:
                    if hasattr(self.notifier, "send_document"):
                        success = await self.notifier.send_document(path, caption=f"Logs: {path.name}")
                        if success:
                            sent = True
                            
            if sent:
                return "Logs envoyés avec succès."
            else:
                return await self._send("Aucun fichier de log trouvé ou erreur d'envoi.")
        except Exception as exc:
            return await self._send(f"❌ Erreur lors de l'envoi des logs: {exc}")

    async def _cmd_help(self, args: list[str]) -> str:
        lines = ["COMMANDES DISPONIBLES"]
        lines.extend(f"{cmd} - {desc}" for cmd, desc in COMMANDS.items())
        return await self._send("\n".join(lines))

    async def _send(self, text: str) -> str:
        await self.notifier.send(text, parse_mode=None)
        return text


async def telegram_command_loop(
    blackboard,
    on_kill: Callable[[], None] | None = None,
    on_restart: Callable[[], None] | None = None,
) -> None:
    commander = TelegramCommander(blackboard, on_kill=on_kill, on_restart=on_restart)
    await commander.run_forever()


def _build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()
