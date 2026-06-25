from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> int:
    heartbeat = Path(sys.argv[1])
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + 0.5
    while time.time() < end:
        heartbeat.write_text(str(time.time()), encoding="utf-8")
        time.sleep(0.1)
    time.sleep(5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
