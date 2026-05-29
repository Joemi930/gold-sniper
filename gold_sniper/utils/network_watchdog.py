"""
Surveillance connectivite Internet (WiFi/Ethernet).
Bloque les nouveaux trades si offline, alerte Discord, reprise automatique.
"""
from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timezone

from config import (
    NETWORK_CHECK_HOST,
    NETWORK_CHECK_INTERVAL,
    NETWORK_OFFLINE_VETO_SECONDS,
)
from utils.discord_notifier import send_discord_notification
from utils.logger import get_logger


def _check_internet_sync(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class NetworkWatchdog:
    """Detecte perte/reprise de connexion reseau."""

    def __init__(self, blackboard) -> None:
        self.blackboard = blackboard
        self.logger = get_logger()
        self._online: bool | None = None
        self._offline_since: datetime | None = None
        self._offline_notified = False
        self._online_notified_after_recovery = True

    async def run(self) -> None:
        self.logger.info(
            "Network Watchdog demarre — ping %s:%s toutes les %ss",
            NETWORK_CHECK_HOST,
            80,
            NETWORK_CHECK_INTERVAL,
        )
        while not self.blackboard.kill_event.is_set():
            try:
                online = await asyncio.to_thread(
                    _check_internet_sync,
                    NETWORK_CHECK_HOST,
                    80,
                    3.0,
                )
                if online:
                    await self._handle_online()
                else:
                    await self._handle_offline()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning(f"Network Watchdog erreur: {exc}")
            await asyncio.sleep(max(5, NETWORK_CHECK_INTERVAL))

    async def _handle_online(self) -> None:
        was_offline = self._online is False
        self._online = True
        self._offline_since = None

        await self.blackboard.update_dict("meta", {
            "network_online": True,
            "network_offline_since": None,
        })

        if was_offline and not self._online_notified_after_recovery:
            self._online_notified_after_recovery = True
            self._offline_notified = False
            self.logger.info("Reseau restaure — reprise normale")
            await send_discord_notification(
                self.blackboard,
                "✅ Connexion Internet rétablie — le bot reprend le fonctionnement normal.",
            )
            await self._clear_network_veto()

    async def _handle_offline(self) -> None:
        now = datetime.now(timezone.utc)
        if self._online is not False:
            self.logger.warning("Connexion Internet perdue — nouveaux trades bloques")
        self._online = False
        if self._offline_since is None:
            self._offline_since = now

        elapsed = (now - self._offline_since).total_seconds()
        await self.blackboard.update_dict("meta", {
            "network_online": False,
            "network_offline_since": self._offline_since.isoformat(),
        })

        if elapsed >= NETWORK_OFFLINE_VETO_SECONDS:
            await self._set_network_veto(elapsed)

        if not self._offline_notified:
            self._offline_notified = True
            self._online_notified_after_recovery = False
            await send_discord_notification(
                self.blackboard,
                "⚠️ Perte de connexion Internet — nouveaux trades suspendus. "
                "Les positions ouvertes restent gerees.",
            )

    async def _set_network_veto(self, elapsed: float) -> None:
        await self.blackboard.update_dict("agents.risk_manager", {
            "veto": True,
            "score": 0,
            "reason": f"NETWORK_OFFLINE_{elapsed:.0f}s",
            "last_updated": datetime.now(timezone.utc),
        })

    async def _clear_network_veto(self) -> None:
        risk = self.blackboard.read_sync("agents.risk_manager") or {}
        reason = str(risk.get("reason", ""))
        if reason.startswith("NETWORK_OFFLINE"):
            await self.blackboard.update_dict("agents.risk_manager", {
                "veto": False,
                "score": 100,
                "reason": "NETWORK_RESTORED",
                "last_updated": datetime.now(timezone.utc),
            })
