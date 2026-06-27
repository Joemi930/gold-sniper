# P4.1 Baseline — Frozen before P4.2 ReplayEngineV2 implementation

- **Date frozen**: 2026-06-27
- **Branch**: P1-Gold_sniper_trading_and_optimisation
- **Commit**: 7ff5bc8 fix(p4.1): fix trades_per_eval_day, trades_per_active_day, optimization findings, profiler coverage, cost drag breakdown
- **Status**: 0 trades (regression from P4 which had 1 trade)
- **Key metrics**: ~475s / 1 week replay, profiler coverage ~47.81%, 7055 POI_REACTION candles evaluated by heavy pipeline

## Contents

- `P4_REPLAY_CORRECTNESS_SPEED_BASELINE.md` — P4 performance baseline
- `P4_REPLAY_CORRECTNESS_SPEED_VALIDATION_REPORT.md` — P4 validation report
- `summary.json` — Frozen replay summary (from baseline_frozen/)
- `trade_journal.jsonl` — Frozen trade journal (from baseline_frozen/)

## Purpose

This baseline serves as the reference point for P4.2 ReplayEngineV2 validation.
All parity tests and regression checks will compare against this snapshot.
