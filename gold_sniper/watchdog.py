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

CHECK_INTERVAL_SECONDS = float(os.getenv("WATCHDOG_EXTERNAL_CHECK_INTERVAL", "5"))
HEARTBEAT_TIMEOUT_SECONDS = float(os.getenv("WATCHDOG_EXTERNAL_TIMEOUT", "60"))
RESTART_DELAY_SECONDS = float(os.getenv("WATCHDOG_EXTERNAL_RESTART_DELAY", "10"))
MAX_RESTARTS = int(os.getenv("WATCHDOG_EXTERNAL_MAX_RESTARTS", "3"))
RESTART_WINDOW_SECONDS = float(os.getenv("WATCHDOG_EXTERNAL_RESTART_WINDOW", "600"))
MAX_CYCLES = os.getenv("WATCHDOG_EXTERNAL_MAX_CYCLES")


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


def notify_telegram(message: str) -> None:
    """Notification Telegram d'urgence, synchrone et sans dependance projet."""
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return

    payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=5)
    except Exception as exc:
        logging.warning(f"Telegram watchdog indisponible: {exc}")


def heartbeat_age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def restart_window_exceeded(restarts: deque[float], config: WatchdogConfig) -> bool:
    now = time.time()
    while restarts and now - restarts[0] > config.restart_window:
        restarts.popleft()
    return len(restarts) >= config.max_restarts


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
    config = config or WatchdogConfig(command=[sys.executable, str(MAIN_SCRIPT)])
    restarts: deque[float] = deque()
    cycles = 0
    logging.info("Watchdog externe demarre.")
    notify_telegram("Gold Sniper Watchdog demarre")

    while True:
        if config.max_cycles is not None and cycles >= config.max_cycles:
            logging.info("Max cycles atteint, validation watchdog terminee.")
            return 0
        if restart_window_exceeded(restarts, config):
            message = (
                "WATCHDOG ARRETE\n"
                f"{len(restarts)} restarts en moins de {int(config.restart_window / 60)} min.\n"
                f"Verifier {LOG_FILE}"
            )
            logging.error(message)
            notify_telegram(message)
            return 2

        cycles += 1
        logging.info(f"Lancement process principal: {' '.join(config.command)}")
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(config.command, cwd=str(config.cwd), **kwargs)
        launch_time = time.time()
        restart_reason: str | None = None

        while True:
            exit_code = proc.poll()
            if exit_code is not None:
                if exit_code == 0:
                    logging.info("Process principal arrete proprement, watchdog en pause.")
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

        restarts.append(time.time())
        message = (
            "CRASH DETECTE - restart automatique\n"
            f"Raison: {restart_reason}\n"
            f"Restart #{len(restarts)}/{config.max_restarts}"
        )
        logging.warning(message)
        notify_telegram(message)
        time.sleep(config.restart_delay)


def _command_from_env() -> list[str]:
    override = os.getenv("GOLD_SNIPER_WATCHDOG_COMMAND")
    if override:
        return shlex.split(override)
    return [r"C:\Users\tetej\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe", str(MAIN_SCRIPT)]


if __name__ == "__main__":
    sys.exit(run_watchdog(WatchdogConfig(command=_command_from_env())))
