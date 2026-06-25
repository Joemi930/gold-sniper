from __future__ import annotations

import unittest

from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary


class TestP2cPerformanceSummary(unittest.TestCase):
    def test_no_trades_has_null_ratios_and_no_enter_diagnostic(self) -> None:
        summary = build_p2c_performance_summary(
            decisions=[{"decision": "WATCH_ONLY", "setup_grade": "D", "readiness_state": "WATCH_ONLY"}],
            events=[],
        )

        self.assertEqual(summary["filled_trades"], 0)
        self.assertIsNone(summary["profit_factor"])
        self.assertIsNone(summary["winrate"])
        self.assertIsNone(summary["expectancy_R"])
        self.assertIsNone(summary["max_drawdown_R"])
        self.assertEqual(summary["diagnostic"], "NO_RECOVERABLE_POI")

    def test_no_losses_does_not_divide_by_zero(self) -> None:
        summary = build_p2c_performance_summary(
            decisions=[{"decision": "ENTER_FULL", "setup_grade": "B"}],
            events=[
                {"event": "open", "setup_grade": "B"},
                {"event": "close", "setup_grade": "B", "pnl": 50.0, "r_multiple": 2.0},
            ],
        )

        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 0)
        self.assertIsNone(summary["profit_factor"])
        self.assertEqual(summary["expectancy_R"], 2.0)

    def test_performance_by_grade_counts_filled_and_missed(self) -> None:
        summary = build_p2c_performance_summary(
            decisions=[
                {"decision": "ENTER_FULL", "setup_grade": "B"},
                {"decision": "ENTER_REDUCED", "setup_grade": "C"},
                {"decision": "WATCH_ONLY", "setup_grade": "D"},
            ],
            events=[
                {"event": "open", "setup_grade": "B"},
                {"event": "close", "setup_grade": "B", "pnl": -10.0, "r_multiple": -1.0},
                {"event": "missed_entry", "setup_grade": "C", "reason": "LIMIT_NOT_TOUCHED_WITH_SPREAD"},
            ],
        )

        self.assertEqual(summary["performance_by_grade"]["B"]["filled_trades"], 1)
        self.assertEqual(summary["performance_by_grade"]["C"]["missed_entries"], 1)
        self.assertEqual(summary["missed_entries"], 1)
        self.assertEqual(summary["missed_entry_distribution"], {"LIMIT_NOT_TOUCHED_WITH_SPREAD": 1})

    def test_window_aggregation_merges_metrics(self) -> None:
        payload = build_p2c_performance_summary(windows=[
            {
                "status": "PASS",
                "window_days": 10,
                "performance_summary": {
                    "total_decisions": 100,
                    "filled_trades": 1,
                    "missed_entries": 1,
                    "wins": 1,
                    "losses": 0,
                    "r_values": [2.0],
                    "decision_distribution": {"ENTER_FULL": 1},
                    "performance_by_grade": {"B": {"decisions": 1, "signals": 1, "filled_trades": 1, "missed_entries": 0, "wins": 1, "losses": 0, "r_values": [2.0]}},
                },
            },
            {
                "status": "PARTIAL_DATA_COVERAGE",
                "window_days": 10,
                "performance_summary": {
                    "total_decisions": 50,
                    "filled_trades": 0,
                    "missed_entries": 1,
                    "wins": 0,
                    "losses": 0,
                    "r_values": [],
                    "decision_distribution": {"WATCH_ONLY": 50},
                    "performance_by_grade": {"C": {"decisions": 1, "signals": 0, "filled_trades": 0, "missed_entries": 1, "wins": 0, "losses": 0, "r_values": []}},
                },
            },
        ])

        self.assertEqual(payload["total_windows"], 2)
        self.assertEqual(payload["windows_partial_data"], 1)
        self.assertEqual(payload["total_decisions"], 150)
        self.assertEqual(payload["filled_trades"], 1)
        self.assertEqual(payload["missed_entries"], 2)
        self.assertEqual(payload["trade_frequency_per_day"], 0.05)


if __name__ == "__main__":
    unittest.main()
