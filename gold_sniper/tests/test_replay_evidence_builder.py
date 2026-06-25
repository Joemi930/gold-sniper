from __future__ import annotations

import json
import unittest

from agents.base_agent import AgentResult
from gold_sniper.replay.evidence_builder import (
    build_evidence_bundle,
    bundle_to_json_dict,
    validate_evidence_bundle,
)
from gold_sniper.strategy.contracts import EvidenceBundle


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


class TestReplayEvidenceBuilder(unittest.TestCase):
    def test_build_evidence_bundle_from_full_agent_results(self):
        results = {
            "agent_1": AgentResult("agent_1", 80, True, "SHORT", "HTF_OK", payload={
                "structure_4h": "BEARISH",
                "structure_15m": "BEARISH",
                "shadow_ict_contract": {
                    "contextual_notes": {
                        "primary_regime": "TREND_DOWN",
                        "htf_draw_on_liquidity": "SELL_SIDE",
                        "institutional_order_flow": "BEARISH",
                    }
                },
            }),
            "agent_2": AgentResult("agent_2", 82, True, "SHORT", "POI_OK", payload={
                "poi_zone": {"zone_type": "OB_CONTINUATION", "lifecycle_state": "FRESH", "score": 82, "top": 2400, "bottom": 2390},
            }),
            "agent_3": AgentResult("agent_3", 75, True, "SHORT", "SWEEP_OK", payload={
                "event": "SWEEP", "sweep_type": "BSL", "sweep_depth_ratio": 0.2,
            }),
            "agent_4": AgentResult("agent_4", 78, True, "SHORT", "OTE_OK", payload={
                "in_ote": True, "in_premium": True, "precision_pct": 70,
            }),
            "agent_5": AgentResult("agent_5", 88, True, "SHORT", "MICRO_OK", payload={
                "displacement_present": True, "retest_confirmed": True,
                "trigger_inside_poi": True, "entry": 2400, "sl": 2410, "tp": 2380,
            }),
            "agent_6": AgentResult("agent_6", 100, True, None, "NEWS_CLEAR", payload={
                "impact_level": "NONE", "feed_alive": True, "calendar_source": "REPLAY_JSONL",
            }),
            "agent_7": AgentResult("agent_7", 90, True, None, "SESSION_OK", payload={
                "session_name": "LONDON_OPEN", "trading_allowed": True,
                "in_kill_zone": True, "kill_zone_name": "LONDON_OPEN",
            }),
        }

        bundle = build_evidence_bundle(results, ts_utc="2026-01-01T12:00:00+00:00")
        self.assertIsInstance(bundle, EvidenceBundle)
        self.assertEqual(set(bundle.observations.keys()), {
            "agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"
        })
        self.assertEqual(validate_evidence_bundle(bundle), [])
        self.assertTrue(bundle.context)
        self.assertTrue(bundle.poi)
        self.assertTrue(bundle.liquidity)
        self.assertTrue(bundle.micro)
        self.assertTrue(bundle.news)
        self.assertTrue(bundle.session)
        self.assertTrue(bundle.risk)

        payload = bundle_to_json_dict(bundle)
        json.dumps(payload)
        keys = set(_walk_keys(payload))
        self.assertTrue(keys.isdisjoint(FORBIDDEN), f"Forbidden keys leaked: {keys & FORBIDDEN}")

    def test_missing_agents_are_unknown_but_stable(self):
        bundle = build_evidence_bundle({})
        self.assertEqual(len(bundle.observations), 7)
        self.assertIn("AGENT_1_RESULT_MISSING", bundle.observations["agent_1"].missing_evidence)
        self.assertEqual(bundle.observations["agent_1"].payload["status"], "UNKNOWN")
        self.assertEqual(validate_evidence_bundle(bundle), [])

    def test_evidence_builder_strips_forbidden_keys_nested(self):
        result = AgentResult(
            agent_id="agent_5",
            score=90,
            hard_filter_pass=True,
            direction="LONG",
            reason="MICRO_OK",
            payload={
                "entry": 2400, "sl": 2390, "tp": 2420,
                "nested": {"order_send": True, "lot": 1.0},
                "displacement_present": True,
                "retest_confirmed": True,
                "trigger_inside_poi": True,
            },
        )
        bundle = build_evidence_bundle({"agent_5": result})
        payload = bundle_to_json_dict(bundle)
        keys = set(_walk_keys(payload))
        self.assertTrue(keys.isdisjoint(FORBIDDEN), f"Forbidden keys leaked: {keys & FORBIDDEN}")

    def test_evidence_builder_strips_forbidden_key_variants(self):
        from gold_sniper.replay.evidence_builder import _strip_forbidden_keys, _find_forbidden_keys

        payload = {
            "entryZone": 1,
            "stopLoss": 2,
            "takeProfit": 3,
            "tradeSignal": "BUY",
            "orderType": "MARKET",
            "brokerRoute": "MT5",
            "safe_value": "OK",
            "nested": {
                "positionSize": 0.1,
                "trigger_strength": 80,
            },
        }
        clean = _strip_forbidden_keys(payload)
        self.assertEqual(clean["safe_value"], "OK")
        self.assertEqual(clean["nested"]["trigger_strength"], 80)
        self.assertEqual(_find_forbidden_keys(clean), [])
        self.assertNotIn("entryZone", clean)
        self.assertNotIn("stopLoss", clean)
        self.assertNotIn("takeProfit", clean)
        self.assertNotIn("tradeSignal", clean)
        self.assertNotIn("orderType", clean)
        self.assertNotIn("brokerRoute", clean)
        self.assertNotIn("positionSize", clean["nested"])


if __name__ == "__main__":
    unittest.main()
