# Gold Sniper P4.2 Guardrails

These rules apply to every turn and every local change in this repository.

## Absolute Prohibitions

- Do not force `ENTER` and do not create artificial signals.
- Do not lower thresholds or weaken veto, session, news, or risk gates.
- Do not modify live broker/order send paths and do not execute `gold_sniper/main.py`.
- Do not change the decision logic of agents, EvidenceBuilder, Kasper, the PDE, or RiskAllocator. Only change when they are called through replay gating.
- Do not run the one-month replay before the one-week replay is validated.
- Do not optimize the strategy before the replay architecture correction is complete.
- Do not delete or weaken tests to make the suite pass. If a legacy test encodes the old incorrect behavior, update it and document why in the commit.
- Do not delete a strategic module without proof of zero non-test imports. If in doubt, disable it in fast mode instead of deleting it.
- Do not `git push` or `git push --force`. Local commits only.

## Canonical Call Chain And D10 Verdict

- The replay hot path is `gold_sniper/replay/replay_engine.py::ReplayEngine.run()` -> `_inject_candle()` -> `_call_decision_hook()` -> `ReplayDecisionPipeline.__call__()`.
- The canonical decision stack is `gold_sniper/strategy/`: contracts, EvidenceBundle, Kasper, PDE, RiskAllocator, POI/readiness contracts, setup taxonomy.
- `gold_sniper/strategies/` is not the canonical decision stack. It is a legacy professional selector/reporting and diagnostics subsystem used by `replay_engine.py` summary helpers and selector tests.
- Keep both packages. Do not rename or delete either package without a fresh non-test import audit and a green full suite.

## P4.2 Acceptance Criteria

- One-week replay runtime is no more than 3 minutes.
- Profiler coverage is at least 95 percent and exposes `unaccounted_ms`.
- Warmup trades are zero and decisions occur only at or after `eval_start`.
- `no_lookahead_guard` is active with zero `LookaheadError`.
- Full vs fast one-day parity is identical for decision hash, `ENTER`, and trades.
- If there are zero trades, the state is `NO_TRADES`; winrate and expectancy are `None`, not zero.
- Reports are readable, show top blockers, and include non-empty `top_reject_reasons`.
- OB/POI routing is clarified and the POI contract has one coherent terminal state.
- No live/broker/order_send changes and no threshold or veto weakening.
- All plan section G tests pass and P3/P4 non-regression remains green.
- D9 regression is resolved by trade recovery or documented with a concrete cause.

## Stop And Report

Stop instead of improvising if full/fast parity diverges without an identified missing feature, D9 cannot be reconciled without threshold/veto changes, D10 becomes ambiguous, the engine remains selective after correct architecture, or a legacy test failure would require changing strategic behavior.
