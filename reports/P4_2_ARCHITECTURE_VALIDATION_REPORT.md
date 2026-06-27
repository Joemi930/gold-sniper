# P4.2 Architecture Validation Report

Date: 2026-06-27
Branch: P1-Gold_sniper_trading_and_optimisation
Baseline tags: p4.1-baseline, baseline-P41

## Scope

This pass verified the existing P4.2 ReplayEngineV2 implementation already present in local commits, added the missing repo-level guardrails, restored full pytest compatibility, added the required parity test file, and attempted the real parity/week validations from the plan.

## Files changed in this pass

| File | Purpose |
|---|---|
| AGENTS.md | Mission prohibitions, acceptance criteria, D10 canonical path |
| docs/research_branch_governance.md | Missing research branch governance doc |
| gold_sniper/agents/agent_5_microscope.py | Diagnostic defaults for insufficient data; Agent5-local handoff reason compatibility |
| gold_sniper/replay_app/Gold_Sniper_Replay.py | ASCII-safe parity output; CLI parity hard-exit after report flush |
| gold_sniper/tests/test_p3_payoff_r_accounting.py | Prevent recursive unittest discover; UTF-8-safe subprocess capture |
| gold_sniper/tests/test_parity_one_day.py | Required P4.2 parity command contract |
| tools/data_import/import_mt5_history.py | Remove static-guard false positives from read-only import tool |

Pre-existing worktree deletions and unrelated changes were not reverted and are not part of this validation patch.

## Tests

| Command | Result | Runtime |
|---|---:|---:|
| python -m pytest gold_sniper/tests/test_profiler_v2.py ... test_cli_parser_p4_2.py -q | 107 passed | 5.41s |
| python -m pytest gold_sniper/tests/test_parity_one_day.py -q | 1 passed | 0.70s |
| python -m pytest gold_sniper/tests/test_cli_parser_p4_2.py gold_sniper/tests/test_parity_one_day.py -q | 15 passed | 6.25s |
| python -m pytest gold_sniper/tests -q | 1676 passed, 38 subtests passed | 73.28s |

Warnings remain deprecation/runtime warnings in legacy tests; no failures remain in pytest.

## Real replay validation

| Validation | Status | Evidence |
|---|---|---|
| Parity 1 day | PARTIAL PASS | parity_report.json shows V2 trades 0, legacy trades 0, trade_count_match=true |
| Parity command return | NOT FULLY VALIDATED | First rerun failed only on cp1252 Unicode output; later rerun wrote V2 summary but legacy path stalled before new report |
| V2 parity runtime | PASS | v2_runtime_ms=34199.6 in parity_report.json |
| 1 week fast | FAIL / NOT VALIDATED | Command exceeded 3 minutes with no week_v2 summary_v2.json written; process stopped |
| 1 month fast | NOT RUN | Correctly not launched because week validation did not pass |

Parity report used:
`gold_sniper/data/replay_runs/parity_1d_parity/parity_report.json`

Key parity fields:
- v2_trades: 0
- legacy_trades: 0
- trade_count_match: true
- v2_hash: 41d1550af912fa73
- legacy_hash: 14ad046d15fec322
- v2_runtime_ms: 34199.6

## Acceptance criteria status

| Criterion | Status | Notes |
|---|---|---|
| 1 week replay <= 3 min | FAIL | week_v2 exceeded 3 minutes and produced no summary in this pass |
| Profiler coverage >= 95 percent | TEST PASS / RUN NOT MEASURED | ProfilerV2 tests pass; real replay was not completed with profile report |
| warmup trades = 0 | PASS | Covered by tests and parity legacy summary warmup_trade_count=0 |
| 0 LookaheadError | PASS | no-lookahead tests pass |
| parity full vs fast 1 day identical | PARTIAL PASS | Trade parity true; full decision/ENTER/trade hash parity remains not proven by real command |
| 0 trade => NO_TRADES state | PASS | V2 parity summary state=NO_TRADES; tests pass |
| readable report with blockers | PASS IN V2 COMPONENTS | Metrics/report tests pass |
| POI terminal state unique | TEST PASS | test_poi_contract.py included in full green suite |
| no live/broker/order_send changes | PASS | No live broker paths modified |
| tests section G + non-regression green | PASS | 1676 pytest tests passed |
| D9 regression resolved/documented | DOCUMENTED | Existing P4.2 report documents diff investigation; no threshold/veto changes made |

## D10 canonical path

Canonical decision stack: `gold_sniper/strategy/`.

`gold_sniper/strategies/` remains a legacy professional selector/reporting subsystem and must not be deleted or renamed without a fresh non-test import audit.

## Blockers

1. Real `week_v2` validation did not meet the 3 minute acceptance gate in this pass; the command did not write `summary_v2.json` before being stopped.
2. The parity CLI can write a valid parity report, but relaunching the same `parity_1d` run remains fragile because the legacy full-scan path can stall before rewriting legacy summary/report.
3. Full decision hash parity is not yet proven by the real replay command; the existing parity report only proves trade-count parity and summary hashes.

## Final status

P4.2 is not declared fully accepted in this report. The codebase test suite is green, baseline tagging is complete, D10 is documented, and parity trade-count evidence exists, but the real week-fast runtime gate failed in this pass. Do not run the one-month replay until parity command termination and week_v2 <= 3 minutes are both revalidated.
