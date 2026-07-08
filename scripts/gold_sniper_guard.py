"""Hidden self-healing launcher for the Gold Sniper PC Manager."""
from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_ROOT = PROJECT_ROOT / "gold_sniper"
DATA_DIR = GOLD_ROOT / "data"
LOG_DIR = GOLD_ROOT / "logs"
LOCK_PATH = DATA_DIR / "guard.lock"
DISABLED_PATH = DATA_DIR / "guard.disabled"
MANAGER_LOCK = DATA_DIR / "pc_manager.lock"
MANAGER_PID = DATA_DIR / "pc_manager.pid"
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
BOOT_DELAY_SECONDS = 180.0
CHECK_INTERVAL_SECONDS = 20.0
MIN_RELAUNCH_INTERVAL_SECONDS = 45.0
_stop_requested = False
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AUTOSTART_GUARD] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "autostart_guard.log", encoding="utf-8")],
)


def _pid_matches(pid: int, marker: str) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        proc = psutil.Process(pid)
        line = " ".join(str(part) for part in (proc.cmdline() or [])).lower()
        cwd = (proc.cwd() or "").lower()
        return marker.lower() in line and str(PROJECT_ROOT).lower() in (line + " " + cwd)
    except Exception:
        return False


def _manager_pids() -> list[int]:
    try:
        import psutil

        found: list[int] = []
        for proc in psutil.process_iter(["pid", "cmdline", "cwd"]):
            try:
                line = " ".join(str(part) for part in (proc.info.get("cmdline") or [])).lower()
                cwd = (proc.info.get("cwd") or "").lower()
                if "gold_sniper.pc_manager" in line and str(PROJECT_ROOT).lower() in (line + " " + cwd):
                    found.append(int(proc.info["pid"]))
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
                continue
        return found
    except ImportError:
        return []


def _acquire_lock() -> None:
    if LOCK_PATH.exists():
        try:
            old_pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            old_pid = 0
        if _pid_matches(old_pid, "gold_sniper_guard.py"):
            raise SystemExit(0)
        LOCK_PATH.unlink(missing_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode("ascii"))
    os.close(fd)


def _release_lock() -> None:
    try:
        if LOCK_PATH.exists() and LOCK_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _clean_stale_manager_artifacts() -> None:
    if _manager_pids():
        return
    for path in (MANAGER_LOCK, MANAGER_PID):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _launch_manager() -> None:
    pythonw = PYTHONW if PYTHONW.exists() else Path(sys.executable)
    _clean_stale_manager_artifacts()
    subprocess.Popen(
        [str(pythonw), "-m", "gold_sniper.pc_manager"],
        cwd=str(PROJECT_ROOT),
        shell=False,
        close_fds=True,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    logging.info("PC Manager launch requested")


def _seconds_since_boot() -> float:
    try:
        import psutil

        return max(0.0, time.time() - float(psutil.boot_time()))
    except Exception:
        return BOOT_DELAY_SECONDS


def _signal_handler(_signum: int, _frame) -> None:
    global _stop_requested
    _stop_requested = True


def main() -> int:
    _acquire_lock()
    atexit.register(_release_lock)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    remaining = max(0.0, BOOT_DELAY_SECONDS - _seconds_since_boot())
    if remaining:
        logging.info("Waiting %.1fs before startup", remaining)
        deadline = time.monotonic() + remaining
        while not _stop_requested and time.monotonic() < deadline:
            time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))

    last_launch = 0.0
    while not _stop_requested:
        try:
            if DISABLED_PATH.exists():
                logging.info("Guard disabled by marker")
                return 0
            if not _manager_pids() and time.monotonic() - last_launch >= MIN_RELAUNCH_INTERVAL_SECONDS:
                _launch_manager()
                last_launch = time.monotonic()
        except Exception:
            logging.exception("Guard iteration failed; retrying")
        time.sleep(CHECK_INTERVAL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
