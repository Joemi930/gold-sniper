import asyncio
import ssl
from typing import Any, Callable

import aiohttp

from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from utils.emergency_shutdown import emergency_shutdown
from utils.logger import get_logger


TELEGRAM_UPDATES_API = "https://api.telegram.org/bot{token}/getUpdates"


def is_authorized_kill_command(update: dict[str, Any], chat_id: str) -> bool:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    command = text.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower() if text else ""
    return str(chat.get("id")) == str(chat_id) and command == "/kill"


def _build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


async def telegram_kill_listener(
    blackboard,
    on_kill: Callable[[], None] | None = None,
) -> None:
    logger = get_logger()
    notifications = blackboard.read_sync("notifications") if blackboard else {}
    token = (notifications or {}).get("telegram_bot_token") or TELEGRAM_TOKEN
    chat_id = (notifications or {}).get("telegram_chat_id") or TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning("Telegram /kill désactivé: TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID manquant.")
        return

    url = TELEGRAM_UPDATES_API.format(token=token)
    offset: int | None = None
    timeout = aiohttp.ClientTimeout(total=35)
    connector = aiohttp.TCPConnector(ssl=_build_ssl_context())
    logger.info("Telegram /kill listener démarré.")

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        while not blackboard.kill_event.is_set():
            try:
                params = {"timeout": 25, "allowed_updates": '["message","edited_message"]'}
                if offset is not None:
                    params["offset"] = offset
                async with session.get(url, params=params) as response:
                    payload = await response.json(content_type=None)
                for update in payload.get("result", []):
                    offset = int(update.get("update_id", 0)) + 1
                    if is_authorized_kill_command(update, chat_id):
                        logger.critical("Commande Telegram /kill reçue.")
                        await emergency_shutdown(blackboard, reason="TELEGRAM_KILL")
                        if on_kill:
                            on_kill()
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Telegram /kill listener erreur non bloquante: {exc}")
                await asyncio.sleep(5.0)

    logger.info("Telegram /kill listener arrêté.")
