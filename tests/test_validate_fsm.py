"""Tests for scripts/validate_fsm.py — FSM validation against traces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigil.models.action import Action
from vigil.neuro.fsm_builder import FsmBuilder
from vigil.symbolic.fsm_checker import FsmChecker, VerifyResult


@pytest.fixture
def synthetic_trace(tmp_path: Path) -> Path:
    """Create a minimal trace that exercises 3 states and 3 transitions."""
    data = {
        "app_package": "com.test.app",
        "screens": {
            "scr_001": {
                "screen_id": "scr_001",
                "activity_name": ".MainActivity",
                "interactable_elements": [
                    {
                        "class_name": "android.widget.TextView",
                        "resource_id": "com.test:id/title",
                        "text": "Home",
                        "depth": 1,
                        "is_clickable": True,
                    },
                    {
                        "class_name": "android.widget.Button",
                        "resource_id": "com.test:id/btn",
                        "text": "Go",
                        "depth": 2,
                        "is_clickable": True,
                    },
                ],
            },
            "scr_002": {
                "screen_id": "scr_002",
                "activity_name": ".MainActivity",
                "interactable_elements": [
                    {
                        "class_name": "android.widget.TextView",
                        "resource_id": "com.test:id/title",
                        "text": "Settings",
                        "depth": 1,
                        "is_clickable": True,
                    },
                    {
                        "class_name": "android.widget.Switch",
                        "resource_id": "com.test:id/toggle",
                        "text": "On",
                        "depth": 2,
                        "is_clickable": True,
                        "is_checkable": True,
                    },
                ],
            },
            "scr_003": {
                "screen_id": "scr_003",
                "activity_name": ".DetailActivity",
                "interactable_elements": [
                    {
                        "class_name": "android.widget.TextView",
                        "resource_id": "com.test:id/title",
                        "text": "Detail",
                        "depth": 1,
                        "is_clickable": True,
                    },
                    {
                        "class_name": "android.widget.ImageView",
                        "resource_id": "com.test:id/icon",
                        "depth": 2,
                        "is_clickable": True,
                    },
                ],
            },
        },
        "traces": [
            {
                "step_number": 1,
                "source_screen_id": "scr_001",
                "target_screen_id": "scr_002",
                "action": {"action_type": "click", "target_element_id": "e_001"},
                "timestamp": "",
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_002",
                "target_screen_id": "scr_003",
                "action": {"action_type": "click", "target_element_id": "e_002"},
                "timestamp": "",
            },
            {
                "step_number": 3,
                "source_screen_id": "scr_003",
                "target_screen_id": "scr_002",
                "action": {"action_type": "navigate_back"},
                "timestamp": "",
            },
        ],
    }
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(data))
    return path


class TestValidateAllAllow:
    """Traces that built the FSM should all ALLOW."""

    def test_all_allow(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)

        # Rebuild mapping
        trace_data = json.loads(synthetic_trace.read_text())
        raw_screens = trace_data["screens"]
        fp_to_state_id, _states = builder._build_states(raw_screens)
        sid_to_state_id = builder._build_screen_mapping(raw_screens, fp_to_state_id)

        checker = FsmChecker(fsm)

        deny_count = 0
        for trace in trace_data["traces"]:
            source_state = sid_to_state_id.get(trace["source_screen_id"])
            target_state = sid_to_state_id.get(trace["target_screen_id"])
            if source_state is None or source_state == target_state:
                continue

            action = Action(**trace["action"])
            action_dict = action.to_fsm_dict()
            result = checker.verify_by_state(source_state, action_dict)
            if result.result == VerifyResult.DENY:
                deny_count += 1

        assert deny_count == 0

    def test_transitions_have_confidence(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)

        for t in fsm.transitions:
            assert (
                t.confidence == 1.0
            ), f"Transition {t.source}→{t.target} has confidence={t.confidence}, expected 1.0"


class TestDetectMissingTransition:
    """Removing a transition should cause DENY."""

    def test_detect_missing_transition(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)

        # Remove the first transition from the FSM
        removed = fsm.transitions.pop(0)
        # Also remove from networkx graph
        if fsm.graph.has_edge(removed.source, removed.target):
            fsm.graph.remove_edge(removed.source, removed.target)

        # Rebuild mapping
        trace_data = json.loads(synthetic_trace.read_text())
        raw_screens = trace_data["screens"]
        fp_to_state_id, _states = builder._build_states(raw_screens)
        sid_to_state_id = builder._build_screen_mapping(raw_screens, fp_to_state_id)

        checker = FsmChecker(fsm)

        deny_count = 0
        for trace in trace_data["traces"]:
            source_state = sid_to_state_id.get(trace["source_screen_id"])
            target_state = sid_to_state_id.get(trace["target_screen_id"])
            if source_state is None or source_state == target_state:
                continue

            action = Action(**trace["action"])
            action_dict = action.to_fsm_dict()
            result = checker.verify_by_state(source_state, action_dict)
            if result.result == VerifyResult.DENY:
                deny_count += 1

        assert deny_count >= 1, "Removing a transition should cause at least one DENY"
