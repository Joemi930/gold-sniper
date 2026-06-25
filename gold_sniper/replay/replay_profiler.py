"""P3-E — Replay Profiler.

Measures time spent per-agent and per-operation during replay.
Add --profile-replay to any replay to activate.

Output: <run_dir>/profile_report.json

Metrics collected:
  - ms_total, ms_per_candle
  - ms_per_agent (decision hook)
  - agent_call_count
  - decision_hook_ms
  - trade_manager_ms
  - news_lookup_ms
  - candle_inject_ms
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProfileTimer:
    """Context manager for timing a code block."""
    label: str
    accumulator: dict[str, float]
    _start: float = 0.0

    def __enter__(self) -> ProfileTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self.accumulator[self.label] = self.accumulator.get(self.label, 0.0) + elapsed_ms


@dataclass
class ReplayProfiler:
    """Collects timing metrics during a replay run."""

    enabled: bool = False
    _total_start: float = 0.0
    _timings: dict[str, float] = field(default_factory=dict)
    _call_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _candle_count: int = 0
    _eval_candle_count: int = 0
    _agent_timings: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _agent_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def total_ms(self) -> float:
        return self._timings.get("total", 0.0)

    @property
    def ms_per_candle(self) -> float:
        return self.total_ms / max(1, self._candle_count)

    def start(self) -> None:
        self._total_start = time.perf_counter()

    def tick_candle(self, eval_active: bool = True) -> None:
        self._candle_count += 1
        if eval_active:
            self._eval_candle_count += 1

    def timer(self, label: str) -> ProfileTimer:
        return ProfileTimer(label, self._timings)

    def record_agent(self, agent_id: str, ms: float) -> None:
        self._agent_timings[agent_id] += ms
        self._agent_counts[agent_id] += 1

    def record_call(self, label: str) -> None:
        self._call_counts[label] += 1

    def finish(self) -> dict[str, Any]:
        elapsed_ms = (time.perf_counter() - self._total_start) * 1000.0
        self._timings["total"] = elapsed_ms
        return self.report()

    def report(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ms_total": round(self.total_ms, 3),
            "ms_per_candle": round(self.ms_per_candle, 6),
            "ms_per_eval_candle": round(
                self.total_ms / max(1, self._eval_candle_count), 6
            ),
            "candles_total": self._candle_count,
            "candles_eval": self._eval_candle_count,
            "timings": {k: round(v, 3) for k, v in sorted(self._timings.items())},
            "agent_timings": {k: round(v, 3) for k, v in sorted(self._agent_timings.items())},
            "agent_call_counts": dict(sorted(self._agent_counts.items())),
            "call_counts": dict(sorted(self._call_counts.items())),
        }

    def write_report(self, run_dir: str | Path) -> Path:
        report_path = Path(run_dir) / "profile_report.json"
        report = self.finish()
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_path


# ── Singleton for global access during replay ──────────────────────────────────
_profiler: ReplayProfiler | None = None


def get_profiler() -> ReplayProfiler:
    global _profiler
    if _profiler is None:
        _profiler = ReplayProfiler()
    return _profiler


def enable_profiling() -> ReplayProfiler:
    global _profiler
    _profiler = ReplayProfiler(enabled=True)
    _profiler.start()
    return _profiler


def disable_profiling() -> None:
    global _profiler
    _profiler = ReplayProfiler(enabled=False)
