from __future__ import annotations

import unittest

from replay.run_replay import build_parser, resolve_replay_agents


class TestP1RunReplayDefaults(unittest.TestCase):
    def test_default_replay_agents_are_empty_at_cli_but_resolved_in_runner(self):
        parser = build_parser()
        args = parser.parse_args(["--run-id", "TEST", "--start", "2026-01-01", "--end", "2026-01-02"])
        self.assertEqual(args.replay_agent, [])

    def test_resolve_replay_agents_defaults_to_all_7(self):
        self.assertEqual(resolve_replay_agents([]), [
            "agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"
        ])

    def test_resolve_replay_agents_respects_explicit_list(self):
        self.assertEqual(resolve_replay_agents(["agent_1", "agent_5"]), ["agent_1", "agent_5"])

    def test_resolve_replay_agents_respects_none(self):
        self.assertEqual(resolve_replay_agents(None), [
            "agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"
        ])


if __name__ == "__main__":
    unittest.main()
