"""Tests for FSM builder and AppFSM methods."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.neuro.fsm_builder import FsmBuilder

TRACE_PATH = (
    Path(__file__).parent.parent / "data/apps/settings/traces/exploration_20260401_022151.json"
)


# ── FsmBuilder tests ──────────────────────────────────────────────


class TestFsmBuilderSynthetic:
    """Tests using synthetic trace data."""

    @pytest.fixture
    def synthetic_trace(self, tmp_path: Path) -> Path:
        """Create a minimal synthetic exploration trace.

        Screens have structurally distinct elements (different class_name/depth)
        so scroll-aware fingerprinting produces unique fingerprints.
        scr_004 is a structural duplicate of scr_001 (same skeleton, different text).
        """
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
                            "resource_id": "com.test:id/btn_nav",
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
                # Structural duplicate of scr_001 (same elements, different text)
                "scr_004": {
                    "screen_id": "scr_004",
                    "activity_name": ".MainActivity",
                    "interactable_elements": [
                        {
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/title",
                            "text": "Home v2",
                            "depth": 1,
                            "is_clickable": True,
                        },
                        {
                            "class_name": "android.widget.Button",
                            "resource_id": "com.test:id/btn_nav",
                            "text": "Navigate",
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
                # Self-loop
                {
                    "step_number": 4,
                    "source_screen_id": "scr_002",
                    "target_screen_id": "scr_002",
                    "action": {"action_type": "scroll_down"},
                    "timestamp": "",
                },
                # Duplicate transition (same source→target→action_type as step 1)
                {
                    "step_number": 5,
                    "source_screen_id": "scr_004",
                    "target_screen_id": "scr_002",
                    "action": {"action_type": "click", "target_element_id": "e_001"},
                    "timestamp": "",
                },
            ],
        }
        path = tmp_path / "trace.json"
        path.write_text(json.dumps(data))
        return path

    def test_deduplication(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        # scr_001 and scr_004 share fingerprint fp_aaa → 3 unique states
        assert len(fsm.states) == 3

    def test_dedup_raw_screens(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        # scr_001 and scr_004 share the same structure → merged into one state
        home_state = [s for s in fsm.states.values() if "Home" in s.name][0]
        assert set(home_state.raw_screens) == {"scr_001", "scr_004"}

    def test_self_loop_excluded_by_default(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        for t in fsm.transitions:
            assert t.source != t.target

    def test_self_loop_included(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace, include_self_loops=True)
        self_loops = [t for t in fsm.transitions if t.source == t.target]
        assert len(self_loops) >= 1

    def test_transition_merging(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        # Step 1 and 5 are both click from Home state → Settings state
        home_sid = [s.state_id for s in fsm.states.values() if "Home" in s.name][0]
        settings_sid = [s.state_id for s in fsm.states.values() if "Settings" in s.name][0]
        merged = [
            t
            for t in fsm.transitions
            if t.action.get("type") == "click" and t.source == home_sid and t.target == settings_sid
        ]
        assert len(merged) == 1
        assert merged[0].observed_count == 2

    def test_initial_state(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        assert fsm.initial_state is not None
        # Initial state should be Home (source of step 1)
        initial = fsm.states[fsm.initial_state]
        assert "Home" in initial.name

    def test_state_naming(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        names = {s.name for s in fsm.states.values()}
        assert "Home" in names
        assert "Settings" in names
        assert "Detail" in names

    def test_hierarchy_inference(self, synthetic_trace: Path) -> None:
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        # .MainActivity has 2 structurally distinct states → should be FRAGMENT level
        main_states = [s for s in fsm.states.values() if s.activity_name == ".MainActivity"]
        assert len(main_states) == 2  # Home and Settings (scr_004 merged with scr_001)
        for s in main_states:
            assert s.hierarchy_level == HierarchyLevel.FRAGMENT
        # .DetailActivity has 1 state → stays ACTIVITY
        detail_states = [s for s in fsm.states.values() if s.activity_name == ".DetailActivity"]
        for s in detail_states:
            assert s.hierarchy_level == HierarchyLevel.ACTIVITY

    def test_scroll_state_merging(self, tmp_path: Path) -> None:
        """Screens at different scroll positions of a scrollable list merge into one state."""
        data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_a": {
                    "screen_id": "scr_a",
                    "activity_name": ".ListActivity",
                    "interactable_elements": [
                        {
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/title",
                            "text": "My List",
                            "depth": 1,
                            "is_clickable": True,
                        },
                        {
                            "class_name": "androidx.recyclerview.widget.RecyclerView",
                            "resource_id": "com.test:id/list",
                            "depth": 1,
                            "is_scrollable": True,
                        },
                        {
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/item",
                            "text": "Item A",
                            "depth": 2,
                            "is_clickable": True,
                        },
                        {
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/item",
                            "text": "Item B",
                            "depth": 2,
                            "is_clickable": True,
                        },
                    ],
                },
                "scr_b": {
                    "screen_id": "scr_b",
                    "activity_name": ".ListActivity",
                    "interactable_elements": [
                        {
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/title",
                            "text": "My List",
                            "depth": 1,
                            "is_clickable": True,
                        },
                        {
                            "class_name": "androidx.recyclerview.widget.RecyclerView",
                            "resource_id": "com.test:id/list",
                            "depth": 1,
                            "is_scrollable": True,
                        },
                        {
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/item",
                            "text": "Item C",
                            "depth": 2,
                            "is_clickable": True,
                        },
                        {
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/item",
                            "text": "Item D",
                            "depth": 2,
                            "is_clickable": True,
                        },
                        {
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/item",
                            "text": "Item E",
                            "depth": 2,
                            "is_clickable": True,
                        },
                    ],
                },
            },
            "traces": [
                {
                    "step_number": 1,
                    "source_screen_id": "scr_a",
                    "target_screen_id": "scr_b",
                    "action": {"action_type": "scroll_down"},
                    "timestamp": "",
                },
            ],
        }
        path = tmp_path / "scroll_trace.json"
        path.write_text(json.dumps(data))

        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(path, include_self_loops=True)

        # Both screens should merge into 1 state (same skeleton, different scroll content)
        assert len(fsm.states) == 1
        state = list(fsm.states.values())[0]
        assert set(state.raw_screens) == {"scr_a", "scr_b"}
        assert state.name == "My List"


@pytest.mark.skipif(not TRACE_PATH.exists(), reason="Real trace file not available")
class TestFsmBuilderRealTrace:
    """Tests using the real Settings exploration trace."""

    def test_build_from_real_trace(self) -> None:
        builder = FsmBuilder("com.android.settings")
        fsm = builder.build_from_trace(TRACE_PATH)
        assert len(fsm.states) > 0
        assert len(fsm.transitions) > 0
        assert fsm.initial_state is not None

    def test_real_trace_stats(self) -> None:
        builder = FsmBuilder("com.android.settings")
        fsm = builder.build_from_trace(TRACE_PATH)
        # Scroll-aware merging reduces ~97 raw screens to ~40-50 states
        assert len(fsm.states) >= 30
        # Should have meaningful transitions
        assert len(fsm.transitions) >= 20

    def test_serialize_deserialize(self, tmp_path: Path) -> None:
        builder = FsmBuilder("com.android.settings")
        fsm = builder.build_from_trace(TRACE_PATH)
        out = tmp_path / "fsm.json"
        fsm.serialize(out)
        fsm2 = AppFSM.deserialize(out)
        assert len(fsm2.states) == len(fsm.states)
        assert len(fsm2.transitions) == len(fsm.transitions)


# ── AppFSM method tests ──────────────────────────────────────────


class TestAppFSMMethods:
    @pytest.fixture
    def sample_fsm(self) -> AppFSM:
        """Create a small FSM: s1 --click--> s2 --click--> s3, s2 --back--> s1."""
        fsm = AppFSM(app_package="com.test.app")
        for i, (name, fp) in enumerate(
            [("Home", "fp_a"), ("List", "fp_b"), ("Detail", "fp_c")], start=1
        ):
            fsm.add_state(
                AbstractState(
                    state_id=f"s{i}",
                    name=name,
                    fingerprint=fp,
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                )
            )
        fsm.add_transition(
            Transition(source="s1", target="s2", action={"type": "click"}, observed_count=5)
        )
        fsm.add_transition(
            Transition(source="s2", target="s3", action={"type": "click"}, observed_count=3)
        )
        fsm.add_transition(
            Transition(source="s2", target="s1", action={"type": "navigate_back"}, observed_count=2)
        )
        fsm.initial_state = "s1"
        return fsm

    def test_is_valid_transition(self, sample_fsm: AppFSM) -> None:
        assert sample_fsm.is_valid_transition("s1", {"type": "click"}) is True
        assert sample_fsm.is_valid_transition("s1", {"type": "navigate_back"}) is False
        assert sample_fsm.is_valid_transition("s3", {"type": "click"}) is False
        assert sample_fsm.is_valid_transition("nonexistent", {"type": "click"}) is False

    def test_is_reachable(self, sample_fsm: AppFSM) -> None:
        assert sample_fsm.is_reachable("s1", "s3") is True
        assert sample_fsm.is_reachable("s1", "s2") is True
        assert sample_fsm.is_reachable("s3", "s1") is False  # No edges from s3
        assert sample_fsm.is_reachable("s1", "nonexistent") is False

    def test_get_shortest_path(self, sample_fsm: AppFSM) -> None:
        assert sample_fsm.get_shortest_path("s1", "s3") == ["s1", "s2", "s3"]
        assert sample_fsm.get_shortest_path("s1", "s2") == ["s1", "s2"]
        assert sample_fsm.get_shortest_path("s3", "s1") == []

    def test_get_transition_target(self, sample_fsm: AppFSM) -> None:
        assert sample_fsm.get_transition_target("s1", {"type": "click"}) == "s2"
        assert sample_fsm.get_transition_target("s2", {"type": "navigate_back"}) == "s1"
        assert sample_fsm.get_transition_target("s1", {"type": "scroll_up"}) is None

    def test_get_transition(self, sample_fsm: AppFSM) -> None:
        t = sample_fsm.get_transition("s1", {"type": "click"})
        assert t is not None
        assert t.target == "s2"
        assert t.observed_count == 5

        assert sample_fsm.get_transition("s1", {"type": "scroll_up"}) is None

    def test_find_similar_state(self, sample_fsm: AppFSM) -> None:
        assert sample_fsm.find_similar_state("fp_a") == "s1"
        assert sample_fsm.find_similar_state("fp_b") == "s2"
        assert sample_fsm.find_similar_state("fp_unknown") is None
