import asyncio
from datetime import datetime, timezone

from config import WATCHDOG_HEARTBEAT_INTERVAL, WATCHDOG_TIMEOUT_CRITICAL, WATCHDOG_TIMEOUT_WARNING
from core.mt5_bridge import bridge
from utils.logger import get_logger
from utils.discord_notifier import send_discord_notification


class MT5Watchdog:
    """Surveille MT5, bloque les nouvelles entrees et tente une reconnexion."""

    def __init__(self, blackboard, max_reconnect_attempts: int = 3) -> None:
        self.blackboard = blackboard
        self.max_reconnect_attempts = max_reconnect_attempts
        self.logger = get_logger()
        self.disconnected_since: datetime | None = None
        self.warning_sent = False
        self.critical_sent = False

    async def run(self) -> None:
        self.logger.info("▶️  MT5 Watchdog demarre — surveillance connexion active")
        while not self.blackboard.kill_event.is_set():
            if await self._is_connected():
                await self._mark_connected()
            else:
                await self._handle_disconnect()
            await asyncio.sleep(max(1, WATCHDOG_HEARTBEAT_INTERVAL))

    async def _is_connected(self) -> bool:
        try:
            return await bridge.is_terminal_connected_async()
        except Exception as exc:
            self.logger.warning(f"MT5 Watchdog: healthcheck impossible ({exc})")
            return False

    async def _mark_connected(self) -> None:
        was_disconnected = self.disconnected_since is not None
        bridge.connected = True
        self.disconnected_since = None
        self.warning_sent = False
        self.critical_sent = False

        await self.blackboard.update_dict("meta", {
            "mt5_connected": True,
            "mt5_disconnected_since": None,
        })

        if was_disconnected:
            await self._clear_disconnect_veto()
            self.logger.info("MT5 reconnecte — reprise de la surveillance normale")
            await send_discord_notification(
                self.blackboard,
                "✅ MT5 reconnecte — le bot reprend la surveillance normale.",
            )

    async def _handle_disconnect(self) -> None:
        now = datetime.now(tz=timezone.utc)
        if self.disconnected_since is None:
            self.disconnected_since = now
            self.logger.warning("MT5 deconnecte — nouvelles entrees bloquees")

        bridge.connected = False
        elapsed = (now - self.disconnected_since).total_seconds()
        await self._set_disconnect_veto(elapsed)

        if elapsed >= WATCHDOG_TIMEOUT_WARNING and not self.warning_sent:
            self.warning_sent = True
            await send_discord_notification(
                self.blackboard,
                "⚠️ MT5 deconnecte — nouvelles entrees bloquees, reconnexion en cours.",
            )

        if await self._try_reconnect():
            await self._mark_connected()
            return

        if elapsed >= WATCHDOG_TIMEOUT_CRITICAL and not self.critical_sent:
            self.critical_sent = True
            self.logger.critical("MT5 toujours deconnecte apres tentatives de reconnexion")
            await send_discord_notification(
                self.blackboard,
                "🚨 MT5 toujours deconnecte — verifie le terminal et les trades ouverts.",
            )

    async def _try_reconnect(self) -> bool:
        for attempt in range(1, self.max_reconnect_attempts + 1):
            if self.blackboard.kill_event.is_set():
                return False
            self.logger.warning(f"MT5 Watchdog: tentative de reconnexion #{attempt}")
            try:
                if await bridge.connect():
                    return True
            except Exception as exc:
                self.logger.warning(f"MT5 Watchdog: reconnexion #{attempt} echouee ({exc})")
            await asyncio.sleep(min(2 * attempt, 10))
        return False

    async def _set_disconnect_veto(self, elapsed_seconds: float) -> None:
        await self.blackboard.update_dict("meta", {
            "mt5_connected": False,
            "mt5_disconnected_since": self.disconnected_since.isoformat() if self.disconnected_since else None,
        })
        await self.blackboard.update_dict("agents.risk_manager", {
            "veto": True,
            "score": 0,
            "reason": f"MT5_DISCONNECTED_{elapsed_seconds:.0f}s",
            "last_updated": datetime.utcnow(),
        })

    async def _clear_disconnect_veto(self) -> None:
        risk_state = self.blackboard.read_sync("agents.risk_manager") or {}
        if str(risk_state.get("reason", "")).startswith("MT5_DISCONNECTED"):
            await self.blackboard.update_dict("agents.risk_manager", {
                "veto": False,
                "score": 100,
                "reason": "MT5_RECONNECTED",
                "last_updated": datetime.utcnow(),
            })
