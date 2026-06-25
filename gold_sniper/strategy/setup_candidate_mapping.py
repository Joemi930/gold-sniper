from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from gold_sniper.strategy.contracts import SetupType
from gold_sniper.strategy.setup_signal_inventory import SetupSignalInventory


@dataclass(frozen=True)
class SetupCandidate:
    candidate_type: SetupType
    confidence: float
    reason: str
    present: list[str]
    missing: list[str]
    is_strict_candidate: bool = False
    is_light_candidate: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidate_type"] = self.candidate_type.value
        return payload


def map_signals_to_setup_candidates(signals: SetupSignalInventory) -> list[SetupCandidate]:
    candidates: list[SetupCandidate] = []

    if signals.missing_core:
        return candidates

    if (
        signals.poi_micro_synergy
        and signals.sweep_detected
        and signals.micro_ready
    ):
        strict = signals.liquidity_ready
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.SWEEP_REVERSAL,
                confidence=0.90 if strict else 0.75,
                reason="POI_MICRO_SYNERGY_WITH_SWEEP_REVERSAL",
                present=_present_subset(
                    signals,
                    "POI_MICRO_SYNERGY",
                    "MICRO_CONFIRMED",
                    "MICRO_INSIDE_POI",
                    "SWEEP_DETECTED",
                    "EFFECTIVE_POI_READY",
                    "LIQUIDITY_READY",
                ),
                missing=_missing_subset(
                    signals,
                    "POI_MICRO_SYNERGY",
                    "MICRO_CONFIRMED",
                    "SWEEP_DETECTED",
                    "LIQUIDITY_READY",
                ),
                is_strict_candidate=strict,
                is_light_candidate=not strict,
            )
        )

    if (
        signals.poi_micro_synergy
        and signals.setup_sweep_evidence
        and not signals.sweep_detected
        and signals.micro_ready
    ):
        strict = signals.liquidity_ready
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.SWEEP_REVERSAL,
                confidence=0.82 if strict else 0.68,
                reason="POI_MICRO_SYNERGY_WITH_MICRO_SWEEP_EVIDENCE",
                present=_present_subset(
                    signals,
                    "POI_MICRO_SYNERGY",
                    "MICRO_CONFIRMED",
                    "MICRO_INSIDE_POI",
                    "SETUP_SWEEP_EVIDENCE",
                    "MICRO_SWEEP_CONFIRMED",
                    "EFFECTIVE_POI_READY",
                    "LIQUIDITY_READY",
                ),
                missing=_missing_subset(
                    signals,
                    "POI_MICRO_SYNERGY",
                    "MICRO_CONFIRMED",
                    "MICRO_INSIDE_POI",
                    "SETUP_SWEEP_EVIDENCE",
                    "SWEEP_DETECTED",
                    "LIQUIDITY_READY",
                ),
                is_strict_candidate=strict,
                is_light_candidate=not strict,
            )
        )

    if signals.poi_present and signals.sweep_detected and (
        signals.reclaim_confirmed or signals.micro_partial
    ):
        strict = signals.micro_ready and signals.liquidity_ready
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.SWEEP_REVERSAL,
                confidence=0.85 if strict else 0.65,
                reason="POI_PLUS_SWEEP_PLUS_RECLAIM_OR_PARTIAL_MICRO",
                present=_present_subset(
                    signals,
                    "POI_PRESENT",
                    "SWEEP_DETECTED",
                    "RECLAIM_CONFIRMED",
                    "MICRO_PARTIAL",
                    "MICRO_READY",
                    "LIQUIDITY_READY",
                ),
                missing=_missing_subset(
                    signals,
                    "POI_PRESENT",
                    "SWEEP_DETECTED",
                    "RECLAIM_CONFIRMED",
                    "MICRO_READY",
                    "LIQUIDITY_READY",
                ),
                is_strict_candidate=strict,
                is_light_candidate=not strict,
            )
        )

    if signals.counter_trend_poi and signals.poi_present and signals.micro_ready and signals.liquidity_ready:
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.REVERSAL_STRICT,
                confidence=0.82,
                reason="COUNTER_TREND_POI_WITH_LIQUIDITY_AND_MICRO",
                present=_present_subset(
                    signals,
                    "COUNTER_TREND_POI",
                    "POI_PRESENT",
                    "LIQUIDITY_READY",
                    "MICRO_READY",
                ),
                missing=_missing_subset(
                    signals,
                    "COUNTER_TREND_POI",
                    "POI_PRESENT",
                    "LIQUIDITY_READY",
                    "MICRO_READY",
                ),
                is_strict_candidate=True,
            )
        )

    if signals.trend_aligned_poi and signals.poi_present and signals.micro_ready and signals.liquidity_ready and signals.timing_ready:
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.CONTINUATION_STRICT,
                confidence=0.85,
                reason="TREND_ALIGNED_POI_LIQUIDITY_MICRO_TIMING_READY",
                present=_present_subset(
                    signals,
                    "TREND_ALIGNED_POI",
                    "POI_PRESENT",
                    "LIQUIDITY_READY",
                    "MICRO_READY",
                    "TIMING_READY",
                ),
                missing=_missing_subset(
                    signals,
                    "TREND_ALIGNED_POI",
                    "POI_PRESENT",
                    "LIQUIDITY_READY",
                    "MICRO_READY",
                    "TIMING_READY",
                ),
                is_strict_candidate=True,
            )
        )

    if (
        signals.poi_micro_synergy
        and signals.trend_aligned_poi
        and signals.micro_ready
        and signals.timing_ready
    ):
        strict = signals.liquidity_ready
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.CONTINUATION_STRICT,
                confidence=0.88,
                reason="POI_MICRO_SYNERGY_TREND_ALIGNED_CONTINUATION",
                present=_present_subset(
                    signals,
                    "POI_MICRO_SYNERGY",
                    "MICRO_CONFIRMED",
                    "MICRO_INSIDE_POI",
                    "TREND_ALIGNED_POI",
                    "EFFECTIVE_POI_READY",
                    "TIMING_READY",
                    "LIQUIDITY_READY",
                ),
                missing=_missing_subset(
                    signals,
                    "POI_MICRO_SYNERGY",
                    "MICRO_CONFIRMED",
                    "TREND_ALIGNED_POI",
                    "TIMING_READY",
                    "LIQUIDITY_READY",
                ),
                is_strict_candidate=strict,
                is_light_candidate=not strict,
            )
        )

    if signals.trend_aligned_poi and signals.poi_present and (
        signals.micro_waiting or signals.micro_partial or signals.liquidity_waiting
    ):
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.CONTINUATION_LIGHT,
                confidence=0.60,
                reason="TREND_ALIGNED_POI_WITH_PARTIAL_CONFIRMATION",
                present=_present_subset(
                    signals,
                    "TREND_ALIGNED_POI",
                    "POI_PRESENT",
                    "MICRO_WAITING",
                    "MICRO_PARTIAL",
                    "LIQUIDITY_WAITING",
                ),
                missing=_missing_subset(
                    signals,
                    "TREND_ALIGNED_POI",
                    "POI_PRESENT",
                    "MICRO_READY",
                    "LIQUIDITY_READY",
                ),
                is_light_candidate=True,
            )
        )

    if signals.trend_aligned_poi and signals.poi_present and signals.in_ote:
        strict = signals.micro_ready and signals.liquidity_ready
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.OTE_PULLBACK,
                confidence=0.70,
                reason="TREND_ALIGNED_POI_IN_OTE",
                present=_present_subset(
                    signals,
                    "TREND_ALIGNED_POI",
                    "POI_PRESENT",
                    "IN_OTE",
                    "TIMING_READY",
                    "MICRO_READY",
                    "LIQUIDITY_READY",
                ),
                missing=_missing_subset(
                    signals,
                    "TREND_ALIGNED_POI",
                    "POI_PRESENT",
                    "IN_OTE",
                    "MICRO_READY",
                    "LIQUIDITY_READY",
                ),
                is_light_candidate=not strict,
                is_strict_candidate=strict,
            )
        )

    if signals.counter_trend_poi and signals.poi_present and (
        signals.micro_waiting or signals.micro_partial or signals.sweep_detected
    ):
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.REVERSAL_LIGHT,
                confidence=0.60,
                reason="COUNTER_TREND_POI_WITH_PARTIAL_REVERSAL_EVIDENCE",
                present=_present_subset(
                    signals,
                    "COUNTER_TREND_POI",
                    "POI_PRESENT",
                    "MICRO_WAITING",
                    "MICRO_PARTIAL",
                    "SWEEP_DETECTED",
                ),
                missing=_missing_subset(
                    signals,
                    "COUNTER_TREND_POI",
                    "POI_PRESENT",
                    "MICRO_READY",
                    "LIQUIDITY_READY",
                ),
                is_light_candidate=True,
            )
        )

    if not candidates and signals.poi_present:
        candidates.append(
            SetupCandidate(
                candidate_type=SetupType.POI_REACTION,
                confidence=0.45,
                reason="POI_PRESENT_WITHOUT_SETUP_CONFIRMATION",
                present=_present_subset(signals, "POI_PRESENT"),
                missing=_missing_subset(
                    signals,
                    "TREND_ALIGNED_POI",
                    "COUNTER_TREND_POI",
                    "MICRO_READY",
                    "LIQUIDITY_READY",
                    "IN_OTE",
                ),
            )
        )

    return candidates


def _present_subset(signals: SetupSignalInventory, *expected: str) -> list[str]:
    present = set(signals.present_signals)
    return [item for item in expected if item in present]


def _missing_subset(signals: SetupSignalInventory, *expected: str) -> list[str]:
    present = set(signals.present_signals)
    return [item for item in expected if item not in present]
