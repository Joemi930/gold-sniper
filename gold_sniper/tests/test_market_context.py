import unittest
import json
from context.market_context import build_default_market_context
from context.zone_lifecycle import ZoneState
from core.blackboard import BlackBoard
from config import LIVE_MODE

class TestMarketContext(unittest.TestCase):

    def test_default_context_json_safe(self):
        """Vérifier que le contexte par défaut est 100% sérialisable en JSON."""
        ctx = build_default_market_context(symbol="TEST", timestamp="2026-06-09T00:00:00Z")
        try:
            json_str = json.dumps(ctx)
            self.assertIsInstance(json_str, str)
        except TypeError as e:
            self.fail(f"Le contexte n'est pas JSON serialisable : {e}")

    def test_default_context_fields(self):
        """Vérifier la présence et les valeurs neutres des champs principaux."""
        ctx = build_default_market_context()
        self.assertEqual(ctx["primary_regime"], "UNKNOWN")
        self.assertEqual(ctx["delivery_phase"], "UNKNOWN")
        self.assertEqual(ctx["setup_hint"], "OBSERVATION")
        self.assertEqual(ctx["htf_bias"], "NEUTRAL")
        self.assertEqual(ctx["htf_draw_on_liquidity"], "UNCLEAR")
        self.assertEqual(ctx["institutional_order_flow"], "MIXED")
        self.assertEqual(ctx["overlays"], [])
        self.assertEqual(ctx["uncertainty_flags"], ["CONTEXT_ENGINE_SKELETON_ONLY"])

    def test_zone_state_literals(self):
        """Vérifier que ZoneState a bien été défini conformément aux specs."""
        # Ceci est principalement vérifié à la compilation/type-checking, 
        # mais on peut vérifier si on l'importe sans erreur.
        from typing import get_args
        
        expected = {
            "FRESH", "WICK_TAGGED", "PARTIALLY_MITIGATED", "MITIGATED",
            "CONSUMED", "INVALIDATED", "STALE", "FLIPPED_BREAKER"
        }
        
        actual = set(get_args(ZoneState))
        self.assertEqual(actual, expected)

    def test_blackboard_integration(self):
        """Vérifier que le Blackboard s'instancie avec le market_context."""
        board = BlackBoard()
        data = board.get_all()
        
        self.assertIn("market_context", data)
        ctx = data["market_context"]
        
        self.assertEqual(ctx["primary_regime"], "UNKNOWN")
        self.assertEqual(ctx["setup_hint"], "OBSERVATION")

if __name__ == "__main__":
    unittest.main()
