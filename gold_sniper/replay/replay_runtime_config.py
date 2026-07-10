"""P4: Replay runtime configuration — controls fast/slow mode, logging, profiling.

Provides a frozen dataclass consumed by ReplayEngine and live_runner to
toggle performance-sensitive behaviour without changing strategy logic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayRuntimeConfig:
    """Immutable runtime flags for replay execution.

    All fields are read-only — create a new instance to change mode.
    """

    fast_replay: bool = False
    minimal_events: bool = False
    profile_replay: bool = False
    event_buffer_size: int = 1000
    state_update_every_n_candles: int = 500
    state_update_every_seconds: float = 1.0
    write_decisions_jsonl: bool = True
    write_decision_snapshots: bool = True
    warmup_decision_pipeline: bool = False
    # P4: agent cache toggle (disabled by default — verify parity first)
    agent_cache_enabled: bool = False
    # P4: TUI throttle
    tui_throttle_enabled: bool = True

    @classmethod
    def fast(cls, *, profile_replay: bool = False) -> "ReplayRuntimeConfig":
        """Pre-built fast-replay configuration.

        - Warmup is context-only (no decision pipeline).
        - Events are minimal (only trade lifecycle events).
        - JSONL writes are buffered (5000 lines).
        - TUI state updates every 1000 candles or 2 seconds.
        - Decision snapshots and decisions.jsonl are skipped.
        """
        return cls(
            fast_replay=True,
            minimal_events=True,
            profile_replay=profile_replay,
            event_buffer_size=5000,
            state_update_every_n_candles=1000,
            state_update_every_seconds=2.0,
            write_decisions_jsonl=False,
            write_decision_snapshots=False,
            warmup_decision_pipeline=False,
            agent_cache_enabled=False,
            tui_throttle_enabled=True,
        )

    @classmethod
    def normal(cls) -> "ReplayRuntimeConfig":
        """Full-fidelity mode — every event and snapshot written."""
        return cls()
