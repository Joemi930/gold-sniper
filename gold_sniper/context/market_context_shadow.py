from typing import Dict, Any

def build_market_context_shadow(event: dict, agents_raw: dict) -> dict:
    a1_score = float(agents_raw.get("agent_1", {}).get("score") or 0.0)
    a1_direction = agents_raw.get("agent_1", {}).get("direction") or "UNKNOWN"
    
    a2_payload = agents_raw.get("agent_2", {})
    a2_pass = a2_payload.get("hard_filter_pass", False)
    
    a3_payload = agents_raw.get("agent_3", {})
    a3_pass = a3_payload.get("hard_filter_pass", False)
    
    a4_payload = agents_raw.get("agent_4", {}).get("payload", {})
    a4_shadow = a4_payload.get("shadow_ote_context", {})
    a4_conflict = a4_shadow.get("premium_conflict_mode", "")
    a4_favorable = bool(a4_shadow) and (a4_conflict != "HARD_VETO")
    
    a5_payload = agents_raw.get("agent_5", {}).get("payload", {})
    a5_shadow = a5_payload.get("shadow_trigger_context", {})
    a5_conflict = a5_shadow.get("missing_trigger_conflict_mode", "")
    a5_favorable = bool(a5_shadow) and (a5_conflict != "HARD_VETO")

    primary_regime = "UNKNOWN"
    delivery_phase = "UNKNOWN"
    draw_on_liquidity = "UNCLEAR"
    order_flow = "MIXED"
    trend_continuation_candidate = False
    reversal_candidate = False
    context_blocker_reason = "NONE"
    
    if a1_score >= 80:
        primary_regime = "STRONG_UP" if a1_direction == "LONG" else "STRONG_DOWN"
        order_flow = "BULLISH" if a1_direction == "LONG" else "BEARISH"
        draw_on_liquidity = "BUY_SIDE" if a1_direction == "LONG" else "SELL_SIDE"
        if not a2_pass:
            context_blocker_reason = "HTF_STRONG_BUT_NO_VALID_POI"
        else:
            delivery_phase = "EXPANSION"
            
        if not a4_favorable:
            trend_continuation_candidate = True
            
    elif a1_score >= 65:
        primary_regime = "RETRACEMENT"
        order_flow = "BULLISH" if a1_direction == "LONG" else "BEARISH"
        if a2_pass:
            delivery_phase = "SNIPER_PULLBACK_CONTEXT"
            
    if a2_pass and a1_score < 65:
        context_blocker_reason = "POI_WITHOUT_HTF_CONFIRMATION"

    # Agent3 sweep + Agent5 trigger + Agent4 favorable
    # Note: Using hard_filter_pass for agent3 and contextual favorability for a5/a4
    if a3_pass and a5_favorable and a4_favorable:
        reversal_candidate = True
        primary_regime = "REVERSAL_CANDIDATE"

    return {
        "primary_regime": primary_regime,
        "delivery_phase": delivery_phase,
        "draw_on_liquidity": draw_on_liquidity,
        "order_flow": order_flow,
        "trend_continuation_candidate": trend_continuation_candidate,
        "reversal_candidate": reversal_candidate,
        "context_confidence": a1_score,
        "context_blocker_reason": context_blocker_reason
    }
