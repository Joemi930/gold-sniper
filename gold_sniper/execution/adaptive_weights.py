class AdaptiveWeightEngine:
    """
    Système d'ajustement adaptatif des poids sur la session courante.
    NE MODIFIE PAS les poids de base permanents — uniquement la session.
    
    LOGIQUE :
    - Si trades avec Agent 3 fort (>80) sont gagnants → augmenter poids Agent 3
    - Si trades avec Agent 4 fort mais Agent 3 faible sont perdants → réduire Agent 4
    - Reset des poids adaptatifs à chaque début de session
    """
    
    BASE_WEIGHTS = {"agent_1": 30, "agent_2": 25, "agent_3": 20, "agent_4": 15, "agent_5": 10}
    MAX_ADJUSTMENT = 5      # ±5 points max d'ajustement par session
    MIN_TRADES_REQUIRED = 3  # Minimum de trades avant d'adapter
    
    def __init__(self):
        self.session_trades = []      # Historique de la session
        self.current_weights = dict(self.BASE_WEIGHTS)
    
    def record_trade_result(self, agent_breakdown: dict, outcome: str):
        """
        outcome = "WIN" | "LOSS" | "BREAKEVEN"
        Appelé par le Trade Manager quand un trade se ferme.
        """
        self.session_trades.append({
            "breakdown": agent_breakdown,
            "outcome": outcome
        })
        if len(self.session_trades) >= self.MIN_TRADES_REQUIRED:
            self._recompute_weights()
    
    def _recompute_weights(self):
        """Recalcule les poids en fonction des performances de session."""
        for agent in self.BASE_WEIGHTS:
            wins_with_high_agent = [
                t for t in self.session_trades
                if t["breakdown"].get(agent, {}).get("score", 0) >= 80 and t["outcome"] == "WIN"
            ]
            losses_with_high_agent = [
                t for t in self.session_trades
                if t["breakdown"].get(agent, {}).get("score", 0) >= 80 and t["outcome"] == "LOSS"
            ]
            
            if len(wins_with_high_agent) + len(losses_with_high_agent) >= 2:
                win_rate = len(wins_with_high_agent) / (len(wins_with_high_agent) + len(losses_with_high_agent))
                # Ajustement proportionnel : win_rate > 70% → +poids, < 30% → -poids
                adjustment = (win_rate - 0.5) * self.MAX_ADJUSTMENT * 2
                adjustment = max(-self.MAX_ADJUSTMENT, min(self.MAX_ADJUSTMENT, adjustment))
                self.current_weights[agent] = self.BASE_WEIGHTS[agent] + adjustment
        
        # Normaliser pour que la somme reste 100
        total = sum(self.current_weights.values())
        if total > 0:
            for agent in self.current_weights:
                self.current_weights[agent] = round(self.current_weights[agent] * 100 / total, 1)
    
    def reset_session(self):
        self.session_trades = []
        self.current_weights = dict(self.BASE_WEIGHTS)
