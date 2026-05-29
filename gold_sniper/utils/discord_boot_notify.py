"""Notifications Discord boot/erreurs via REST (sans gateway)."""
from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)


def notify_boot(message: str) -> None:
    token = os.getenv("DISCORD_TOKEN", "")
    channel_id = os.getenv("DISCORD_ALERTS_CHANNEL_ID", "")
    if not token or not channel_id:
        return

    payload = json.dumps({"content": message[:2000]}).encode("utf-8")
    request = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except Exception as exc:
        logger.warning("Discord boot notify indisponible: %s", exc)
