# =============================================================================
# GOLD SNIPER v1.0 -- SYSTEM TRAY (Windows notification area)
# =============================================================================
#
# Icone dans la barre de notification Windows via pystray.
# Permet de voir l'etat du bot et d'ouvrir le dashboard sans ouvrir un terminal.
#
# Dependance optionnelle : pip install pystray pillow
# Si pystray n'est pas installe, le module se degrade silencieusement.
#
# =============================================================================

from __future__ import annotations

import asyncio
import threading
from typing import Callable

from utils.logger import get_logger

_logger = get_logger()

# Icone 16x16 minimale en format brut (PNG 1x1 transparent encode en dur)
# Remplacer par un fichier .ico reel pour une icone visible.
_ICON_FALLBACK = None


def _try_import_pystray():
    """Importe pystray de facon douce -- retourne None si absent."""
    try:
        import pystray
        from PIL import Image
        return pystray, Image
    except ImportError:
        return None, None


class SystemTrayIcon:
    """
    Gere l'icone Gold Sniper dans la barre de notification Windows.

    Usage :
        tray = SystemTrayIcon(on_quit=lambda: bb.trigger_kill())
        tray.start()          # non-bloquant, tourne dans un thread daemon
        tray.update_status("TRADING | PAPER | Score: 72")
        tray.stop()
    """

    def __init__(
        self,
        on_quit: Callable[[], None] | None = None,
        on_open_dashboard: Callable[[], None] | None = None,
    ) -> None:
        self._on_quit = on_quit
        self._on_open_dashboard = on_open_dashboard
        self._icon = None
        self._thread: threading.Thread | None = None
        self._status = "Gold Sniper V3 - Demarrage..."

    def start(self) -> None:
        """Demarre l'icone systray dans un thread daemon (non-bloquant)."""
        pystray, Image = _try_import_pystray()
        if pystray is None:
            _logger.debug("pystray non installe -- icone systray desactivee.")
            return

        try:
            # Cree une icone 16x16 dorée simple
            img = Image.new("RGBA", (16, 16), color=(214, 168, 79, 255))
            menu = pystray.Menu(
                pystray.MenuItem("Gold Sniper V3", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Ouvrir le dashboard",
                    lambda: self._on_open_dashboard() if self._on_open_dashboard else None,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Quitter",
                    lambda: self._on_quit() if self._on_quit else None,
                ),
            )
            self._icon = pystray.Icon(
                "gold_sniper",
                img,
                "Gold Sniper V3",
                menu,
            )
            self._thread = threading.Thread(
                target=self._icon.run,
                daemon=True,
                name="SystemTray",
            )
            self._thread.start()
            _logger.info("Icone systray Windows demarree.")
        except Exception as exc:
            _logger.warning(f"Systray impossible a demarrer: {exc}")

    def update_status(self, status: str) -> None:
        """Met a jour le tooltip de l'icone systray."""
        self._status = status
        if self._icon:
            try:
                self._icon.title = f"Gold Sniper V3 | {status}"
            except Exception:
                pass

    def stop(self) -> None:
        """Arrete l'icone systray proprement."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        _logger.debug("Systray arrete.")


async def systray_loop(blackboard, on_quit=None, on_open_dashboard=None) -> None:
    """
    Coroutine de mise a jour periodique du tooltip systray.
    Lance l'icone et rafraichit le statut toutes les 5 secondes.
    """
    tray = SystemTrayIcon(on_quit=on_quit, on_open_dashboard=on_open_dashboard)
    tray.start()

    try:
        while not blackboard.kill_event.is_set():
            try:
                market = blackboard.get_market()
                orch = blackboard.get_all().get("orchestrator", {})
                mode = "LIVE" if blackboard.get_all().get("meta", {}).get("live_mode") else "PAPER"
                status = (
                    f"{mode} | {market.get('session', 'OFF')} | "
                    f"Score: {orch.get('final_score', 0)} | "
                    f"{orch.get('decision', 'WAIT')}"
                )
                tray.update_status(status)
            except Exception:
                pass
            await asyncio.sleep(5.0)
    finally:
        tray.stop()
