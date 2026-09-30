# P4.2 — Canonical Call-Chain Documentation (Phase 3)

## Replay decision path (verified from code)

```
ReplayEngine.run()                          # replay_engine.py:132
  └─ for candle in clock:                   # L140 — every M1 candle
       ├─ _inject_candle(candle, index)     # L172-174 — MTF update, blackboard
       │   ├─ blackboard.update_dict("market_data.current_tick", ...)
       │   ├─ blackboard.update_market(...)
       │   ├─ blackboard.read_sync("market_data.candles.1m").append(candle)
       │   ├─ _mtf_builder.update(candle)   # multi_timeframe_builder.py:31
       │   │   └─ aggregate_candles() for each TF close
       │   └─ _inject_external_timeframe()  # pre-loaded 5m/15m/1H/4H
       │
       ├─ [WARMUP GATE] if not eval_active: continue   # L178-181
       │
       ├─ _call_decision_hook(candle, ...)  # L195 — THE HEAVY PATH
       │   └─ self.on_decision_hook(candle, blackboard)
       │       = ReplayDecisionPipeline.__call__()   # decision_pipeline.py:83
       │       ├─ Agents 1-7 run sequentially       # L92-108
       │       ├─ build_evidence_bundle_from_blackboard()  # L120
       │       ├─ validate_evidence_bundle()               # L125
       │       ├─ evaluate_professional_decision()         # L127
       │       ├─ evaluate_readiness_risk_gate()           # L1018
       │       ├─ build_kasper_evidence_bundle()           # L1043
       │       ├─ evaluate_kasper_scenario()               # L1056
       │       └─ Kasper/PDE alignment bridge              # L1112-1247
       │
       ├─ trade_manager.on_p1_decision(candle, decision)  # L230
       │   └─ SimulatedTradeManager — 2-leg lifecycle
       │
       └─ _append_event() / _build_summary()
```

## D10 Resolution: `strategies/` vs `gold_sniper/strategy/`

**VERDICT: NOT a duplicate. Different subsystems, different roles.**

### `gold_sniper/strategies/` (legacy professional selectors)
- **Files**: `professional_strategy_selector.py`, `ob_five_star_evidence.py`,
  `ob_five_star_strict.py`, `fvg_*.py`, `premium_strict.py`, etc.
- **Role**: OB lifecycle normalization, FVG pattern scoring, professional strategy
  classification for **reporting/summary/diagnostics**
- **Used by**: `replay_engine.py` L18-19 (for `_build_summary`),
  `test_professional_strategy_selector.py`
- **Import path**: `from strategies.*` (no `gold_sniper.` prefix — legacy)

### `gold_sniper/strategy/` (canonical P1+ strategy)
- **Files**: `contracts.py`, `kasper_scenario_engine.py`,
  `professional_decision_engine.py`, `risk_allocator.py`,
  `poi_readiness_contract.py`, `poi_rejection_contract.py`, etc.
- **Role**: Kasper scenario evaluation, PDE decision, risk allocation,
  POI contracts — **the decision-making core**
- **Used by**: `decision_pipeline.py`, agents, tests
- **Import path**: `from gold_sniper.strategy.*`

### Action taken
- **NO deletion** — these are separate subsystems
- **NO rename** — would break imports across the codebase
- **Documented** — this analysis prevents future confusion
- The `strategies/` modules are marked as "legacy, disable in fast mode"
  per the plan (§E)

## Key findings
1. The heavy path (agents → EvidenceBuilder → PDE → Kasper) runs on EVERY M1 candle
   during eval — this is D1, the root cause of 475s/week runtime
2. `_inject_candle` also runs on every candle including warmup (D2)
3. The `strategies/` imports in `replay_engine.py` are only for summary building,
   NOT for per-candle decisions — they are NOT on the hot path
4. The decision pipeline does NOT call `strategies/` modules — it uses
   `gold_sniper/strategy/` exclusively
