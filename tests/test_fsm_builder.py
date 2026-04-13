"""Tests for FSM builder and AppFSM methods."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    HierarchyLevel,
    SubFsmTemplate,
    Transition,
)
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


# ── Post-processing tests ──────────────────────────────────────


class TestMergeScrollDuplicates:
    def test_merge_scroll_duplicates(self) -> None:
        """States sharing (activity, base_name) get merged."""
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="Sound #1",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".SubSettings",
            raw_screens=["scr_01"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="Sound #2",
            fingerprint="fp2",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".SubSettings",
            raw_screens=["scr_02"],
        )
        s3 = AbstractState(
            state_id="s3",
            name="Display",
            fingerprint="fp3",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".SubSettings",
            raw_screens=["scr_03"],
        )
        for s in (s1, s2, s3):
            fsm.add_state(s)
        fsm.add_transition(
            Transition(
                source="s1",
                target="s3",
                action={"type": "click"},
                observed_count=1,
            )
        )
        fsm.add_transition(
            Transition(
                source="s2",
                target="s3",
                action={"type": "click"},
                observed_count=2,
            )
        )
        fsm.add_transition(
            Transition(
                source="s3",
                target="s1",
                action={"type": "navigate_back"},
                observed_count=1,
            )
        )

        builder = FsmBuilder("com.test")
        merged = builder._merge_scroll_duplicates(fsm)

        assert merged == 1
        assert len(fsm.states) == 2
        assert "s1" in fsm.states  # canonical
        assert "s2" not in fsm.states  # merged away
        assert fsm.states["s1"].name == "Sound"  # "#N" stripped
        assert set(fsm.states["s1"].raw_screens) == {"scr_01", "scr_02"}

    def test_merge_preserves_transitions(self) -> None:
        """Merged state gets the union of both states' transitions."""
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="List #1",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Activity",
            raw_screens=["scr_01"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="List #2",
            fingerprint="fp2",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Activity",
            raw_screens=["scr_02"],
        )
        s3 = AbstractState(
            state_id="s3",
            name="Detail A",
            fingerprint="fp3",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".DetailA",
        )
        s4 = AbstractState(
            state_id="s4",
            name="Detail B",
            fingerprint="fp4",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".DetailB",
        )
        for s in (s1, s2, s3, s4):
            fsm.add_state(s)
        # s1 → s3, s2 → s4 (different targets from each scroll position)
        fsm.add_transition(
            Transition(
                source="s1",
                target="s3",
                action={"type": "click"},
                observed_count=1,
            )
        )
        fsm.add_transition(
            Transition(
                source="s2",
                target="s4",
                action={"type": "click"},
                observed_count=1,
            )
        )

        builder = FsmBuilder("com.test")
        builder._merge_scroll_duplicates(fsm)

        # Canonical s1 should now have transitions to both s3 and s4
        targets = {t.target for t in fsm.transitions if t.source == "s1"}
        assert "s3" in targets
        assert "s4" in targets

    def test_merge_updates_initial_state(self) -> None:
        """If initial_state is a duplicate, it gets redirected."""
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="Home #1",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Main",
            raw_screens=["scr_01"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="Home #2",
            fingerprint="fp2",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Main",
            raw_screens=["scr_02"],
        )
        for s in (s1, s2):
            fsm.add_state(s)
        fsm.initial_state = "s2"  # set to duplicate

        builder = FsmBuilder("com.test")
        builder._merge_scroll_duplicates(fsm)

        assert fsm.initial_state == "s1"  # redirected to canonical


class TestRemoveErrorStates:
    def test_remove_error_states(self) -> None:
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="Settings",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        s2 = AbstractState(
            state_id="s2",
            name="Webpage not available",
            fingerprint="fp2",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        for s in (s1, s2):
            fsm.add_state(s)
        fsm.add_transition(
            Transition(
                source="s1",
                target="s2",
                action={"type": "click"},
                observed_count=1,
            )
        )
        fsm.initial_state = "s1"

        builder = FsmBuilder("com.test")
        removed = builder._remove_error_states(fsm)

        assert removed == 1
        assert "s2" not in fsm.states
        assert len(fsm.transitions) == 0  # transition to s2 also removed
        assert fsm.initial_state == "s1"  # preserved

    def test_remove_keeps_normal_states(self) -> None:
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="WiFi Settings",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
        fsm.add_state(s1)

        builder = FsmBuilder("com.test")
        removed = builder._remove_error_states(fsm)

        assert removed == 0
        assert "s1" in fsm.states


class TestToggleSelfLoops:
    """Tests for preserving toggle (checkable) self-loop transitions."""

    def test_toggle_self_loop_preserved(self, tmp_path: Path) -> None:
        """Self-loops targeting checkable elements should be preserved."""
        data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_001": {
                    "screen_id": "scr_001",
                    "activity_name": ".MainActivity",
                    "interactable_elements": [
                        {
                            "element_id": "e_001",
                            "class_name": "android.widget.Switch",
                            "resource_id": "com.test:id/bt_switch",
                            "text": "",
                            "depth": 1,
                            "is_clickable": True,
                            "is_checkable": True,
                            "is_checked": False,
                        },
                        {
                            "element_id": "e_002",
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/title",
                            "text": "Bluetooth",
                            "depth": 1,
                            "is_clickable": True,
                        },
                    ],
                },
            },
            "traces": [
                {
                    "step_number": 1,
                    "source_screen_id": "scr_001",
                    "target_screen_id": "scr_001",
                    "action": {"action_type": "click", "target_element_id": "e_001"},
                    "timestamp": "",
                },
                {
                    "step_number": 2,
                    "source_screen_id": "scr_001",
                    "target_screen_id": "scr_001",
                    "action": {"action_type": "click", "target_element_id": "e_002"},
                    "timestamp": "",
                },
            ],
        }
        path = tmp_path / "trace.json"
        path.write_text(json.dumps(data))

        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(path, include_self_loops=False)

        # Toggle self-loop (e_001, checkable) should be preserved
        # Non-toggle self-loop (e_002, not checkable) should be excluded
        self_loops = [t for t in fsm.transitions if t.source == t.target]
        assert len(self_loops) == 1
        assert self_loops[0].action.get("target") == "e_001"

    def test_non_toggle_self_loop_still_excluded(self, tmp_path: Path) -> None:
        """Self-loops on non-checkable elements should still be excluded."""
        data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_001": {
                    "screen_id": "scr_001",
                    "activity_name": ".MainActivity",
                    "interactable_elements": [
                        {
                            "element_id": "e_001",
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/title",
                            "text": "Settings",
                            "depth": 1,
                            "is_clickable": True,
                        },
                    ],
                },
            },
            "traces": [
                {
                    "step_number": 1,
                    "source_screen_id": "scr_001",
                    "target_screen_id": "scr_001",
                    "action": {"action_type": "click", "target_element_id": "e_001"},
                    "timestamp": "",
                },
            ],
        }
        path = tmp_path / "trace.json"
        path.write_text(json.dumps(data))

        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(path, include_self_loops=False)

        self_loops = [t for t in fsm.transitions if t.source == t.target]
        assert len(self_loops) == 0

    def test_is_toggle_action(self) -> None:
        """_is_toggle_action correctly identifies checkable elements."""
        raw_screens = {
            "scr_001": {
                "interactable_elements": [
                    {"element_id": "e_001", "is_checkable": True},
                    {"element_id": "e_002", "is_checkable": False},
                ],
            },
        }
        trace_toggle = {
            "source_screen_id": "scr_001",
            "action": {"target_element_id": "e_001"},
        }
        trace_normal = {
            "source_screen_id": "scr_001",
            "action": {"target_element_id": "e_002"},
        }
        trace_missing = {
            "source_screen_id": "scr_001",
            "action": {"target_element_id": "e_999"},
        }

        assert FsmBuilder._is_toggle_action(trace_toggle, raw_screens) is True
        assert FsmBuilder._is_toggle_action(trace_normal, raw_screens) is False
        assert FsmBuilder._is_toggle_action(trace_missing, raw_screens) is False


# ── Sub-FSM Template tests ──────────────────────────────────────────


class TestBuildSubFsmTemplates:
    @staticmethod
    def _make_dynamic_fsm() -> AppFSM:
        """FSM: list_state (DYNAMIC) -click-> detail_1, detail_2, detail_3 (same fp)."""
        fsm = AppFSM(app_package="com.test.app")
        list_state = AbstractState(
            state_id="s_list",
            name="ItemList",
            fingerprint="fp_list",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            container_type=ContainerType.DYNAMIC,
        )
        fsm.add_state(list_state)
        for i in range(1, 4):
            fsm.add_state(
                AbstractState(
                    state_id=f"s_detail_{i}",
                    name=f"Detail {i}",
                    fingerprint="fp_detail_shared",
                    hierarchy_level=HierarchyLevel.FRAGMENT,
                )
            )
            fsm.add_transition(
                Transition(
                    source="s_list",
                    target=f"s_detail_{i}",
                    action={"type": "click"},
                    observed_count=1,
                )
            )
        # Add a non-click transition (back) that should not be collapsed
        fsm.add_state(
            AbstractState(
                state_id="s_home",
                name="Home",
                fingerprint="fp_home",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
        fsm.add_transition(
            Transition(
                source="s_list",
                target="s_home",
                action={"type": "navigate_back"},
                observed_count=1,
            )
        )
        fsm.initial_state = "s_home"
        return fsm

    def test_creates_template_for_dynamic_container(self) -> None:
        fsm = self._make_dynamic_fsm()
        builder = FsmBuilder("com.test.app")
        count = builder._build_sub_fsm_templates(fsm)
        assert count == 1
        assert "tmpl_s_list" in fsm.sub_fsm_templates

    def test_template_references_source_state(self) -> None:
        fsm = self._make_dynamic_fsm()
        builder = FsmBuilder("com.test.app")
        builder._build_sub_fsm_templates(fsm)

        tmpl = fsm.sub_fsm_templates["tmpl_s_list"]
        assert tmpl.source_state_id == "s_list"
        assert tmpl.entry_fingerprint == "fp_detail_shared"
        assert tmpl.parameter_schema == {"selected_item": "string"}

    def test_state_gets_template_id(self) -> None:
        fsm = self._make_dynamic_fsm()
        builder = FsmBuilder("com.test.app")
        builder._build_sub_fsm_templates(fsm)
        assert fsm.states["s_list"].sub_fsm_template_id == "tmpl_s_list"

    def test_collapses_duplicate_targets(self) -> None:
        fsm = self._make_dynamic_fsm()
        assert len(fsm.states) == 5  # list + 3 details + home
        builder = FsmBuilder("com.test.app")
        builder._build_sub_fsm_templates(fsm)
        # 2 detail states should be removed (one representative kept)
        detail_states = [s for s in fsm.states if s.startswith("s_detail_")]
        assert len(detail_states) == 1

    def test_back_transition_preserved(self) -> None:
        fsm = self._make_dynamic_fsm()
        builder = FsmBuilder("com.test.app")
        builder._build_sub_fsm_templates(fsm)
        back_transitions = [t for t in fsm.transitions if t.action.get("type") == "navigate_back"]
        assert len(back_transitions) == 1
        assert back_transitions[0].source == "s_list"
        assert back_transitions[0].target == "s_home"

    def test_no_template_for_static_container(self) -> None:
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s1",
                name="Settings",
                fingerprint="fp_s1",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                container_type=ContainerType.STATIC,
            )
        )
        builder = FsmBuilder("com.test.app")
        count = builder._build_sub_fsm_templates(fsm)
        assert count == 0
        assert fsm.sub_fsm_templates == {}

    def test_no_template_for_single_target(self) -> None:
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s1",
                name="List",
                fingerprint="fp_list",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                container_type=ContainerType.DYNAMIC,
            )
        )
        fsm.add_state(
            AbstractState(
                state_id="s2",
                name="Detail",
                fingerprint="fp_detail",
                hierarchy_level=HierarchyLevel.FRAGMENT,
            )
        )
        fsm.add_transition(Transition(source="s1", target="s2", action={"type": "click"}))
        builder = FsmBuilder("com.test.app")
        count = builder._build_sub_fsm_templates(fsm)
        assert count == 0

    def test_serialization_roundtrip(self, tmp_path: Path) -> None:
        fsm = self._make_dynamic_fsm()
        builder = FsmBuilder("com.test.app")
        builder._build_sub_fsm_templates(fsm)

        path = tmp_path / "fsm.json"
        fsm.serialize(path)

        restored = AppFSM.deserialize(path)
        assert "tmpl_s_list" in restored.sub_fsm_templates
        tmpl = restored.sub_fsm_templates["tmpl_s_list"]
        assert tmpl.source_state_id == "s_list"
        assert tmpl.entry_fingerprint == "fp_detail_shared"
        assert restored.states["s_list"].sub_fsm_template_id == "tmpl_s_list"


class TestTemplateBasedValidation:
    def test_click_valid_via_template(self) -> None:
        """DYNAMIC state with template: click is valid even without explicit edge."""
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s_list",
                name="List",
                fingerprint="fp_list",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                container_type=ContainerType.DYNAMIC,
                sub_fsm_template_id="tmpl_1",
            )
        )
        fsm.sub_fsm_templates["tmpl_1"] = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
        )
        assert fsm.is_valid_transition("s_list", {"type": "click"}) is True

    def test_non_click_not_valid_via_template(self) -> None:
        """Template only covers click — scroll_up should still fail."""
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s_list",
                name="List",
                fingerprint="fp_list",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                container_type=ContainerType.DYNAMIC,
                sub_fsm_template_id="tmpl_1",
            )
        )
        fsm.sub_fsm_templates["tmpl_1"] = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
        )
        assert fsm.is_valid_transition("s_list", {"type": "scroll_up"}) is False

    def test_static_state_no_template_fallthrough(self) -> None:
        """STATIC state: no template lookup, normal edge check only."""
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s1",
                name="Settings",
                fingerprint="fp_s1",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                container_type=ContainerType.STATIC,
            )
        )
        assert fsm.is_valid_transition("s1", {"type": "click"}) is False
