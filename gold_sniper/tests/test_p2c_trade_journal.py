from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from replay.trade_journal import TradeJournal, TradeJournalEvent, event_from_dict


class TestP2cTradeJournal(unittest.TestCase):
    def test_event_serializable(self):
        event = TradeJournalEvent(event="close", time="2026-06-01T00:00:00Z", ticket=1, fill_price=2000.0)

        self.assertEqual(event.to_dict()["fill_price"], 2000.0)

    def test_save_jsonl_writes_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trade_journal.jsonl"
            journal = TradeJournal()
            journal.add(TradeJournalEvent(event="open", time="t", ticket=1, fill_price=2015.0))
            journal.save_jsonl(path)

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["event"], "open")
            self.assertEqual(rows[0]["fill_price"], 2015.0)

    def test_event_from_dict_preserves_fill_fields(self):
        event = event_from_dict(
            {
                "event": "close",
                "time": "t",
                "ticket": 1,
                "fill_price": 2005.0,
                "spread_points": 20.0,
                "slippage_points": 5.0,
                "commission": 0.0,
                "r_multiple": 1.2,
            }
        )

        self.assertEqual(event.fill_price, 2005.0)
        self.assertEqual(event.spread_points, 20.0)
        self.assertEqual(event.slippage_points, 5.0)
        self.assertEqual(event.commission, 0.0)
        self.assertEqual(event.r_multiple, 1.2)


if __name__ == "__main__":
    unittest.main()
