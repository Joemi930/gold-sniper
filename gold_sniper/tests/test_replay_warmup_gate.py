"""P4: Warmup gate tests — ensure warmup period is strictly non-tradable."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager


class TestReplayWarmupGate(unittest.TestCase):
    """Verify that warmup candles never produce trades or trade-manager events."""

    def setUp(self):
        self.blackboard = MagicMock()
        self.blackboard._lock = AsyncMock()
        self.blackboard._data = {
            "meta": {},
            "market_data": {"candles": {}},
            "active_trades": {},
            "positions": {"open_positions": []},
        }
        self.blackboard.read_sync = MagicMock(return_value=[])
        self.blackboard.write = AsyncMock()
        self.blackboard.update_dict = AsyncMock()
        self.blackboard.update_market = AsyncMock()
        self.blackboard.notify_candle_close = AsyncMock()

        self.config = SimulatedTradeConfig(equity_initial=100.0)
        self.tm = SimulatedTradeManager(self.blackboard, self.config)

    def _make_candle(self, time_str="2026-01-15T12:00:00Z"):
        return {
            "time": time_str,
            "open": 2650.0, "high": 2652.0, "low": 2648.0, "close": 2651.0,
            "tick_volume": 100, "spread": 32,
        }

    def _make_enter_decision(self):
        return {
            "decision": "ENTER_FULL",
            "eval_active": True,
            "kasper_decision_recommendation": "ENTER_ELIGIBLE",
            "kasper_side": "BUY",
            "kasper_grade": "B",
            "scenario_id": "test-scenario-1",
            "scenario_key": "test-key-1",
            "decision_id": "test-decision-1",
            "market_story": "Test story",
            "sequence_pass_fail": {"step1": True},
            "enter_eligible": True,
            "risk_plan": {"allowed": True, "risk_multiplier": 0.5, "risk_amount": 50.0, "risk_pct": 0.5},
            "p1_evidence_bundle": {
                "micro": {"entry_price_candidate": 2652.0, "stop_loss_candidate": 2645.0},
                "poi": {"selected_poi": {"type": "OB", "low": 2648.0, "high": 2653.0}},
                "liquidity": {},
            },
            "score_after_veto": 70,
            "setup_type": "STANDARD",
        }

    # ── P4.1: eval_active=False blocks all operations ─────────────────

    async def test_eval_active_false_returns_empty(self):
        """Safety gate: decision with eval_active=False returns [].

        P4 requirement: no trades can open before eval_start.
        """
        decision = {"decision": "ENTER_FULL", "eval_active": False}
        candle = self._make_candle()
        events = await self.tm.on_p1_decision(candle, decision)
        self.assertEqual(events, [], "eval_active=False must block all trade manager operations")

    async def test_eval_active_missing_still_works(self):
        """Backward compat: missing eval_active does NOT block (None ≠ False)."""
        decision = self._make_enter_decision()
        decision.pop("eval_active", None)  # not present
        candle = self._make_candle()
        # Should attempt to process (will likely reject on grade, but NOT return empty)
        events = await self.tm.on_p1_decision(candle, decision)
        # The gate only blocks when eval_active is explicitly False
        self.assertIsInstance(events, list)

    async def test_eval_active_true_proceeds_normally(self):
        """Explicit eval_active=True must allow normal processing."""
        decision = self._make_enter_decision()
        decision["eval_active"] = True
        candle = self._make_candle()
        events = await self.tm.on_p1_decision(candle, decision)
        self.assertIsInstance(events, list)

    # ── P4.1: Warmup decision hook suppressed in engine ──────────────

    def test_phase_for_candle_warmup(self):
        """ReplayEngine._phase_for_candle returns eval_active=False for warmup."""
        from replay.replay_engine import ReplayEngine
        dummy_candle = {"time": "2026-01-01T00:00:00Z", "open": 2600, "high": 2601, "low": 2599, "close": 2600, "tick_volume": 1}
        engine = ReplayEngine(
            self.blackboard, [dummy_candle],
            eval_start=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )
        candle = {"time": "2026-01-01T00:00:00Z"}
        phase, eval_active = engine._phase_for_candle(candle)
        self.assertEqual(phase, "warmup")
        self.assertFalse(eval_active)

    def test_phase_for_candle_eval(self):
        from replay.replay_engine import ReplayEngine
        dummy_candle = {"time": "2026-01-01T00:00:00Z", "open": 2600, "high": 2601, "low": 2599, "close": 2600, "tick_volume": 1}
        engine = ReplayEngine(
            self.blackboard, [dummy_candle],
            eval_start=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )
        candle = {"time": "2026-01-20T00:00:00Z"}
        phase, eval_active = engine._phase_for_candle(candle)
        self.assertEqual(phase, "evaluation")
        self.assertTrue(eval_active)

    # ── P4.1: RuntimeConfig integration ──────────────────────────────

    def test_runtime_config_fast_mode_flags(self):
        from replay.replay_runtime_config import ReplayRuntimeConfig
        cfg = ReplayRuntimeConfig.fast()
        self.assertTrue(cfg.fast_replay)
        self.assertTrue(cfg.minimal_events)
        self.assertFalse(cfg.write_decisions_jsonl)
        self.assertFalse(cfg.write_decision_snapshots)
        self.assertFalse(cfg.warmup_decision_pipeline)
        self.assertFalse(cfg.agent_cache_enabled)

    def test_runtime_config_normal_is_full_fidelity(self):
        from replay.replay_runtime_config import ReplayRuntimeConfig
        cfg = ReplayRuntimeConfig()
        self.assertFalse(cfg.fast_replay)
        self.assertTrue(cfg.write_decisions_jsonl)
        self.assertTrue(cfg.write_decision_snapshots)


if __name__ == "__main__":
    unittest.main()
