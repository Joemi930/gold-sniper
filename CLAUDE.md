# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current phase: P1-READY (Gap-Closed, Provenance-Verified)

The repo is in **P1-ready** — data audit complete, gap closed, spread realism fixed, ready for baseline replays. The Replay Control Center V3.2 is built and tested. See `reports/DATA_PROVENANCE_AUDIT_REPORT.md` for complete provenance audit and `reports/P1_GOLD_SNIPER_REPLAY_APP_REPORT.md` for final P1 status.

**Data state:** 201,513 M1 candles (Dec 2025 → Jun 2026 continuous), 7 timeframes (incl. D1), spread=32 pts on histdata.com segment. Gap CLOSED.

**Hard prohibitions (PERMANENT until live-audit pass):**
- Never set `LIVE_MODE=1` or `ALLOW_BROKER_WRITES=1`
- Never call `mt5.order_send()` outside `execution/broker_gateway.py` → `execution/execution_guard.py`
- Never forced-ENTER, never lower thresholds tactically, never make `POI_REACTION` tradable
- Never leak future candles into the decision engine during replay
- Never call broker-write MT5 APIs from agents, strategy, replay, or data import code

## Architecture overview

Gold Sniper is a Python/asyncio XAUUSD trading engine following SMC/ICT/Kasper methodology. It has **two parallel pipelines** — the live runtime (legacy, disabled) and the replay/shadow Kasper pipeline (the active, validated path).

### Replay/shadow Kasper pipeline (the path that works)

```
CSV candles + JSONL news
  → replay.run_replay / ReplayEngine
  → ReplayDecisionPipeline (Agents 1–7 replay variants)
  → EvidenceBuilder → EvidenceBundle
  → KasperScenarioEngine (sequence verification, scoring, grade A+..D)
  → ProfessionalDecisionEngine (ENTER_FULL / ENTER_REDUCED / WAIT / REJECT)
  → RiskAllocator (grade → risk_pct)
  → SimulatedTradeManager (2-leg lifecycle: TP1 + protected runner → TP2)
  → trade_journal / summary.json / metrics
```

Critical source files for this path:
- `gold_sniper/replay/run_replay.py` — CLI entry point (`--run-id`, `--start`, `--end`, `--initial-equity`, `--diagnose-*`)
- `gold_sniper/replay/decision_pipeline.py` — orchestrates replay agents + EvidenceBuilder + PDE + Kasper
- `gold_sniper/replay/evidence_builder.py` — builds unified `EvidenceBundle` from agent observations
- `gold_sniper/strategy/kasper_scenario_engine.py` — Kasper sequence gates (HTF bias → liquidity → sweep → displacement → BOS → POI → retest → micro → risk)
- `gold_sniper/strategy/professional_decision_engine.py` — final ENTER/WAIT/REJECT with hard veto, readiness, scorecard, eligibility
- `gold_sniper/strategy/contracts.py` — all shared enums/dataclasses: `DecisionAction`, `SetupGrade`, `SetupType`, `TradeSide`, etc.
- `gold_sniper/strategy/risk_allocator.py` — grade → risk mapping (A_PLUS=1.00%, A=0.75%, B=0.50%, C=0.25%, D=0)
- `gold_sniper/replay/simulated_trade_manager.py` — 2-leg trade simulation with daily limiter, duplicate gate, fill model

### Live runtime (legacy, disabled)

```
MT5 ticks → mt5_bridge → tick_ingestion → candle_builder → BLACKBOARD
  → Agents 1–7 (live variants) → orchestrator → trade_signals
  → TradeManager → BrokerGateway → ExecutionGuard (fail-closed) → order_send
```

The live path exists but is **not validated** — it does not yet integrate Kasper/PDE directly. The orchestrator (`core/orchestrator.py`) produces `trade_signals` from agent scores using legacy logic, not the modern strategy pipeline.

Key live files (do not modify without plan approval):
- `gold_sniper/core/blackboard.py` — shared state dict (`market_data`, `agent_results`, `trade_signals`, `positions`, `daily_stats`, `meta`)
- `gold_sniper/execution/broker_gateway.py` — **the only allowed path** for `order_send`
- `gold_sniper/execution/execution_guard.py` — fail-closed gate before any broker write
- `gold_sniper/config.py` — **the single source of truth** for all runtime parameters (no constants hardcoded elsewhere)

### Agent roles (same across live and replay)

| Agent | Role | Key outputs |
|-------|------|-------------|
| Agent1 (Meteo) | HTF context, bias, structure | Bias (bullish/bearish/neutral), trend |
| Agent2 (Cartographe) | POI detection: OB, FVG, imbalance | POI zones, tradable/non-tradable |
| Agent3 (Liquidite) | Liquidity pools, sweep detection | Sweep side/type, liquidity events |
| Agent4 (Fibonacci) | Structure, BOS/CHoCH, OTE, premium/discount | Structure shifts, scenario hints |
| Agent5 (Microscope) | Micro confirmation, entry levels | Entry/SL/TP, trigger status, RR |
| Agent6 (Sentinelle) | News, spread, external conditions | Hard veto, blackout, hostile feed |
| Agent7 (Chronos) | Sessions, killzones, timing | Session label, Asia/Friday blocks |

### Data flow: replay temporal integrity

Replay injects candles progressively — the engine sees only past + current candle, never the future. Warmup data (typically December for a January evaluation) builds initial context but produces no official trades.

Data structure:
```
gold_sniper/data/historical/XAUUSD/
  1m/    # Source of truth (M1)
  5m/    # Derived or imported
  15m/
  1H/
  4H/
  manifest.json
```

Required but currently missing: M30 and D1 timeframes.

## Build, test, and run commands

```powershell
# Install dependencies
cd gold_sniper
powershell -ExecutionPolicy Bypass -File scripts\install_deps.ps1
# Or: python -m pip install -r requirements.txt
pip install rich  # Required for the Replay Control Center TUI

# ── Replay Control Center V3.2 (recommended entry point) ─────
# Interactive terminal menu
python -m gold_sniper.replay_app.Gold_Sniper_Replay

# CLI mode (no menu)
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu \
  --start 2026-01-01 --end 2026-01-08 \
  --warmup-start 2025-12-25 --run-id my_replay --initial-equity 100.0

# Generate synthetic test data (no MT5 needed)
python -m gold_sniper.replay_app.Gold_Sniper_Replay --generate-synthetic

# Check data availability
python -m gold_sniper.replay_app.Gold_Sniper_Replay --check-data

# ── Legacy replay CLI ────────────────────────────────────────
python -m gold_sniper.replay.run_replay \
  --run-id smoke_test \
  --start 2026-01-01T00:00:00Z \
  --end 2026-01-08T00:00:00Z \
  --warmup-start 2025-12-01T00:00:00Z \
  --initial-equity 100.0

# ── Testing ──────────────────────────────────────────────────
# Run all tests
python -m unittest discover gold_sniper/tests -q

# Run a specific test file
python -m unittest gold_sniper.tests.test_p3_trade_lifecycle_two_legs -v

# Quick syntax validation (no MT5 needed)
python -m py_compile gold_sniper/config.py gold_sniper/core/blackboard.py gold_sniper/execution/trade_manager.py

# ── Data tools ───────────────────────────────────────────────
# Import MT5 historical data (read-only APIs only)
python tools/data_import/import_mt5_history.py --help

# Normalize news calendar CSV → JSONL
python tools/data_import/normalize_calendar_csv.py --help
```

## Key conventions

- **Configuration**: `gold_sniper/config.py` is the single source of truth for ALL runtime constants. Never hardcode values in other modules — import from config.
- **No secrets in repo**: `.env`, tokens, credentials, `data/memory.db`, logs, caches, and replay run outputs are all gitignored. Use `.env.example` as the template.
- **Enums in contracts**: All shared types (`DecisionAction`, `SetupGrade`, `SetupType`, etc.) live in `gold_sniper/strategy/contracts.py`. Import from there, don't redefine.
- **Blackboard pattern**: Live agents read/write through `BLACKBOARD` dict keys. Replay agents return structured observations — they don't touch the live blackboard.
- **Fail-closed execution**: `ExecutionGuard` must pass before any broker write. If guard state is uncertain, the default is BLOCK.
- **Test structure**: Tests in `gold_sniper/tests/` follow naming `test_<component>.py`. P1/P2/P3 prefixes indicate validation phase. Many tests call into replay infrastructure — they don't need MT5.
- **Agent observation pattern**: Each agent produces a typed observation (e.g., `Agent1Observation`). The EvidenceBuilder consumes these. PDE and Kasper consume the EvidenceBundle. Don't bypass this chain.

## Important divergences to resolve before any live activation

These are documented in `architecture.md` §12 and are blockers for live-safe operation:

1. **Protected SL**: Live legacy uses `BE_PLUS_RR = 0.10` (config.py), P3 replay uses `0.5R`. Must be harmonized.
2. **Live PDE integration**: The live orchestrator does not call `KasperScenarioEngine` or `ProfessionalDecisionEngine` — it uses legacy agent-score aggregation. The path `EvidenceBuilder → Kasper → PDE → RiskAllocator` is only proven in replay.
3. **Missing M30/D1 data**: Required for full multi-timeframe validation (6-12 month replays blocked without them).
4. **`C_CONFIRMED` grade**: Used in P3 replay policy but not formalized in `strategy/contracts.py` `SetupGrade` enum. Needs reconciliation.
5. **News default**: Confirm replay default picks up the most recent normalized calendar JSONL, not an older partial file.

## Design authority

`architecture.md` is the canonical architecture reference — it was produced by a full local audit and consolidates all contracts, doctrines, and known limitations. `P1_GOLD_SNIPER_TRADING_AND_OPTIMISATION.md` is the active P1 implementation specification. When these conflict with code comments or older docs, the architecture doc and P1 spec take precedence.
