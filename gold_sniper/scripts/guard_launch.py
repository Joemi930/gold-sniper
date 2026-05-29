"""Usage: python guard_launch.py manager|bot  (exit 0 = OK lancer, 1 = deja actif)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.single_instance import can_start_bot_stack, can_start_manager


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: guard_launch.py manager|bot", file=sys.stderr)
        return 2
    mode = sys.argv[1].strip().lower()
    if mode == "manager":
        return 0 if can_start_manager() else 1
    if mode in ("bot", "gold", "stack"):
        return 0 if can_start_bot_stack() else 1
    print(f"Mode inconnu: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
