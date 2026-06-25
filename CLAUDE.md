# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project identity

Gold Sniper — XAUUSD trading engine, SMC/ICT/Kasper model. **P1-clean phase: offline, replay-only, shadow-only.** No live trading, no paper trading, no broker execution. The goal is a unified Kasper-driven XAUUSD engine that detects only premium setups, decides with full Kasper logic, manages real risk, logs every decision, and proves statistical edge on long replay.

**Repo**: [Joemi930/gold-sniper](https://github.com/Joemi930/gold-sniper)
**Current branch**: `P1-kasper-brain-core`
**Active phase**: P2.2 — Scenario Identity & Side Consistency Audit
**Role**: Maçon (Codex) — developer executor. Build, fix, test, verify, report. Don't invent doctrine.

## Permanent rules (doctrine over mechanics)

1. **No new autonomous strategy modules.** Every market concept enters the unified pipeline only as hard veto, soft score, POI quality signal, liquidity state, session/news/risk gate, micro confirmation feature, or explanatory field.
2. **Legacy modules under `gold_sniper/strategies/` are frozen.** Do not extend them.
3. **Never force `ENTER`, `enter_eligible=True`, or positive `risk_multiplier`.**
4. **`POI_REACTION` is not tradable.** Do not make it tradable.
5. **Never modify `risk_allocator`, `hard_veto_registry`, risk mapping, or thresholds without explicit proof and authorization.**
6. **No broker wire, no `LIVE_MODE`, no `order_send`, no paper execution.** `MetaTrader5` imports must stay confined to `gold_sniper/execution/`.
7. **Never commit generated artifacts** — `summary.json`, `decisions.jsonl`, `events.jsonl`, `trade_journal.jsonl`, validation reports, large CSVs, secrets, `.env`.
8. **Workflow**: understand → modify minimally → test → verify → report. Don't fix what isn't broken.
9. **Don't cheat on doctrine to make tests green or curves pretty.** Fix mechanics, not thresholds.

## Unified pipeline architecture

```
Agents 1..7
  → EvidenceBuilder (assembles EvidenceBundle, runs post-bundle reconciliation)
  → KasperScenarioEngine (narrative brain, strategic authority — scenario_id, market_story, grade)
  → SetupTaxonomy + ScoreCard (secondary confluence, sanity check)
  → ProfessionalDecisionEngine (execution gate aligned with Kasper)
  → RiskAllocator (grade → risk % mapping)
  → SimulatedTradeManager (shadow execution, lifecycle, duplicate gate, daily limiter)
  → ReplayEngine (historical proof)
  → PerformanceSummary (statistical proof)
```

Kasper decides. PDE executes if gates are clean. Risk allocates by grade. TradeManager simulates. No module bypasses Kasper.

### Key modules

| Directory | Role |
|---|---|
| `gold_sniper/strategy/` | Decision core: Kasper engine, PDE, contracts, readiness, risk, taxonomy |
| `gold_sniper/replay/` | Offline replay: evidence builder, decision pipeline, trade manager, execution model, journal |
| `gold_sniper/agents/` | Evidence producers (1-7) + POI contracts — produce observations, NOT decisions |
| `gold_sniper/validation/` | Performance summary, smoke validator, multi-window validation |
| `gold_sniper/data_pipeline/` | Candle manifests, news JSONL, timeframe aggregation |
| `gold_sniper/core/` | Blackboard, engine, orchestrator (legacy/frozen — not modified in P1) |
| `gold_sniper/execution/` | Broker gateway, trade manager (legacy — isolated, not used in replay) |
| `gold_sniper/strategies/` | Frozen legacy strategies — read-only, not extended |
| `gold_sniper/context/` | Market context, regime detection, zone lifecycle |

### Critical strategy files

| File | Role |
|---|---|
| `strategy/kasper_scenario_engine.py` | Narrative brain — scenario evaluation, market_story, grade, ENTER_ELIGIBLE |
| `strategy/kasper_contracts.py` | Immutable dataclasses for normalized agent outputs (Kasper lens layer) |
| `strategy/professional_decision_engine.py` | Execution gate — ENTER_FULL/REDUCED/WATCH/REJECT, aligned with Kasper |
| `strategy/contracts.py` | Core dataclasses: `EvidenceBundle`, `DecisionResult`, `RiskPlan`, `ScoreCard` |
| `strategy/risk_allocator.py` | Grade → risk % mapping (A_PLUS=1%, A=0.75%, B=0.50%, C/D=0%) |
| `strategy/setup_taxonomy.py` | Setup classification and entry thresholds |
| `strategy/enter_eligibility.py` | Final gate before trade execution |
| `strategy/hard_veto_registry.py` | Hard blocks (news, session, spread, cooldown) |
| `strategy/readiness.py` | Multi-section readiness check |
| `strategy/scorecard.py` | Scoring engine with veto pipeline (secondary, not primary authority) |
| `strategy/poi_micro_synergy_contract.py` | Post-agent POI/micro alignment |
| `strategy/liquidity_reconciliation.py` | Post-bundle liquidity evidence reconciliation |
| `strategy/poi_rejection_contract.py` | POI rejection decomposition (fatal/recoverable/unknown) |
| `replay/evidence_builder.py` | Assembles agent observations into `EvidenceBundle` |
| `replay/decision_pipeline.py` | Offline replay decision pipeline |
| `replay/simulated_trade_manager.py` | Shadow trade lifecycle, duplicate gate, daily limiter |
| `replay/shadow_live_policy.py` | Replay-only grade→risk%, equity-based sizing config |
| `replay/replay_engine.py` | Orchestrates replay, persists decisions/events |
| `replay/run_replay.py` | CLI entry point for offline replays |

### Agents (evidence producers only)

| Agent | Responsibility | Must NOT do |
|---|---|---|
| Agent1 (Météo) | HTF bias, structure, BOS/CHoCH, DOL | Never give an entry |
| Agent2 (Cartographe) | OB, FVG, breakers, POI quality, freshness | Never give an entry; POI alone insufficient |
| Agent3 (Liquidité) | Sweeps, reintegration, displacement, DOL | Sweep alone insufficient |
| Agent4 (Timing) | OTE zones, premium/discount, pullback quality | Timing alone insufficient |
| Agent5 (Microscope) | M1 trigger, micro CHoCH/BOS, retest, RR estimate | Never confirm without Agent1/2/3 |
| Agent6 (Sentinelle) | High-impact news, USD news, news veto | Never bypass news high impact |
| Agent7 (Chronos) | Session, killzone, Asia block, Friday halt, cooldown | Never bypass session blocks |

### Grade → Risk mapping (source of truth)

```
A_PLUS → ENTER_FULL → 1.00% capital
A      → ENTER_REDUCED → 0.75% capital
B      → ENTER_REDUCED → 0.50% capital
C      → WATCH_ONLY / 0%
D      → REJECT / 0%
```

If a replay cap is applied, it must be explicit: `requested_risk_pct`, `effective_risk_pct`, `risk_cap_applied`, `risk_cap_reason`.

### Kasper ENTER formula

```
ENTER = scenario_valid
        AND hard_veto_clear
        AND risk_realistic
        AND execution_possible
        AND kasper_side coherent
        AND RR >= 1.5
        AND scenario_key + decision_id present
        AND market_story present
        AND sequence_pass_fail complete
```

### Side consistency (absolute rule)

```
kasper_side == pde_side == signal.side == trade.type
```
Mismatch → `REJECT: SIDE_MISMATCH_KASPER_PDE_TRADE`

## Key commands

### Tests

```bash
# All tests
python -m unittest discover gold_sniper/tests

# Targeted test file
python -m unittest gold_sniper.tests.test_kasper_scenario_engine -v
python -m unittest gold_sniper.tests.test_p2_1_pde_kasper_alignment -v
python -m unittest gold_sniper.tests.test_p1_1_kasper_authority -v

# Phase regression suite
python -m unittest gold_sniper.tests.test_p2e_phase17_decision_chain_gates gold_sniper.tests.test_p2e_phase16_liquidity_reconciliation gold_sniper.tests.test_p2e_phase15_liquidity_sweep_candidate gold_sniper.tests.test_p2e_phase14_enter_eligibility_gate_decomposition gold_sniper.tests.test_p2e_phase7a_setup_taxonomy gold_sniper.tests.test_p2e_phase7b_enter_eligibility gold_sniper.tests.test_p2e_phase7c_risk_multiplier gold_sniper.tests.test_p2e_phase7d_readiness_coherence gold_sniper.tests.test_p2e_phase7e_pipeline_contract gold_sniper.tests.test_p2e_phase7f_final_validation gold_sniper.tests.test_p2e_smoke_metrics_contract gold_sniper.tests.test_p2c_performance_summary gold_sniper.tests.test_p2d_readiness
```

### Replay

```bash
# Run offline replay (1 month default)
python -m gold_sniper.replay.run_replay --start 2026-05-01 --end 2026-06-05

# With custom run ID
python -m gold_sniper.replay.run_replay --start 2026-05-01 --end 2026-06-05 --run-id P2_2_SCENARIO_ID_SIDE_1M_V1
```

### Security scan (run before every commit)

```bash
git diff --check
grep -R "order_send\|MetaTrader5\|LIVE_MODE\|ALLOW_BROKER_WRITES\|mt5\.order" -n gold_sniper/strategy gold_sniper/replay gold_sniper/agents gold_sniper/validation gold_sniper/data_pipeline || true
```

### Lint / compile check

```bash
python -m py_compile gold_sniper/strategy/*.py gold_sniper/replay/*.py
```

## Branch strategy

- `master` / `main` — stable base, rarely modified directly
- `P1-kasper-brain-core` — **current active branch**, Kasper authority + PDE alignment + scenario identity
- Other `P1-*` and `P2*-*` branches — feature/phase branches
- Work on the active phase branch; commit with `fix(pX.Y):` prefix
- Never force-push to shared branches

## Commit conventions

```bash
git commit -m "fix(p2.2): brief description of what was fixed"
# Co-Authored-By: Claude <noreply@anthropic.com>
```

Push when work is complete and tests pass:
```bash
git push origin P1-kasper-brain-core
```

## Autonomous loop protocol

Work in loops until the phase objective is met:
```
PLAN → IMPLEMENT → TEST → REPLAY → AUDIT → FIX → RE-TEST → REPORT LOCAL → NEXT PHASE only if criteria OK
```

- Don't escalate minor bugs — auto-audit, diagnose, fix, test, re-run.
- If a P0 doctrinal issue requires higher authority: stop, produce `BLOCKED_REPORT`, don't bypass.
- Never run 6-month replay until scenario identity and side consistency are proven.
- Target: 1-2 trades/day, 65%+ winrate, positive expectancy_R, controlled drawdown — but these are performance targets, never an excuse to force trades.

## Reports

- Write phase reports to `docs/P{X}_{Y}_*.md`
- Never paste full `decisions.jsonl`, `trade_journal`, or `events.jsonl` — give paths + counters + anomalies + 1-3 examples max
- Reports must include: SHA, branch, commit, push status, working tree clean, replay run_id, tests run, blockers fixed, remaining risks

## What to never delete

`AGENTS.md`, `README.md`, phase docs, tests, `gold_sniper/agents/`, `gold_sniper/strategy/`, `gold_sniper/replay/`, `gold_sniper/context/`, `gold_sniper/validation/`, historical data, `.env.example`

## What to never commit

`.mcp.json`, `.serena/`, `.claude/settings.local.json`, secrets, massive replay artifacts, `.env`, `__pycache__/`, `.pytest_cache/`
