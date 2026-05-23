# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.0 — TELEGRAM NOTIFIER
# ═══════════════════════════════════════════════════════════════════════════════
#
# Module utilitaire pour les notifications Telegram.
# Supporte le Markdown MT (MarkdownV2 Telegram).
# File-and-forget : n'interrompt jamais le flux principal.
#
# FIXES : Remplace le stub vide (BUG#5) par une implémentation réelle.
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
import aiohttp
from utils.logger import get_logger

logger = get_logger()


async def send_telegram_notification(blackboard, message: str) -> None:
    """
    Envoie une notification Telegram si TELEGRAM_ENABLED = True.
    N'interrompt jamais le flux principal (fire-and-forget avec try/except).
    Supports Markdown format.
    """
    try:
        notifs = blackboard.read_sync("notifications")
        if not notifs or not notifs.get("telegram_enabled"):
            return

        token = notifs.get("telegram_bot_token", "")
        chat_id = notifs.get("telegram_chat_id", "")

        if not token or not chat_id:
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"⚠️ Telegram: Réponse {resp.status} — {body[:100]}")
                else:
                    logger.debug(f"📲 Telegram notif envoyée.")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"⚠️ Telegram notification échouée (non-bloquant) : {e}")


async def send_eod_report(blackboard) -> None:
    """
    Envoie le rapport de fin de journée (End-of-Day Summary).
    Appelé à la fin de chaque session de trading ou manuellement.
    """
    try:
        daily = blackboard.read_sync("daily_stats") or {}
        meta = blackboard.read_sync("meta") or {}

        realized = daily.get("realized_pnl", 0.0)
        floating = daily.get("floating_pnl", 0.0)
        total_pnl = realized + floating
        trades_today = meta.get("daily_trade_count", 0)
        closed = daily.get("trades_closed", 0)

        emoji = "🟢" if total_pnl >= 0 else "🔴"

        message = (
            f"{emoji} *RAPPORT FIN DE JOURNÉE — Gold Sniper V2*\n"
            f"─────────────────────\n"
            f"📊 Trades ouverts: `{trades_today}`\n"
            f"✅ Trades clôturés: `{closed}`\n"
            f"💰 PnL Réalisé: `{realized:+.2f} USD`\n"
            f"📈 PnL Flottant: `{floating:+.2f} USD`\n"
            f"💼 PnL Total: `{total_pnl:+.2f} USD`\n"
            f"─────────────────────\n"
            f"⚙️ Mode: `{'LIVE 🔴' if meta.get('live_mode') else 'PAPER 📝'}`"
        )

        await send_telegram_notification(blackboard, message)

    except Exception as e:
        logger.warning(f"⚠️ EOD Report échoué : {e}")
