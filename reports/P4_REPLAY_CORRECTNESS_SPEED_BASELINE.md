# P4 Baseline Freeze

**Commit:** 2df8361
**Branch:** P1-Gold_sniper_trading_and_optimisation
**Date:** 2026-06-27

## Observed anomalies (from prior 1-month replay)
- Runtime: 273 minutes for 1 month
- Warmup trades: suspected (trades potentially opening before eval_start)
- trades_per_day: 0.00 (despite 27 trades)
- winrate_full_win: 0.0% (despite TP1/TP2 in table)
- winrate_tp1_touch: 0.0%
- cost_drag_R: 0.0000R (despite pure/expectancy divergence)
- full_sl_count confused with leg-level sl_hit_count
- Period reporting mixes warmup and evaluation
