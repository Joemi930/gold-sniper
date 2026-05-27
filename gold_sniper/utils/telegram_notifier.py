import asyncio
import ssl
from datetime import datetime
from html import escape
import os
from pathlib import Path

import aiohttp

from utils.logger import get_logger
import config

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_DOC_API = "https://api.telegram.org/bot{token}/sendDocument"

logger = get_logger()


class TelegramNotifier:
    """Envoie des alertes Telegram pour les événements importants."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        verify_ssl: bool = True,
        allow_insecure_ssl_fallback: bool = True,
    ):
        self.token = token
        self.chat_id = chat_id
        self.url = TELEGRAM_API.format(token=token)
        self.verify_ssl = verify_ssl
        self.allow_insecure_ssl_fallback = allow_insecure_ssl_fallback
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Ouvre une session HTTP réutilisable."""
        if not self._session or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._build_ssl_context(self.verify_ssl))
            self._session = aiohttp.ClientSession(connector=connector)

    async def stop(self) -> None:
        """Ferme proprement la session HTTP."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def send(self, message: str, parse_mode: str | None = "HTML") -> bool:
        """Envoie un message Telegram. Retourne True si succès."""
        if not self.token or not self.chat_id:
            return False

        owns_session = False
        if not self._session or self._session.closed:
            await self.start()
            owns_session = True

        try:
            assert self._session is not None
            return await self._post_message(self._session, message, parse_mode)
        except (aiohttp.ClientConnectorCertificateError, ssl.SSLError) as exc:
            if not self.verify_ssl or not self.allow_insecure_ssl_fallback:
                logger.warning(f"Telegram SSL échoué: {exc}")
                return False
            logger.warning("Telegram SSL local invalide, retry avec fallback TLS contrôlé.")
            await self.stop()
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                return await self._post_message(session, message, parse_mode)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Telegram notification échouée (non-bloquant): {exc}")
            return False
        finally:
            if owns_session:
                await self.stop()

    async def _post_message(
        self,
        session: aiohttp.ClientSession,
        message: str,
        parse_mode: str | None,
    ) -> bool:
        """Poste le message via une session aiohttp existante."""
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        async with session.post(
            self.url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                logger.debug("Telegram notification envoyée.")
                return True
            body = await resp.text()
            logger.warning(f"Telegram réponse {resp.status}: {body[:160]}")
            return False

    async def send_document(self, file_path: str | Path, caption: str = "") -> bool:
        """Envoie un fichier document (ex: log) via Telegram."""
        if not self.token or not self.chat_id:
            return False

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            logger.warning(f"Telegram send_document: fichier introuvable {path}")
            return False

        owns_session = False
        if not self._session or self._session.closed:
            await self.start()
            owns_session = True

        url = TELEGRAM_DOC_API.format(token=self.token)
        try:
            assert self._session is not None
            data = aiohttp.FormData()
            data.add_field("chat_id", self.chat_id)
            if caption:
                data.add_field("caption", caption)
            data.add_field("document", open(path, "rb"), filename=path.name)
            
            async with self._session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    logger.debug(f"Document {path.name} envoyé avec succès.")
                    return True
                body = await resp.text()
                logger.warning(f"Telegram send_document erreur {resp.status}: {body[:160]}")
                return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Telegram send_document échoué (non-bloquant): {exc}")
            return False
        finally:
            if owns_session:
                await self.stop()

    def _build_ssl_context(self, verify_ssl: bool) -> ssl.SSLContext | bool:
        """Construit un contexte TLS fiable, avec certifi si disponible."""
        if not verify_ssl:
            return False
        try:
            import certifi

            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()

    async def notify_signal(
        self,
        score: float,
        direction: str,
        stars: int,
        agent_breakdown: dict,
        regime: str,
    ) -> None:
        """Alerte quand l'orchestrateur valide un signal."""
        star_emoji = "⭐" * stars
        direction_emoji = "📈" if direction == "LONG" else "📉"
        agents_text = "\n".join(
            f"  {'✅' if data.get('hf') else '❌'} {escape(agent.upper())}: {float(data.get('score', 0)):.0f}/100"
            for agent, data in agent_breakdown.items()
        )

        message = (
            f"🎯 <b>SIGNAL GOLD SNIPER</b> {direction_emoji}\n"
            f"{'─' * 30}\n"
            f"⭐ Score : <b>{score:.1f}/100</b> {star_emoji}\n"
            f"📊 Direction : <b>{escape(direction)}</b>\n"
            f"🌍 Régime : {escape(regime)}\n"
            f"⏰ Heure : {datetime.utcnow().strftime('%H:%M:%S')} UTC\n"
            f"{'─' * 30}\n"
            f"<b>Agents :</b>\n{agents_text}"
        )
        await self.send(message)

    async def notify_trade_opened(
        self,
        direction: str,
        entry: float,
        sl: float,
        tp1: float,
        lot: float,
    ) -> None:
        """Alerte quand un trade est ouvert."""
        rr = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
        message = (
            f"{'🟢' if direction == 'LONG' else '🔴'} <b>TRADE OUVERT</b>\n"
            f"{'─' * 30}\n"
            f"Direction : <b>{escape(direction)}</b>\n"
            f"Entrée  : <code>{entry:.2f}</code>\n"
            f"Stop L  : <code>{sl:.2f}</code>\n"
            f"Take P  : <code>{tp1:.2f}</code>\n"
            f"Lot     : <b>{lot:.2f}</b>\n"
            f"R:R     : <b>1:{rr:.1f}</b>"
        )
        await self.send(message)

    async def notify_trade_closed(self, direction: str, pnl: float, reason: str) -> None:
        """Alerte quand un trade est clôturé."""
        emoji = "✅" if pnl > 0 else "❌"
        message = (
            f"{emoji} <b>TRADE FERMÉ</b>\n"
            f"{'─' * 30}\n"
            f"Direction : {escape(direction)}\n"
            f"P&amp;L : <b>{'+' if pnl > 0 else ''}{pnl:.2f}$</b>\n"
            f"Raison : {escape(reason)}"
        )
        await self.send(message)

    async def notify_daily_report(self, trades: int, wins: int, pnl: float, equity: float) -> None:
        """Envoie le rapport journalier analytique."""
        winrate = (wins / trades * 100) if trades > 0 else 0
        message = (
            f"📊 <b>RAPPORT JOURNALIER</b>\n"
            f"{'─' * 30}\n"
            f"Trades     : {trades}\n"
            f"Wins       : {wins} ({winrate:.0f}%)\n"
            f"P&amp;L Journée: <b>{'+' if pnl > 0 else ''}{pnl:.2f}$</b>\n"
            f"Equity     : <b>{equity:.2f}$</b>"
        )
        await self.send(message)

    async def notify_exceptional_setup(
        self,
        score: float,
        direction: str,
        agent_breakdown: dict,
    ) -> None:
        """Alerte pour setup ≥92 bloqué par la limite de trades."""
        agents_text = "\n".join(
            f"  {escape(agent.upper())}: {float(data.get('score', 0)):.0f}/100"
            for agent, data in agent_breakdown.items()
        )
        message = (
            f"🔔 <b>SETUP EXCEPTIONNEL DÉTECTÉ</b>\n"
            f"⚠️ Limite de trades atteinte — décision manuelle requise\n"
            f"{'─' * 30}\n"
            f"Score : <b>{score:.1f}/100</b> 🌟\n"
            f"Direction : <b>{escape(direction)}</b>\n"
            f"<b>Agents :</b>\n{agents_text}"
        )
        await self.send(message)

    async def _notify_news_alert_legacy(self, event_name: str, impact: str, minutes_to: int) -> None:
        """Alerte avant une news économique bloquante."""
        emoji = "🔴" if impact == "HIGH" else "🟠"
        message = (
            f"{emoji} <b>NEWS ALERT</b>\n"
            f"{'─' * 30}\n"
            f"Événement : <b>{escape(event_name)}</b>\n"
            f"Impact    : <b>{escape(impact)}</b>\n"
            f"Dans      : {minutes_to} minutes\n"
            f"→ Trading bloqué"
        )
        await self.send(message)

    async def notify_news_alert(
        self,
        event_name: str,
        impact: str,
        minutes_to: int,
        gold_impact: str | None = None,
    ) -> None:
        """Alerte avant une news economique bloquante avec impact attendu sur l'or."""
        emoji = "RED" if impact == "HIGH" else "ORANGE"
        message = (
            f"{emoji} <b>NEWS ALERT</b>\n"
            f"{'-' * 30}\n"
            f"Evenement : <b>{escape(event_name)}</b>\n"
            f"Impact    : <b>{escape(impact)}</b>\n"
            f"Dans      : {minutes_to} minutes\n"
            f"Or        : {{escape(gold_impact or f'volatilite {{config.MT5_SYMBOL}} attendue')}}\n"
            f"-> Trading bloque"
        )
        await self.send(message)

    async def notify_news_result(
        self,
        event_name: str,
        actual: str,
        forecast: str,
        gold_impact: str | None = None,
    ) -> None:
        """Alerte apres publication du resultat macro."""
        message = (
            f"NEWS RESULT\n"
            f"{'-' * 30}\n"
            f"Evenement : <b>{escape(event_name)}</b>\n"
            f"Reel      : <b>{escape(actual)}</b>\n"
            f"Prevision : <b>{escape(forecast)}</b>\n"
            f"Or        : {{escape(gold_impact or f'surveiller la reaction {{config.MT5_SYMBOL}}')}}"
        )
        await self.send(message)

    async def notify_news_feed_down(self) -> None:
        """Alerte quand le feed news est indisponible."""
        message = (
            "⚠️ <b>NEWS FEED DOWN</b>\n"
            "Le scraper de calendrier économique ne répond plus.\n"
            "Mode ASSUME HOSTILE activé.\n"
            "→ Aucun trade jusqu'au retour du feed."
        )
        await self.send(message)

    async def notify_risk_alert(self, alert_type: str, details: str) -> None:
        """Alerte de risque critique."""
        message = (
            f"🚨 <b>ALERTE RISQUE : {escape(alert_type)}</b>\n"
            f"{'─' * 30}\n"
            f"{escape(details)}"
        )
        await self.send(message)

    async def notify_consecutive_losses(self, count: int) -> None:
        """Alerte après plusieurs pertes consécutives."""
        message = (
            f"⛔ <b>{count} TRADES PERDANTS CONSÉCUTIFS</b>\n"
            "Pause forcée de 2 heures activée.\n"
            "Rapport diagnostic en cours..."
        )
        await self.send(message)


def _notifier_from_config() -> TelegramNotifier:
    """Construit un notifier à partir de config.py."""
    from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

    return TelegramNotifier(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)


async def send_telegram_notification(blackboard, message: str) -> None:
    """
    Wrapper non-bloquant compatible avec l'ancien code.
    Utilise la config du Blackboard si elle existe, sinon config.py.
    """
    try:
        notifications = blackboard.read_sync("notifications") if blackboard else {}
        if notifications and not notifications.get("telegram_enabled", False):
            return

        token = notifications.get("telegram_bot_token") if notifications else None
        chat_id = notifications.get("telegram_chat_id") if notifications else None

        if token and chat_id:
            notifier = TelegramNotifier(token, chat_id)
        else:
            notifier = _notifier_from_config()

        await notifier.send(message, parse_mode=None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"Telegram wrapper échoué (non-bloquant): {exc}")


async def send_eod_report(blackboard) -> None:
    """Envoie le rapport de fin de journée depuis le Blackboard."""
    try:
        daily = blackboard.read_sync("daily_stats") or {}
        meta = blackboard.read_sync("meta") or {}

        realized = daily.get("realized_pnl", 0.0)
        floating = daily.get("floating_pnl", 0.0)
        total_pnl = realized + floating
        trades = meta.get("daily_trade_count", 0)
        wins = daily.get("winning_trades", daily.get("wins", 0))
        equity = (meta.get("account_info") or {}).get("equity", 0.0)

        notifier = _notifier_from_config()
        await notifier.notify_daily_report(trades=trades, wins=wins, pnl=total_pnl, equity=equity)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"EOD Telegram report échoué: {exc}")
