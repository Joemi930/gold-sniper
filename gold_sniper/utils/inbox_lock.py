"""Verrou fichier cross-process pour discord_inbox.jsonl."""
from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path


@contextlib.contextmanager
def inbox_lock(lock_path: Path, timeout: float = 10.0):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    handle = None
    while time.monotonic() < deadline:
        try:
            handle = open(lock_path, "a+b")
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (OSError, BlockingIOError):
            if handle:
                handle.close()
                handle = None
            time.sleep(0.05)
    if handle is None:
        raise TimeoutError(f"Impossible d'acquerir le lock inbox: {lock_path}")
    try:
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
