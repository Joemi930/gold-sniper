from __future__ import annotations

import json
import unittest

from agents.base_agent import AgentResult
from agents.agent_1_meteo import build_agent_1_observation
from agents.agent_2_cartographe import build_agent_2_observation
from agents.agent_3_liquidite import build_agent_3_observation
from agents.agent_4_fibonacci import build_agent_4_observation
from agents.agent_5_microscope import build_agent_5_observation
from agents.agent_6_sentinelle import build_agent_6_observation
from agents.agent_7_chronos import build_agent_7_observation
from gold_sniper.strategy.contracts import AgentObservation


FORBIDDEN = {
    "decision", "action", "order", "order_send", "execute", "execution", "broker",
    "trade_signal", "signal", "entry", "entry_price", "sl", "stop_loss",
    "tp", "take_profit", "lot", "lots", "volume", "position_size",
    "permission", "recommendation",
}


def _walk_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k).lower()
            yield from _walk_keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


class TestP1AgentContracts(unittest.TestCase):
    def test_all_agent_observations_unknown_when_missing(self):
        builders = [
            build_agent_1_observation,
            build_agent_2_observation,
            build_agent_3_observation,
            build_agent_4_observation,
            build_agent_5_observation,
            build_agent_6_observation,
            build_agent_7_observation,
        ]
        for builder in builders:
            obs = builder(None)
            self.assertIsInstance(obs, AgentObservation)
            payload = obs.payload
            self.assertEqual(payload["status"], "UNKNOWN")
            self.assertIn("schema_version", payload)
            self.assertIn("agent_id", payload)
            self.assertIn("unknown_fields", payload)
            json.dumps(obs.to_dict())

    def test_agent_5_strips_execution_like_fields_by_not_copying_payload_raw(self):
        result = AgentResult(
            agent_id="agent_5",
            score=90.0,
            hard_filter_pass=True,
            direction="LONG",
            reason="MICRO_OK",
            payload={
                "entry": 2400.0,
                "sl": 2390.0,
                "tp": 2420.0,
                "lot": 0.10,
                "displacement_present": True,
                "retest_confirmed": True,
                "trigger_inside_poi": True,
            },
        )
        obs = build_agent_5_observation(result)
        keys = set(_walk_keys(obs.payload))
        self.assertTrue(keys.isdisjoint(FORBIDDEN))
        self.assertTrue(obs.payload["displacement_present"])
        self.assertTrue(obs.payload["retest_confirmed"])

    def test_agent_6_news_observation_maps_veto_to_evidence_not_decision(self):
        result = AgentResult(
            agent_id="agent_6",
            score=0.0,
            hard_filter_pass=False,
            direction=None,
            reason="NEWS_BLACKOUT_HIGH - FOMC",
            veto=True,
            payload={
                "blocked": True,
                "veto": True,
                "impact_level": "HIGH",
                "stealth_mode": False,
                "feed_alive": True,
                "calendar_source": "REPLAY_JSONL",
            },
        )
        obs = build_agent_6_observation(result)
        self.assertEqual(obs.payload["impact_level"], "HIGH")
        self.assertTrue(obs.payload["high_impact_window"])
        self.assertNotIn("decision", obs.payload)
        self.assertNotIn("order", obs.payload)

    def test_every_agent_observation_payload_has_no_forbidden_keys(self):
        sample = AgentResult(
            agent_id="agent_1",
            score=80.0,
            hard_filter_pass=True,
            direction="SHORT",
            reason="OK",
            payload={},
        )
        observations = [
            build_agent_1_observation(sample),
            build_agent_2_observation(AgentResult("agent_2", 70, True, "SHORT", "POI_OK", payload={"poi_zone": {"score": 70}})),
            build_agent_3_observation(AgentResult("agent_3", 70, True, "SHORT", "LIQUIDITY_OK", payload={"event": "SWEEP"})),
            build_agent_4_observation(AgentResult("agent_4", 70, True, "SHORT", "OTE_OK", payload={"in_ote": True, "in_discount": False, "in_premium": True})),
            build_agent_5_observation(AgentResult("agent_5", 70, True, "SHORT", "MICRO_OK", payload={"displacement_present": True})),
            build_agent_6_observation(AgentResult("agent_6", 100, True, None, "NEWS_CLEAR", payload={"impact_level": "NONE", "feed_alive": True})),
            build_agent_7_observation(AgentResult("agent_7", 80, True, None, "SESSION_OK", payload={"session_name": "LONDON_OPEN", "trading_allowed": True})),
        ]
        for obs in observations:
            keys = set(_walk_keys(obs.payload))
            self.assertTrue(keys.isdisjoint(FORBIDDEN), f"{obs.agent_id} has forbidden keys: {keys & FORBIDDEN}")

    def test_agent_1_htf_alignment_requires_structures_to_match_direction(self):
        result = AgentResult(
            agent_id="agent_1",
            score=80.0,
            hard_filter_pass=True,
            direction="LONG",
            reason="MIXED_CONTEXT",
            payload={
                "structure_4h": "BEARISH",
                "structure_15m": "BULLISH",
            },
        )
        obs = build_agent_1_observation(result)
        self.assertFalse(obs.payload["htf_aligned"])
        self.assertIn("HTF_MTF_ALIGNMENT_MISMATCH", obs.missing_evidence)

    def test_agent_builders_tolerate_dirty_payload_shapes(self):
        dirty = AgentResult(
            agent_id="agent_2",
            score=50.0,
            hard_filter_pass=True,
            direction="SHORT",
            reason="DIRTY",
            payload={
                "poi_zone": "bad",
                "active_ob": [],
                "active_fvg": None,
                "shadow_ict_contract": "bad",
                "shadow_ote_context": "bad",
                "shadow_trigger_context": "bad",
            },
        )
        observations = [
            build_agent_1_observation(dirty),
            build_agent_2_observation(dirty),
            build_agent_3_observation(dirty),
            build_agent_4_observation(dirty),
            build_agent_5_observation(dirty),
            build_agent_6_observation(dirty),
            build_agent_7_observation(dirty),
        ]
        for obs in observations:
            json.dumps(obs.to_dict())
            self.assertIn("status", obs.payload)


if __name__ == "__main__":
    unittest.main()
