"""Root-cause fix: Kasper's POI gate ignored effective_poi_status.

A POI revalidated by micro-confirmation (RECOVERABLE_REJECTED → READY_FOR_TRIGGER,
i.e. effective_poi_status = READY_FOR_TRIGGER) was treated as untradable by Kasper
because from_existing_agent2_context derived `tradable` only from the raw fields.
Every PDE ENTER signal on such a POI was then vetoed (KASPER_NOT_ENTER_ELIGIBLE:REJECT),
producing 0 trades. Kasper must honor the same effective_poi_status the rest of the
system uses. This is a consistency fix, not a threshold change.
"""
from __future__ import annotations

from gold_sniper.strategy.kasper_contracts import from_existing_agent2_context


def test_raw_untradable_but_effective_ready_is_tradable():
    poi = {
        "selected_poi": {
            "tradable": False,            # raw contract: not tradable
            "execution_readiness": "RECOVERABLE_REJECTED",
        },
        "poi_available": False,
        "effective_poi_status": "READY_FOR_TRIGGER",  # revalidated by micro-synergy
    }
    ctx = from_existing_agent2_context(poi)
    assert ctx.selected_poi.tradable is True


def test_effective_status_nested_under_synergy_is_honored():
    poi = {
        "selected_poi": {"tradable": False},
        "poi_micro_synergy": {"effective_poi_status": "SYNERGY_READY_FOR_TRIGGER"},
    }
    ctx = from_existing_agent2_context(poi)
    assert ctx.selected_poi.tradable is True


def test_genuinely_rejected_poi_stays_untradable():
    # No revalidation: effective status is still a rejected/weak state → NOT tradable.
    poi = {
        "selected_poi": {"tradable": False, "execution_readiness": "RECOVERABLE_REJECTED"},
        "poi_available": False,
        "effective_poi_status": "POI_TOO_WEAK",
    }
    ctx = from_existing_agent2_context(poi)
    assert ctx.selected_poi.tradable is False


def test_raw_tradable_still_works():
    poi = {"selected_poi": {"tradable": True}}
    ctx = from_existing_agent2_context(poi)
    assert ctx.selected_poi.tradable is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
