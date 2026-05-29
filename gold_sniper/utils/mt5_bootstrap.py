"""Vérification et lancement du terminal MetaTrader 5 avant Gold Sniper."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import config

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
MT5_SCRIPT = ROOT_DIR / "scripts" / "start_mt5_minimized.ps1"
MT5_PROCESS_NAMES = ("terminal64", "terminal")


def _terminal_executable() -> Path:
    raw = config.MT5_TERMINAL_PATH or r"C:\Program Files\MetaTrader 5\terminal64.exe"
    return Path(raw)


def is_mt5_process_running() -> bool:
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in {f"{n}.exe" for n in MT5_PROCESS_NAMES}:
                return True
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Detection processus MT5 via psutil: %s", exc)

    if subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
        capture_output=True,
        text=True,
        creationflags=0x08000000,
    ).stdout.find("terminal64.exe") >= 0:
        return True
    return subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal.exe"],
        capture_output=True,
        text=True,
        creationflags=0x08000000,
    ).stdout.find("terminal.exe") >= 0


def _run_start_mt5_script(wait_seconds: int) -> bool:
    exe = _terminal_executable()
    if not exe.exists():
        logger.error("MT5 introuvable: %s", exe)
        return False
    if not MT5_SCRIPT.exists():
        logger.error("Script introuvable: %s", MT5_SCRIPT)
        return False

    env = dict(os.environ)
    env["MT5_TERMINAL_PATH"] = str(exe)
    cmd = [
        "powershell",
        "-NoProfile",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(MT5_SCRIPT),
        "-TerminalPath",
        str(exe),
        "-WaitSeconds",
        str(wait_seconds),
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=wait_seconds + 30,
            creationflags=0x08000000,
        )
        if result.returncode != 0 and result.stderr:
            logger.warning("start_mt5_minimized: %s", result.stderr.strip()[:500])
    except subprocess.TimeoutExpired:
        logger.warning("start_mt5_minimized timeout apres %ss", wait_seconds + 30)
    except Exception as exc:
        logger.error("Echec lancement MT5: %s", exc)
        return False
    return True


def ensure_mt5_running(wait_seconds: int | None = None) -> tuple[bool, str]:
    """
    Garantit que le processus MT5 tourne.
    Retourne (succes, message_detail).
    """
    if wait_seconds is None:
        wait_seconds = config.MT5_BOOT_WAIT_SECONDS

    if is_mt5_process_running():
        return True, "MT5 deja actif"

    exe = _terminal_executable()
    if not exe.exists():
        return False, f"Executable MT5 introuvable: {exe}"

    logger.info("Lancement MT5: %s", exe)
    if not _run_start_mt5_script(wait_seconds):
        return False, "Impossible d executer start_mt5_minimized.ps1"

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if is_mt5_process_running():
            return True, "MT5 demarre automatiquement"
        time.sleep(1.0)

    if is_mt5_process_running():
        return True, "MT5 demarre automatiquement"
    return False, f"MT5 non detecte apres {wait_seconds}s"
