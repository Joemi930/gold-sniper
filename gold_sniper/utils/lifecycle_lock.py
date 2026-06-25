"""Verrou lifecycle cross-process (!start / !kill) + dedup messages Discord."""
from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIFECYCLE_LOCK = ROOT / "data" / "lifecycle.lock"
LIFECYCLE_DEDUP = ROOT / "data" / "lifecycle_dedup.json"


@contextlib.contextmanager
def lifecycle_lock(timeout: float = 600.0):
    from utils.inbox_lock import inbox_lock

    with inbox_lock(LIFECYCLE_LOCK, timeout=timeout):
        yield


def claim_discord_message(message_id: int | str, cmd: str, ttl: float = 120.0) -> bool:
    """
    Retourne True si ce message peut etre traite (premier manager qui claim).
    False = un autre manager ou une instance precedente l'a deja pris.
    """
    from utils.inbox_lock import inbox_lock

    key = f"{message_id}:{cmd}"
    now = time.time()
    dedup_lock = LIFECYCLE_DEDUP.with_suffix(".lock")
    LIFECYCLE_DEDUP.parent.mkdir(parents=True, exist_ok=True)
    try:
        with inbox_lock(dedup_lock, timeout=5.0):
            data: dict[str, float] = {}
            if LIFECYCLE_DEDUP.exists():
                try:
                    raw = json.loads(LIFECYCLE_DEDUP.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = {k: float(v) for k, v in raw.items()}
                except (json.JSONDecodeError, TypeError, ValueError):
                    data = {}
            data = {k: t for k, t in data.items() if now - t < ttl}
            if key in data:
                return False
            data[key] = now
            LIFECYCLE_DEDUP.write_text(json.dumps(data), encoding="utf-8")
            return True
    except TimeoutError:
        return False
