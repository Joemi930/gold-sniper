"""P4.2 — ProfilerV2 with ≥95% coverage target.

Replaces the coarse P3/P4 profiler with per-component timing buckets:
  feature_update, inject_candle, candidate_scan, agents (per-agent),
  evidence_builder, kasper_pde, risk, trade_lifecycle, report_writer.

Exposes `unaccounted_ms` so invisible runtime is never hidden.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


# ── Mandatory section names (the 8 buckets the plan requires) ──────────
SECTION_FEATURE_UPDATE = "feature_update"
SECTION_INJECT_CANDLE = "inject_candle"
SECTION_CANDIDATE_SCAN = "candidate_scan"
SECTION_AGENTS = "agents"            # sub-keys: agent_1..agent_7
SECTION_EVIDENCE_BUILDER = "evidence_builder"
SECTION_KASPER_PDE = "kasper_pde"
SECTION_RISK = "risk"
SECTION_TRADE_LIFECYCLE = "trade_lifecycle"
SECTION_REPORT_WRITER = "report_writer"

MANDATORY_SECTIONS = [
    SECTION_FEATURE_UPDATE,
    SECTION_INJECT_CANDLE,
    SECTION_CANDIDATE_SCAN,
    SECTION_AGENTS,
    SECTION_EVIDENCE_BUILDER,
    SECTION_KASPER_PDE,
    SECTION_RISK,
    SECTION_TRADE_LIFECYCLE,
    SECTION_REPORT_WRITER,
]


@dataclass
class SectionStat:
    """Aggregated timing for one named section."""
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    min_ms: float = float("inf")

    @property
    def avg_ms(self) -> float:
        return self.total_ms / max(1, self.count)


@dataclass
class ProfilerV2:
    """Per-component profiler with ≥95% coverage target.

    Usage::

        prof = ProfilerV2()
        prof.start()

        with prof.section("feature_update"):
            fs.update(candle)

        with prof.section("candidate_scan"):
            window = discovery.scan(fs, t)

        # per-agent sub-sections are recorded via prof.record_agent()
        with prof.section("agents"):
            for agent in agents:
                t0 = time.perf_counter()
                result = agent.run(...)
                prof.record_agent(agent.id, (time.perf_counter() - t0) * 1000)

        prof.finish()
        print(prof.report())
    """

    enabled: bool = True
    _total_start: float = 0.0
    _total_ms: float = 0.0
    _sections: dict[str, SectionStat] = field(default_factory=lambda: defaultdict(SectionStat))
    _agent_timings: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _agent_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _candle_count: int = 0
    _eval_candle_count: int = 0
    _warmup_candle_count: int = 0

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        self._total_start = time.perf_counter()

    def finish(self) -> dict[str, Any]:
        self._total_ms = (time.perf_counter() - self._total_start) * 1000.0
        return self.report()

    # ── candle counting ────────────────────────────────────────────────

    def tick_candle(self, eval_active: bool = True) -> None:
        self._candle_count += 1
        if eval_active:
            self._eval_candle_count += 1
        else:
            self._warmup_candle_count += 1

    # ── section timing ─────────────────────────────────────────────────

    @contextmanager
    def section(self, name: str) -> Iterator[None]:
        """Context manager that accumulates ms into the named bucket."""
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            s = self._sections[name]
            s.count += 1
            s.total_ms += ms
            s.max_ms = max(s.max_ms, ms)
            s.min_ms = min(s.min_ms, ms)

    def record_agent(self, agent_id: str, ms: float) -> None:
        """Record per-agent timing (called inside the 'agents' section)."""
        self._agent_timings[agent_id] += ms
        self._agent_counts[agent_id] += 1

    # ── coverage ───────────────────────────────────────────────────────

    @property
    def accounted_ms(self) -> float:
        """Sum of all section timings (what we measured)."""
        return sum(s.total_ms for s in self._sections.values())

    @property
    def unaccounted_ms(self) -> float:
        """Runtime NOT captured by any section."""
        return max(0.0, self._total_ms - self.accounted_ms)

    def coverage_pct(self) -> float:
        """Percentage of total runtime covered by named sections."""
        denom = max(1e-9, self._total_ms)
        return round(self.accounted_ms / denom * 100.0, 2)

    # ── report ─────────────────────────────────────────────────────────

    def report(self) -> dict[str, Any]:
        """Produce a compact profiling report."""
        sections_out: dict[str, dict[str, Any]] = {}
        for name in MANDATORY_SECTIONS:
            s = self._sections.get(name)
            if s is None:
                sections_out[name] = {"count": 0, "total_ms": 0.0, "avg_ms": 0.0, "max_ms": 0.0}
                continue
            sections_out[name] = {
                "count": s.count,
                "total_ms": round(s.total_ms, 3),
                "avg_ms": round(s.avg_ms, 3),
                "max_ms": round(s.max_ms, 3),
                "pct_of_total": round(s.total_ms / max(1e-9, self._total_ms) * 100.0, 2),
            }

        # Any extra sections that were timed but aren't in the mandatory list
        for name, s in self._sections.items():
            if name not in sections_out:
                sections_out[name] = {
                    "count": s.count,
                    "total_ms": round(s.total_ms, 3),
                    "avg_ms": round(s.avg_ms, 3),
                    "max_ms": round(s.max_ms, 3),
                    "pct_of_total": round(s.total_ms / max(1e-9, self._total_ms) * 100.0, 2),
                }

        # Rank by total time
        ranked = sorted(sections_out.items(), key=lambda kv: kv[1]["total_ms"], reverse=True)
        top_bottlenecks = [
            {"rank": i + 1, "section": name, **data}
            for i, (name, data) in enumerate(ranked[:10])
        ]

        agent_report: dict[str, dict[str, Any]] = {}
        for agent_id in sorted(self._agent_timings):
            total = self._agent_timings[agent_id]
            count = self._agent_counts[agent_id]
            agent_report[agent_id] = {
                "total_ms": round(total, 3),
                "count": count,
                "avg_ms": round(total / max(1, count), 3),
                "pct_of_total": round(total / max(1e-9, self._total_ms) * 100.0, 2),
            }

        coverage = self.coverage_pct()

        return {
            "enabled": self.enabled,
            "ms_total": round(self._total_ms, 3),
            "ms_per_candle": round(self._total_ms / max(1, self._candle_count), 6),
            "candles_total": self._candle_count,
            "candles_eval": self._eval_candle_count,
            "candles_warmup": self._warmup_candle_count,
            "accounted_ms": round(self.accounted_ms, 3),
            "unaccounted_ms": round(self.unaccounted_ms, 3),
            "coverage_pct": coverage,
            "coverage_sufficient": coverage >= 95.0,
            "sections": sections_out,
            "agent_timings": agent_report,
            "top_bottlenecks": top_bottlenecks,
        }

    def write_report(self, run_dir: str | Path) -> Path:
        """Write profile report to <run_dir>/profile_report_v2.json."""
        report_path = Path(run_dir) / "profile_report_v2.json"
        data = self.finish() if self._total_ms == 0.0 else self.report()
        report_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_path


# ── Singleton ──────────────────────────────────────────────────────────
_profiler_v2: ProfilerV2 | None = None


def get_profiler_v2() -> ProfilerV2:
    global _profiler_v2
    if _profiler_v2 is None:
        _profiler_v2 = ProfilerV2()
    return _profiler_v2


def enable_profiling_v2() -> ProfilerV2:
    global _profiler_v2
    _profiler_v2 = ProfilerV2(enabled=True)
    _profiler_v2.start()
    return _profiler_v2


def disable_profiling_v2() -> None:
    global _profiler_v2
    _profiler_v2 = ProfilerV2(enabled=False)
