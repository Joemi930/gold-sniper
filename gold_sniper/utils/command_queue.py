"""File-based command queue: pc_manager enqueues, main.py processes."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

QUEUE_FILE = Path("data") / "command_queue.json"
PC_MANAGER_COMMANDS = frozenset({"/start", "/kill", "/restart", "/pc_status"})


def _read_queue() -> dict:
    if not QUEUE_FILE.exists():
        return {"commands": []}
    try:
        payload = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("commands"), list):
            return payload
    except Exception:
        pass
    return {"commands": []}


def _write_queue(payload: dict) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(QUEUE_FILE)


def enqueue_command(cmd: str, update_id: int | None = None) -> str:
    """Append a command for main.py to process. Returns command id."""
    cmd = cmd.split()[0].split("@", maxsplit=1)[0].lower()
    if cmd in PC_MANAGER_COMMANDS:
        raise ValueError(f"Commande reservee au PC Manager: {cmd}")
    payload = _read_queue()
    command_id = uuid.uuid4().hex[:12]
    payload["commands"].append(
        {
            "id": command_id,
            "cmd": cmd,
            "ts": datetime.now(timezone.utc).isoformat(),
            "update_id": update_id,
            "processed": False,
        }
    )
    # Garder une file raisonnable
    payload["commands"] = payload["commands"][-200:]
    _write_queue(payload)
    return command_id


def fetch_pending_commands(limit: int = 20) -> list[dict]:
    payload = _read_queue()
    pending = [c for c in payload.get("commands", []) if not c.get("processed")]
    return pending[:limit]


def mark_processed(command_id: str) -> None:
    payload = _read_queue()
    changed = False
    for item in payload.get("commands", []):
        if item.get("id") == command_id and not item.get("processed"):
            item["processed"] = True
            item["processed_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
            break
    if changed:
        _write_queue(payload)


def prune_processed(max_age_seconds: float = 3600.0) -> None:
    payload = _read_queue()
    now = time.time()
    kept = []
    for item in payload.get("commands", []):
        if not item.get("processed"):
            kept.append(item)
            continue
        ts = item.get("processed_at") or item.get("ts")
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            age = now - dt.timestamp()
        except Exception:
            age = 0.0
        if age < max_age_seconds:
            kept.append(item)
    if len(kept) != len(payload.get("commands", [])):
        payload["commands"] = kept
        _write_queue(payload)
