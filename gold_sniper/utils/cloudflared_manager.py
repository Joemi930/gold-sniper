"""Arret des processus cloudflared / ecouteurs dashboard (orphelins apres !kill)."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from config import CLOUDFLARED_PATH, DASHBOARD_PORT
except ImportError:
    CLOUDFLARED_PATH = r"C:\Users\tetej\AppData\Local\Programs\cloudflared\cloudflared.exe"
    DASHBOARD_PORT = 8765


def _force_kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                timeout=15,
            )
        else:
            import os
            import signal

            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        logger.debug("kill pid %s: %s", pid, exc)


def find_cloudflared_pids(dashboard_port: int | None = None) -> list[int]:
    """PIDs cloudflared tunnel vers le dashboard local."""
    port = dashboard_port if dashboard_port is not None else DASHBOARD_PORT
    port_token = f"localhost:{port}"
    pids: list[int] = []
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if "cloudflared" not in name:
                    continue
                cmdline = proc.info.get("cmdline") or []
                line = " ".join(str(x) for x in cmdline).lower()
                if "tunnel" not in line:
                    continue
                if port_token in line.replace("127.0.0.1", "localhost"):
                    pids.append(int(proc.info["pid"]))
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
                continue
    except ImportError:
        logger.warning("psutil requis pour detecter cloudflared")
    return list(dict.fromkeys(pids))


def find_dashboard_listeners(dashboard_port: int | None = None) -> list[int]:
    """Processus qui ecoutent encore sur le port du dashboard."""
    port = dashboard_port if dashboard_port is not None else DASHBOARD_PORT
    pids: list[int] = []
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if not conn.laddr or conn.laddr.port != port:
                continue
            if conn.status != psutil.CONN_LISTEN:
                continue
            if conn.pid and conn.pid > 0:
                pids.append(int(conn.pid))
    except (ImportError, psutil.AccessDenied):
        pass
    return list(dict.fromkeys(pids))


def stop_cloudflared_processes(
    dashboard_port: int | None = None,
    *,
    include_listeners: bool = True,
    exclude_pids: set[int] | None = None,
) -> list[int]:
    """
    Arrete cloudflared tunnel + liberateurs de port dashboard.
    Retourne la liste des PIDs tues.
    """
    port = dashboard_port if dashboard_port is not None else DASHBOARD_PORT
    killed: list[int] = []
    skip = set(exclude_pids or ())
    skip.add(os.getpid())

    for pid in find_cloudflared_pids(port):
        logger.info("Arret cloudflared PID %s", pid)
        _force_kill_pid(pid)
        killed.append(pid)

    if include_listeners:
        for pid in find_dashboard_listeners(port):
            if pid in skip:
                continue
            try:
                import psutil

                name = (psutil.Process(pid).name() or "").lower()
            except Exception:
                name = ""
            if "cloudflared" in name:
                continue
            logger.info("Arret ecouteur port %s PID %s (%s)", port, pid, name)
            _force_kill_pid(pid)
            killed.append(pid)

    return list(dict.fromkeys(killed))


def cleanup_before_tunnel(
    dashboard_port: int | None = None,
    settle_seconds: float = 1.5,
    *,
    include_listeners: bool = False,
) -> None:
    """
    Nettoie cloudflared orphelins avant un tunnel.

    include_listeners=False par defaut : ne pas tuer le dashboard du processus
    courant (main.py vient de binder le port 8765).
    include_listeners=True uniquement avant un nouveau lancement (!start / prepare).
    """
    stopped = stop_cloudflared_processes(
        dashboard_port,
        include_listeners=include_listeners,
    )
    if stopped and settle_seconds > 0:
        time.sleep(settle_seconds)


def cloudflared_binary_exists() -> bool:
    return Path(CLOUDFLARED_PATH).exists()
