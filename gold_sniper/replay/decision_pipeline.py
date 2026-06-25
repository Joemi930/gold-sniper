"""Replay-only decision pipeline for one-shot agents."""
from __future__ import annotations

import inspect
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Sequence

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

from agents.base_agent import AgentResult
from gold_sniper.replay.evidence_builder import (
    build_evidence_bundle_from_blackboard,
    bundle_to_json_dict,
    validate_evidence_bundle,
)
from gold_sniper.strategy.professional_decision_engine import evaluate_professional_decision
from gold_sniper.strategy.readiness_risk_gate_contract import evaluate_readiness_risk_gate
from gold_sniper.strategy.kasper_contracts import build_kasper_evidence_bundle
from gold_sniper.strategy.kasper_scenario_engine import evaluate_kasper_scenario
from gold_sniper.strategy.risk_allocator import allocate_risk
from dataclasses import replace
from gold_sniper.strategy.contracts import DecisionAction, EvidenceBundle, SetupGrade, SetupType
from utils.agent_dashboard_helpers import idle_result


ReplayAgentRunner = Callable[[dict[str, Any], Any], Awaitable[AgentResult | None] | AgentResult | None]


class ReplayDecisionPipeline:
    def __init__(
        self,
        *,
        agent_runners: Sequence[ReplayAgentRunner] | None = None,
        use_orchestrator: bool = False,
        alignment_diagnostics: Iterable[str] | None = None,
    ) -> None:
        if use_orchestrator:
            raise ValueError("P1-clean forbids replay live-orchestrator coupling")
        self.agent_runners = list(agent_runners or [])
        self.use_orchestrator = False
        self.alignment_diagnostics = set(alignment_diagnostics or [])

    @classmethod
    def from_agent_ids(
        cls,
        agent_ids: Iterable[str],
        *,
        use_orchestrator: bool = False,
        news_events: Sequence[dict[str, Any]] | None = None,
        news_feed_alive: bool = True,
        news_source: str = "REPLAY_JSONL",
        diagnose_agents: Iterable[str] | None = None,
        alignment_diagnostics: Iterable[str] | None = None,
    ) -> "ReplayDecisionPipeline":
        if use_orchestrator:
            raise ValueError("P1-clean forbids replay live-orchestrator coupling")
        runners: list[ReplayAgentRunner] = []
        diagnostics = set(diagnose_agents or [])
        for agent_id in agent_ids:
            if agent_id == "agent_1":
                runners.append(run_replay_agent_1)
            elif agent_id == "agent_2":
                runners.append(make_replay_agent_2(diagnose=agent_id in diagnostics))
            elif agent_id == "agent_3":
                runners.append(run_replay_agent_3)
            elif agent_id == "agent_4":
                runners.append(run_replay_agent_4)
            elif agent_id == "agent_5":
                runners.append(make_replay_agent_5(diagnose=agent_id in diagnostics))
            elif agent_id == "agent_6":
                runners.append(make_replay_agent_6(news_events or [], feed_alive=news_feed_alive, source=news_source))
            elif agent_id == "agent_7":
                runners.append(run_replay_agent_7)
            else:
                raise ValueError(f"Replay agent not supported yet: {agent_id}")
        return cls(
            agent_runners=runners,
            use_orchestrator=use_orchestrator,
            alignment_diagnostics=alignment_diagnostics,
        )

    async def __call__(self, candle: dict[str, Any], blackboard) -> dict[str, Any]:
        agent_errors: dict[str, str] = {}
        for runner in self.agent_runners:
            agent_id = _runner_agent_id(runner)
            try:
                result = runner(candle, blackboard)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                agent_errors[agent_id] = str(exc)
                continue
            if result is not None:
                await _publish_agent_result(blackboard, result)

        agent_results = _collect_agent_results(blackboard)
        agents = {result.agent_id: _agent_event_payload(result) for result in agent_results}
        for agent_id, error in agent_errors.items():
            agents.setdefault(agent_id, {})["error"] = error
        alignment_diagnostic = _alignment_diagnostic_payload(candle, blackboard, self.alignment_diagnostics)

        # P1-replay: build EvidenceBundle → run PDE
        ts_utc = candle["time"].isoformat() if hasattr(candle["time"], "isoformat") else str(candle["time"])
        bundle = build_evidence_bundle_from_blackboard(
            blackboard,
            symbol=str(candle.get("symbol") or "XAUUSD"),
            ts_utc=ts_utc,
        )
        validation_errors = validate_evidence_bundle(bundle)
        decision_result = evaluate_professional_decision(bundle)
        p1_payload = _p1_decision_payload(bundle, decision_result, validation_errors)

        # P2-E Phase11: propagate micro contract from EvidenceBundle.micro
        _bundle_micro = bundle.micro if isinstance(bundle.micro, dict) else {}
        p1_payload["micro_contract_status"] = _bundle_micro.get("micro_contract_status")
        p1_payload["micro_contract_readiness"] = _bundle_micro.get("readiness_state")
        p1_payload["micro_contract_reason"] = _bundle_micro.get("readiness_reason")
        p1_payload["micro_contract_confirmed"] = _bundle_micro.get("micro_is_confirmed")
        p1_payload["micro_contract_waiting_trigger"] = _bundle_micro.get("micro_is_waiting_trigger")
        p1_payload["micro_contract_invalid"] = _bundle_micro.get("micro_is_invalid")
        p1_payload["micro_contract_missing_data"] = _bundle_micro.get("micro_is_missing_data")
        p1_payload["micro_contract_outside_poi"] = _bundle_micro.get("micro_is_outside_poi")
        p1_payload["micro_contract_missing_fields"] = _bundle_micro.get("micro_missing_fields", [])
        p1_payload["micro_contract_present_fields"] = _bundle_micro.get("micro_present_fields", [])
        p1_payload["micro_contract_contradictions"] = _bundle_micro.get("micro_contradictions", [])
        p1_payload["micro_evidence"] = _bundle_micro.get("micro_evidence", {})
        p1_payload["sweep_1m_confirmed"] = _bundle_micro.get("sweep_1m_confirmed")
        p1_payload["choch_detected"] = _bundle_micro.get("choch_detected")
        p1_payload["trigger_inside_poi"] = _bundle_micro.get("trigger_inside_poi")
        p1_payload["price_in_agent2_poi"] = _bundle_micro.get("price_in_agent2_poi")
        p1_payload["trigger_outside_poi"] = _bundle_micro.get("trigger_outside_poi")
        p1_payload["retest_confirmed"] = _bundle_micro.get("retest_confirmed")
        p1_payload["trigger_confirmed"] = _bundle_micro.get("trigger_confirmed")
        p1_payload["candles_1m_count"] = _bundle_micro.get("candles_1m_count")

        if not agent_results:
            return {
                **p1_payload,
                "agents": agents,
                "agent_errors": agent_errors,
                "alignment_diagnostic": alignment_diagnostic,
                "orchestrator": {
                    "enabled": False,
                    "decision": "DISABLED",
                    "reason": "P1_REPLAY_ORCHESTRATOR_FORBIDDEN",
                },
                "reject_reason": p1_payload.get("veto_code") or ("P1_REPLAY_VALIDATED_DECISION_" + p1_payload.get("decision", "REJECT")),
            }


        return {
            **p1_payload,
            "agents": agents,
            "agent_errors": agent_errors,
            "alignment_diagnostic": alignment_diagnostic,
            "orchestrator": {
                "enabled": False,
                "decision": "DISABLED",
                "reason": "P1_CLEAN_REPLAY_LIVE_ORCHESTRATOR_FORBIDDEN",
            },
            "reject_reason": p1_payload.get("veto_code") or ("P1_REPLAY_VALIDATED_DECISION_" + p1_payload.get("decision", "REJECT")),
        }


async def run_replay_agent_1(candle: dict[str, Any], blackboard) -> AgentResult:
    del candle
    from agents.agent_1_meteo import calculate_agent_1_result

    candles_4h = list(blackboard.read_sync("market_data.candles.4H") or [])
    candles_15m = list(blackboard.read_sync("market_data.candles.15m") or [])
    if len(candles_4h) < 10 or len(candles_15m) < 10:
        return idle_result(
            "agent_1",
            reason="REPLAY_WAITING_INSUFFICIENT_CANDLES",
            hard_filter_pass=False,
        )

    atr_14 = _safe_read(blackboard, "market_data.atr_14")
    if not atr_14:
        ranges = [float(c["high"]) - float(c["low"]) for c in candles_15m[-14:]]
        atr_14 = sum(ranges) / len(ranges) if ranges else 0.001

    result, structure_4h, structure_15m = await calculate_agent_1_result(candles_4h, candles_15m, atr_14)
    await blackboard.update_dict(
        "agents.agent_1",
        {
            "bias": result.direction if result.direction else "NEUTRAL",
            "market_phase": "EXPANSION" if result.score > 0 else "PULLBACK",
            "score": result.score,
            "direction": result.direction,
            "reason": result.reason,
            "hard_filter_pass": result.hard_filter_pass,
        },
    )
    await blackboard.update_dict(
        "market_analysis.market_structure",
        {
            "trend_4h": structure_4h["state"],
            "trend_15m": structure_15m["state"],
            "overall_bias": result.direction if result.direction else "NEUTRAL",
        },
    )
    return result


def make_replay_agent_2(*, diagnose: bool = False) -> ReplayAgentRunner:
    async def runner(candle: dict[str, Any], blackboard) -> AgentResult:
        return await run_replay_agent_2(candle, blackboard, diagnose=diagnose)

    runner.replay_agent_id = "agent_2"  # type: ignore[attr-defined]
    return runner


async def run_replay_agent_2(candle: dict[str, Any], blackboard, *, diagnose: bool = False) -> AgentResult:
    import asyncio
    import numpy as np

    from agents.agent_1_meteo import detect_swings
    from agents.agent_2_cartographe import (
        _arrays_to_ohlcv,
        _estimate_atr_14,
        detect_breaker_blocks,
        detect_fvg,
        detect_order_blocks,
        build_replay_agent_2_diagnostic,
        build_p2a_poi_connectivity_payload,
        select_best_order_block_with_ote_confluence,
        score_agent_2,
    )

    agent1_result = blackboard.read_sync("agent_results.agent_1")
    if not agent1_result or agent1_result.score == 0 or not agent1_result.direction:
        await blackboard.update_dict("agents.agent_2", {"order_blocks": []})
        return AgentResult("agent_2", 0, False, None, "WAITING_ON_AGENT1_FAIL")

    direction = agent1_result.direction
    candles_15m = list(blackboard.read_sync("market_data.candles.15m") or [])
    atr_14 = _safe_read(blackboard, "market_data.atr_14") or _estimate_atr_14(candles_15m)
    current_tick = blackboard.read_sync("market_data.current_tick")
    if len(candles_15m) < 10 or not atr_14 or not current_tick:
        return idle_result("agent_2", reason="WAITING_INSUFFICIENT_DATA", hard_filter_pass=True)

    high = np.array([c["high"] for c in candles_15m], dtype=float)
    low = np.array([c["low"] for c in candles_15m], dtype=float)
    open_ = np.array([c["open"] for c in candles_15m], dtype=float)
    close = np.array([c["close"] for c in candles_15m], dtype=float)
    volume_values = [float(c.get("volume") or c.get("tick_volume") or c.get("real_volume") or 0.0) for c in candles_15m]
    volume = np.array(volume_values, dtype=float) if any(volume_values) else None
    loop = asyncio.get_running_loop()
    swings = await loop.run_in_executor(None, lambda: detect_swings(high, low, close, n=3, atr_14=atr_14))
    agent1_meta = agent1_result.payload or {}
    htf_bias = agent1_meta.get("structure_4h") or direction
    liquidity_pools = dict(blackboard.get_all().get("market_analysis", {}).get("liquidity_pools", {}) or {})
    fvgs = detect_fvg(high, low, atr_14, direction)
    obs = await loop.run_in_executor(
        None,
        lambda: detect_order_blocks(
            high,
            low,
            open_,
            close,
            swings["swing_highs"],
            swings["swing_lows"],
            atr_14,
            direction,
            htf_bias=htf_bias,
            liquidity_pools=liquidity_pools,
            volume=volume,
        ),
    )
    ohlcv = _arrays_to_ohlcv(open_, high, low, close, volume)
    breakers = detect_breaker_blocks([], ohlcv)
    best_ob = select_best_order_block_with_ote_confluence(obs, candles_15m, swings, direction)
    best_fvg = fvgs[0] if fvgs else None
    bid = float(current_tick.get("bid", 0.0))
    ask = float(current_tick.get("ask", 0.0))
    current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(close[-1])
    result = score_agent_2(best_ob, best_fvg, current_price, atr_14, blackboard)

    # ── P2-A Connectivity Repair — toujours présent, même sans diagnose ──
    p2a_payload = build_p2a_poi_connectivity_payload(
        obs=obs,
        fvgs=fvgs,
        best_ob=best_ob,
        best_fvg=best_fvg,
        direction=direction,
        current_price=current_price,
        atr_14=atr_14,
        blackboard=blackboard,
    )
    payload = dict(result.payload or {})
    payload["p2a_poi_connectivity"] = p2a_payload
    result = AgentResult(
        agent_id=result.agent_id,
        score=result.score,
        hard_filter_pass=result.hard_filter_pass,
        direction=result.direction,
        reason=result.reason,
        payload=payload,
        veto=result.veto,
        risk_modifier=result.risk_modifier,
    )
    # ──────────────────────────────────────────────────────────────────────

    if diagnose:
        payload = dict(result.payload or {})
        payload["diagnostic"] = build_replay_agent_2_diagnostic(
            candle=candle,
            blackboard=blackboard,
            candles_15m=candles_15m,
            candles_4h=list(blackboard.read_sync("market_data.candles.4H") or []),
            obs=obs,
            fvgs=fvgs,
            selected_ob=best_ob,
            atr_14=atr_14,
            direction=direction,
            final_reason=result.reason,
            hard_filter_pass=result.hard_filter_pass,
            score=result.score,
        )
        result = AgentResult(
            agent_id=result.agent_id,
            score=result.score,
            hard_filter_pass=result.hard_filter_pass,
            direction=result.direction,
            reason=result.reason,
            payload=payload,
            veto=result.veto,
            risk_modifier=result.risk_modifier,
        )
    payload = result.payload or {}
    ui_zones = [*obs]
    if best_fvg:
        ui_zones.append(best_fvg)
    await blackboard.update_agent(
        "agent_2",
        {
            "score": result.score,
            "direction": result.direction,
            "active_ob": payload.get("active_ob"),
            "active_fvg": payload.get("active_fvg"),
            "breaker_blocks": breakers,
            "poi_zone": payload.get("poi_zone"),
            "ob_score": payload.get("ob_score", 0),
            "zone_is_fresh": payload.get("zone_is_fresh", False),
            "reason": result.reason,
            "hard_filter_pass": result.hard_filter_pass,
        },
    )
    await blackboard.update_dict("agents.agent_2", {"order_blocks": ui_zones, "fvgs": fvgs})
    await blackboard.update_dict("market_analysis.zones", {"order_blocks": ui_zones, "fvgs": fvgs, "breaker_blocks": breakers})
    return result


async def run_replay_agent_3(candle: dict[str, Any], blackboard) -> AgentResult:
    del candle
    import asyncio
    import numpy as np

    from agents.agent_1_meteo import detect_swings
    from agents.agent_3_liquidite import (
        _estimate_atr_14,
        _enrich_agent3_result_with_handoff,
        _extract_agent2_p2a_liquidity_anchor,
        _to_ohlcv,
        check_asian_range,
        detect_equal_levels,
        detect_inducement,
        detect_liquidity_event,
        score_agent_3,
    )

    agent2_result = blackboard.read_sync("agent_results.agent_2")
    agent1_result = blackboard.read_sync("agent_results.agent_1")
    liquidity_anchor, liquidity_handoff = _extract_agent2_p2a_liquidity_anchor(blackboard)
    if not agent1_result or not agent2_result:
        await blackboard.update_dict("agents.agent_3", {"equal_highs": [], "equal_lows": []})
        return AgentResult(
            "agent_3",
            30,
            True,
            None,
            "WAITING_ON_AGENT2_RESULT",
            payload={
                "execution_readiness": "UNAVAILABLE",
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "LIQUIDITY_WAITING_AGENT2_RESULT",
                "agent3_poi_handoff": liquidity_handoff,
                "liquidity_handoff_status": "P2A_POI_MISSING",
            },
        )
    if not liquidity_anchor:
        await blackboard.update_dict("agents.agent_3", {"equal_highs": [], "equal_lows": []})
        return AgentResult(
            "agent_3",
            30,
            True,
            None,
            "WAITING_ON_AGENT2_POI",
            payload={
                "execution_readiness": "UNAVAILABLE",
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "LIQUIDITY_POI_MISSING",
                "agent3_poi_handoff": liquidity_handoff,
                "liquidity_handoff_status": "P2A_POI_MISSING",
            },
        )

    direction = agent1_result.direction
    if not direction:
        return idle_result("agent_3", reason="WAITING_NO_DIRECTION", score=30)

    candles_15m = list(blackboard.read_sync("market_data.candles.15m") or [])
    candles_1m = list(blackboard.read_sync("market_data.candles.1m") or [])
    atr_14 = _safe_read(blackboard, "market_data.atr_14") or _estimate_atr_14(candles_15m)
    if len(candles_15m) < 10 or not atr_14:
        return idle_result("agent_3", reason="WAITING_INSUFFICIENT_DATA", score=30)

    high = np.array([c["high"] for c in candles_15m], dtype=float)
    low = np.array([c["low"] for c in candles_15m], dtype=float)
    open_ = np.array([c["open"] for c in candles_15m], dtype=float)
    close = np.array([c["close"] for c in candles_15m], dtype=float)
    loop = asyncio.get_running_loop()
    swings = await loop.run_in_executor(None, lambda: detect_swings(high, low, close, n=3, atr_14=atr_14))
    eq_levels = detect_equal_levels(swings["swing_highs"], swings["swing_lows"], high, low, atr_14)
    eqh_level = eq_levels["eqh"][0]["level"] if eq_levels["eqh"] else 0.0
    eql_level = eq_levels["eql"][0]["level"] if eq_levels["eql"] else 0.0
    event = detect_liquidity_event(high, low, close, eqh_level, eql_level, atr_14, direction)
    asian_range = check_asian_range(candles_1m, atr_14)
    ohlcv = _to_ohlcv(open_, high, low, close)
    swing_lows_idx = [item["index"] for item in swings["swing_lows"]]
    swing_highs_idx = [item["index"] for item in swings["swing_highs"]]
    major_low = min((item["price"] for item in swings["swing_lows"]), default=None)
    major_high = max((item["price"] for item in swings["swing_highs"]), default=None)
    idm = detect_inducement(ohlcv, swing_lows_idx, major_low, direction, atr_14, swing_highs_idx, major_high)
    result = score_agent_3(event, asian_range, direction, idm)
    result = _enrich_agent3_result_with_handoff(result, liquidity_anchor, liquidity_handoff)
    payload = result.payload or {}
    await blackboard.update_agent(
        "agent_3",
        {
            "score": result.score,
            "direction": result.direction,
            "eqh_levels": eq_levels["eqh"],
            "eql_levels": eq_levels["eql"],
            "sweep_detected": payload.get("event") == "SWEEP",
            "break_detected": payload.get("event") == "BREAK",
            "sweep_side": payload.get("sweep_type"),
            "sweep_depth_ratio": payload.get("sweep_depth_ratio", 0.0),
            "asian_range": asian_range,
            "idm_detected": payload.get("idm_detected", False),
            "idm_swept": payload.get("idm_swept", False),
            "reason": result.reason,
            "hard_filter_pass": result.hard_filter_pass,
            "execution_readiness": payload.get("execution_readiness"),
            "readiness_state": payload.get("readiness_state"),
            "readiness_reason": payload.get("readiness_reason"),
            "agent3_poi_handoff": payload.get("agent3_poi_handoff"),
            "agent3_consumed_poi": payload.get("agent3_consumed_poi"),
            "liquidity_handoff_status": payload.get("liquidity_handoff_status"),
        },
    )
    return result


async def run_replay_agent_4(candle: dict[str, Any], blackboard) -> AgentResult:
    del candle
    import numpy as np

    from agents.agent_1_meteo import detect_swings
    from agents.agent_4_fibonacci import (
        _estimate_atr_14,
        diagnose_contextual_ote,
        enrich_agent4_result_with_handoff,
        enrich_agent4_result_with_swing,
        extract_agent2_p2a_ote_anchor,
        score_fibonacci_ote,
        select_ote_swing_with_agent2_anchor,
    )

    agent2_result = blackboard.read_sync("agent_results.agent_2")
    agent1_result = blackboard.read_sync("agent_results.agent_1")
    ote_anchor, ote_handoff = extract_agent2_p2a_ote_anchor(blackboard)
    if not agent1_result or not agent2_result:
        await blackboard.update_dict("agents.agent_4", {"price_in_ote": False})
        return AgentResult(
            "agent_4",
            25,
            False,
            None,
            "WAITING_ON_AGENT2_RESULT",
            payload={
                "execution_readiness": "UNAVAILABLE",
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "OTE_WAITING_AGENT2_RESULT",
                "agent4_poi_handoff": ote_handoff,
                "ote_handoff_status": "P2A_POI_MISSING",
            },
        )
    if not ote_anchor:
        await blackboard.update_dict("agents.agent_4", {"price_in_ote": False})
        return AgentResult(
            "agent_4",
            25,
            False,
            None,
            "WAITING_ON_AGENT2_POI",
            payload={
                "execution_readiness": "UNAVAILABLE",
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "OTE_POI_MISSING",
                "agent4_poi_handoff": ote_handoff,
                "ote_handoff_status": "P2A_POI_MISSING",
            },
        )

    direction = agent1_result.direction
    if not direction:
        return idle_result("agent_4", reason="WAITING_NO_DIRECTION", score=25)

    candles_15m = list(blackboard.read_sync("market_data.candles.15m") or [])
    atr_14 = _safe_read(blackboard, "market_data.atr_14") or _estimate_atr_14(candles_15m)
    current_tick = blackboard.read_sync("market_data.current_tick")
    if len(candles_15m) < 10 or not atr_14 or not current_tick:
        return idle_result("agent_4", reason="WAITING_INSUFFICIENT_DATA", score=25)

    high = np.array([c["high"] for c in candles_15m], dtype=float)
    low = np.array([c["low"] for c in candles_15m], dtype=float)
    close = np.array([c["close"] for c in candles_15m], dtype=float)
    swings = detect_swings(high, low, close, n=3, atr_14=atr_14)
    if not swings["swing_highs"] or not swings["swing_lows"]:
        return idle_result("agent_4", reason="WAITING_NO_SWINGS", score=25)

    swing_selection = select_ote_swing_with_agent2_anchor(candles_15m, swings, direction, ote_anchor)
    if not swing_selection:
        return idle_result("agent_4", reason="WAITING_NO_SWINGS", score=25)
    fib_levels = swing_selection["levels"]
    bid = current_tick.get("bid", 0.0)
    ask = current_tick.get("ask", 0.0)
    current_price = (bid + ask) / 2 if bid > 0 else close[-1]
    market = blackboard.get_market()
    result = score_fibonacci_ote(current_price, fib_levels, direction, market.get("dxy_bias", "NEUTRAL"))
    result = enrich_agent4_result_with_swing(result, swing_selection)
    result = enrich_agent4_result_with_handoff(result, ote_anchor, ote_handoff)
    payload = result.payload or {}

    # P1.28 shadow diagnostic — no decision change
    legacy_veto = payload.get("forbidden", False)
    agent1_score = agent1_result.score
    shadow_diag = diagnose_contextual_ote(
        payload, current_price, direction, agent1_score, legacy_veto
    )
    payload["shadow_ote_context"] = shadow_diag
    result = AgentResult(
        agent_id=result.agent_id,
        score=result.score,
        hard_filter_pass=result.hard_filter_pass,
        direction=result.direction,
        reason=result.reason,
        payload=payload,
        veto=result.veto,
        risk_modifier=result.risk_modifier,
    )

    swing_used = payload.get("swing_used") or {}
    await blackboard.update_agent(
        "agent_4",
        {
            "score": result.score,
            "direction": result.direction,
            "swing_used": swing_used,
            "ote_anchor_mode": payload.get("ote_anchor_mode"),
            "agent4_swing_contains_agent2_zone": payload.get("agent4_swing_contains_agent2_zone", False),
            "poi_zone_used": payload.get("poi_zone_used"),
            "ote_low": payload.get("ote_low"),
            "ote_high": payload.get("ote_high"),
            "ote_sweet": payload.get("ote_sweet"),
            "equilibrium": payload.get("equilibrium"),
            "in_ote": payload.get("in_ote", False),
            "price_in_ote": payload.get("price_in_ote", False),
            "in_discount": payload.get("in_discount", False),
            "in_premium": payload.get("in_premium", False),
            "precision_pct": payload.get("precision_pct", 0.0),
            "execution_readiness": payload.get("execution_readiness"),
            "readiness_state": payload.get("readiness_state"),
            "readiness_reason": payload.get("readiness_reason"),
            "agent4_poi_handoff": payload.get("agent4_poi_handoff"),
            "agent4_consumed_poi": payload.get("agent4_consumed_poi"),
            "ote_handoff_status": payload.get("ote_handoff_status"),
            "reason": result.reason,
            "hard_filter_pass": result.hard_filter_pass,
            "payload": payload,
        },
    )
    return result


def make_replay_agent_5(*, diagnose: bool = False) -> ReplayAgentRunner:
    async def runner(candle: dict[str, Any], blackboard) -> AgentResult:
        return await run_replay_agent_5(candle, blackboard, diagnose=diagnose)

    runner.replay_agent_id = "agent_5"  # type: ignore[attr-defined]
    return runner


async def run_replay_agent_5(candle: dict[str, Any], blackboard, *, diagnose: bool = False) -> AgentResult:
    from agents.agent_5_microscope import run_agent_5, diagnose_contextual_trigger

    candles_1m = list(blackboard.read_sync("market_data.candles.1m") or [])
    result = await run_agent_5(candles_1m, blackboard, diagnose=diagnose, replay_candle=candle)
    
    # P1.29 shadow diagnostic — no decision change
    agent1_result = blackboard.read_sync("agent_results.agent_1")
    agent1_score = agent1_result.score if agent1_result else 0.0
    payload = result.payload or {}
    legacy_hard_filter_pass = result.hard_filter_pass
    
    shadow_diag = diagnose_contextual_trigger(
        payload, agent1_score, legacy_hard_filter_pass
    )
    payload["shadow_trigger_context"] = shadow_diag
    
    result = AgentResult(
        agent_id=result.agent_id,
        score=result.score,
        hard_filter_pass=result.hard_filter_pass,
        direction=result.direction,
        reason=result.reason,
        payload=payload,
        veto=result.veto,
        risk_modifier=result.risk_modifier,
    )
    await blackboard.update_agent("agent_5", {"payload": payload})
    return result


def make_replay_agent_6(
    events: Sequence[dict[str, Any]],
    *,
    feed_alive: bool = True,
    source: str = "REPLAY_JSONL",
) -> ReplayAgentRunner:
    async def runner(candle: dict[str, Any], blackboard) -> AgentResult:
        return await run_replay_agent_6(candle, blackboard, events, feed_alive=feed_alive, source=source)

    runner.replay_agent_id = "agent_6"  # type: ignore[attr-defined]
    return runner


async def run_replay_agent_6(
    candle: dict[str, Any],
    blackboard,
    events: Sequence[dict[str, Any]],
    *,
    feed_alive: bool = True,
    source: str = "REPLAY_JSONL",
) -> AgentResult:
    from agents.agent_6_sentinelle import evaluate_calendar_state, is_gold_relevant_event, normalize_calendar_event

    now = _ensure_utc(candle["time"])
    relevant_events = [
        event
        for raw_event in events
        if is_gold_relevant_event((event := normalize_calendar_event(dict(raw_event), now)))
    ]
    state = evaluate_calendar_state(relevant_events, now, feed_alive=feed_alive)
    await blackboard.update_agent(
        "agent_6",
        {
            "score": state["score"],
            "blocked": state["blocked"],
            "veto": state["veto"],
            "impact_level": state["impact_level"],
            "next_event": state["next_event"],
            "resume_at": state["resume_at"],
            "feed_alive": state["feed_alive"],
            "stealth_mode": state["stealth_mode"],
            "reason": state["reason"],
            "calendar_source": source,
            "last_error": None if feed_alive else "REPLAY_NEWS_CALENDAR_MISSING_OR_EMPTY",
        },
    )
    await blackboard.update_dict(
        "risk_management.volatility_gate",
        {
            "allow_trade": not state["veto"],
            "next_news_time": state["next_event"]["time"] if state["next_event"] else None,
            "news_blackout": state["blocked"],
            "stealth_mode": state["stealth_mode"],
            "impact_level": state["impact_level"],
            "reason": state["reason"],
        },
    )
    return AgentResult(
        agent_id="agent_6",
        score=state["score"],
        reason=state["reason"],
        direction=None,
        hard_filter_pass=not state["veto"],
        veto=state["veto"],
        payload={**state, "calendar_source": source, "events_loaded": len(relevant_events)},
    )


async def run_replay_agent_7(candle: dict[str, Any], blackboard) -> AgentResult:
    from agents.agent_7_chronos import calculate_volume_profile, score_agent_7

    utc_time = _ensure_utc(candle["time"])
    candles_15m = list(blackboard.read_sync("market_data.candles.15m") or [])
    session_candles = [
        stored
        for stored in candles_15m
        if "time" in stored and _ensure_utc(stored["time"]).date() == utc_time.date()
    ]
    volume_profile = calculate_volume_profile(session_candles)
    tick = blackboard.read_sync("market_data.current_tick")
    bid = float(tick.get("bid", 0.0) if tick else 0.0)
    ask = float(tick.get("ask", 0.0) if tick else 0.0)
    current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(candle["close"])
    result = score_agent_7(utc_time, volume_profile, current_price)
    payload = result.payload or {}
    await blackboard.update_agent(
        "agent_7",
        {
            "score": result.score,
            "in_kill_zone": payload.get("in_kill_zone"),
            "kill_zone_name": payload.get("kill_zone_name"),
            "risk_modifier": result.risk_modifier,
            "trading_allowed": payload.get("trading_allowed"),
            "vp_poc": volume_profile.get("poc"),
            "vp_vah": volume_profile.get("vah"),
            "vp_val": volume_profile.get("val"),
            "session_name": payload.get("session_name"),
            "reason": result.reason,
        },
    )
    await blackboard.update_market({"session": payload.get("session_name")})
    return result


async def _publish_agent_result(blackboard, result: AgentResult) -> None:
    await blackboard.write_agent_result(result.agent_id, result, trigger_orchestrator=False)
    if result.agent_id in blackboard.get_all().get("agents", {}):
        await blackboard.update_agent(
            result.agent_id,
            {
                "score": result.score,
                "direction": result.direction,
                "reason": result.reason,
                "hard_filter_pass": result.hard_filter_pass,
                "veto": result.veto,
            },
        )


def _collect_agent_results(blackboard) -> list[AgentResult]:
    raw = blackboard.get_all().get("agent_results", {}) or {}
    return [
        result
        for agent_id in ("agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7")
        if isinstance((result := raw.get(agent_id)), AgentResult)
    ]



def _agent_event_payload(result: AgentResult) -> dict[str, Any]:
    return {
        "score": result.score,
        "hard_filter_pass": result.hard_filter_pass,
        "direction": result.direction,
        "veto": result.veto,
        "reason": result.reason,
        "payload": result.payload,
    }


def _alignment_diagnostic_payload(candle: dict[str, Any], blackboard, diagnostics: set[str]) -> dict[str, Any] | None:
    if "poi_ote" not in diagnostics:
        return None
    from replay.alignment_diagnostic import build_poi_ote_alignment_diagnostic

    return build_poi_ote_alignment_diagnostic(candle=candle, blackboard=blackboard)


def _runner_agent_id(runner: ReplayAgentRunner) -> str:
    explicit = getattr(runner, "replay_agent_id", None)
    if explicit:
        return str(explicit)
    name = getattr(runner, "__name__", runner.__class__.__name__)
    if name.startswith("run_replay_"):
        return name.replace("run_replay_", "", 1)
    return name


def _safe_read(blackboard, path: str) -> Any:
    try:
        return blackboard.read_sync(path)
    except KeyError:
        return None


def _ensure_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _p1_decision_payload(bundle, decision_result, validation_errors: list[str]) -> dict[str, Any]:
    decision_dict = decision_result.to_dict() if hasattr(decision_result, "to_dict") else dict(decision_result.__dict__)
    bundle_dict = bundle_to_json_dict(bundle)
    score_breakdown = decision_dict.get("score_breakdown") or {}
    classification = score_breakdown.get("setup_classification") or {}
    classification_evidence = classification.get("evidence") or {}
    setup_signal_inventory = classification_evidence.get("signals", {})
    setup_candidates = classification_evidence.get("candidates", [])
    signal_payload = setup_signal_inventory if isinstance(setup_signal_inventory, dict) else {}
    best_setup_candidate = _best_candidate(setup_candidates)
    enter_eligibility = score_breakdown.get("enter_eligibility") or {}
    risk_plan = decision_dict.get("risk_plan") or {}
    setup_type = score_breakdown.get("setup_type") or classification.get("setup_type") or "UNKNOWN"
    decision_value = decision_dict.get("decision", "REJECT")
    setup_grade = decision_dict.get("setup_grade", "D")
    bundle_poi = bundle_dict.get("poi") if isinstance(bundle_dict.get("poi"), dict) else {}
    bundle_raw = bundle_dict.get("raw") if isinstance(bundle_dict.get("raw"), dict) else {}
    poi_micro_payload = {}
    if isinstance(bundle_poi.get("poi_micro_synergy"), dict):
        poi_micro_payload = bundle_poi.get("poi_micro_synergy") or {}
    elif isinstance(bundle_raw.get("poi_micro_synergy"), dict):
        poi_micro_payload = bundle_raw.get("poi_micro_synergy") or {}
    poi_micro_synergy = bool(
        poi_micro_payload.get("synergy")
        or bundle_poi.get("poi_micro_synergy_enabled")
        or signal_payload.get("poi_micro_synergy")
    )
    poi_rejection_payload = {}
    if isinstance(bundle_poi.get("poi_rejection"), dict):
        poi_rejection_payload = bundle_poi.get("poi_rejection") or {}
    elif isinstance(signal_payload.get("poi_rejection"), dict):
        poi_rejection_payload = signal_payload.get("poi_rejection") or {}

    payload = {
        "decision": decision_value,
        "setup_grade": setup_grade,
        "side": bundle_dict.get("side"),
        "confidence_score": decision_dict.get("confidence_score", 0.0),
        "score_before_veto": decision_dict.get("score_before_veto", 0.0),
        "score_after_veto": decision_dict.get("score_after_veto", 0.0),
        "hard_veto": decision_dict.get("hard_veto", False),
        "veto_code": decision_dict.get("veto_code"),
        "blocked_stage": decision_dict.get("blocked_stage"),
        "replay_invalid": decision_dict.get("replay_invalid", False),
        "readiness_state": decision_dict.get("readiness_state", "UNAVAILABLE"),
        "readiness_reason": decision_dict.get("readiness_reason", "READINESS_UNAVAILABLE"),
        "readiness_by_section": decision_dict.get("readiness_by_section", {}),
        "missing_evidence": decision_dict.get("missing_evidence", []),
        "soft_issues": decision_dict.get("soft_issues", []),
        "risk_plan": decision_dict.get("risk_plan", {}),
        "risk_multiplier": decision_dict.get("risk_multiplier", 0.0),
        "required_execution_mode": decision_dict.get("required_execution_mode", "shadow_only"),
        # P2-E Phase 7A: setup taxonomy fields
        "setup_type": setup_type,
        "setup_classification": classification,
        "setup_family": classification.get("family", "UNKNOWN"),
        "setup_classification_reason": classification.get("reason", "UNKNOWN"),
        "setup_classification_confidence": classification.get("confidence", 0.0),
        # ── end Phase 7A ──
        # P2-E Phase 7B: enter eligibility fields
        "enter_eligible": bool(decision_dict.get("enter_eligible", False)),
        "enter_eligibility_reason": decision_dict.get("enter_eligibility_reason") or enter_eligibility.get("reason", "UNKNOWN"),
        "enter_eligibility_blockers": decision_dict.get("enter_eligibility_blockers") or enter_eligibility.get("blockers", []),
        "enter_eligibility_checks": decision_dict.get("enter_eligibility_checks") or enter_eligibility.get("checks", {}),
        "risk_preview": decision_dict.get("risk_preview") or enter_eligibility.get("risk_preview", {}),
        # ── end Phase 7B ──
        # P2-E Phase 7C: risk multiplier mapping
        "grade_risk_multiplier": (risk_plan.get("metadata") or {}).get("grade_risk_multiplier"),
        "effective_risk_pct": (risk_plan.get("metadata") or {}).get("effective_risk_pct"),
        "setup_max_risk_multiplier": (risk_plan.get("metadata") or {}).get("setup_max_risk_multiplier"),
        "risk_allowed": risk_plan.get("allowed", False),
        "risk_reason": risk_plan.get("reason", "UNKNOWN"),
        # ── end Phase 7C ──
        # P2-E Phase 7D: readiness coherence
        "readiness_coherence": score_breakdown.get("readiness_coherence", {}),
        "readiness_non_ready_sections": (score_breakdown.get("readiness_coherence") or {}).get("non_ready_sections", {}),
        "readiness_missing_ready_blockers": (score_breakdown.get("readiness_coherence") or {}).get("missing_ready_blockers", []),
        # P2-E Phase8: setup signals and near-miss diagnostics
        "setup_signal_inventory": signal_payload,
        "setup_candidates": setup_candidates if isinstance(setup_candidates, list) else [],
        "best_setup_candidate": best_setup_candidate,
        # P2-E Phase15: distinguish macro liquidity sweep from micro setup sweep evidence
        "micro_sweep_confirmed": signal_payload.get("micro_sweep_confirmed"),
        "setup_sweep_evidence": signal_payload.get("setup_sweep_evidence"),
        "setup_sweep_evidence_source": signal_payload.get("setup_sweep_evidence_source"),
        # P2-E Phase16: reconciled liquidity evidence audit
        "liquidity_evidence_source": signal_payload.get("liquidity_evidence_source"),
        "micro_liquidity_confirmed": signal_payload.get("micro_liquidity_confirmed"),
        "liquidity_reconciled": signal_payload.get("liquidity_reconciled"),
        "liquidity_reconciliation_reason": signal_payload.get("liquidity_reconciliation_reason"),
        "liquidity_reconciliation_blockers": signal_payload.get("liquidity_reconciliation_blockers", []),
        "liquidity_reconciliation_payload": bundle_dict.get("liquidity", {}),
        "near_miss_rank_score": _near_miss_rank_score(
            decision=decision_value,
            setup_type=setup_type,
            setup_grade=setup_grade,
            signals=setup_signal_inventory,
            candidates=setup_candidates,
        ),
        "near_miss_missing_signals": _near_miss_missing_signals(setup_signal_inventory, best_setup_candidate),
        "near_miss_present_signals": _near_miss_present_signals(setup_signal_inventory),
        # P2-E Phase10: POI contract coherence diagnostics
        "poi_contract_status": signal_payload.get("poi_contract_status"),
        "poi_contract_reason": signal_payload.get("poi_contract_reason"),
        "poi_contract_contradictions": signal_payload.get("poi_contract_contradictions", []),
        "poi_quality_breakdown": signal_payload.get("poi_quality_breakdown", {}),
        "poi_score_source": signal_payload.get("poi_score_source"),
        "poi_score_is_computed": signal_payload.get("poi_score_is_computed"),
        # P2-E Phase13: Agent2 POI rejection decomposition
        "poi_rejection_code": (
            bundle_poi.get("poi_rejection_code")
            or signal_payload.get("poi_rejection_code")
            or poi_rejection_payload.get("code")
        ),
        "poi_rejection_source": (
            bundle_poi.get("poi_rejection_source")
            or signal_payload.get("poi_rejection_source")
            or poi_rejection_payload.get("source")
        ),
        "poi_rejection_severity": (
            bundle_poi.get("poi_rejection_severity")
            or signal_payload.get("poi_rejection_severity")
            or poi_rejection_payload.get("severity")
        ),
        "poi_rejection_fatal": bool(
            bundle_poi.get("poi_rejection_fatal")
            or signal_payload.get("poi_rejection_fatal")
            or poi_rejection_payload.get("fatal")
        ),
        "poi_rejection_recoverable": bool(
            bundle_poi.get("poi_rejection_recoverable")
            or signal_payload.get("poi_rejection_recoverable")
            or poi_rejection_payload.get("recoverable")
        ),
        "poi_rejection_reason": (
            bundle_poi.get("poi_rejection_reason")
            or signal_payload.get("poi_rejection_reason")
            or poi_rejection_payload.get("reason")
        ),
        "poi_rejection": poi_rejection_payload,
        # P2-E Phase12: POI-Micro synergy diagnostics
        "poi_micro_synergy": poi_micro_synergy,
        "poi_micro_synergy_status": (
            poi_micro_payload.get("status")
            or bundle_poi.get("poi_micro_synergy_status")
            or signal_payload.get("poi_micro_synergy_status")
        ),
        "poi_micro_reason": (
            poi_micro_payload.get("reason")
            or bundle_poi.get("poi_micro_reason")
            or signal_payload.get("poi_micro_reason")
        ),
        "micro_confirmed": (
            poi_micro_payload.get("micro_confirmed")
            if "micro_confirmed" in poi_micro_payload
            else signal_payload.get("micro_confirmed")
        ),
        "micro_inside_poi": (
            poi_micro_payload.get("micro_inside_poi")
            if "micro_inside_poi" in poi_micro_payload
            else signal_payload.get("micro_inside_poi")
        ),
        "micro_outside_poi": (
            poi_micro_payload.get("micro_outside_poi")
            if "micro_outside_poi" in poi_micro_payload
            else signal_payload.get("micro_outside_poi")
        ),
        "effective_poi_status": (
            poi_micro_payload.get("effective_poi_status")
            or bundle_poi.get("effective_poi_status")
            or signal_payload.get("effective_poi_status")
        ),
        "poi_micro_upgraded_poi_status": (
            poi_micro_payload.get("upgraded_poi_status")
            or bundle_poi.get("poi_micro_upgraded_poi_status")
        ),
        "poi_micro_remaining_blockers": (
            poi_micro_payload.get("remaining_blockers")
            or bundle_poi.get("poi_micro_remaining_blockers")
            or []
        ),
        "poi_micro_synergy_payload": poi_micro_payload,
        "p1_evidence_bundle": bundle_dict,
        "p1_evidence_validation_errors": list(validation_errors),
    }
    gate_result = evaluate_readiness_risk_gate(payload)
    payload.update(
        {
            "gate_primary_blocker": gate_result.primary_blocker,
            "gate_blockers": gate_result.blockers,
            "gate_decomposition": gate_result.to_dict(),
            "setup_tradable": gate_result.setup_tradable,
            "has_setup_candidate": gate_result.has_setup_candidate,
            "has_tradable_setup_candidate": gate_result.has_tradable_setup_candidate,
            "risk_allowed": gate_result.risk_allowed,
        }
    )

    # ── P1 Kasper Brain Core: scenario-driven decision enrichment ──────
    _bundle_ctx = bundle_dict.get("context") if isinstance(bundle_dict.get("context"), dict) else None
    _bundle_poi = bundle_dict.get("poi") if isinstance(bundle_dict.get("poi"), dict) else None
    _bundle_liq = bundle_dict.get("liquidity") if isinstance(bundle_dict.get("liquidity"), dict) else None
    _bundle_timing = bundle_dict.get("timing") if isinstance(bundle_dict.get("timing"), dict) else None
    _bundle_micro = bundle_dict.get("micro") if isinstance(bundle_dict.get("micro"), dict) else None
    _bundle_news = bundle_dict.get("news") if isinstance(bundle_dict.get("news"), dict) else None
    _bundle_sess = bundle_dict.get("session") if isinstance(bundle_dict.get("session"), dict) else None

    try:
        kasper_bundle = build_kasper_evidence_bundle(
            context=_bundle_ctx,
            poi=_bundle_poi,
            liquidity=_bundle_liq,
            timing=_bundle_timing,
            micro=_bundle_micro,
            news=_bundle_news,
            session=_bundle_sess,
            symbol=str(bundle_dict.get("symbol") or "XAUUSD"),
            timestamp=payload.get("timestamp"),
            extra_sweep_evidence=True,  # Phase16: use reconciled sweep/micro evidence
        )
        kasper_result = evaluate_kasper_scenario(kasper_bundle)
        payload.update({
            "scenario_id": kasper_result.scenario_id,
            "scenario_key": kasper_result.scenario_key,
            "decision_id": kasper_result.decision_id,
            "scenario_type": kasper_result.scenario_type,
            "market_story": kasper_result.story,
            "sequence_pass_fail": kasper_result.sequence,
            "missing_confluence": kasper_result.missing_confluence,
            "entry_reason": kasper_result.entry_reason,
            "invalidation_reason": kasper_result.invalidation_reason,
            "target_reason": kasper_result.target_reason,
            "kasper_grade": kasper_result.grade,
            "kasper_score": kasper_result.score,
            "kasper_side": kasper_result.side,
            "kasper_error": kasper_result.kasper_error,
            "kasper_rr_estimate": kasper_bundle.agent5.micro_confirmation.rr_estimate,
            "kasper_decision_recommendation": kasper_result.decision_recommendation,
            "hard_veto_reason": kasper_result.blocking_reason,
        })
    except Exception:
        # Kasper evaluation is non-blocking — if it fails, the PDE decision still stands
        payload.update({
            "scenario_id": None,
            "scenario_key": None,
            "decision_id": None,
            "scenario_type": "kasper_error",
            "market_story": "Kasper scenario engine error — fallback to PDE score-only decision.",
            "sequence_pass_fail": {},
            "missing_confluence": "Kasper engine exception",
            "entry_reason": None,
            "invalidation_reason": None,
            "target_reason": None,
            "kasper_grade": "D",
            "kasper_score": 0.0,
            "kasper_side": "NONE",
            "kasper_error": "Kasper engine exception",
            "kasper_rr_estimate": None,
            "kasper_decision_recommendation": "REJECT",
            "hard_veto_reason": None,
        })

    # ── P2.1 PDE/Kasper Alignment Bridge ───────────────────────────────
    # When KasperScenarioEngine gives ENTER_ELIGIBLE (A_PLUS/A/B, 8/8 gates,
    # RR valid, all structural gates passed) but the legacy PDE scorecard
    # says WATCH_ONLY or WAIT_FOR_TRIGGER, promote to ENTER_REDUCED.
    #
    # The promotion does NOT bypass hard veto, news, session, risk guards,
    # or duplicate gates — those are enforced downstream by the trade manager.
    # It only overrides the legacy scorecard threshold when Kasper authority
    # has already validated the full scenario.
    _kasper_rec = str(payload.get("kasper_decision_recommendation") or "").upper()
    _kasper_grade = str(payload.get("kasper_grade") or "D").upper()
    _pde_decision = str(payload.get("decision") or "REJECT").upper()
    _hard_veto = bool(payload.get("hard_veto", False))
    _replay_invalid = bool(payload.get("replay_invalid", False))
    _kasper_seq = payload.get("sequence_pass_fail") if isinstance(payload.get("sequence_pass_fail"), dict) else {}
    _risk_precheck_pass = _kasper_seq.get("risk_precheck") == "PASS"

    _kasper_enters = _kasper_rec == "ENTER_ELIGIBLE"
    _pde_blocking = _pde_decision in {"WATCH_ONLY", "WAIT_FOR_TRIGGER", "WAIT_FOR_BETTER_PRICE"}
    _pde_reject = _pde_decision == "REJECT"
    _grade_executable = _kasper_grade in {"A_PLUS", "A", "B"}

    # P2.3: PDE vetos that Kasper's gates already validate are overridable
    # by Kasper authority. These are micro/POI/trigger domain vetos, not
    # news/session/risk guards (which Kasper's hard_veto already covers).
    _pde_veto_code = str(payload.get("veto_code") or "").upper()
    _kasper_overridable_vetos = {
        "TRIGGER_OUTSIDE_POI", "POI_MISSING", "POI_INVALID_SHAPE",
        "POI_QUALITY_WATCH_C", "POI_QUALITY_WATCH_D",
        "MICRO_TRIGGER_TYPE_MISSING", "MICRO_PAYLOAD_MISSING",
        "MICRO_CONFIRMATION_WATCH_C", "MICRO_CONFIRMATION_WATCH_D",
        "DISPLACEMENT_MISSING", "RECLAIM_OR_ACCEPTANCE_MISSING",
        "RETEST_MISSING", "MICRO_MISSING",
    }
    _pde_veto_overridable = _pde_veto_code in _kasper_overridable_vetos

    # P2.3: Promote when Kasper says ENTER_ELIGIBLE:
    #   A) PDE says WATCH_ONLY/WAIT and no hard veto → promote (original P2.1 logic)
    #   B) PDE says REJECT due to overridable veto (POI/micro/trigger) AND
    #      no news/session/risk veto → Kasper authority overrides
    _should_promote = (
        _kasper_enters and _grade_executable and _risk_precheck_pass
        and not _replay_invalid
        and (
            (_pde_blocking and not _hard_veto)
            or (_pde_reject and _hard_veto and _pde_veto_overridable)
        )
    )

    if _should_promote:
        # ── Promote PDE decision ──────────────────────────────────
        payload["decision"] = "ENTER_REDUCED"
        payload["kasper_pde_alignment_status"] = "PROMOTED"
        payload["kasper_pde_alignment_reason"] = (
            f"Kasper {_kasper_grade} ENTER_ELIGIBLE with valid RR overrides "
            f"legacy PDE {_pde_decision} — scorecard threshold bypassed by Kasper authority"
        )
        payload["pde_blocking_reason"] = f"Legacy PDE said {_pde_decision} (scorecard threshold)"
        payload["trade_open_source"] = "KASPER_AUTHORITY"
        # P2.3: Update setup_grade to Kasper grade (was legacy PDE grade D)
        payload["setup_grade"] = _kasper_grade

        # ── Re-allocate risk for ENTER_REDUCED ────────────────────
        try:
            _kasper_rr = float(payload.get("kasper_score", 0) or 0)
            _setup_grade_str = payload.get("setup_grade", "B")
            try:
                _grade_enum = SetupGrade(_setup_grade_str)
            except ValueError:
                _grade_enum = SetupGrade.B
            # Use Kasper grade for risk, not legacy PDE grade
            if _kasper_grade == "A_PLUS":
                _grade_enum = SetupGrade.A_PLUS
            elif _kasper_grade == "A":
                _grade_enum = SetupGrade.A
            elif _kasper_grade == "B":
                _grade_enum = SetupGrade.B

            _new_risk = allocate_risk(
                action=DecisionAction.ENTER_REDUCED,
                grade=_grade_enum,
                evidence=bundle,
                capital=100.0,
                enter_eligible=True,
            )
            # P2.3: If taxonomy classified as POI_REACTION (risk=0%),
            # Kasper already validated this is SWEEP_REVERSAL (all 8 gates).
            # Re-allocate with correct setup type.
            _rp_dict = _new_risk.to_dict()
            _rp_meta = _rp_dict.get("metadata") or {}
            if _rp_meta.get("setup_type") == "POI_REACTION" and not _new_risk.allowed:
                from dataclasses import replace as _replace
                _fixed_bundle = _replace(bundle, setup_type=SetupType.SWEEP_REVERSAL)
                _new_risk = allocate_risk(
                    action=DecisionAction.ENTER_REDUCED,
                    grade=_grade_enum,
                    evidence=_fixed_bundle,
                    capital=100.0,
                    enter_eligible=True,
                )
                _rp_dict = _new_risk.to_dict()
            payload["risk_plan"] = _rp_dict
            payload["risk_multiplier"] = _new_risk.risk_multiplier
            payload["risk_allowed"] = _new_risk.allowed
            payload["risk_reason"] = _new_risk.reason
            payload["enter_eligible"] = True
            payload["enter_eligibility_reason"] = "KASPER_AUTHORITY_PROMOTED"
            payload["enter_eligibility_blockers"] = []
            # Grade risk mapping
            payload["grade_risk_multiplier"] = _new_risk.metadata.get("grade_risk_multiplier")
            payload["effective_risk_pct"] = _new_risk.metadata.get("effective_risk_pct")
            payload["risk_grade_pct"] = _new_risk.metadata.get("grade_risk_multiplier")
        except Exception:
            # Risk allocation failed — don't promote
            payload["decision"] = _pde_decision  # revert
            payload["kasper_pde_alignment_status"] = "RISK_ALLOCATION_FAILED"
            payload["kasper_pde_alignment_reason"] = (
                f"Kasper ENTER_ELIGIBLE but risk allocation failed — kept PDE {_pde_decision}"
            )
    elif _kasper_enters and _kasper_rec == "ENTER_ELIGIBLE":
        payload["kasper_pde_alignment_status"] = "BLOCKED_BY_VETO"
        payload["kasper_pde_alignment_reason"] = (
            f"Kasper ENTER_ELIGIBLE but hard_veto={_hard_veto} replay_invalid={_replay_invalid}"
        )
    elif _kasper_enters and not _grade_executable:
        payload["kasper_pde_alignment_status"] = "GRADE_NOT_EXECUTABLE"
        payload["kasper_pde_alignment_reason"] = f"Kasper grade {_kasper_grade} is not executable"
    elif _kasper_enters and not _risk_precheck_pass:
        payload["kasper_pde_alignment_status"] = "RISK_PRECHECK_FAILED"
        payload["kasper_pde_alignment_reason"] = "Kasper ENTER_ELIGIBLE but risk_precheck not PASS"
    else:
        payload["kasper_pde_alignment_status"] = "NO_ALIGNMENT_NEEDED"
        payload["kasper_pde_alignment_reason"] = (
            f"Kasper={_kasper_rec}, PDE={_pde_decision} — no promotion needed"
        )
    # ── end P2.1 alignment bridge ──────────────────────────────────────

    return payload


def _best_candidate(candidates):
    if not isinstance(candidates, list) or not candidates:
        return {}
    return max(
        (candidate for candidate in candidates if isinstance(candidate, dict)),
        key=lambda candidate: _float(candidate.get("confidence")),
        default={},
    )


def _near_miss_rank_score(
    *,
    decision: Any,
    setup_type: Any,
    setup_grade: Any,
    signals: Any,
    candidates: Any,
) -> float:
    score = 0.0
    if str(setup_type or "UNKNOWN") in {"UNKNOWN", "POI_REACTION"}:
        score += 10.0
    if str(decision or "UNKNOWN") in {"WATCH_ONLY", "WAIT_FOR_TRIGGER"}:
        score += 10.0
    grade = str(setup_grade or "D")
    if grade == "B":
        score += 8.0
    elif grade == "C":
        score += 5.0
    signal_payload = signals if isinstance(signals, dict) else {}
    if signal_payload.get("poi_present"):
        score += 5.0
    if signal_payload.get("trend_aligned_poi") or signal_payload.get("counter_trend_poi"):
        score += 5.0
    if signal_payload.get("micro_waiting") or signal_payload.get("micro_partial"):
        score += 5.0
    if signal_payload.get("liquidity_waiting") or signal_payload.get("sweep_detected"):
        score += 5.0
    if signal_payload.get("in_ote") or signal_payload.get("timing_ready"):
        score += 5.0
    candidate_items = candidates if isinstance(candidates, list) else []
    confidences = [
        _float(candidate.get("confidence"))
        for candidate in candidate_items
        if isinstance(candidate, dict)
    ]
    if confidences:
        score += max(confidences) * 10.0
    return round(score, 4)


def _near_miss_present_signals(signals: Any) -> list[str]:
    if not isinstance(signals, dict):
        return []
    values = signals.get("present_signals")
    return [str(item) for item in values] if isinstance(values, list) else []


def _near_miss_missing_signals(signals: Any, best_candidate: Any) -> list[str]:
    if isinstance(best_candidate, dict) and isinstance(best_candidate.get("missing"), list):
        return [str(item) for item in best_candidate.get("missing") or []]
    if isinstance(signals, dict) and isinstance(signals.get("missing_core"), list):
        return [str(item) for item in signals.get("missing_core") or []]
    return []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
