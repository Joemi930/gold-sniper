from __future__ import annotations

from typing import Any

from gold_sniper.strategy.contracts import BlockedStage, EvidenceBundle, HardVetoResult, SetupType, VetoSeverity


def evaluate_hard_veto(evidence: EvidenceBundle | dict[str, Any] | None) -> HardVetoResult:
    bundle = _coerce_bundle(evidence)

    data_veto = _data_invalid(bundle)
    if data_veto:
        return data_veto

    for rule in (
        _news_high_impact,
        _post_news_stealth,
        _session_tokyo_asia,
        _poi_deep_mitigation,
        _poi_opposes_context,
        _reversal_without_sweep,
        _max_daily_loss,
        _max_weekly_loss,
        _max_drawdown,
        _friday_halt,
        _no_lookahead_violation,
        _trigger_outside_poi,
    ):
        result = rule(bundle)
        if result.hard_veto:
            return result

    return HardVetoResult()


def _coerce_bundle(evidence: EvidenceBundle | dict[str, Any] | None) -> EvidenceBundle:
    if isinstance(evidence, EvidenceBundle):
        return evidence
    if isinstance(evidence, dict):
        return EvidenceBundle.from_dict(evidence)
    return EvidenceBundle()


def _veto(code: str, reason: str, stage: BlockedStage, *, replay_invalid: bool = False) -> HardVetoResult:
    return HardVetoResult(
        hard_veto=not replay_invalid,
        veto_code=code,
        veto_reason=reason,
        blocked_stage=stage,
        severity=VetoSeverity.REPLAY_INVALID if replay_invalid else VetoSeverity.HARD,
        replay_invalid=replay_invalid,
    )


def _data_invalid(bundle: EvidenceBundle) -> HardVetoResult | None:
    if bundle.raw.get("lookahead_violation") is True:
        return _veto("NO_LOOKAHEAD_VIOLATION", "Future data was used by replay evidence.", BlockedStage.DATA)
    if bundle.raw.get("replay_invalid") is True or bundle.raw.get("data_invalid") is True:
        return _veto("REPLAY_DATA_INVALID", "Replay data is missing, stale, or inconsistent.", BlockedStage.DATA, replay_invalid=True)
    return None


def _news_high_impact(bundle: EvidenceBundle) -> HardVetoResult:
    news = bundle.news
    reason = str(news.get("reason") or news.get("news_reason") or "").upper()
    if news.get("high_impact_window") is True or news.get("news_blocked") is True or "NEWS_BLACKOUT_HIGH" in reason:
        return _veto("NEWS_HIGH_IMPACT_WINDOW", "High impact news blackout window.", BlockedStage.NEWS)
    return HardVetoResult()


def _post_news_stealth(bundle: EvidenceBundle) -> HardVetoResult:
    news = bundle.news
    reason = str(news.get("reason") or news.get("news_reason") or "").upper()
    if news.get("post_news_stealth") is True or "POST_NEWS_STEALTH" in reason:
        return _veto("NEWS_POST_EVENT_STEALTH", "Post-news stealth normalization not complete.", BlockedStage.NEWS)
    return HardVetoResult()


def _session_tokyo_asia(bundle: EvidenceBundle) -> HardVetoResult:
    session = str(bundle.session.get("session") or bundle.session.get("session_label") or "").upper()
    if bundle.session.get("is_hard_block") is True:
        return _veto("SESSION_EXPLICIT_HARD_BLOCK", "Session module explicitly blocked trading.", BlockedStage.SESSION)
    if session in {"TOKYO", "ASIA", "ASIAN", "TOKYO_ASIA"}:
        return _veto("SESSION_TOKYO_ASIA_BLOCK", "Tokyo/Asia session is blocked for P1.", BlockedStage.SESSION)
    return HardVetoResult()


def _poi_deep_mitigation(bundle: EvidenceBundle) -> HardVetoResult:
    poi = bundle.poi
    mitigation = _float(poi.get("mitigation_pct") or poi.get("deepest_penetration_pct") or 0.0)
    if mitigation > 50.0:
        return _veto("POI_DEEP_MITIGATION_GT_50", "POI mitigation/deep penetration exceeds 50%.", BlockedStage.POI)
    return HardVetoResult()


def _poi_opposes_context(bundle: EvidenceBundle) -> HardVetoResult:
    poi = bundle.poi
    if poi.get("opposes_htf_dol") is True or poi.get("aligned_with_context") is False:
        return _veto("POI_OPPOSES_HTF_DOL", "POI direction opposes HTF bias or draw-on-liquidity.", BlockedStage.POI)
    return HardVetoResult()


def _reversal_without_sweep(bundle: EvidenceBundle) -> HardVetoResult:
    if bundle.setup_type != SetupType.REVERSAL_STRICT:
        return HardVetoResult()
    sweep = bool(bundle.liquidity.get("sweep_detected") or bundle.liquidity.get("sweep_rejected"))
    if not sweep:
        return _veto("REVERSAL_WITHOUT_SWEEP", "Reversal setup requires sweep/rejection evidence.", BlockedStage.LIQUIDITY)
    return HardVetoResult()


def _max_daily_loss(bundle: EvidenceBundle) -> HardVetoResult:
    if bundle.risk.get("max_daily_loss_hit") is True:
        return _veto("MAX_DAILY_LOSS_GUARD", "Max daily loss guard hit.", BlockedStage.RISK)
    return HardVetoResult()


def _max_weekly_loss(bundle: EvidenceBundle) -> HardVetoResult:
    if bundle.risk.get("max_weekly_loss_hit") is True:
        return _veto("MAX_WEEKLY_LOSS_GUARD", "Max weekly loss guard hit.", BlockedStage.RISK)
    return HardVetoResult()


def _max_drawdown(bundle: EvidenceBundle) -> HardVetoResult:
    if bundle.risk.get("max_drawdown_hit") is True:
        return _veto("MAX_DRAWDOWN_GUARD", "Max drawdown guard hit.", BlockedStage.RISK)
    return HardVetoResult()


def _friday_halt(bundle: EvidenceBundle) -> HardVetoResult:
    if bundle.session.get("friday_halt") is True:
        return _veto("FRIDAY_HALT", "Friday halt active.", BlockedStage.SESSION)
    return HardVetoResult()


def _no_lookahead_violation(bundle: EvidenceBundle) -> HardVetoResult:
    if bundle.raw.get("future_candles_used") is True:
        return _veto("NO_LOOKAHEAD_VIOLATION", "Future candles used.", BlockedStage.DATA)
    return HardVetoResult()


def _trigger_outside_poi(bundle: EvidenceBundle) -> HardVetoResult:
    if bundle.micro.get("outside_poi") is True or bundle.micro.get("trigger_outside_poi") is True:
        return _veto("TRIGGER_OUTSIDE_POI", "Micro trigger is outside the POI zone.", BlockedStage.MICRO)
    return HardVetoResult()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
