"""P4.2 fix — ReplayEngineV2 must drive trades through the REAL SimulatedTradeManager.

Regression target: a candidate that yields ENTER_REDUCED + risk_allowed produced
0 trades because the engine used a toy stub lifecycle (prices=0, hardcoded RR) and
never called SimulatedTradeManager.on_p1_decision. These tests pin the corrected
wiring using a fake async trade manager (no replay data required).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gold_sniper.replay.candidate_window import DecisionRecord
from gold_sniper.replay.replay_engine_v2 import ReplayEngineV2


# ── Fakes ──────────────────────────────────────────────────────────────
class FakeTradeManager:
    """Records every on_p1_decision call; reports 1 closed trade in summary."""

    def __init__(self):
        self.calls: list[dict] = []

    async def on_p1_decision(self, candle, decision):
        self.calls.append(decision)
        return []

    def summary(self):
        return {"total_trades": 1, "expectancy_R": 0.30}


class FakeDiscovery:
    """Yields a window only on the target candle index."""

    def __init__(self, hit_index: int):
        self.hit_index = hit_index
        self._i = -1
        self._gate_rejections: dict[str, int] = {}

    def scan(self, fs, t, price):
        self._i += 1
        return object() if self._i == self.hit_index else None

    # no-op recorders used by the engine
    def record_setup_type(self, *_): ...
    def record_poi_reaction_skip(self): ...
    def is_tradable_setup(self, setup_type): return setup_type == "SWEEP_REVERSAL"


class FakeEvaluator:
    def __init__(self, rec): self._rec = rec
    def evaluate(self, window, blackboard, candle=None): return self._rec


def _candles(n: int):
    base = datetime(2025, 12, 8, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        out.append({
            "time": base + timedelta(minutes=i),
            "open": 2000.0, "high": 2001.0, "low": 1999.0, "close": 2000.0,
        })
    return out


def _enter_record():
    return DecisionRecord(
        window=None, decision="ENTER_REDUCED", setup_grade="A",
        setup_type="SWEEP_REVERSAL", side="BUY", confidence_score=0.8,
        hard_veto=False, veto_code=None, risk_multiplier=0.75, risk_allowed=True,
        reject_reason=None,
        p1_payload={"decision": "ENTER_REDUCED", "side": "BUY",
                    "risk_plan": {"allowed": True, "entry": 2000.0, "sl": 1990.0},
                    "risk_multiplier": 0.75, "setup_grade": "A"},
    )


def _build_engine(tm, discovery, evaluator, candles):
    eng = ReplayEngineV2(
        candles_1m=candles, decision_pipeline=lambda *a, **k: {}, trade_manager=tm,
        eval_start=candles[0]["time"], eval_end=candles[-1]["time"],
    )
    # inject fakes after construction (engine builds its own in run())
    eng._inject_fakes = (discovery, evaluator)  # type: ignore[attr-defined]
    return eng


# The engine builds discovery/evaluator inside run(); patch them in via subclass.
class _Engine(ReplayEngineV2):
    fake_discovery = None
    fake_evaluator = None

    def run(self, blackboard=None, profile=False):
        # monkeypatch the two collaborators right before the loop
        import gold_sniper.replay.replay_engine_v2 as mod
        orig_disc = mod.CandidateDiscoveryEngine
        orig_eval = mod.CandidateWindowEvaluator
        mod.CandidateDiscoveryEngine = lambda *a, **k: self.fake_discovery
        mod.CandidateWindowEvaluator = lambda *a, **k: self.fake_evaluator
        try:
            return super().run(blackboard=blackboard, profile=profile)
        finally:
            mod.CandidateDiscoveryEngine = orig_disc
            mod.CandidateWindowEvaluator = orig_eval


def test_enter_decision_reaches_real_trade_manager():
    candles = _candles(5)
    tm = FakeTradeManager()
    eng = _Engine(
        candles_1m=candles, decision_pipeline=lambda *a, **k: {}, trade_manager=tm,
        eval_start=candles[0]["time"], eval_end=candles[-1]["time"],
    )
    eng.fake_discovery = FakeDiscovery(hit_index=2)
    eng.fake_evaluator = FakeEvaluator(_enter_record())

    summary = eng.run()

    # trade manager called on EVERY eval candle (open-position management)
    assert len(tm.calls) == len(candles)
    # exactly one call carried the real ENTER payload (the windowed candle)
    enters = [c for c in tm.calls if c.get("decision") == "ENTER_REDUCED"]
    assert len(enters) == 1
    # it carried the REAL risk_plan, not a stub
    assert enters[0]["risk_plan"]["entry"] == 2000.0
    assert enters[0]["risk_plan"]["sl"] == 1990.0
    # summary trade truth comes from the manager, not toy counters
    assert summary["trade_count"] == 1
    assert summary["state"] == "TRADES"
    assert summary["expectancy_r"] == 0.30


def test_no_window_still_manages_open_positions():
    candles = _candles(4)
    tm = FakeTradeManager()
    eng = _Engine(
        candles_1m=candles, decision_pipeline=lambda *a, **k: {}, trade_manager=tm,
        eval_start=candles[0]["time"], eval_end=candles[-1]["time"],
    )
    eng.fake_discovery = FakeDiscovery(hit_index=99)  # never hits
    eng.fake_evaluator = FakeEvaluator(_enter_record())

    eng.run()

    # manager still called every eval candle, all no-op REJECT
    assert len(tm.calls) == len(candles)
    assert all(c.get("decision") == "REJECT" for c in tm.calls)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
