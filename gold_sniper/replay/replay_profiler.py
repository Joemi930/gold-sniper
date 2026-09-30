"""P3-E / P4 — Replay Profiler.

Measures time spent per-agent, per-section, and per-operation during replay.
Add --profile-replay to any replay to activate.

Output: <run_dir>/profile_report.json

Metrics collected:
  - ms_total, ms_per_candle, ms_per_eval_candle
  - per-section timings (inject, decision, trade_manager, etc.)
  - per-agent timings with avg/max/count
  - top bottlenecks
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


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
class SectionStat:
    """Aggregated timing stats for one section."""
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0


@dataclass
class ReplayProfiler:
    """Collects timing metrics during a replay run.

    P4: adds section-level profiling with count/avg/max.
    """

    enabled: bool = False
    _total_start: float = 0.0
    _timings: dict[str, float] = field(default_factory=dict)
    _call_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _candle_count: int = 0
    _eval_candle_count: int = 0
    _agent_timings: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _agent_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # P4: section-level stats
    _sections: dict[str, SectionStat] = field(default_factory=lambda: defaultdict(SectionStat))

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

    @contextmanager
    def section(self, name: str) -> Iterator[None]:
        """P4: context manager for timing a named section."""
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            ms = (time.perf_counter() - t0) * 1000
            s = self._sections[name]
            s.count += 1
            s.total_ms += ms
            s.max_ms = max(s.max_ms, ms)

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
        section_data = {}
        for name, s in sorted(self._sections.items()):
            count = s.count
            total = s.total_ms
            section_data[name] = {
                "count": count,
                "total_ms": round(total, 3),
                "avg_ms": round(total / count, 3) if count else 0.0,
                "max_ms": round(s.max_ms, 3),
            }

        # Identify top bottlenecks by total time
        ranked = sorted(section_data.items(), key=lambda x: x[1]["total_ms"], reverse=True)
        top_bottlenecks = [{"rank": i + 1, "section": k, **v} for i, (k, v) in enumerate(ranked[:10])]

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
            "sections": section_data,
            "top_bottlenecks": top_bottlenecks,
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
