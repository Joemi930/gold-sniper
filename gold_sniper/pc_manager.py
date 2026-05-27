import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# Load config variables securely without importing the whole heavy framework
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WATCHDOG_HEARTBEAT = ROOT_DIR / "watchdog_heartbeat.tmp"

PC_NAME = socket.gethostname()
STARTUP_TIME = time.time()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PC_MANAGER] %(message)s",
    handlers=[
        logging.FileHandler(ROOT_DIR / "logs" / "pc_manager.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def is_gold_sniper_running() -> bool:
    """Returns True if watchdog is actively updating its heartbeat, meaning Gold Sniper is ON."""
    if not WATCHDOG_HEARTBEAT.exists():
        return False
    # If heartbeat was updated in the last 15 seconds, it's running
    age = time.time() - WATCHDOG_HEARTBEAT.stat().st_mtime
    return age < 15.0

def send_telegram_request(method: str, payload: dict = None) -> dict:
    if not TELEGRAM_TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    headers = {"Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        logging.debug(f"Telegram API error: {e}")
        return {}

def poll_telegram(offset: int) -> int:
    """Polls telegram for /start or callbacks. Returns new offset."""
    response = send_telegram_request("getUpdates", {"offset": offset, "timeout": 10})
    if not response or not response.get("ok"):
        return offset

    updates = response.get("result", [])
    for update in updates:
        offset = update["update_id"] + 1
        
        # Handle regular messages
        if "message" in update and "text" in update["message"]:
            msg_date = update["message"].get("date", 0)
            if msg_date < STARTUP_TIME:
                continue
                
            text = update["message"]["text"].strip()
            chat_id = str(update["message"]["chat"]["id"])
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue
                
            if text.startswith("/start"):
                logging.info("Received /start command")
                keyboard = {
                    "inline_keyboard": [[
                        {"text": f"🚀 Lancer sur {PC_NAME}", "callback_data": f"start_gold_sniper_{PC_NAME}"}
                    ]]
                }
                msg = (
                    f"🖥️ **Gold Sniper PC Manager intercepté par : {PC_NAME}**\n\n"
                    "L'application est actuellement ARRÊTÉE sur cette machine.\n"
                    "Veux-tu la démarrer en arrière-plan ?"
                )
                send_telegram_request("sendMessage", {
                    "chat_id": chat_id,
                    "text": msg,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                })
        
        # Handle button clicks
        elif "callback_query" in update:
            callback = update["callback_query"]
            data = callback.get("data", "")
            cb_id = callback.get("id")
            chat_id = str(callback.get("message", {}).get("chat", {}).get("id"))
            msg_id = callback.get("message", {}).get("message_id")
            
            if data == f"start_gold_sniper_{PC_NAME}" and chat_id == str(TELEGRAM_CHAT_ID):
                logging.info(f"Launch triggered via callback for {PC_NAME}")
                send_telegram_request("answerCallbackQuery", {
                    "callback_query_id": cb_id,
                    "text": f"Démarrage en cours sur {PC_NAME}..."
                })
                send_telegram_request("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": f"✅ Commande de démarrage lancée sur **{PC_NAME}**.\nLe Watchdog prend le relais."
                })
                
                # Launch LancerGoldSniper.vbs
                vbs_path = ROOT_DIR / "LancerGoldSniper.vbs"
                if vbs_path.exists():
                    subprocess.Popen(["wscript.exe", str(vbs_path)], shell=True)
                else:
                    logging.error("LancerGoldSniper.vbs not found!")
                    send_telegram_request("sendMessage", {
                        "chat_id": chat_id,
                        "text": f"❌ Fichier LancerGoldSniper.vbs introuvable sur {PC_NAME}."
                    })
                
                # Once launched, we sleep immediately so Gold Sniper takes over polling
                time.sleep(5)
                
    return offset

def main():
    logging.info(f"PC Manager demarre sur {PC_NAME}")
    offset = 0
    while True:
        try:
            if is_gold_sniper_running():
                # Gold Sniper is running, do not poll Telegram to avoid stealing messages!
                time.sleep(10)
            else:
                # Gold Sniper is OFF, we act as the standby listener
                offset = poll_telegram(offset)
                time.sleep(1)
        except Exception as e:
            logging.error(f"Erreur boucle principale: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
