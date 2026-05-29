"""Instrumentation NDJSON pour la session debug 74860f."""
from __future__ import annotations

import json
import time
from pathlib import Path

DEBUG_LOG = Path(__file__).resolve().parent.parent / "debug-74860f.log"
SESSION_ID = "74860f"


def debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
    run_id: str = "pre-fix",
) -> None:
    # region agent log
    try:
        entry = {
            "sessionId": SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with DEBUG_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # endregion
