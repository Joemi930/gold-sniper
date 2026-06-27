"""P4.2 parity contract for the one-day full-vs-fast command path."""

from __future__ import annotations

import json

from gold_sniper.replay_app import Gold_Sniper_Replay as replay_app


def test_fast_full_parity_one_day(monkeypatch, tmp_path):
    monkeypatch.setattr(replay_app, "DEFAULT_OUTPUT_ROOT", tmp_path)

    def fake_v2(**kwargs):
        run_dir = tmp_path / kwargs["run_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "summary_v2.json").write_text(
            json.dumps(
                {
                    "engine": "v2",
                    "trade_count": 1,
                    "decisions": [{"candidate_id": "c1", "decision": "ENTER"}],
                    "trades": [{"ticket": 1, "outcome": "TP1"}],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 0

    def fake_legacy(**kwargs):
        run_dir = tmp_path / kwargs["run_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "summary.json").write_text(
            json.dumps(
                {
                    "engine": "legacy",
                    "parent_trades": 1,
                    "decisions": [{"candidate_id": "c1", "decision": "ENTER"}],
                    "trades": [{"ticket": 1, "outcome": "TP1"}],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(replay_app, "_run_replay_v2", fake_v2)
    monkeypatch.setattr(replay_app, "_run_replay_interactive", fake_legacy)

    rc = replay_app._run_parity_mode(
        run_id="parity_1d",
        start="2025-12-08",
        end="2025-12-09",
        warmup_start="2025-12-01",
        initial_equity=100.0,
        no_tui=True,
    )

    report_path = tmp_path / "parity_1d_parity" / "parity_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert rc == 0
    assert report["trade_count_match"] is True
    assert report["v2_trades"] == report["legacy_trades"] == 1
    assert report["v2_hash"]
    assert report["legacy_hash"]
