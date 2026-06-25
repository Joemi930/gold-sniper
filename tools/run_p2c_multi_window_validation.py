from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
gold_sniper_path = PROJECT_ROOT / "gold_sniper"
if str(gold_sniper_path) not in sys.path:
    sys.path.insert(0, str(gold_sniper_path))

from gold_sniper.validation.p2c_multi_window_validation import main


if __name__ == "__main__":
    raise SystemExit(main())
