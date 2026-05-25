import os
import threading
from pathlib import Path
from typing import Callable

from utils.logger import get_logger


TRAY_TITLE = "Gold Sniper V2.1 — actif"


def _build_icon_image():
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (64, 64), (18, 18, 18, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=(212, 175, 55, 255), outline=(255, 241, 170, 255), width=3)
    try:
        font = ImageFont.truetype("arial.ttf", 19)
    except Exception:
        font = ImageFont.load_default()
    draw.text((18, 22), "GS", fill=(20, 20, 20, 255), font=font)
    return image


def open_logs_folder() -> None:
    logs_dir = Path("logs").resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    os.startfile(str(logs_dir))


def run_system_tray(
    stop_event: threading.Event,
    on_emergency_stop: Callable[[], None] | None = None,
) -> None:
    import pystray

    logger = get_logger()
    icon = pystray.Icon("gold_sniper", _build_icon_image(), TRAY_TITLE)

    def show_logs(_icon, _item):
        open_logs_folder()

    def emergency_stop(_icon, _item):
        logger.critical("Arrêt d'urgence demandé depuis le system tray.")
        if on_emergency_stop:
            on_emergency_stop()
        stop_event.set()
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Voir les logs", show_logs),
        pystray.MenuItem("Arrêt d'urgence", emergency_stop),
    )

    def watch_stop_event():
        stop_event.wait()
        icon.stop()

    threading.Thread(target=watch_stop_event, daemon=True).start()
    icon.run()
