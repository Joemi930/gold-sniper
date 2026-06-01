from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
LOG_FILE = ROOT_DIR / "logs" / "watchdog.log"
MAIN_SCRIPT = ROOT_DIR / "main.py"
HEARTBEAT_FILE = ROOT_DIR / "watchdog_heartbeat.tmp"
KILL_FLAG_FILE = ROOT_DIR / "kill_flag.txt"
WATCHDOG_LOCK_FILE = ROOT_DIR / "data" / "watchdog.lock"
WATCHDOG_STATE_FILE = ROOT_DIR / "data" / "watchdog_state.json"
PC_MANAGER_PID_FILE = ROOT_DIR / "data" / "pc_manager.pid"
WATCHDOG_RECOVERY_FILE = ROOT_DIR / "data" / "watchdog_recovery.json"
BOT_READY_FILE = ROOT_DIR / "data" / "bot_ready.json"
DATA_KILL_FLAG_FILE = ROOT_DIR / "data" / "kill_flag.txt"
LANCER_MANAGER_VBS = ROOT_DIR / "LancerManager.vbs"
LANCER_MANAGER_VBS_FALLBACK = ROOT_DIR / "lancer_manager.vbs"
LANCER_MANAGER_BAT_FALLBACK = ROOT_DIR / "LancerManager.bat"

CHECK_INTERVAL_SECONDS = float(os.getenv("WATCHDOG_EXTERNAL_CHECK_INTERVAL", "5"))
HEARTBEAT_TIMEOUT_SECONDS = float(os.getenv("WATCHDOG_EXTERNAL_TIMEOUT", "60"))
RESTART_DELAY_SECONDS = float(os.getenv("WATCHDOG_EXTERNAL_RESTART_DELAY", "30"))
MAX_RESTARTS = int(os.getenv("WATCHDOG_EXTERNAL_MAX_RESTARTS", "7"))
RESTART_WINDOW_SECONDS = float(os.getenv("WATCHDOG_EXTERNAL_RESTART_WINDOW", "600"))
MAX_CYCLES = os.getenv("WATCHDOG_EXTERNAL_MAX_CYCLES")
SOFT_BOOT_TIMEOUT_SECONDS = float(os.getenv("WATCHDOG_SOFT_BOOT_TIMEOUT", "90"))
MANAGER_BOOT_TIMEOUT_SECONDS = float(os.getenv("WATCHDOG_MANAGER_BOOT_TIMEOUT", "120"))
NUCLEAR_BOOT_TIMEOUT_SECONDS = float(os.getenv("WATCHDOG_NUCLEAR_BOOT_TIMEOUT", "180"))


@dataclass
class WatchdogConfig:
    command: list[str]
    cwd: Path = ROOT_DIR
    heartbeat_file: Path = HEARTBEAT_FILE
    check_interval: float = CHECK_INTERVAL_SECONDS
    heartbeat_timeout: float = HEARTBEAT_TIMEOUT_SECONDS
    restart_delay: float = RESTART_DELAY_SECONDS
    max_restarts: int = MAX_RESTARTS
    restart_window: float = RESTART_WINDOW_SECONDS
    max_cycles: int | None = int(MAX_CYCLES) if MAX_CYCLES else None


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
        level=logging.INFO,
        format="%(asctime)s [WATCHDOG] %(message)s",
    )


def notify_discord(message: str) -> None:
    """Notification Discord d'urgence via REST (pas de gateway)."""
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
        urllib.request.urlopen(request, timeout=5)
    except Exception as exc:
        logging.warning(f"Discord watchdog indisponible: {exc}")


notify_telegram = notify_discord  # alias legacy interne


def heartbeat_age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def restart_window_exceeded(restarts: deque[float], config: WatchdogConfig) -> bool:
    now = time.time()
    while restarts and now - restarts[0] > config.restart_window:
        restarts.popleft()
    return len(restarts) >= config.max_restarts


def _kill_flag_present() -> bool:
    return KILL_FLAG_FILE.exists() or DATA_KILL_FLAG_FILE.exists()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_watchdog_lock() -> bool:
    WATCHDOG_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if WATCHDOG_LOCK_FILE.exists():
        try:
            old = int(WATCHDOG_LOCK_FILE.read_text(encoding="utf-8").strip())
            if _pid_alive(old) and old != os.getpid():
                logging.critical("Watchdog deja actif (PID %s).", old)
                return False
        except (ValueError, OSError):
            WATCHDOG_LOCK_FILE.unlink(missing_ok=True)
    WATCHDOG_LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _write_watchdog_state(watchdog_pid: int, main_pid: int | None) -> None:
    WATCHDOG_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "watchdog_pid": watchdog_pid,
        "main_pid": main_pid,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    WATCHDOG_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _wait_for_bot_ready(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _kill_flag_present():
            return False
        if BOT_READY_FILE.exists():
            try:
                data = json.loads(BOT_READY_FILE.read_text(encoding="utf-8"))
                phase = (data.get("phase") or "").strip()
                if phase and phase != "cloudflare_failed":
                    return True
                if data.get("cloudflare_url") or data.get("pid"):
                    return True
            except json.JSONDecodeError:
                pass
        time.sleep(1.0)
    return False


def _pc_manager_pid() -> int | None:
    for path in (PC_MANAGER_PID_FILE, ROOT_DIR / "data" / "pc_manager.lock"):
        if not path.exists():
            continue
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if _pid_alive(pid):
            return pid
    return None


def _request_manager_recovery(attempt: int, reason: str) -> bool:
    if _kill_flag_present():
        return False
    if _pc_manager_pid() is None:
        return False
    WATCHDOG_RECOVERY_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "action": "restart_requested",
        "reason": reason,
        "attempt": attempt,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    WATCHDOG_RECOVERY_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logging.warning("Recovery niveau 2 demande au pc_manager: %s", payload)
    return True


def _kill_terminal64() -> None:
    if sys.platform != "win32":
        return
    subprocess.run(["taskkill", "/F", "/IM", "terminal64.exe"], capture_output=True)


def _kill_gold_sniper_processes_except_self() -> None:
    try:
        import psutil

        my_pid = os.getpid()
        protected_pids = {my_pid}
        try:
            protected_pids.update(parent.pid for parent in psutil.Process(my_pid).parents())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                pid = int(proc.info["pid"])
                if pid in protected_pids:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "gold_sniper" in cmdline.lower():
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError):
                continue
    except ImportError:
        logging.warning("psutil indisponible: nuclear reset partiel")


def _cleanup_nuclear_locks() -> None:
    for path in (
        PC_MANAGER_PID_FILE,
        ROOT_DIR / "data" / "pc_manager.lock",
        ROOT_DIR / "data" / "watchdog.lock",
        BOT_READY_FILE,
        KILL_FLAG_FILE,
        DATA_KILL_FLAG_FILE,
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logging.warning("Nettoyage lock impossible %s: %s", path, exc)


def _manager_launcher_command() -> list[str] | None:
    if LANCER_MANAGER_VBS.exists():
        return ["wscript.exe", str(LANCER_MANAGER_VBS)]
    if LANCER_MANAGER_VBS_FALLBACK.exists():
        return ["wscript.exe", str(LANCER_MANAGER_VBS_FALLBACK)]
    if LANCER_MANAGER_BAT_FALLBACK.exists():
        return ["cmd.exe", "/c", str(LANCER_MANAGER_BAT_FALLBACK)]
    return None


def _launch_manager_vbs() -> bool:
    command = _manager_launcher_command()
    if command is None:
        logging.error("Launcher manager introuvable")
        return False
    try:
        subprocess.Popen(
            command,
            cwd=str(ROOT_DIR),
            shell=False,
        )
        return True
    except Exception as exc:
        logging.error("Lancement LancerManager.vbs impossible: %s", exc)
        return False


def _nuclear_reset(attempt: int, reason: str) -> bool:
    if _kill_flag_present():
        logging.info("kill_flag present: nuclear reset ignore.")
        return False
    logging.critical("WATCHDOG NUCLEAR RESET tentative %s: %s", attempt, reason)
    notify_discord("🔴 WATCHDOG NUCLEAR RESET — Redémarrage complet MT5 + Gold Sniper")
    _kill_terminal64()
    _kill_gold_sniper_processes_except_self()
    time.sleep(30.0)
    if _kill_flag_present():
        logging.info("kill_flag apparu pendant nuclear reset: abandon relance.")
        return False
    _cleanup_nuclear_locks()
    if not _launch_manager_vbs():
        notify_discord("🔴 WATCHDOG NUCLEAR RESET — LancerManager.vbs introuvable ou impossible")
        return False
    if _wait_for_bot_ready(NUCLEAR_BOOT_TIMEOUT_SECONDS):
        logging.info("Nuclear reset confirme par bot_ready.json")
        return True
    notify_discord("🔴 WATCHDOG NUCLEAR RESET ÉCHOUÉ — bot_ready.json absent après 180s")
    time.sleep(300.0)
    return False


def terminate_process(proc: subprocess.Popen, reason: str) -> None:
    if proc.poll() is not None:
        return
    logging.warning(f"Kill du process principal: {reason}")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logging.warning("Terminate timeout, kill force.")
        proc.kill()
        proc.wait(timeout=10)


def run_watchdog(config: WatchdogConfig | None = None) -> int:
    setup_logging()
    from utils.single_instance import (
        can_start_bot_stack,
        clear_stack_artifacts,
        terminate_duplicate_watchdogs,
    )

    terminate_duplicate_watchdogs()
    if not can_start_bot_stack():
        logging.info("Pile deja active ou kill_flag — watchdog ne demarre pas.")
        clear_stack_artifacts()
        return 0
    if not _acquire_watchdog_lock():
        return 1
    if _kill_flag_present():
        logging.info("kill_flag present — watchdog ne demarre pas le moteur.")
        return 0
    config = config or WatchdogConfig(command=[sys.executable, str(MAIN_SCRIPT)])
    restarts: deque[float] = deque()
    recovery_attempt = 0
    cycles = 0
    logging.info("Watchdog externe demarre (PID %s).", os.getpid())
    notify_discord("Gold Sniper Watchdog demarre")

    while True:
        if _kill_flag_present():
            logging.info("kill_flag detecte — arret watchdog sans relance.")
            _write_watchdog_state(os.getpid(), None)
            return 0
        if config.max_cycles is not None and cycles >= config.max_cycles:
            logging.info("Max cycles atteint, validation watchdog terminee.")
            return 0
        if restart_window_exceeded(restarts, config):
            logging.warning(
                "Fenetre de restart saturee (%s/%s), escalation progressive active.",
                len(restarts),
                config.max_restarts,
            )

        cycles += 1
        logging.info(f"Lancement process principal: {' '.join(config.command)}")
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(config.command, cwd=str(config.cwd), **kwargs)
        _write_watchdog_state(os.getpid(), proc.pid)
        launch_time = time.time()
        restart_reason: str | None = None
        if _wait_for_bot_ready(SOFT_BOOT_TIMEOUT_SECONDS):
            if recovery_attempt:
                logging.info("Boot confirme par bot_ready.json — compteur recovery reset.")
            recovery_attempt = 0
            restarts.clear()

        while True:
            if _kill_flag_present():
                terminate_process(proc, "kill_flag")
                logging.info("kill_flag — process principal arrete.")
                return 0
            exit_code = proc.poll()
            if exit_code is not None:
                if exit_code == 0:
                    logging.info("Process principal arrete proprement, watchdog en pause.")
                    _write_watchdog_state(os.getpid(), None)
                    return 0
                restart_reason = f"crash code={exit_code}"
                break

            age = heartbeat_age_seconds(config.heartbeat_file)
            uptime = time.time() - launch_time
            if age is None:
                if uptime > config.heartbeat_timeout:
                    restart_reason = f"heartbeat absent depuis {uptime:.1f}s"
                    terminate_process(proc, restart_reason)
                    break
            else:
                heartbeat_mtime = config.heartbeat_file.stat().st_mtime
                heartbeat_seen_after_launch = heartbeat_mtime >= launch_time - 1.0
                if heartbeat_seen_after_launch and age > config.heartbeat_timeout:
                    restart_reason = f"heartbeat stale age={age:.1f}s"
                    terminate_process(proc, restart_reason)
                    break
                if not heartbeat_seen_after_launch and uptime > config.heartbeat_timeout:
                    restart_reason = f"heartbeat non rafraichi depuis le lancement ({uptime:.1f}s)"
                    terminate_process(proc, restart_reason)
                    break

            time.sleep(config.check_interval)

        recovery_attempt += 1
        restarts.append(time.time())
        if recovery_attempt <= 3:
            message = (
                "WATCHDOG NIVEAU 1 - soft restart\n"
                f"Raison: {restart_reason}\n"
                f"Tentative {recovery_attempt}/3"
            )
            logging.warning(message)
            notify_telegram(message)
            time.sleep(config.restart_delay)
            continue

        if recovery_attempt <= 6:
            if _request_manager_recovery(recovery_attempt, "heartbeat_lost"):
                notify_discord(
                    f"♻️ Watchdog a demandé un restart propre — tentative {recovery_attempt}"
                )
                if _wait_for_bot_ready(MANAGER_BOOT_TIMEOUT_SECONDS):
                    logging.info("Recovery niveau 2 confirme par bot_ready.json")
                    recovery_attempt = 0
                    restarts.clear()
                    return 0
                logging.warning("Recovery niveau 2 timeout, escalation continue.")
                time.sleep(config.restart_delay)
                continue

            logging.warning("pc_manager absent: passage direct au niveau 3.")

        if _nuclear_reset(recovery_attempt, restart_reason or "heartbeat_lost"):
            recovery_attempt = 0
            restarts.clear()
            return 0
        time.sleep(config.restart_delay)


def _command_from_env() -> list[str]:
    override = os.getenv("GOLD_SNIPER_WATCHDOG_COMMAND")
    if override:
        return shlex.split(override)
    try:
        from config import PYTHON_BIN

        py = PYTHON_BIN if Path(PYTHON_BIN).is_file() else sys.executable
    except ImportError:
        py = sys.executable
    return [py, str(MAIN_SCRIPT)]


if __name__ == "__main__":
    sys.exit(run_watchdog(WatchdogConfig(command=_command_from_env())))
