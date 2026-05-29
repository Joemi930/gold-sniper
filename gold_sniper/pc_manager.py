"""
PC Manager — seul processus avec connexion gateway Discord.
Gère !start !kill !restart !pc_status + routage inbox + boutons trade.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

from utils.ssl_bundle import configure_ssl_environment

configure_ssl_environment()

import discord
from discord import Intents

from utils.discord_commands import (
    LIFECYCLE_COMMANDS,
    format_help_text,
    normalize_command,
)
from utils.discord_boot_notify import notify_boot
from utils.mt5_bootstrap import ensure_mt5_running, is_mt5_process_running

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_USER_ID = int(os.getenv("DISCORD_USER_ID", "0") or "0")
DISCORD_COMMANDS_CHANNEL = int(os.getenv("DISCORD_COMMANDS_CHANNEL_ID", "0") or "0")

try:
    from config import BOOT_READY_TIMEOUT, CLOUDFLARED_PATH
except ImportError:
    BOOT_READY_TIMEOUT = 180.0
    CLOUDFLARED_PATH = r"C:\Users\tetej\AppData\Local\Programs\cloudflared\cloudflared.exe"

WATCHDOG_HEARTBEAT = ROOT_DIR / "watchdog_heartbeat.tmp"
KILL_FLAG = ROOT_DIR / "kill_flag.txt"
BOT_READY = ROOT_DIR / "data" / "bot_ready.json"
WATCHDOG_STATE = ROOT_DIR / "data" / "watchdog_state.json"
DISCORD_INBOX = ROOT_DIR / "data" / "discord_inbox.jsonl"
MANAGER_LOCK = ROOT_DIR / "data" / "pc_manager.lock"
VBS_LAUNCHER = ROOT_DIR / "LancerGoldSniper.vbs"
PROJECT_ROOT_LOWER = str(ROOT_DIR).lower()

PC_NAME = socket.gethostname()
STARTUP_TIME = time.time()

DEBOUNCE = {
    "start": 30.0,
    "kill": 10.0,
    "restart": 45.0,
    "pc_status": 5.0,
}
STACK_SPAWN_TIMEOUT = 35.0  # MT5 deja demarre par pc_manager avant watchdog
_last_cmd: dict[str, float] = {}
_lifecycle_lock = threading.Lock()
INBOX_DEDUP_SECONDS = 5.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PC_MANAGER] %(message)s",
    handlers=[
        logging.FileHandler(ROOT_DIR / "logs" / "pc_manager.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


def is_gold_sniper_running() -> bool:
    if not WATCHDOG_HEARTBEAT.exists():
        return False
    age = time.time() - WATCHDOG_HEARTBEAT.stat().st_mtime
    return age < 15.0


def _reset_start_cooldowns() -> None:
    _last_cmd.pop("start", None)
    _last_cmd.pop("restart", None)


def _debounced(cmd: str) -> bool:
    if cmd in ("start", "restart") and _lifecycle_lock.locked():
        return True
    now = time.monotonic()
    cooldown = DEBOUNCE.get(cmd, 5.0)
    if now - _last_cmd.get(cmd, 0) < cooldown:
        return True
    _last_cmd[cmd] = now
    return False


def _resolve_pythonw() -> str:
    try:
        from config import PYTHON_BIN

        preferred = Path(PYTHON_BIN)
        if preferred.is_file():
            return str(preferred)
    except ImportError:
        pass
    for name in ("pythonw.exe", "pyw.exe", "python.exe"):
        path = shutil.which(name)
        if path and "WindowsApps" not in path.replace("/", "\\"):
            return path
    return sys.executable


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _find_project_pids(*markers: str) -> list[int]:
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


def _terminate_duplicate_pc_managers() -> None:
    from utils.single_instance import terminate_duplicate_managers

    terminate_duplicate_managers(keep_pid=os.getpid())


def _acquire_single_instance() -> None:
    MANAGER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    my_pid = os.getpid()
    if MANAGER_LOCK.exists():
        try:
            old_pid = int(MANAGER_LOCK.read_text(encoding="utf-8").strip())
            if _pid_alive(old_pid) and old_pid != my_pid:
                logging.critical(
                    "PC Manager deja actif (PID %s). Arret de cette instance.", old_pid
                )
                sys.exit(0)
        except (ValueError, OSError):
            MANAGER_LOCK.unlink(missing_ok=True)
    try:
        fd = os.open(str(MANAGER_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(my_pid).encode("utf-8"))
        os.close(fd)
    except FileExistsError:
        try:
            old_pid = int(MANAGER_LOCK.read_text(encoding="utf-8").strip())
            if _pid_alive(old_pid) and old_pid != my_pid:
                logging.critical("Lock occupe par PID %s — arret.", old_pid)
                sys.exit(0)
        except (ValueError, OSError):
            pass
        MANAGER_LOCK.unlink(missing_ok=True)
        fd = os.open(str(MANAGER_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(my_pid).encode("utf-8"))
        os.close(fd)

    def _release() -> None:
        try:
            if (
                MANAGER_LOCK.exists()
                and MANAGER_LOCK.read_text(encoding="utf-8").strip() == str(os.getpid())
            ):
                MANAGER_LOCK.unlink(missing_ok=True)
        except OSError:
            pass

    import atexit

    atexit.register(_release)


def _recent_inbox_has_same_command(content: str) -> bool:
    if not DISCORD_INBOX.exists():
        return False
    try:
        lines = DISCORD_INBOX.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in reversed(lines[-20:]):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("content") != content:
            continue
        ts = entry.get("ts", "")
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            if time.time() - t < INBOX_DEDUP_SECONDS:
                return True
        except ValueError:
            return True
    return False


def _enqueue_command(content: str, user_id: int) -> None:
    if _recent_inbox_has_same_command(content):
        logging.debug("Inbox dedup: ignore %s", content)
        return
    DISCORD_INBOX.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "content": content,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with open(DISCORD_INBOX, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_pids_from_json(path: Path) -> list[int]:
    pids: list[int] = []
    if not path.exists():
        return pids
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("pid", "watchdog_pid", "main_pid", "python_pid"):
            if key in data and data[key]:
                pids.append(int(data[key]))
        if "pids" in data and isinstance(data["pids"], list):
            pids.extend(int(p) for p in data["pids"] if p)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return list(dict.fromkeys(pids))


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        if not _pid_alive(pid):
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception as exc:
            logging.debug("kill pid %s: %s", pid, exc)


def _write_kill_flag() -> None:
    KILL_FLAG.write_text(
        datetime.now(timezone.utc).isoformat(),
        encoding="utf-8",
    )


def _clear_kill_flag() -> None:
    if KILL_FLAG.exists():
        KILL_FLAG.unlink(missing_ok=True)


def _clear_inbox() -> None:
    DISCORD_INBOX.parent.mkdir(parents=True, exist_ok=True)
    DISCORD_INBOX.write_text("", encoding="utf-8")


def _kill_gold_sniper_stack() -> None:
    from utils.single_instance import prepare_clean_stack_start

    _reset_start_cooldowns()
    _write_kill_flag()
    time.sleep(2.0)
    pids = (
        _read_pids_from_json(WATCHDOG_STATE)
        + _read_pids_from_json(BOT_READY)
        + _find_project_pids("watchdog.py")
        + _find_project_pids("main.py")
        + _find_project_pids("LancerGoldSniper.vbs")
        + _find_project_pids("GoldSniper.bat")
        + _find_project_pids("start_mt5_minimized")
    )
    pids = list(dict.fromkeys(p for p in pids if _pid_alive(p)))
    if pids:
        logging.info("Kill stack PIDs: %s", pids)
        _kill_pids(pids)
    prepare_clean_stack_start()
    _clear_inbox()


def _bot_already_active() -> bool:
    if not is_gold_sniper_running():
        return False
    if BOT_READY.exists():
        try:
            data = json.loads(BOT_READY.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if pid and _pid_alive(pid):
                return True
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return is_gold_sniper_running()
    return is_gold_sniper_running()


def _cloudflared_missing() -> bool:
    return not Path(CLOUDFLARED_PATH).exists()


def _wait_for_stack_spawn(timeout: float = STACK_SPAWN_TIMEOUT) -> bool:
    from utils.single_instance import find_pids, get_watchdog_pid, heartbeat_fresh

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if find_pids("main.py") or get_watchdog_pid():
            return True
        if heartbeat_fresh():
            return True
        time.sleep(1.0)
    return False


def _launch_bot() -> bool:
    """Lance watchdog directement (MT5 deja gere par _ensure_mt5_or_fail)."""
    from utils.single_instance import can_start_bot_stack, prepare_clean_stack_start

    if not can_start_bot_stack():
        logging.info("Pile residuelle detectee — nettoyage avant lancement")
        prepare_clean_stack_start()
        if not can_start_bot_stack():
            logging.error("Lancement refuse: pile toujours active apres nettoyage")
            return False
    watchdog_py = ROOT_DIR / "watchdog.py"
    if not watchdog_py.exists():
        logging.error("watchdog.py introuvable")
        return False
    py = _resolve_pythonw()
    logging.info("Lancement watchdog: %s %s", py, watchdog_py)
    subprocess.Popen(
        [py, str(watchdog_py)],
        cwd=str(ROOT_DIR),
        shell=False,
    )
    return _wait_for_stack_spawn()


def _wait_for_bot_ready(timeout: float | None = None) -> dict | None:
    if timeout is None:
        timeout = BOOT_READY_TIMEOUT
    deadline = time.monotonic() + timeout
    valid_phases = {"cloudflare_ready", "engine_ready", ""}
    while time.monotonic() < deadline:
        if BOT_READY.exists():
            try:
                data = json.loads(BOT_READY.read_text(encoding="utf-8"))
                phase = (data.get("phase") or "").strip()
                if phase == "cloudflare_failed":
                    return None
                url = (data.get("cloudflare_url") or "").strip()
                if url.startswith("https://") and "trycloudflare.com" in url:
                    if phase in valid_phases:
                        return data
            except json.JSONDecodeError:
                pass
        time.sleep(1.0)
    return None


def _cleanup_stale_boot_state() -> None:
    heartbeat_stale = True
    if WATCHDOG_HEARTBEAT.exists():
        age = time.time() - WATCHDOG_HEARTBEAT.stat().st_mtime
        heartbeat_stale = age >= 15.0
    if heartbeat_stale and not is_gold_sniper_running():
        if BOT_READY.exists():
            BOT_READY.unlink(missing_ok=True)
        pids = _read_pids_from_json(WATCHDOG_STATE) + _read_pids_from_json(BOT_READY)
        pids = [p for p in pids if _pid_alive(p)]
        if pids:
            logging.info("Nettoyage processus orphelins: %s", pids)
            _kill_pids(pids)


def _ensure_mt5_or_fail() -> tuple[bool, str]:
    ok, detail = ensure_mt5_running()
    if not ok:
        msg = (
            "❌ MT5 introuvable ou ne demarre pas\n"
            f"{detail}\n"
            "Verifie MT5_TERMINAL_PATH dans .env"
        )
        notify_boot(msg)
        return False, msg
    if "automatiquement" in detail.lower() or "demarre" in detail.lower():
        notify_boot(f"🟡 {detail}")
    return True, detail


def _timeout_start_message() -> str:
    cf_path = Path(CLOUDFLARED_PATH)
    if not cf_path.exists():
        return (
            "❌ cloudflared introuvable\n"
            f"Chemin attendu : `{cf_path}`\n"
            "Installe cloudflared ou définis CLOUDFLARED_PATH dans .env"
        )
    return (
        "⚠️ Démarrage lancé mais timeout Cloudflare\n"
        f"Attente max : {int(BOOT_READY_TIMEOUT)}s — URL trycloudflare.com non reçue.\n"
        f"Vérifie `logs/pc_manager.log` et que cloudflared tourne (`{cf_path}`)."
    )


def _perform_kill(silent: bool = False) -> str:
    _kill_gold_sniper_stack()
    if not silent:
        return "🔴 Gold Sniper arrêté — Envoie !start pour relancer"
    return ""


def _perform_start_locked() -> str:
    if _cloudflared_missing():
        msg = _timeout_start_message()
        notify_boot(msg)
        return msg
    if _bot_already_active():
        return "⚠️ Gold Sniper est déjà en cours d'exécution"
    from utils.single_instance import prepare_clean_stack_start

    prepare_clean_stack_start()
    _cleanup_stale_boot_state()
    mt5_ok, mt5_detail = _ensure_mt5_or_fail()
    if not mt5_ok:
        return mt5_detail
    _clear_kill_flag()
    time.sleep(0.5)
    if not _launch_bot():
        return (
            "❌ Le moteur ne demarre pas (watchdog bloque ou deja actif).\n"
            "Envoie `!kill`, attends 5 s, puis `!start`."
        )
    ready = _wait_for_bot_ready()
    if ready:
        url = ready.get("cloudflare_url", "")
        phase = ready.get("phase", "")
        if url and "trycloudflare.com" in url:
            extra = ""
            if phase == "cloudflare_ready":
                extra = "\n(Moteur encore en chargement — commandes trading bientot actives.)"
            elif mt5_detail != "MT5 deja actif":
                extra = f"\n({mt5_detail})"
            return f"✅ Gold Sniper démarré — {url}{extra}"
    msg = _timeout_start_message()
    notify_boot(msg)
    return msg


def _perform_restart_locked() -> str:
    if _cloudflared_missing():
        msg = _timeout_start_message()
        notify_boot(msg)
        return msg
    _perform_kill(silent=True)
    time.sleep(5)
    from utils.single_instance import prepare_clean_stack_start

    prepare_clean_stack_start()
    _cleanup_stale_boot_state()
    mt5_ok, mt5_detail = _ensure_mt5_or_fail()
    if not mt5_ok:
        return mt5_detail
    _clear_kill_flag()
    time.sleep(0.5)
    if not _launch_bot():
        return (
            "❌ Redemarrage echoue — moteur ne part pas.\n"
            "Envoie `!kill`, attends 5 s, puis `!start`."
        )
    ready = _wait_for_bot_ready()
    if ready and ready.get("cloudflare_url"):
        return f"♻️ Gold Sniper redémarré — {ready['cloudflare_url']}"
    msg = _timeout_start_message()
    notify_boot(msg)
    return msg


def _perform_start() -> str:
    from utils.lifecycle_lock import lifecycle_lock

    with lifecycle_lock():
        with _lifecycle_lock:
            return _perform_start_locked()


def _perform_restart() -> str:
    from utils.lifecycle_lock import lifecycle_lock

    with lifecycle_lock():
        with _lifecycle_lock:
            return _perform_restart_locked()


def _run_lifecycle(cmd: str) -> str:
    if cmd == "start":
        return _perform_start()
    if cmd == "kill":
        return _perform_kill()
    if cmd == "restart":
        return _perform_restart()
    if cmd == "pc_status":
        return _pc_status_text()
    return ""


def _pc_status_text() -> str:
    from utils.system_metrics import format_ram_cpu

    ram_cpu_line = format_ram_cpu()
    uptime = int(time.time() - STARTUP_TIME)
    bot_on = is_gold_sniper_running()
    cf = "—"
    if BOT_READY.exists():
        try:
            cf = json.loads(BOT_READY.read_text(encoding="utf-8")).get("cloudflare_url", "—")
        except json.JSONDecodeError:
            pass
    mt5_on = is_mt5_process_running()
    return (
        f"🖥️ **{PC_NAME}**\n"
        f"Bot Gold Sniper: {'🟢 ACTIF' if bot_on else '🔴 ARRÊTÉ'}\n"
        f"MT5 terminal: {'🟢 ACTIF' if mt5_on else '🔴 ARRÊTÉ'}\n"
        f"{ram_cpu_line}\n"
        f"Uptime manager: {uptime // 3600}h {(uptime % 3600) // 60}m\n"
        f"Dashboard: {cf}"
    )


def _offline_status_message() -> str:
    return (
        "🔴 **Moteur Gold Sniper arrêté** (pas de heartbeat)\n"
        "Lance `!start` et attends le message avec l'URL Cloudflare.\n\n"
        + _pc_status_text()
    )


class PCManagerBot(discord.Client):
    def __init__(self, **kwargs) -> None:
        intents = Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents, **kwargs)

    async def _async_setup_hook(self) -> None:
        """Configure le connecteur aiohttp avant static_login (Python 3.14 / SSL)."""
        import aiohttp
        from discord.utils import MISSING

        from utils.ssl_bundle import create_ssl_context, ssl_verify_enabled

        if self.http.connector is MISSING:
            self.http.connector = aiohttp.TCPConnector(
                limit=0,
                ssl=create_ssl_context(),
            )
        if not ssl_verify_enabled():
            logging.warning(
                "DISCORD_SSL_VERIFY=0 — verification SSL Discord desactivee"
            )
        await super()._async_setup_hook()

    async def on_ready(self) -> None:
        logging.info("PC Manager Discord connecté: %s", self.user)

    def _authorized(self, message: discord.Message) -> bool:
        if DISCORD_USER_ID and message.author.id != DISCORD_USER_ID:
            return False
        if DISCORD_COMMANDS_CHANNEL and message.channel.id != DISCORD_COMMANDS_CHANNEL:
            return False
        return True

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self._authorized(message):
            return
        text = (message.content or "").strip()
        if not text:
            return

        cmd, _args, normalized = normalize_command(text)

        if cmd in LIFECYCLE_COMMANDS:
            await self._handle_lifecycle(message, cmd.replace("-", "_"))
            return

        if is_gold_sniper_running():
            _enqueue_command(normalized, message.author.id)
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass
            return

        if cmd in ("help",):
            await message.channel.send(format_help_text())
            return
        if cmd in ("status",):
            await message.channel.send(_offline_status_message())
            return
        await message.channel.send(
            "🔴 Moteur arrêté — lance `!start` et réessaie quand tu vois l'URL Cloudflare.\n"
            "`!help` pour la liste des commandes."
        )

    async def _handle_lifecycle(self, message: discord.Message, cmd: str) -> None:
        from utils.lifecycle_lock import claim_discord_message

        if cmd == "kill":
            _reset_start_cooldowns()
        if not claim_discord_message(message.id, cmd):
            logging.info("Lifecycle dedup: ignore %s id=%s", cmd, message.id)
            return
        if _debounced(cmd):
            await message.channel.send("⏳ Commande ignorée (cooldown ou démarrage déjà en cours).")
            return
        if cmd in ("start", "restart"):
            await message.channel.send(
                "⏳ Démarrage en cours — le cold start MT5 peut prendre 2 à 3 minutes…"
            )
        reply = await asyncio.to_thread(_run_lifecycle, cmd)
        if reply:
            await message.channel.send(reply)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != DISCORD_USER_ID:
            return
        custom_id = getattr(interaction, "data", None) and getattr(interaction.data, "custom_id", None)
        if not custom_id and hasattr(interaction, "custom_id"):
            custom_id = interaction.custom_id
        if not custom_id:
            return
        if custom_id == "gs_pause":
            if is_gold_sniper_running():
                _enqueue_command("!pause", interaction.user.id)
                await interaction.response.send_message("⏸️ Pause demandée", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "🔴 Bot arrêté — lance !start d'abord", ephemeral=True
                )
        elif custom_id == "gs_kill":
            if _debounced("kill"):
                await interaction.response.send_message("Cooldown actif", ephemeral=True)
                return
            msg = await asyncio.to_thread(_perform_kill)
            await interaction.response.send_message(msg, ephemeral=True)


def _is_fresh_windows_boot(max_uptime_seconds: float = 600.0) -> bool:
    try:
        import psutil

        return (time.time() - psutil.boot_time()) < max_uptime_seconds
    except Exception:
        return False


def _boot_policy() -> None:
    from utils.single_instance import can_start_bot_stack

    if _is_fresh_windows_boot() and KILL_FLAG.exists():
        logging.info("Boot Windows frais — suppression kill_flag obsolete")
        _clear_kill_flag()
    if KILL_FLAG.exists():
        logging.info("Boot policy: kill_flag présent => attente !start")
        return
    if not can_start_bot_stack() or _bot_already_active():
        logging.info("Boot policy: bot déjà actif — pas d'autostart")
        return
    logging.info("Boot policy: pas de kill_flag => autostart bot")
    notify_boot("🔄 Autostart Gold Sniper en cours apres demarrage Windows…")
    reply = _perform_start()
    if reply:
        notify_boot(reply)


def main() -> None:
    if not DISCORD_TOKEN:
        logging.critical("DISCORD_TOKEN manquant dans .env")
        sys.exit(1)
    _terminate_duplicate_pc_managers()
    _acquire_single_instance()
    logging.info("PC Manager démarré sur %s (PID %s)", PC_NAME, os.getpid())
    threading.Timer(8.0, lambda: threading.Thread(target=_boot_policy, daemon=True).start()).start()
    bot = PCManagerBot()
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
