"""Superviseur externe du moteur Gold Sniper.

Le PC Manager lance ce processus. Le watchdog lance ``main.py``, surveille
son processus et le heartbeat, puis effectue des redemarrages bornes.
"""
from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
LOCK_PATH = DATA_DIR / "watchdog.lock"
STATE_PATH = DATA_DIR / "watchdog_state.json"
RECOVERY_PATH = DATA_DIR / "watchdog_recovery.json"
HEARTBEAT_PATH = ROOT_DIR / "watchdog_heartbeat.tmp"
KILL_FLAG = ROOT_DIR / "kill_flag.txt"
MAIN_PATH = ROOT_DIR / "main.py"
MAX_RESTARTS = 5
STARTUP_GRACE_SECONDS = 45.0
HEARTBEAT_CRITICAL_SECONDS = 30.0
POLL_SECONDS = 2.0
BACKOFF_SECONDS = (2, 5, 10, 20, 30)

_stop_requested = False
_main_process: subprocess.Popen[Any] | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def _write_state(status: str, restart_count: int, **extra: Any) -> None:
    payload: dict[str, Any] = {
        "pid": os.getpid(),
        "watchdog_pid": os.getpid(),
        "main_pid": _main_process.pid if _main_process and _main_process.poll() is None else None,
        "status": status,
        "restart_count": restart_count,
        "updated_at": _utc_now(),
    }
    payload.update(extra)
    _atomic_json(STATE_PATH, payload)


def _acquire_lock() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            old_pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            old_pid = 0
        if old_pid and _pid_alive(old_pid) and old_pid != os.getpid():
            raise RuntimeError(f"watchdog deja actif PID {old_pid}")
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


def _signal_handler(_signum: int, _frame: Any) -> None:
    global _stop_requested
    _stop_requested = True


def _python_executable() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        return str(exe)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _spawn_main() -> subprocess.Popen[Any]:
    creationflags = 0x08000000 if os.name == "nt" else 0
    return subprocess.Popen(
        [_python_executable(), str(MAIN_PATH)],
        cwd=str(ROOT_DIR),
        shell=False,
        creationflags=creationflags,
    )


def _heartbeat_age() -> float | None:
    if not HEARTBEAT_PATH.exists():
        return None
    try:
        return max(0.0, time.time() - HEARTBEAT_PATH.stat().st_mtime)
    except OSError:
        return None


def _stop_main(grace_seconds: float = 15.0) -> None:
    global _main_process
    proc = _main_process
    if not proc or proc.poll() is not None:
        return
    deadline = time.monotonic() + grace_seconds
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.5)
    if proc.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/F", "/T"],
                    capture_output=True,
                    timeout=10,
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _request_manager_recovery(restart_count: int, reason: str) -> None:
    _atomic_json(
        RECOVERY_PATH,
        {
            "action": "restart_requested",
            "attempt": restart_count,
            "reason": reason,
            "requested_at": _utc_now(),
        },
    )


def run() -> int:
    global _main_process
    if not MAIN_PATH.exists():
        print(f"main.py introuvable: {MAIN_PATH}", file=sys.stderr)
        return 2
    _acquire_lock()
    atexit.register(_release_lock)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    restart_count = 0
    last_reason = "initial_start"

    while not _stop_requested:
        if KILL_FLAG.exists():
            _write_state("stopped_by_kill_flag", restart_count)
            _stop_main()
            return 0
        HEARTBEAT_PATH.unlink(missing_ok=True)
        _main_process = _spawn_main()
        started_at = time.monotonic()
        _write_state("main_starting", restart_count, reason=last_reason, started_at=_utc_now())
        while not _stop_requested:
            if KILL_FLAG.exists():
                _write_state("stopping", restart_count, reason="kill_flag")
                _stop_main()
                _write_state("stopped_by_kill_flag", restart_count)
                return 0
            exit_code = _main_process.poll()
            if exit_code is not None:
                last_reason = f"main_exit_{exit_code}"
                break
            age = _heartbeat_age()
            uptime = time.monotonic() - started_at
            if age is not None and age <= HEARTBEAT_CRITICAL_SECONDS:
                _write_state(
                    "main_running",
                    restart_count,
                    heartbeat_age=round(age, 3),
                    uptime_seconds=round(uptime, 1),
                )
            elif uptime > STARTUP_GRACE_SECONDS:
                last_reason = "heartbeat_timeout"
                _write_state(
                    "main_unresponsive",
                    restart_count,
                    heartbeat_age=age,
                    uptime_seconds=round(uptime, 1),
                )
                _stop_main(grace_seconds=0.0)
                break
            time.sleep(POLL_SECONDS)

        if _stop_requested:
            _stop_main(grace_seconds=0.0)
            _write_state("watchdog_stopped", restart_count)
            return 0
        restart_count += 1
        _write_state("restart_pending", restart_count, reason=last_reason)
        if restart_count > MAX_RESTARTS:
            _write_state("restart_exhausted", restart_count, reason=last_reason)
            _request_manager_recovery(restart_count, last_reason)
            return 1
        time.sleep(BACKOFF_SECONDS[min(restart_count - 1, len(BACKOFF_SECONDS) - 1)])

    _stop_main(grace_seconds=0.0)
    _write_state("watchdog_stopped", restart_count)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(0)
