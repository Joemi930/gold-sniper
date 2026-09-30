# Research Branch Governance

Gold Sniper research branches, including `P1-opus`, are shadow-only by default.
They may run replay, backtest, diagnostics, and PAPER validation workflows, but
they must not be capable of broker writes.

Broker-write isolation rules:

- `RUN_MODE=LIVE` is denied on research branches by `safety.research_branch_guard`.
- `ALLOW_BROKER_WRITES=1` is ignored when research shadow-only mode is active.
- Direct `order_send` usage stays isolated in `gold_sniper/execution/broker_gateway.py`.
- Execution paths must pass through `ExecutionGuard` before any broker action.

Override `GOLD_SNIPER_RESEARCH_SHADOW_ONLY=1` can force the same shadow-only
behavior outside a named research branch when validating safety controls.
