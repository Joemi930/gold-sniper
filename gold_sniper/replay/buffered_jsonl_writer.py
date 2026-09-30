"""P4: Buffered JSONL writer — reduces I/O syscalls by batching writes.

Replaces per-event file opens in replay_engine._append_event().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BufferedJsonlWriter:
    """Append-only JSONL writer with an in-memory buffer.

    Flushes automatically when the buffer reaches *flush_every* lines,
    and on explicit ``flush()`` or ``close()``.
    """

    def __init__(
        self,
        path: Path,
        *,
        flush_every: int = 1000,
        encoding: str = "utf-8",
    ) -> None:
        self.path = Path(path)
        self.flush_every = max(1, int(flush_every))
        self.encoding = encoding
        self._buffer: list[str] = []
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate / create
        self.path.write_text("", encoding=self.encoding)

    # ------------------------------------------------------------------
    def write(self, obj: dict[str, Any]) -> None:
        """Append a JSON object to the buffer; auto-flush if full."""
        if self._closed:
            raise RuntimeError("BufferedJsonlWriter is closed")
        self._buffer.append(
            json.dumps(obj, ensure_ascii=False, default=str)
        )
        if len(self._buffer) >= self.flush_every:
            self.flush()

    # ------------------------------------------------------------------
    def flush(self) -> None:
        """Write all buffered lines to disk."""
        if not self._buffer:
            return
        with self.path.open("a", encoding=self.encoding) as handle:
            handle.write("\n".join(self._buffer))
            handle.write("\n")
        self._buffer.clear()

    # ------------------------------------------------------------------
    def close(self) -> None:
        """Flush remaining lines and mark the writer as closed."""
        if not self._closed:
            self.flush()
            self._closed = True

    # ------------------------------------------------------------------
    @property
    def buffered_lines(self) -> int:
        return len(self._buffer)

    @property
    def closed(self) -> bool:
        return self._closed
