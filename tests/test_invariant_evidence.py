"""Tests for the per-state invariant evidence builder."""

from __future__ import annotations

from typing import Any

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.neuro.invariant_evidence import (
    build_all_invariant_evidence,
    build_invariant_evidence,
)


def _fsm() -> AppFSM:
    fsm = AppFSM(app_package="com.app")
    fsm.add_state(
        AbstractState(
            state_id="home",
            name="Home",
            fingerprint="f1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name="com.app.Home",
            raw_screens=["h"],
        )
    )
    fsm.add_state(
        AbstractState(
            state_id="detail",
            name="Detail",
            fingerprint="f2",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            raw_screens=["d1", "d2"],
        )
    )
    fsm.initial_state = "home"
    fsm.add_transition(
        Transition(
            source="home",
            target="detail",
            action={"type": "click", "target": "e1", "target_text": "Open"},
            confidence=0.9,
        )
    )
    fsm.add_transition(
        Transition(source="detail", target="home", action={"type": "navigate_back"}, confidence=0.8)
    )
    return fsm


def _raw_screens() -> dict[str, Any]:
    return {
        "h": {
            "screen_id": "h",
            "interactable_elements": [
                {
                    "element_id": "e1",
                    "resource_id": "com.app:id/open",
                    "text": "Open",
                    "is_clickable": True,
                }
            ],
        },
        "d1": {
            "screen_id": "d1",
            "interactable_elements": [
                {"element_id": "t", "resource_id": "com.app:id/title", "text": "Detail"}
            ],
        },
        "d2": {
            "screen_id": "d2",
            "interactable_elements": [
                {"element_id": "t", "resource_id": "com.app:id/title", "text": "Detail"}
            ],
        },
    }


def test_build_invariant_evidence_for_state() -> None:
    fsm = _fsm()
    raw = _raw_screens()
    evidence = build_invariant_evidence(fsm, fsm.states["detail"], raw)

    assert evidence.target_state_id == "detail"
    assert evidence.target_state_name == "Detail"
    assert evidence.observation_count == 2
    assert len(evidence.observations) == 2
    # Arrival registry resolves the title element from the representative screen.
    assert "com.app:id/title" in evidence.arrival_registry.resource_id_to_alias
    # Incoming home->detail, outgoing detail->home.
    assert [t.source_state_id for t in evidence.incoming] == ["home"]
    assert [t.target_state_id for t in evidence.outgoing] == ["home"]
    # No app prior supplied -> no static hints.
    assert evidence.static_prior_hints == []


def test_build_all_invariant_evidence_covers_every_state() -> None:
    fsm = _fsm()
    evidence = build_all_invariant_evidence(fsm, _raw_screens())
    assert {e.target_state_id for e in evidence} == {"home", "detail"}


def test_missing_raw_screens_degrade_gracefully() -> None:
    fsm = _fsm()
    evidence = build_invariant_evidence(fsm, fsm.states["detail"], {})
    assert evidence.observation_count == 0
    assert evidence.observations == []
    assert evidence.arrival_registry.state_id == "detail"


def test_canonical_action_key_str_preserves_falsy_values() -> None:
    import json

    from vigil.neuro.invariant_evidence import canonical_action_key_str

    zero = canonical_action_key_str({"type": "scroll", "value": 0})
    false = canonical_action_key_str({"type": "scroll", "value": False})
    absent = canonical_action_key_str({"type": "scroll"})

    # Falsy-but-present identity values survive (an `if value` filter would drop them).
    assert json.loads(zero).get("value") == 0
    assert json.loads(false).get("value") is False
    # ...and stay distinguishable from an absent value and from each other.
    assert zero != absent
    assert false != absent
    assert zero != false
    # Deterministic: same action -> identical string.
    assert canonical_action_key_str({"type": "scroll", "value": 0}) == zero
