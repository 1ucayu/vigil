"""Shared pytest fixtures for Vigil test suite."""

import pytest

from vigil.models.action import Action, ActionType
from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition


@pytest.fixture
def sample_fsm() -> AppFSM:
    """Create a minimal FSM for testing (3 states, 2 transitions)."""
    fsm = AppFSM(app_package="com.android.settings")

    s1 = AbstractState(
        state_id="s1",
        name="MainSettings",
        fingerprint="fp_main",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        activity_name="com.android.settings.Settings",
    )
    s2 = AbstractState(
        state_id="s2",
        name="WiFiSettings",
        fingerprint="fp_wifi",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s1",
        activity_name="com.android.settings.Settings",
    )
    s3 = AbstractState(
        state_id="s3",
        name="WiFiDetail",
        fingerprint="fp_wifi_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        parent_state="s2",
        activity_name="com.android.settings.Settings",
    )

    fsm.add_state(s1)
    fsm.add_state(s2)
    fsm.add_state(s3)
    fsm.initial_state = "s1"

    t1 = Transition(
        source="s1",
        target="s2",
        action={"type": "click", "target": "wifi_entry"},
        confidence=0.95,
        observed_count=10,
    )
    t2 = Transition(
        source="s2",
        target="s3",
        action={"type": "click", "target": "wifi_network"},
        guard='read(wifi_network, text) != ""',
        confidence=0.85,
        observed_count=5,
    )

    fsm.add_transition(t1)
    fsm.add_transition(t2)

    return fsm


@pytest.fixture
def sample_action() -> Action:
    """Create a sample click action for testing."""
    return Action(
        action_type=ActionType.CLICK,
        target_element_id="wifi_entry",
        target_bounds=[100, 200, 300, 250],
    )
