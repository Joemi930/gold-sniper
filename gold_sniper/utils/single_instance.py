"""Garde-fous anti-doublons manager / watchdog / main."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
MANAGER_LOCK = ROOT_DIR / "data" / "pc_manager.lock"
WATCHDOG_LOCK = ROOT_DIR / "data" / "watchdog.lock"
KILL_FLAG = ROOT_DIR / "kill_flag.txt"
WATCHDOG_HEARTBEAT = ROOT_DIR / "watchdog_heartbeat.tmp"
PROJECT_ROOT_LOWER = str(ROOT_DIR).lower()
HEARTBEAT_MAX_AGE = 15.0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_lock_pid(lock_path: Path) -> int | None:
    if not lock_path.exists():
        return None
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
        return pid if _pid_alive(pid) else None
    except (ValueError, OSError):
        return None


def find_pids(*markers: str) -> list[int]:
    markers_l = [m.lower() for m in markers]
    found: list[int] = []
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "cmdline", "cwd"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                line = " ".join(str(x) for x in cmdline).lower()
                if not any(m in line for m in markers_l):
                    continue
                cwd = (proc.info.get("cwd") or "").lower()
                if PROJECT_ROOT_LOWER not in line and PROJECT_ROOT_LOWER not in cwd:
                    continue
                if any(m in line for m in markers_l):
                    found.append(int(proc.info["pid"]))
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
                continue
    except ImportError:
        pass
    return list(dict.fromkeys(found))


def get_manager_pid() -> int | None:
    lock_pid = _read_lock_pid(MANAGER_LOCK)
    if lock_pid:
        return lock_pid
    pids = find_pids("pc_manager.py")
    return pids[0] if pids else None


def get_watchdog_pid() -> int | None:
    lock_pid = _read_lock_pid(WATCHDOG_LOCK)
    if lock_pid:
        return lock_pid
    pids = find_pids("watchdog.py")
    return pids[0] if pids else None


def heartbeat_fresh() -> bool:
    if not WATCHDOG_HEARTBEAT.exists():
        return False
    age = time.time() - WATCHDOG_HEARTBEAT.stat().st_mtime
    return age < HEARTBEAT_MAX_AGE


def is_stack_running() -> bool:
    """Vrai seulement si main ou watchdog+heartbeat vivants (pas fichier heartbeat seul)."""
    if KILL_FLAG.exists():
        return False
    main_pids = find_pids("main.py")
    if main_pids:
        return True
    wpid = get_watchdog_pid()
    if wpid and heartbeat_fresh():
        return True
    return False


def clear_stack_artifacts() -> None:
    """Supprime heartbeat / locks / bot_ready apres !kill ou avant !start."""
    for path in (WATCHDOG_HEARTBEAT, WATCHDOG_LOCK, ROOT_DIR / "data" / "bot_ready.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def prepare_clean_stack_start() -> None:
    """Arrete watchdog/main residuels et libere cloudflared."""
    from utils.cloudflared_manager import cleanup_before_tunnel

    terminate_duplicate_watchdogs()
    for pid in find_pids("watchdog.py", "main.py"):
        _force_kill(pid)
    clear_stack_artifacts()
    cleanup_before_tunnel(settle_seconds=1.5, include_listeners=True)


def can_start_manager(exclude_pid: int | None = None) -> bool:
    pid = get_manager_pid()
    if pid is None:
        return True
    if exclude_pid and pid == exclude_pid:
        return True
    logger.info("Manager deja actif (PID %s) — lancement ignore", pid)
    return False


def can_start_bot_stack() -> bool:
    if KILL_FLAG.exists():
        logger.info("kill_flag present — lancement bot ignore")
        return False
    if is_stack_running():
        logger.info("Pile Gold Sniper deja active — lancement ignore")
        return False
    return True


def _force_kill(pid: int) -> None:
    import subprocess
    import sys

    if not _pid_alive(pid):
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                timeout=10,
            )
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def terminate_duplicate_managers(keep_pid: int | None = None) -> list[int]:
    killed: list[int] = []
    for pid in find_pids("pc_manager.py"):
        if keep_pid and pid == keep_pid:
            continue
        _force_kill(pid)
        killed.append(pid)
        logger.warning("pc_manager doublon arrete PID %s", pid)
    return killed


def terminate_duplicate_watchdogs(keep_pid: int | None = None) -> list[int]:
    killed: list[int] = []
    for pid in find_pids("watchdog.py"):
        if keep_pid and pid == keep_pid:
            continue
        _force_kill(pid)
        killed.append(pid)
        logger.warning("watchdog doublon arrete PID %s", pid)
    return killed
