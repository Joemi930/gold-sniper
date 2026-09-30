"""Notifications Discord de boot via REST, avec fallback de canal."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def _channel_candidates() -> list[str]:
    values = [
        os.getenv("DISCORD_ALERTS_CHANNEL_ID", ""),
        os.getenv("DISCORD_COMMANDS_CHANNEL_ID", ""),
        os.getenv("DISCORD_LOGS_CHANNEL_ID", ""),
    ]
    return list(dict.fromkeys(value for value in values if value))


def notify_boot(message: str) -> bool:
    token = os.getenv("DISCORD_TOKEN", "")
    if not token:
        return False
    payload = json.dumps({"content": message[:2000]}).encode("utf-8")
    last_error: Exception | None = None
    for channel_id in _channel_candidates():
        request = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bot {token}",
                "User-Agent": "GoldSniper/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {401, 403, 404}:
                continue
            break
        except Exception as exc:
            last_error = exc
            break
    if last_error:
        logger.warning("Discord boot notify indisponible: %s", last_error)
    return False
