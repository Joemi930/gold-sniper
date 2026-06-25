"""Signal de boot pour pc_manager (data/bot_ready.json)."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import config

PHASE_CLOUDFLARE_READY = "cloudflare_ready"
PHASE_ENGINE_READY = "engine_ready"
PHASE_CLOUDFLARE_FAILED = "cloudflare_failed"


def write_bot_ready(
    cloudflare_url: str | None,
    phase: str = PHASE_CLOUDFLARE_READY,
) -> None:
    path = Path(config.BOT_READY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ready_at": datetime.now(timezone.utc).isoformat(),
        "cloudflare_url": cloudflare_url or "",
        "phase": phase,
        "pid": os.getpid(),
        "mode": "LIVE" if config.LIVE_MODE else "PAPER",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def mark_engine_ready(cloudflare_url: str | None = None) -> None:
    path = Path(config.BOT_READY_PATH)
    existing_url = cloudflare_url or ""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing_url = existing_url or data.get("cloudflare_url", "")
        except json.JSONDecodeError:
            pass
    write_bot_ready(existing_url or None, phase=PHASE_ENGINE_READY)
