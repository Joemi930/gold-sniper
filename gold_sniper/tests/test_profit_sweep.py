"""Profit sweep (capital protection doctrine) — unit tests."""
import os, unittest
from replay.simulated_trade_manager import SimulatedTradeManager


class _Cfg:
    equity_initial = 100.0


def _mk(enabled=True, mult=2.0, pct=0.5):
    m = object.__new__(SimulatedTradeManager)
    m.config = _Cfg()
    m.equity = 100.0
    m.peak_equity = 100.0
    m.max_drawdown = 0.0
    m.max_drawdown_pct_peak = 0.0
    m._sweep_enabled = enabled
    m._sweep_trigger_mult = mult
    m._sweep_pct = pct
    m.sweep_ref = 100.0
    m.withdrawn_total = 0.0
    m.sweep_count = 0
    return m


class TestProfitSweep(unittest.TestCase):
    def test_sweeps_at_double_and_takes_half(self):
        m = _mk()
        m._apply_pnl(+100)  # 100 -> 200, doubles -> sweep
        self.assertEqual(m.sweep_count, 1)
        self.assertEqual(m.withdrawn_total, 50.0)
        self.assertEqual(m.equity, 150.0)
        self.assertEqual(m.sweep_ref, 150.0)

    def test_withdrawal_is_not_a_drawdown(self):
        m = _mk()
        m._apply_pnl(+100)          # sweep: 200 -> 150 (withdrawal, NOT a loss)
        self.assertEqual(m.max_drawdown_pct_peak, 0.0)
        m._apply_pnl(-30)           # real loss 150 -> 120
        self.assertAlmostEqual(m.max_drawdown_pct_peak, 20.0, places=2)

    def test_total_value_conserved(self):
        m = _mk()
        m._apply_pnl(+100)
        self.assertEqual(m.equity + m.withdrawn_total, 200.0)

    def test_disabled_by_default_flag(self):
        m = _mk(enabled=False)
        m._apply_pnl(+500)
        self.assertEqual(m.sweep_count, 0)
        self.assertEqual(m.withdrawn_total, 0.0)
        self.assertEqual(m.equity, 600.0)

    def test_no_sweep_without_profit_trigger(self):
        m = _mk()
        m._apply_pnl(+50)  # 150 < 200
        self.assertEqual(m.sweep_count, 0)


if __name__ == "__main__":
    unittest.main()
