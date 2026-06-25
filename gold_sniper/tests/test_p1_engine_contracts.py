from __future__ import annotations

import unittest

from gold_sniper.strategy.contracts import (
    AgentObservation,
    DecisionAction,
    DecisionResult,
    EvidenceBundle,
    EvidenceSource,
    ExecutionMode,
    HardVetoResult,
    ReplayDecisionRecord,
    RiskPlan,
    ScoreCard,
    ScoreComponent,
    SetupGrade,
    SetupType,
    TradeSide,
    VetoSeverity,
)


class TestContractsSerialization(unittest.TestCase):
    def test_evidence_bundle_roundtrip(self):
        bundle = EvidenceBundle(
            symbol="XAUUSD",
            setup_type=SetupType.REVERSAL_STRICT,
            side=TradeSide.SELL,
            context={"htf_aligned": True},
        )
        payload = bundle.to_dict()
        self.assertEqual(payload["setup_type"], "REVERSAL_STRICT")
        self.assertEqual(payload["side"], "SELL")
        restored = EvidenceBundle.from_dict(payload)
        self.assertEqual(restored.symbol, "XAUUSD")
        self.assertEqual(restored.setup_type, SetupType.REVERSAL_STRICT)
        self.assertEqual(restored.side, TradeSide.SELL)

    def test_evidence_bundle_from_empty_dict(self):
        bundle = EvidenceBundle.from_dict({})
        self.assertEqual(bundle.symbol, "XAUUSD")
        self.assertEqual(bundle.setup_type, SetupType.UNKNOWN)

    def test_evidence_bundle_with_observations(self):
        data = {
            "observations": {
                "agent_1": {
                    "agent_id": "agent_1",
                    "passed": True,
                    "score": 85.0,
                    "confidence": 0.9,
                    "reason": "ALIGNED",
                }
            }
        }
        bundle = EvidenceBundle.from_dict(data)
        self.assertIn("agent_1", bundle.observations)
        obs = bundle.observations["agent_1"]
        self.assertEqual(obs.agent_id, "agent_1")
        self.assertTrue(obs.passed)
        self.assertEqual(obs.score, 85.0)

    def test_hard_veto_result_to_dict(self):
        veto = HardVetoResult(
            hard_veto=True,
            veto_code="NEWS_HIGH_IMPACT_WINDOW",
            veto_reason="High impact news blackout.",
            severity=VetoSeverity.HARD,
        )
        d = veto.to_dict()
        self.assertEqual(d["hard_veto"], True)
        self.assertEqual(d["veto_code"], "NEWS_HIGH_IMPACT_WINDOW")
        self.assertEqual(d["severity"], "HARD")

    def test_hard_veto_default_not_vetoed(self):
        veto = HardVetoResult()
        self.assertFalse(veto.hard_veto)
        self.assertFalse(veto.replay_invalid)

    def test_score_card_to_dict(self):
        card = ScoreCard(
            score_before_veto=88.0,
            score_after_veto=0.0,
            grade=SetupGrade.D,
            missing_evidence=["CONTEXT_MISSING"],
        )
        d = card.to_dict()
        self.assertEqual(d["score_before_veto"], 88.0)
        self.assertEqual(d["score_after_veto"], 0.0)
        self.assertEqual(d["grade"], "D")

    def test_decision_result_to_dict(self):
        result = DecisionResult(
            action=DecisionAction.ENTER_FULL,
            setup_grade=SetupGrade.A_PLUS,
            confidence_score=0.9,
            score_before_veto=88.0,
            score_after_veto=88.0,
            required_execution_mode=ExecutionMode.SHADOW_ONLY,
        )
        d = result.to_dict()
        self.assertEqual(d["action"], "ENTER_FULL")
        self.assertEqual(d["setup_grade"], "A_PLUS")
        self.assertEqual(d["required_execution_mode"], "SHADOW_ONLY")

    def test_risk_plan_to_dict(self):
        plan = RiskPlan(
            capital=100.0,
            risk_pct=1.0,
            risk_amount=1.0,
            risk_multiplier=1.0,
            allowed=True,
            reason="SHADOW_RISK_ALLOCATED",
        )
        d = plan.to_dict()
        self.assertEqual(d["allowed"], True)
        self.assertEqual(d["reason"], "SHADOW_RISK_ALLOCATED")

    def test_score_component_weighted(self):
        comp = ScoreComponent("poi", value=80.0, weight=0.20)
        self.assertEqual(comp.weighted(), 16.0)

    def test_replay_decision_record_to_dict(self):
        bundle = EvidenceBundle()
        veto = HardVetoResult()
        card = ScoreCard()
        decision = DecisionResult(action=DecisionAction.REJECT, setup_grade=SetupGrade.D)
        risk = RiskPlan()
        record = ReplayDecisionRecord(
            evidence=bundle,
            hard_veto=veto,
            scorecard=card,
            decision=decision,
            risk_plan=risk,
        )
        d = record.to_dict()
        self.assertIn("evidence", d)
        self.assertIn("decision", d)

    def test_evidence_bundle_from_dict_tolerates_invalid_sections(self):
        bundle = EvidenceBundle.from_dict({
            "context": "bad",
            "poi": [],
            "liquidity": None,
            "micro": "bad",
            "news": [],
            "session": "bad",
            "risk": None,
            "raw": "bad",
        })

        self.assertEqual(bundle.context, {})
        self.assertEqual(bundle.poi, {})
        self.assertEqual(bundle.liquidity, {})
        self.assertEqual(bundle.micro, {})
        self.assertEqual(bundle.news, {})
        self.assertEqual(bundle.session, {})
        self.assertEqual(bundle.risk, {})
        self.assertEqual(bundle.raw, {})

    def test_no_broker_or_mt5_in_contracts(self):
        """Contracts must contain no broker or MT5 tokens."""
        import inspect
        from gold_sniper.strategy import contracts as mod

        source = inspect.getsource(mod)
        forbidden = ["MetaTrader5", "order_send", "execution.", "core.orchestrator"]
        for token in forbidden:
            self.assertNotIn(token, source, f"Token {token} found in contracts.py")

    def test_evidence_bundle_from_dict_tolerates_invalid_observation_values(self):
        bundle = EvidenceBundle.from_dict({
            "observations": {
                "agent_x": {
                    "agent_id": "agent_x",
                    "source": "BAD_SOURCE",
                    "score": "N/A",
                    "confidence": "bad",
                }
            }
        })
        obs = bundle.observations["agent_x"]
        self.assertEqual(obs.source, EvidenceSource.AGENT)
        self.assertEqual(obs.score, 0.0)
        self.assertEqual(obs.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
