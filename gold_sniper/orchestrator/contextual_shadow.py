"""
P1.30 — Orchestrateur contextuel shadow.
Fonction pure : lit les agents bruts depuis les events replay,
classe le setup ICT et produit un diagnostic, sans modifier aucune décision.
"""
from __future__ import annotations
from typing import Any, Dict, Optional


# ── Matrices doctrinales ───────────────────────────────────────────────────────
REQUIRED_AGENTS_BY_SETUP: Dict[str, list] = {
    "SNIPER_PULLBACK":    ["agent_1", "agent_2", "agent_4", "agent_7"],
    "TREND_CONTINUATION": ["agent_1", "agent_2", "agent_7"],
    "REVERSAL":           ["agent_2", "agent_3", "agent_4", "agent_5", "agent_7"],
    "OBSERVATION":        [],
}

OPTIONAL_AGENTS_BY_SETUP: Dict[str, list] = {
    "SNIPER_PULLBACK":    ["agent_3", "agent_5"],
    "TREND_CONTINUATION": ["agent_4", "agent_5"],
    "REVERSAL":           ["agent_1"],
    "OBSERVATION":        ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"],
}


def _extract_agent_fields(agents_raw: Dict[str, Any]) -> dict:
    """
    Extrait les champs nécessaires depuis le dict `agents` brut d'un event replay.
    Gère les cas spéciaux Agent6 (veto vs hard_filter_pass) et
    Agent4/5 (shadow contexts).
    """
    def score(aid: str) -> float:
        a = agents_raw.get(aid, {})
        return float(a.get("score") or 0.0)

    def passes(aid: str) -> bool:
        a = agents_raw.get(aid, {})
        hfp = a.get("hard_filter_pass")
        if hfp is None:
            # Fallback : dériver depuis veto si disponible
            veto = a.get("veto")
            if veto is not None:
                return not bool(veto)
            return False
        return bool(hfp)

    def payload(aid: str) -> dict:
        return agents_raw.get(aid, {}).get("payload") or {}

    def contract(aid: str) -> dict:
        return payload(aid).get("shadow_ict_contract", {})

    def passes_contextually(aid: str) -> bool:
        c = contract(aid)
        if c:
            return not c.get("hard_veto", False)
        return passes(aid)

    # Agent 6 : hard_filter_pass peut être None, veto est la vraie source
    a6_contract = contract("agent_6")
    a6_veto = agents_raw.get("agent_6", {}).get("veto")
    if a6_contract:
        a6_pass = not a6_contract.get("hard_veto", False)
    elif agents_raw.get("agent_6", {}).get("hard_filter_pass") is not None:
        a6_pass = bool(agents_raw.get("agent_6", {}).get("hard_filter_pass"))
    elif a6_veto is not None:
        a6_pass = not bool(a6_veto)
    else:
        a6_pass = False

    a4_shadow_here = bool(payload("agent_4").get("shadow_ote_context", {}))
    a5_shadow_here = bool(payload("agent_5").get("shadow_trigger_context", {}))

    return {
        "a1_score": score("agent_1"),
        "a2_pass": passes_contextually("agent_2"),
        "a3_pass": passes_contextually("agent_3"),
        "a4_legacy_pass": passes("agent_4"),
        "a4_shadow_present": a4_shadow_here,
        "a4_contextual_pass": passes_contextually("agent_4"),
        "a5_legacy_pass": passes("agent_5"),
        "a5_shadow_present": a5_shadow_here,
        "a5_contextual_pass": passes_contextually("agent_5"),
        "a6_pass": a6_pass,
        "a6_veto_raw": a6_veto,
        "a7_pass": passes_contextually("agent_7"),
    }


def build_contextual_orchestrator_shadow(
    agents_raw: Dict[str, Any],
    market_context: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    P1.30 — Classification contextuelle shadow (fonction pure).
    Ne modifie pas les décisions réelles.
    """
    f = _extract_agent_fields(agents_raw)

    a1_score        = f["a1_score"]
    a2_pass         = f["a2_pass"]
    a3_pass         = f["a3_pass"]
    a4_ctx_pass     = f["a4_contextual_pass"]
    a4_shadow_here  = f["a4_shadow_present"]
    a5_ctx_pass     = f["a5_contextual_pass"]
    a6_pass         = f["a6_pass"]
    a7_pass         = f["a7_pass"]

    # ── Classification du setup type ──────────────────────────────────────────
    setup_type = "OBSERVATION"
    classification_blocker_reason = "NO_SETUP_RULE_MATCHED"

    if not a6_pass:
        setup_type = "OBSERVATION"
        classification_blocker_reason = "NEWS_VETO"

    elif a1_score >= 80:
        setup_type = "TREND_CONTINUATION"
        classification_blocker_reason = "" if a2_pass else "WAIT_FOR_AGENT2"

    elif a1_score >= 65 and a4_shadow_here:
        setup_type = "SNIPER_PULLBACK"
        classification_blocker_reason = "" if a2_pass else "WAIT_FOR_AGENT2"

    elif a3_pass and a5_ctx_pass and a4_ctx_pass:
        setup_type = "REVERSAL"
        classification_blocker_reason = ""

    else:
        # Diagnostiquer la cause précise du blocage
        if a1_score == 0.0:
            classification_blocker_reason = "AGENT1_SCORE_MISSING"
        elif a1_score >= 65 and not a4_shadow_here:
            classification_blocker_reason = "AGENT4_SHADOW_MISSING"
        elif a1_score < 65 and a2_pass:
            classification_blocker_reason = "AGENT1_SCORE_BELOW_65"
        else:
            classification_blocker_reason = "NO_SETUP_RULE_MATCHED"

    required_agents = REQUIRED_AGENTS_BY_SETUP.get(setup_type, [])
    optional_agents = OPTIONAL_AGENTS_BY_SETUP.get(setup_type, [])

    # ── Évaluation des agents requis ─────────────────────────────────────────
    # Utilise les passes legacy pour les agents 1,2,3,7 et contextuels pour 4,5
    legacy_passes = {
        "agent_1": a1_score >= 65,
        "agent_2": a2_pass,
        "agent_3": a3_pass,
        "agent_4": a4_ctx_pass,
        "agent_5": a5_ctx_pass,
        "agent_6": a6_pass,
        "agent_7": a7_pass,
    }

    missing_required_agents = [ra for ra in required_agents if not legacy_passes.get(ra, False)]
    required_pass_count = len(required_agents) - len(missing_required_agents)
    required_fail_count = len(missing_required_agents)
    optional_pass_count = sum(1 for oa in optional_agents if legacy_passes.get(oa, False))
    hard_veto_agents = [a for a in missing_required_agents if a in required_agents]
    if not a6_pass and "agent_6" not in hard_veto_agents:
        hard_veto_agents.append("agent_6")

    # ── Mode de décision contextuel ──────────────────────────────────────────
    contextual_decision_mode = "WAIT"
    reason_contextual = ""
    human_context_decision_shadow = "REJECT_CONTEXT_INVALID"

    if not a6_pass:
        contextual_decision_mode = "REJECT"
        reason_contextual = "AGENT6_VETO_OR_NEWS"
        human_context_decision_shadow = "REJECT_CONTEXT_INVALID"
    elif setup_type == "OBSERVATION":
        contextual_decision_mode = "WAIT"
        reason_contextual = f"SETUP_OBSERVATION:{classification_blocker_reason}"
        human_context_decision_shadow = "REJECT_CONTEXT_INVALID"
    elif missing_required_agents:
        if missing_required_agents == ["agent_5"]:
            contextual_decision_mode = "CANDIDATE_MICRO"
            reason_contextual = "WAITING_FOR_TRIGGER"
            human_context_decision_shadow = "WAIT_LTF_CONFIRMATION"
        elif "agent_4" in missing_required_agents and "agent_2" not in missing_required_agents:
            contextual_decision_mode = "WAIT"
            reason_contextual = f"MISSING_REQUIRED:{','.join(missing_required_agents)}"
            human_context_decision_shadow = "WAIT_OTE_RETRACEMENT"
        elif "agent_2" in missing_required_agents:
            contextual_decision_mode = "WAIT"
            reason_contextual = f"MISSING_REQUIRED:{','.join(missing_required_agents)}"
            human_context_decision_shadow = "WAIT_ZONE_DEVELOPMENT"
        else:
            contextual_decision_mode = "WAIT"
            reason_contextual = f"MISSING_REQUIRED:{','.join(missing_required_agents)}"
            human_context_decision_shadow = "WAIT_ZONE_DEVELOPMENT"
    else:
        contextual_decision_mode = "STANDARD_PAPER"
        reason_contextual = "ALL_REQUIRED_ALIGN"
        human_context_decision_shadow = "GO_SNIPER"

    return {
        # Classification principale
        "setup_type": setup_type,
        "contextual_decision_mode": contextual_decision_mode,
        "reason_contextual": reason_contextual,
        "classification_blocker_reason": classification_blocker_reason,
        "human_context_decision_shadow": human_context_decision_shadow,
        # Agents requis / optionnels
        "required_agents": required_agents,
        "optional_agents": optional_agents,
        "required_pass_count": required_pass_count,
        "required_fail_count": required_fail_count,
        "missing_required_agents": missing_required_agents,
        "optional_pass_count": optional_pass_count,
        "hard_veto_agents": hard_veto_agents,
        # Diagnostic des champs lus
        "agent1_score_seen": a1_score,
        "agent2_pass_seen": a2_pass,
        "agent3_pass_seen": a3_pass,
        "agent4_pass_seen": a4_ctx_pass,
        "agent4_shadow_present": a4_shadow_here,
        "agent5_pass_seen": a5_ctx_pass,
        "agent5_shadow_present": f["a5_shadow_present"],
        "agent6_veto_seen": not a6_pass,
        # Invariant de sécurité
        "legacy_decision_unchanged": True,
    }


def build_shadow_ict_human_orchestrator_decision(
    agents_raw: Dict[str, Any],
    market_context: Optional[Dict[str, Any]] = None,
    blackboard_memory: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    P1.43 — ICT Human Orchestrator Decision
    Utilise les shadow_ict_contract des agents pour produire une décision SMC humaine.
    """
    if market_context is None:
        market_context = {}
    if blackboard_memory is None:
        blackboard_memory = {}

    def get_contract(aid: str) -> dict:
        return agents_raw.get(aid, {}).get("payload", {}).get("shadow_ict_contract", {})

    a1_contract = get_contract("agent_1")
    a2_contract = get_contract("agent_2")
    a3_contract = get_contract("agent_3")
    a4_contract = get_contract("agent_4")
    a5_contract = get_contract("agent_5")
    a6_contract = get_contract("agent_6")
    a7_contract = get_contract("agent_7")

    # 1. News hard veto
    a6_veto = a6_contract.get("hard_veto", False)
    if a6_veto:
        return {
            "decision": "REJECT",
            "reason": "NEWS_VETO",
            "human_explanation": "Macro event / news lockout active. Rejecting any setup to protect capital.",
            "dominant_context": market_context,
            "blocking_layer": "NEWS",
            "next_required_evidence": "WAIT_POST_NEWS_NORMALIZATION"
        }

    # 2. Temps/session
    a7_ctx = a7_contract.get("contextual_notes", {})
    trading_allowed = a7_ctx.get("trading_allowed", True)
    if not trading_allowed:
        return {
            "decision": "WAIT",
            "reason": "WAIT_FOR_SESSION",
            "human_explanation": "Current time is outside active killzones/macro windows or low liquidity.",
            "dominant_context": market_context,
            "blocking_layer": "TIME",
            "next_required_evidence": "WAIT_FOR_ACTIVE_SESSION"
        }

    # 3. HTF bias / DOL / OF — read from A1 raw fields + mctx
    a1_score = float(agents_raw.get("agent_1", {}).get("score") or 0.0)
    a1_direction = agents_raw.get("agent_1", {}).get("direction") or "UNKNOWN"

    dol = market_context.get("draw_on_liquidity", "UNCLEAR")
    order_flow = market_context.get("order_flow", "MIXED")
    regime = market_context.get("primary_regime", "UNKNOWN")

    is_short = a1_direction == "SHORT"
    is_long = a1_direction == "LONG"

    # A1 score < 60: regime completely unclear → wait for narrative to build
    if a1_score < 60:
        return {
            "decision": "WAIT",
            "reason": "WAIT_FOR_HTF_NARRATIVE",
            "human_explanation": "HTF A1 score too low to establish a clear directional bias. Watching.",
            "dominant_context": market_context,
            "blocking_layer": "HTF_NARRATIVE",
            "next_required_evidence": "A1_SCORE_GE_60"
        }

    # A1 score 60-79: moderate bias — check alignment
    # Score >= 80: strong narrative → DOL is confirmed open by mctx
    dol_open = (
        (is_short and (dol == "SELL_SIDE" or a1_score >= 80)) or
        (is_long and (dol == "BUY_SIDE" or a1_score >= 80))
    )
    of_intact = (
        (is_short and order_flow in ("BEARISH", "MIXED")) or
        (is_long and order_flow in ("BULLISH", "MIXED")) or
        (a1_score >= 80)  # strong A1 implies OF intact
    )

    if not dol_open:
        return {
            "decision": "WAIT",
            "reason": "WAIT_FOR_DOL_ALIGNMENT",
            "human_explanation": "HTF bias exists but Draw on Liquidity direction is not confirmed yet.",
            "dominant_context": market_context,
            "blocking_layer": "HTF_NARRATIVE",
            "next_required_evidence": "DOL_CONFIRMED_OPEN"
        }

    if not of_intact:
        return {
            "decision": "WAIT",
            "reason": "WAIT_FOR_OF_ALIGNMENT",
            "human_explanation": "Institutional Order Flow is not aligned with the HTF bias.",
            "dominant_context": market_context,
            "blocking_layer": "HTF_NARRATIVE",
            "next_required_evidence": "WAIT_FOR_OF_ALIGNMENT"
        }

    # 4. POI — use A2 hard_filter_pass as the canonical veto signal (with contract as enrichment)
    a2_raw = agents_raw.get("agent_2", {})
    a2_hard_filter_pass = bool(a2_raw.get("hard_filter_pass", False))
    a2_veto = a2_contract.get("hard_veto", not a2_hard_filter_pass)  # use contract if present, else invert filter
    zone_lifecycle = (a2_contract.get("contextual_notes", {}) or {}).get("zone_lifecycle", "UNKNOWN")
    
    if not a2_contract:
        return {
            "decision": "WAIT",
            "reason": "WAIT_FOR_POI",
            "human_explanation": "HTF narrative is strong, but no POI has been identified yet.",
            "dominant_context": market_context,
            "blocking_layer": "POI",
            "next_required_evidence": "MATURE_POI_OR_VALID_REBALANCE"
        }
    
    if a2_veto and zone_lifecycle == "WICK_TAGGED":
        return {
            "decision": "WAIT",
            "reason": "WAIT_ZONE_DEVELOPMENT",
            "human_explanation": "POI is identified but still developing (WICK_TAGGED). Waiting for mature mitigation.",
            "dominant_context": market_context,
            "blocking_layer": "POI",
            "next_required_evidence": "MATURE_MITIGATION"
        }

    if a2_veto:
        return {
            "decision": "WAIT",
            "reason": "WAIT_FOR_POI",
            "human_explanation": "POI is invalid or fully mitigated. Waiting for new structure.",
            "dominant_context": market_context,
            "blocking_layer": "POI",
            "next_required_evidence": "NEW_MATURE_POI"
        }

    # 5. Trigger micro — use A5 hard_filter_pass as canonical signal
    a5_raw = agents_raw.get("agent_5", {})
    a5_hard_filter_pass = bool(a5_raw.get("hard_filter_pass", False))
    
    score_shadow = float(a5_contract.get("score", 0.0))
    a5_uncertainty = a5_contract.get("uncertainty", "HIGH")
    session_favorable = a7_contract.get("contextual_notes", {}).get("trading_allowed", True)
    
    # Calculate shadow decision based on score
    if score_shadow >= 75 and session_favorable and a5_uncertainty == "LOW":
        return {
            "decision": "PREMIUM_PAPER_SHADOW",
            "reason": "ALL_ALIGNED_HIGH_CONFIDENCE_SHADOW",
            "human_explanation": f"A+ setup: Narrative, POI, and Micro trigger (score={score_shadow}) aligned with low noise.",
            "dominant_context": market_context,
            "blocking_layer": "NONE",
            "next_required_evidence": "NONE"
        }
    elif score_shadow >= 60:
        return {
            "decision": "STANDARD_PAPER_SHADOW",
            "reason": "ALL_ALIGNED_SHADOW",
            "human_explanation": f"Standard setup: Context and trigger (score={score_shadow}) are aligned.",
            "dominant_context": market_context,
            "blocking_layer": "NONE",
            "next_required_evidence": "NONE"
        }
    else:
        return {
            "decision": "CANDIDATE_MICRO",
            "reason": "WAITING_FOR_TRIGGER",
            "human_explanation": f"Valid POI and HTF context present, but waiting for LTF trigger confirmation (score={score_shadow}).",
            "dominant_context": market_context,
            "blocking_layer": "MICRO_TRIGGER",
            "next_required_evidence": "LTF_CHoCH_OR_DISPLACEMENT_SCORE_60"
        }
