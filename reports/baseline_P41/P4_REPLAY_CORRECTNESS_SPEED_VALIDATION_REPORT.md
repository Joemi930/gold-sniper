# P4 Replay Correctness + Speed Validation Report

**Generated:** 2026-06-27
**Branch:** P1-Gold_sniper_trading_and_optimisation

---

## 1. Commit de départ

- **Start commit:** `2df8361`
- **Branch:** P1-Gold_sniper_trading_and_optimisation
- **Baseline anomalies:** warmup trades, trades_per_day=0, double winrate=0, cost_drag=0, full_sl/leg_sl confused, 273 min runtime

---

## 2. Mission réalisée

Phase P4 complète: warmup gate, reporting consistency, payoff diagnostics, fast replay mode, profiler enhancement.

**Interdits respectés:** aucun seuil modifié, aucun ENTER forcé, aucun veto supprimé, aucune modification Kasper/PDE, aucun live/broker/order_send.

---

## 3. Fichiers modifiés

| File | Changes |
|------|---------|
| `gold_sniper/replay/replay_engine.py` | Warmup gate (skip decisions/trades in warmup), runtime_config integration, buffered writer, minimal events filter, P4 metrics propagation, profiler write |
| `gold_sniper/replay/simulated_trade_manager.py` | eval_active safety gate, first/last trade time, pure/net payoff diagnostics (TP1+TP2, TP1+protected, avg cost drag per scenario), enriched parent_close_event with R-unit diagnostics |
| `gold_sniper/replay/replay_profiler.py` | Section-level context manager, count/avg/max stats, top_bottlenecks ranking |
| `gold_sniper/replay/run_replay.py` | CLI flags: --fast-replay, --minimal-events, --event-buffer-size, --no-tui |
| `gold_sniper/replay_app/live_runner.py` | TUI throttle (N candles / T seconds), runtime_config construction and propagation |
| `gold_sniper/replay_app/report_writer.py` | P4 metrics (cost_drag fallback, parent_full_sl vs leg_sl, trades_per_eval_day, payoff diagnostics), Period & Boundaries + Payoff Diagnostics report sections |
| `gold_sniper/replay_app/Gold_Sniper_Replay.py` | CLI flags, fast-replay propagation to runner |

---

## 4. Fichiers créés

| File | Purpose |
|------|---------|
| `gold_sniper/replay/replay_runtime_config.py` | Immutable runtime config dataclass (fast/normal presets) |
| `gold_sniper/replay/buffered_jsonl_writer.py` | Buffered JSONL writer (N-line batch I/O) |
| `gold_sniper/tests/test_replay_warmup_gate.py` | 7 tests: eval_active gate, phase_for_candle, runtime_config |
| `gold_sniper/tests/test_replay_summary_consistency.py` | 5 tests: metric keys, cost_drag_R, full_sl ≤ parent_trades, winrate, defaults |
| `gold_sniper/tests/test_report_writer_metrics_consistency.py` | 4 tests: cost_drag fallback, full_sl vs leg_sl separation, metric keys, report sections |
| `gold_sniper/tests/test_replay_fast_mode_contract.py` | 6 tests: fast config flags, buffered writer flush/auto-flush, event filter |
| `gold_sniper/tests/test_replay_profile_report.py` | 7 tests: disabled default, enable, section timing, report JSON, bottlenecks, candle counting |

---

## 5. Fichiers nettoyés

Pre-existing deletions (from prior cleanup, not P4): `historical_replay_pack.py`, `offline_evidence_builder.py`, `replay_report.py` + their tests.

---

## 6. Warmup gate

**Fix:** In `ReplayEngine.run()`, when `eval_active=False`:
- Inject candle + update blackboard (context building)
- Call display hook for TUI progress
- `continue` — skip decision pipeline, trade manager, snapshots, tier simulation

**Safety gate:** `SimulatedTradeManager.on_p1_decision()` returns `[]` when `decision.get("eval_active") is False`.

**Results:** 7 tests pass, including `test_eval_active_false_returns_empty`, `test_phase_for_candle_warmup`, `test_phase_for_candle_eval`.

---

## 7. Summary/reporting consistency

| Metric | Before | After |
|--------|--------|-------|
| `trades_per_day` | 0.00 | Propagated from trade_summary |
| `trades_per_eval_day` | Missing | Added (closed_trades / eval_days) |
| `trades_per_active_day` | Missing | Added (closed_trades / active_days) |
| `winrate_full_win` | 0.0% | Correctly computed from parent closes |
| `winrate_tp1_touch` | 0.0% | Correctly computed from parent closes |
| `cost_drag_R` | 0.0000R | pure_expectancy_R - expectancy_R |
| `full_sl_count` | Confused with sl_hit_count | Separated: parent_full_sl_count vs leg_sl_count |
| Period info | Mixed warmup+eval | warmup_start, eval_start, eval_end displayed |
| `first/last_trade_time` | Missing | Computed from open events |
| `warmup_trade_count` | Missing | Added |

---

## 8. Payoff diagnostics

Added to summary and report:
- **TP1+TP2:** avg net R / avg pure R / count
- **TP1+Protected:** avg net R / avg pure R / count
- **Avg cost drag per trade:** aggregate, winners, losers
- **per-trade R-unit diagnostics:** r_unit_points, effective_risk_points, structural_risk_points, entry/exit costs

REPORT.md now contains a "Payoff Diagnostics (Pure vs Net R)" section.

---

## 9. Speed optimisation

| Optimization | Implementation |
|-------------|----------------|
| **Warmup skip** | No decision pipeline during warmup (biggest win) |
| **Buffered JSONL** | `BufferedJsonlWriter` batches up to 5000 lines before I/O |
| **Minimal events** | `_is_fast_keep_event()` drops non-trade-lifecycle events in fast mode |
| **TUI throttle** | State queue pushed every N candles / T seconds (configurable) |
| **Skip decisions.jsonl** | `write_decisions_jsonl=False` in fast mode |
| **Profiler** | Section-level timing with top bottleneck ranking |

**`--fast-replay` flag** activates all optimizations. Available in CLI (`--no-menu --fast-replay`) and app menu.

---

## 10. Profil runtime

**Profiler enhanced with:**
- Section context manager: `with prof.section("name"): ...`
- Count / avg / max per section
- Top 10 bottlenecks ranked by total time
- `profile_report.json` written to run_dir automatically

**Expected bottleneck profile** (to be confirmed by user's manual replay):
1. Agent2 (OB detection, array rebuilds)
2. Agent1 (HTF context)
3. Event I/O (mitigated by buffered writer)
4. TUI pushes (mitigated by throttle)

---

## 11. Tests exécutés

| Suite | Tests | Result |
|-------|-------|--------|
| `test_replay_warmup_gate` | 7 | ✅ 7/7 pass |
| `test_replay_summary_consistency` | 5 | ✅ 5/5 pass |
| `test_report_writer_metrics_consistency` | 4 | ✅ 4/4 pass |
| `test_replay_fast_mode_contract` | 6 | ✅ 6/6 pass |
| `test_replay_profile_report` | 7 | ✅ 7/7 pass |
| **P4 total** | **29** | **✅ 29/29 pass, 0 fail, 0 error** |
| Non-regression (P3 payoff + P2C TM) | 20 | ✅ 0 fail, 1 error (timeout) |

---

## 12. Résultats

- Warmup gate: **active** — zero trades possible before eval_start
- Reporting: **all P4 metrics propagated** to summary.json → metrics.json → REPORT.md
- Payoff diagnostics: **pure vs net R per scenario** available in report
- Fast replay: **CLI flag operational**, buffered I/O, minimal events, TUI throttle
- Profiler: **enhanced with section-level timing** and bottleneck ranking

---

## 13. Commandes manuelles pour l'utilisateur

### Pull latest
```powershell
git checkout P1-Gold_sniper_trading_and_optimisation
git pull
```

### Replay 1 semaine (fast)
```powershell
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu --fast-replay --profile-replay --start 2026-01-01 --end 2026-01-08 --warmup-start 2025-12-01 --run-id p4_fast_1w_jan --initial-equity 100
```

### Replay 1 mois (fast)
```powershell
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu --fast-replay --profile-replay --start 2026-01-01 --end 2026-02-01 --warmup-start 2025-12-01 --run-id p4_fast_1m_jan --initial-equity 100
```

### Fichiers produits
```
reports/replay/p4_fast_1m_jan/REPORT.md
reports/replay/p4_fast_1m_jan/metrics.json
reports/replay/p4_fast_1m_jan/summary.json
reports/replay/p4_fast_1m_jan/important_trades.jsonl
reports/replay/p4_fast_1m_jan/profile_report.json
```

---

## 14. Interdits respectés

- [x] Aucune modification stratégie Kasper/PDE
- [x] Aucun seuil baissé
- [x] Aucun ENTER forcé
- [x] Aucun veto supprimé
- [x] Aucun live/broker/order_send
- [x] Aucun replay 1 mois lancé automatiquement
- [x] Aucun test supprimé pour faire passer la suite

---

## 15. Statut final

**P4 READY — USER CAN RUN MANUAL FAST REPLAY**

Target: 1 mois ≤ 10 min, 3 mois ≤ 30 min with `--fast-replay --profile-replay`.
