"""Tests for FSM builder and AppFSM methods."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    HierarchyLevel,
    SubFsmTemplate,
    Transition,
    TransitionLookupStatus,
    canonical_action_key,
)
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.fsm_builder import FsmBuilder
from vigil.symbolic.fsm_checker import FsmChecker, VerifyResult
from vigil.symbolic.state_locator import LocateResult, StateLocator

TRACE_PATH = (
    Path(__file__).parent.parent
    / "data/apps/com_android_settings/traces/exploration_20260401_022151.json"
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
        # Section B: SCROLL_UP / SCROLL_DOWN / INPUT_TEXT self-loops are
        # meaningful affordances and must NOT be dropped even when
        # include_self_loops=False. Plain CLICK no-op self-loops are still
        # dropped. The synthetic trace contains a scroll_down self-loop on
        # scr_002, so the default-built FSM still has that one self-loop.
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(synthetic_trace)
        self_loops = [t for t in fsm.transitions if t.source == t.target]
        # All self-loops present must be scroll/input (meaningful), never click no-ops.
        for t in self_loops:
            atype = (t.action.get("type") or t.action.get("action_type") or "").lower()
            assert atype in {
                "scroll_up",
                "scroll_down",
                "input_text",
            }, f"Unexpected self-loop preserved: {atype}"
        assert any(
            (t.action.get("type") or t.action.get("action_type") or "").lower() == "scroll_down"
            for t in self_loops
        ), "Expected scroll_down self-loop to be preserved"

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

    def test_low_trust_in_app_trace_downgrades_transition(self, tmp_path: Path) -> None:
        data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_001": {
                    "screen_id": "scr_001",
                    "activity_name": ".MainActivity",
                    "interactable_elements": [
                        {
                            "class_name": "android.widget.Button",
                            "resource_id": "com.test:id/next",
                            "text": "Next",
                            "depth": 1,
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
                            "text": "Done",
                            "depth": 2,
                            "is_clickable": False,
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
                    "metadata": {
                        "scope_pre": "in_app",
                        "scope_post": "in_app",
                        "low_trust_scope": True,
                    },
                },
            ],
        }
        path = tmp_path / "low_trust_trace.json"
        path.write_text(json.dumps(data))

        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(path)

        assert len(fsm.transitions) == 1
        transition = fsm.transitions[0]
        assert transition.confidence < 0.7
        assert transition.low_trust is True

    def test_android_system_scope_trace_skipped(self, tmp_path: Path) -> None:
        data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_001": {
                    "screen_id": "scr_001",
                    "activity_name": ".MainActivity",
                    "interactable_elements": [
                        {
                            "class_name": "android.widget.Button",
                            "resource_id": "com.test:id/next",
                            "text": "Next",
                            "depth": 1,
                            "is_clickable": True,
                        },
                    ],
                },
                "scr_002": {
                    "screen_id": "scr_002",
                    "activity_name": ".PermissionDialog",
                    "interactable_elements": [
                        {
                            "class_name": "android.widget.Button",
                            "resource_id": "android:id/button1",
                            "text": "Allow",
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
                    "metadata": {
                        "scope_pre": "in_app",
                        "scope_post": "android_system",
                        "low_trust_scope": True,
                    },
                },
            ],
        }
        path = tmp_path / "android_system_trace.json"
        path.write_text(json.dumps(data))

        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(path)

        trace_edges = [t for t in fsm.transitions if t.action.get("target") == "e_001"]
        assert trace_edges == []

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
        """States sharing (activity, base_name) AND compatible structural
        fingerprints get merged. The new policy (Section I) refuses to
        merge same-name states whose skeletons disagree, so scroll
        duplicates must share fingerprint."""
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="Sound #1",
            fingerprint="fp_sound",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".SubSettings",
            raw_screens=["scr_01"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="Sound #2",
            fingerprint="fp_sound",
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
            fingerprint="fp_list",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Activity",
            raw_screens=["scr_01"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="List #2",
            fingerprint="fp_list",
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
            fingerprint="fp_home",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Main",
            raw_screens=["scr_01"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="Home #2",
            fingerprint="fp_home",
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

    def test_incompatible_fingerprints_not_merged(self) -> None:
        """Section I: two same-name states with different structural
        fingerprints must NOT be merged. The builder logs a diagnostic
        instead."""
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="Inbox",
            fingerprint="fp_inbox_v1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Inbox",
            raw_screens=["scr_01"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="Inbox",
            fingerprint="fp_inbox_v2_with_compose_fab",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Inbox",
            raw_screens=["scr_02"],
        )
        for s in (s1, s2):
            fsm.add_state(s)
        builder = FsmBuilder("com.test")
        merged = builder._merge_scroll_duplicates(fsm)
        assert merged == 0
        assert "s1" in fsm.states
        assert "s2" in fsm.states

    def test_scroll_self_loop_preserved_across_merge(self) -> None:
        """Section B+I: a SCROLL_DOWN self-loop survives merge."""
        fsm = AppFSM(app_package="com.test")
        s1 = AbstractState(
            state_id="s1",
            name="Feed #1",
            fingerprint="fp_feed",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Feed",
            raw_screens=["scr_01"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="Feed #2",
            fingerprint="fp_feed",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            activity_name=".Feed",
            raw_screens=["scr_02"],
        )
        for s in (s1, s2):
            fsm.add_state(s)
        # s2 → s2 scroll_down self-loop (meaningful affordance)
        fsm.add_transition(
            Transition(
                source="s2",
                target="s2",
                action={"type": "scroll_down"},
                observed_count=1,
            )
        )
        # s1 → s2 plain click self-loop (no-op after merge) should be dropped
        fsm.add_transition(
            Transition(
                source="s1",
                target="s2",
                action={"type": "click"},
                observed_count=1,
            )
        )
        builder = FsmBuilder("com.test")
        builder._merge_scroll_duplicates(fsm)
        scroll_self_loops = [
            t
            for t in fsm.transitions
            if t.source == t.target and (t.action.get("type") or "").lower() == "scroll_down"
        ]
        assert scroll_self_loops, "scroll_down self-loop must survive merge"


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
        """FSM: list_state (DYNAMIC) -click-> detail_1, detail_2, detail_3.

        Details share ``structural_fingerprint`` ``sfp_detail`` (so the
        template builder collapses them) but carry different functional
        ``fingerprint`` values (each detail is semantically distinct —
        the real-world case is "list of Wi-Fi networks", each with its
        own SSID). Click transitions carry varying ``target_text`` so
        parameter_schema extraction emits ``item_name``.
        """
        fsm = AppFSM(app_package="com.test.app")
        list_state = AbstractState(
            state_id="s_list",
            name="ItemList",
            fingerprint="fp_list",
            structural_fingerprint="sfp_list",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            container_type=ContainerType.DYNAMIC,
        )
        fsm.add_state(list_state)
        for i in range(1, 4):
            fsm.add_state(
                AbstractState(
                    state_id=f"s_detail_{i}",
                    name=f"Detail {i}",
                    fingerprint=f"fp_detail_{i}",
                    structural_fingerprint="sfp_detail",
                    hierarchy_level=HierarchyLevel.FRAGMENT,
                )
            )
            fsm.add_transition(
                Transition(
                    source="s_list",
                    target=f"s_detail_{i}",
                    action={"type": "click", "target_text": f"Item {i}"},
                    observed_count=1,
                )
            )
        # Add a non-click transition (back) that should not be collapsed
        fsm.add_state(
            AbstractState(
                state_id="s_home",
                name="Home",
                fingerprint="fp_home",
                structural_fingerprint="sfp_home",
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
        # New: entry_fingerprint is the structural_fingerprint shared across
        # the detail targets. parameter_schema names the varying action
        # property; item_skeleton echoes the shared structural fp.
        assert tmpl.entry_fingerprint == "sfp_detail"
        assert tmpl.parameter_schema == {"item_name": "string"}
        assert tmpl.item_skeleton == "sfp_detail"

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
        assert tmpl.entry_fingerprint == "sfp_detail"
        assert restored.states["s_list"].sub_fsm_template_id == "tmpl_s_list"


class TestTemplateBasedValidation:
    def test_click_valid_via_template(self) -> None:
        """DYNAMIC state with template: bare click without identity is UNCERTAIN
        (template_binding_missing). Click with identity routing to a concrete
        template edge resolves to MATCH.
        """
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
        fsm.add_state(
            AbstractState(
                state_id="s_detail",
                name="Detail",
                fingerprint="fp_detail",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
        fsm.sub_fsm_templates["tmpl_1"] = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
            states={"s_detail": fsm.states["s_detail"]},
        )
        # Bare click — no identity field — must NOT bind to the template.
        assert fsm.is_valid_transition("s_list", {"type": "click"}) is None

        # Click with identity that matches a concrete template edge resolves.
        fsm.add_transition(
            Transition(
                source="s_list",
                target="s_detail",
                action={"type": "click", "target_text": "Item A"},
                confidence=0.9,
            )
        )
        assert fsm.is_valid_transition("s_list", {"type": "click", "target_text": "Item A"}) is True

        # Click with identity that does NOT match any concrete edge returns
        # UNCERTAIN (template_binding_missing), not MATCH.
        assert fsm.is_valid_transition("s_list", {"type": "click", "target_text": "Item Z"}) is None

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


class TestClassifyContainersStructural:
    """Structural fallback for container_type when grounder hasn't run."""

    @staticmethod
    def _fsm_with_list_page() -> AppFSM:
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s_list",
                name="ItemList",
                fingerprint="fp_list",
                structural_fingerprint="sfp_list",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                raw_screens=["rs_list"],
            )
        )
        for i in range(1, 4):
            fsm.add_state(
                AbstractState(
                    state_id=f"s_detail_{i}",
                    name=f"Detail {i}",
                    fingerprint=f"fp_detail_{i}",
                    structural_fingerprint="sfp_detail",
                    hierarchy_level=HierarchyLevel.FRAGMENT,
                )
            )
            fsm.add_transition(
                Transition(
                    source="s_list",
                    target=f"s_detail_{i}",
                    action={"type": "click", "target_text": f"Item {i}"},
                    observed_count=1,
                )
            )
        return fsm

    def test_scrollable_list_labeled_dynamic(self) -> None:
        fsm = self._fsm_with_list_page()
        builder = FsmBuilder("com.test.app")
        builder._raw_screens = {
            "rs_list": {
                "elements": [
                    {"class_name": "android.widget.RecyclerView", "is_scrollable": True},
                    {"class_name": "android.widget.TextView", "is_scrollable": False},
                ]
            }
        }
        count = builder._classify_containers_structural(fsm)
        assert count == 1
        assert fsm.states["s_list"].container_type == ContainerType.DYNAMIC

    def test_no_scrollable_not_labeled(self) -> None:
        fsm = self._fsm_with_list_page()
        builder = FsmBuilder("com.test.app")
        builder._raw_screens = {
            "rs_list": {
                "elements": [
                    {"class_name": "android.widget.TextView", "is_scrollable": False},
                ]
            }
        }
        count = builder._classify_containers_structural(fsm)
        assert count == 0
        assert fsm.states["s_list"].container_type == ContainerType.NONE

    def test_preserves_existing_label(self) -> None:
        fsm = self._fsm_with_list_page()
        fsm.states["s_list"].container_type = ContainerType.STATIC
        builder = FsmBuilder("com.test.app")
        builder._raw_screens = {
            "rs_list": {
                "elements": [
                    {"class_name": "android.widget.RecyclerView", "is_scrollable": True},
                ]
            }
        }
        count = builder._classify_containers_structural(fsm)
        assert count == 0
        assert fsm.states["s_list"].container_type == ContainerType.STATIC

    def test_template_built_without_grounder(self) -> None:
        """Classifier + template builder together produce a template on raw FSM."""
        fsm = self._fsm_with_list_page()
        builder = FsmBuilder("com.test.app")
        builder._raw_screens = {
            "rs_list": {
                "elements": [
                    {"class_name": "android.widget.RecyclerView", "is_scrollable": True},
                ]
            }
        }
        builder._classify_containers_structural(fsm)
        templates = builder._build_sub_fsm_templates(fsm)
        assert templates == 1
        tmpl = fsm.sub_fsm_templates["tmpl_s_list"]
        # Varying target_text across rows → item_name parameter.
        assert tmpl.parameter_schema == {"item_name": "string"}
        assert tmpl.item_skeleton == "sfp_detail"


# ── Canonical action identity (Sigma = <tau, q, v>) ──────────────


class TestCanonicalActionIdentity:
    @pytest.fixture
    def two_click_fsm(self) -> AppFSM:
        """One source state with two distinct click transitions to different targets."""
        fsm = AppFSM(app_package="com.test.app")
        for sid in ("s1", "s2", "s3"):
            fsm.add_state(
                AbstractState(
                    state_id=sid,
                    name=sid,
                    fingerprint=f"fp_{sid}",
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                )
            )
        fsm.add_transition(
            Transition(source="s1", target="s2", action={"type": "click", "target": "e_a"})
        )
        fsm.add_transition(
            Transition(source="s1", target="s3", action={"type": "click", "target": "e_b"})
        )
        fsm.initial_state = "s1"
        return fsm

    def test_target_disambiguates_two_click_transitions(self, two_click_fsm: AppFSM) -> None:
        assert two_click_fsm.get_transition_target("s1", {"type": "click", "target": "e_a"}) == "s2"
        assert two_click_fsm.get_transition_target("s1", {"type": "click", "target": "e_b"}) == "s3"

    def test_mismatched_target_rejected(self, two_click_fsm: AppFSM) -> None:
        # Proposed widget id matches no transition — should not collapse to type only.
        assert two_click_fsm.get_transition_target("s1", {"type": "click", "target": "e_c"}) is None

    def test_type_only_proposal_is_uncertain_for_multiple_clicks(
        self, two_click_fsm: AppFSM
    ) -> None:
        assert two_click_fsm.is_valid_transition("s1", {"type": "click"}) is None

    def test_value_disambiguates_transitions(self) -> None:
        fsm = AppFSM(app_package="com.test.app")
        for sid in ("s1", "s2", "s3"):
            fsm.add_state(
                AbstractState(
                    state_id=sid,
                    name=sid,
                    fingerprint=f"fp_{sid}",
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                )
            )
        fsm.add_transition(
            Transition(source="s1", target="s2", action={"type": "set_text", "value": "alice"})
        )
        fsm.add_transition(
            Transition(source="s1", target="s3", action={"type": "set_text", "value": "bob"})
        )
        assert fsm.get_transition_target("s1", {"type": "set_text", "value": "alice"}) == "s2"
        assert fsm.get_transition_target("s1", {"type": "set_text", "value": "bob"}) == "s3"
        assert fsm.get_transition_target("s1", {"type": "set_text", "value": "charlie"}) is None


# ── Alignment patches: AppPrior s0, action discrimination, APE refinement, provenance ──

from vigil.neuro.app_prior import ActivityInfo, AppPrior  # noqa: E402


def _write_trace(tmp_path: Path, screens: dict, traces: list) -> Path:
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "app_package": "com.test.app",
                "screens": screens,
                "traces": traces,
            }
        )
    )
    return path


def _screen(sid: str, activity: str, title: str, *, extra=None, has_modal=False):
    elems = [
        {
            "element_id": "e_title",
            "class_name": "android.widget.TextView",
            "resource_id": "com.test:id/title",
            "text": title,
            "depth": 1,
            "is_clickable": False,
        }
    ]
    if extra:
        elems.extend(extra)
    return {
        "screen_id": sid,
        "activity_name": activity,
        "metadata": {"has_modal": has_modal, "page_title": title},
        "interactable_elements": elems,
    }


def _raw_screen_from_dict(screen: dict[str, Any]) -> RawScreen:
    return RawScreen(
        screen_id=screen["screen_id"],
        activity_name=screen.get("activity_name"),
        metadata=screen.get("metadata", {}),
        elements=[
            UIElement(
                element_id=el.get("element_id", f"el_{idx}"),
                class_name=el.get("class_name", "android.view.View"),
                resource_id=el.get("resource_id"),
                text=el.get("text"),
                content_description=el.get("content_description"),
                is_clickable=el.get("is_clickable", False),
                is_long_clickable=el.get("is_long_clickable", False),
                is_scrollable=el.get("is_scrollable", False),
                is_editable=el.get("is_editable", False),
                is_checkable=el.get("is_checkable", False),
                depth=el.get("depth", 0),
            )
            for idx, el in enumerate(
                screen.get("interactable_elements", screen.get("elements", []))
            )
        ],
    )


class TestAppPriorInitialState:
    """AppPrior's launcher activity should pick s0 even when it's not first in the trace."""

    def test_launcher_activity_chooses_s0(self, tmp_path: Path) -> None:
        screens = {
            "scr_home": _screen("scr_home", "com.test.app.HomeActivity", "Home"),
            "scr_main": _screen(
                "scr_main",
                "com.test.app.MainActivity",
                "Main",
                extra=[
                    {
                        "element_id": "e_go",
                        "class_name": "android.widget.Button",
                        "resource_id": "com.test:id/go",
                        "text": "Go",
                        "depth": 2,
                        "is_clickable": True,
                    }
                ],
            ),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_home",
                "target_screen_id": "scr_main",
                "action": {"action_type": "navigate_back"},
            }
        ]
        trace = _write_trace(tmp_path, screens, traces)
        prior = AppPrior(
            package_name="com.test.app",
            entry_activity="com.test.app.MainActivity",
            activities=[
                ActivityInfo(name="com.test.app.MainActivity", is_launcher=True),
            ],
        )
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(trace, app_prior=prior)
        initial = fsm.states[fsm.initial_state]
        assert initial.activity_name == "com.test.app.MainActivity"


class TestApePriorOnlyProducesNoEdges:
    """AppPrior on its own (no real trace edges) must produce zero transitions."""

    def test_static_only_zero_edges(self, tmp_path: Path) -> None:
        screens = {
            "scr_only": _screen("scr_only", "com.test.app.MainActivity", "Main"),
        }
        trace = _write_trace(tmp_path, screens, [])
        prior = AppPrior(
            package_name="com.test.app",
            entry_activity="com.test.app.MainActivity",
            activities=[ActivityInfo(name="com.test.app.MainActivity", is_launcher=True)],
        )
        builder = FsmBuilder("com.test.app")
        fsm = builder.build_from_trace(trace, app_prior=prior)
        assert fsm.transitions == []


class TestActionSignatureDiscrimination:
    """Same action type on different widgets must yield two distinct transitions."""

    def test_two_buttons_keep_two_edges(self, tmp_path: Path) -> None:
        screens = {
            "scr_home": _screen(
                "scr_home",
                ".MainActivity",
                "Home",
                extra=[
                    {
                        "element_id": "e_a",
                        "class_name": "android.widget.Button",
                        "resource_id": "com.test:id/btn_a",
                        "text": "A",
                        "depth": 2,
                        "is_clickable": True,
                    },
                    {
                        "element_id": "e_b",
                        "class_name": "android.widget.Button",
                        "resource_id": "com.test:id/btn_b",
                        "text": "B",
                        "depth": 2,
                        "is_clickable": True,
                    },
                ],
            ),
            "scr_a": _screen("scr_a", ".PageA", "PageA"),
            "scr_b": _screen("scr_b", ".PageB", "PageB"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_home",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_a"},
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_home",
                "target_screen_id": "scr_b",
                "action": {"action_type": "click", "target_element_id": "e_b"},
            },
        ]
        trace = _write_trace(tmp_path, screens, traces)
        fsm = FsmBuilder("com.test.app").build_from_trace(trace)
        outgoing_from_home = [t for t in fsm.transitions if t.source == "s_001"]
        # Two distinct click edges, not one ambiguous merge.
        click_targets = {t.target for t in outgoing_from_home if t.action.get("type") == "click"}
        assert len(click_targets) == 2


class TestApeRefinementSplit:
    """When raw screens differ by secondary features, refinement should split."""

    def test_split_distinguishable_secondary_features(self, tmp_path: Path) -> None:
        # Two raw screens with the same page_title 'Home' (so same primary
        # fingerprint), but distinguishable secondary features: one has a
        # modal flag set, the other doesn't. Both click the same button but
        # go to different targets — APE refinement should split the source.
        click_elem = {
            "element_id": "e_go",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/btn_go",
            "text": "Go",
            "depth": 2,
            "is_clickable": True,
        }
        anchor_alpha = {
            "element_id": "e_x",
            "class_name": "T",
            "resource_id": "",
            "text": "AnchorAlpha",
            "depth": 1,
            "is_clickable": False,
        }
        anchor_beta = {
            "element_id": "e_y",
            "class_name": "T",
            "resource_id": "",
            "text": "AnchorBeta",
            "depth": 1,
            "is_clickable": False,
        }
        screens = {
            "scr_h1": _screen(
                "scr_h1",
                ".MainActivity",
                "Home",
                extra=[click_elem, anchor_alpha],
            ),
            "scr_h2": _screen(
                "scr_h2",
                ".MainActivity",
                "Home",
                extra=[click_elem, anchor_beta],
            ),
            "scr_a": _screen("scr_a", ".PageA", "PageA"),
            "scr_b": _screen("scr_b", ".PageB", "PageB"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_h1",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_h2",
                "target_screen_id": "scr_b",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
        ]
        trace = _write_trace(tmp_path, screens, traces)
        fsm = FsmBuilder("com.test.app").build_from_trace(trace)
        # Refinement must have produced a split (recorded in evolution_log).
        actions = [e.get("action") for e in fsm.evolution_log]
        assert "split" in actions, f"evolution_log={fsm.evolution_log}"

    def test_split_fingerprints_disambiguate_state_locator_and_checker(
        self, tmp_path: Path
    ) -> None:
        click_elem = {
            "element_id": "e_go",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/btn_go",
            "text": "Go",
            "depth": 2,
            "is_clickable": True,
        }
        anchor_alpha = {
            "element_id": "e_alpha",
            "class_name": "android.widget.TextView",
            "resource_id": "",
            "text": "AnchorAlpha",
            "depth": 1,
            "is_clickable": False,
        }
        anchor_beta = {
            "element_id": "e_beta",
            "class_name": "android.widget.TextView",
            "resource_id": "",
            "text": "AnchorBeta",
            "depth": 1,
            "is_clickable": False,
        }
        screens = {
            "scr_h1": _screen(
                "scr_h1",
                ".MainActivity",
                "Home",
                extra=[click_elem, anchor_alpha],
            ),
            "scr_h2": _screen(
                "scr_h2",
                ".MainActivity",
                "Home",
                extra=[click_elem, anchor_beta],
            ),
            "scr_a": _screen("scr_a", ".PageA", "PageA"),
            "scr_b": _screen("scr_b", ".PageB", "PageB"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_h1",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_h2",
                "target_screen_id": "scr_b",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
        ]
        trace = _write_trace(tmp_path, screens, traces)

        fsm = FsmBuilder("com.test.app").build_from_trace(trace)
        h1_state = next(s for s in fsm.states.values() if "scr_h1" in s.raw_screens)
        h2_state = next(s for s in fsm.states.values() if "scr_h2" in s.raw_screens)
        base_fp = FsmBuilder._compute_functional_fingerprint(screens["scr_h1"])

        assert h1_state.fingerprint != h2_state.fingerprint
        assert h1_state.structural_fingerprint != h2_state.structural_fingerprint
        assert h1_state.fingerprint != base_fp
        assert h2_state.fingerprint != base_fp

        locator = StateLocator(fsm)
        h1_loc = locator.locate(_raw_screen_from_dict(screens["scr_h1"]))
        h2_loc = locator.locate(_raw_screen_from_dict(screens["scr_h2"]))
        assert h1_loc.result is LocateResult.EXACT
        assert h1_loc.state_id == h1_state.state_id
        assert h2_loc.result is LocateResult.EXACT
        assert h2_loc.state_id == h2_state.state_id

        action = {
            "type": "click",
            "target": "e_go",
            "target_resource_id": "com.test:id/btn_go",
            "target_text": "Go",
            "target_class": "android.widget.Button",
        }
        checker = FsmChecker(fsm)
        h1_out = checker.verify(_raw_screen_from_dict(screens["scr_h1"]), action)
        h2_out = checker.verify(_raw_screen_from_dict(screens["scr_h2"]), action)
        assert h1_out.result is VerifyResult.ALLOW
        assert h1_out.current_state_id == h1_state.state_id
        assert h1_out.target_state_id == next(
            s.state_id for s in fsm.states.values() if "scr_a" in s.raw_screens
        )
        assert h2_out.result is VerifyResult.ALLOW
        assert h2_out.current_state_id == h2_state.state_id
        assert h2_out.target_state_id == next(
            s.state_id for s in fsm.states.values() if "scr_b" in s.raw_screens
        )

    def test_non_conflicting_outgoing_edges_are_partitioned_by_source_screen(
        self, tmp_path: Path
    ) -> None:
        go_elem = {
            "element_id": "e_go",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/btn_go",
            "text": "Go",
            "depth": 2,
            "is_clickable": True,
        }
        help_elem = {
            "element_id": "e_help",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/btn_help",
            "text": "Help",
            "depth": 2,
            "is_clickable": True,
        }
        anchor_alpha = {
            "element_id": "e_alpha",
            "class_name": "android.widget.TextView",
            "resource_id": "",
            "text": "AnchorAlpha",
            "depth": 1,
            "is_clickable": False,
        }
        anchor_beta = {
            "element_id": "e_beta",
            "class_name": "android.widget.TextView",
            "resource_id": "",
            "text": "AnchorBeta",
            "depth": 1,
            "is_clickable": False,
        }
        screens = {
            "scr_h1": _screen(
                "scr_h1",
                ".MainActivity",
                "Home",
                extra=[go_elem, help_elem, anchor_alpha],
            ),
            "scr_h2": _screen(
                "scr_h2",
                ".MainActivity",
                "Home",
                extra=[go_elem, help_elem, anchor_beta],
            ),
            "scr_a": _screen("scr_a", ".PageA", "PageA"),
            "scr_b": _screen("scr_b", ".PageB", "PageB"),
            "scr_help": _screen("scr_help", ".Help", "Help"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_h1",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_h2",
                "target_screen_id": "scr_b",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
            {
                "step_number": 3,
                "source_screen_id": "scr_h1",
                "target_screen_id": "scr_help",
                "action": {"action_type": "click", "target_element_id": "e_help"},
            },
        ]
        trace = _write_trace(tmp_path, screens, traces)

        fsm = FsmBuilder("com.test.app").build_from_trace(trace)
        h1_state = next(s for s in fsm.states.values() if "scr_h1" in s.raw_screens)
        h2_state = next(s for s in fsm.states.values() if "scr_h2" in s.raw_screens)
        help_edges_h1 = [
            t
            for t in fsm.transitions
            if t.source == h1_state.state_id
            and t.action.get("target_resource_id") == "com.test:id/btn_help"
        ]
        help_edges_h2 = [
            t
            for t in fsm.transitions
            if t.source == h2_state.state_id
            and t.action.get("target_resource_id") == "com.test:id/btn_help"
        ]

        assert len(help_edges_h1) == 1
        assert help_edges_h1[0].confidence == 1.0
        assert help_edges_h1[0].low_trust is False
        assert not [t for t in help_edges_h2 if t.confidence > 0.5 and not t.low_trust]


class TestApeRefinementDowngrade:
    """Fully indistinguishable screens must keep both edges but lose trust."""

    def test_indistinguishable_screens_downgrade(self, tmp_path: Path) -> None:
        click_elem = {
            "element_id": "e_go",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/btn_go",
            "text": "Go",
            "depth": 2,
            "is_clickable": True,
        }
        # Both source screens have IDENTICAL secondary features.
        screens = {
            "scr_h1": _screen("scr_h1", ".MainActivity", "Home", extra=[click_elem]),
            "scr_h2": _screen("scr_h2", ".MainActivity", "Home", extra=[click_elem]),
            "scr_a": _screen("scr_a", ".PageA", "PageA"),
            "scr_b": _screen("scr_b", ".PageB", "PageB"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_h1",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_h2",
                "target_screen_id": "scr_b",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
        ]
        trace = _write_trace(tmp_path, screens, traces)
        fsm = FsmBuilder("com.test.app").build_from_trace(trace)

        actions = [e.get("action") for e in fsm.evolution_log]
        assert "downgrade" in actions, f"evolution_log={fsm.evolution_log}"
        # Both edges remain; their confidence is capped at 0.5 and low_trust=True.
        outgoing = [t for t in fsm.transitions if t.source == "s_001"]
        # If they were merged into one ambiguous edge that would also be wrong.
        # Two distinct targets must still be reachable.
        targets = {t.target for t in outgoing}
        assert len(targets) >= 2
        for t in outgoing:
            assert t.low_trust is True
            assert t.confidence <= 0.5


class TestProvenanceNonEmpty:
    """Every transition (observed or inferred) must carry at least one provenance entry."""

    def test_every_transition_has_provenance(self, tmp_path: Path) -> None:
        screens = {
            "scr_001": _screen(
                "scr_001",
                ".MainActivity",
                "Home",
                extra=[
                    {
                        "element_id": "e_go",
                        "class_name": "android.widget.Button",
                        "resource_id": "com.test:id/btn_go",
                        "text": "Go",
                        "depth": 2,
                        "is_clickable": True,
                    }
                ],
            ),
            "scr_002": _screen("scr_002", ".PageA", "PageA"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_001",
                "target_screen_id": "scr_002",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            }
        ]
        trace = _write_trace(tmp_path, screens, traces)
        fsm = FsmBuilder("com.test.app").build_from_trace(trace)
        assert fsm.transitions
        for t in fsm.transitions:
            assert t.provenance, f"transition {t.source}→{t.target} missing provenance"
            entry = t.provenance[0]
            assert entry.confidence_source in {"observed", "inferred_dialog", "inferred_tab"}


# ── Follow-up alignment: template collapse, identity-required fallback, ──
# ── secondary-index safety, multi-signature refinement guard. ──


class TestLosslessTemplateCollapse:
    """SubFsmTemplate collapse must preserve raw_screens and re-source transitions."""

    def _build_dynamic_list_trace(self, tmp_path: Path) -> Path:
        # Container "Home" has three list rows leading to three structurally-
        # identical detail pages (same skeleton, different titles). The builder
        # should collapse the three detail states into one template
        # representative.
        click_a = {
            "element_id": "e_a",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/row_a",
            "text": "Row A",
            "depth": 2,
            "is_clickable": True,
        }
        click_b = {
            "element_id": "e_b",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/row_b",
            "text": "Row B",
            "depth": 2,
            "is_clickable": True,
        }
        click_c = {
            "element_id": "e_c",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/row_c",
            "text": "Row C",
            "depth": 2,
            "is_clickable": True,
        }
        scrollable = {
            "element_id": "e_list",
            "class_name": "android.widget.ScrollView",
            "resource_id": "com.test:id/list",
            "depth": 1,
            "is_scrollable": True,
        }
        # Detail pages must share the same structural skeleton.
        detail_skeleton = [
            {
                "element_id": "e_detail_title",
                "class_name": "android.widget.TextView",
                "resource_id": "com.test:id/detail_title",
                "depth": 1,
                "is_clickable": False,
            },
            {
                "element_id": "e_back",
                "class_name": "android.widget.ImageButton",
                "resource_id": "com.test:id/back",
                "depth": 1,
                "is_clickable": True,
            },
        ]
        screens = {
            "scr_home": _screen(
                "scr_home",
                ".HomeActivity",
                "Home",
                extra=[scrollable, click_a, click_b, click_c],
            ),
            "scr_a": {
                "screen_id": "scr_a",
                "activity_name": ".DetailActivity",
                "metadata": {"page_title": "Detail A"},
                "interactable_elements": [
                    {**el, "text": "A" if el["element_id"] == "e_detail_title" else el.get("text")}
                    for el in detail_skeleton
                ],
            },
            "scr_b": {
                "screen_id": "scr_b",
                "activity_name": ".DetailActivity",
                "metadata": {"page_title": "Detail B"},
                "interactable_elements": [
                    {**el, "text": "B" if el["element_id"] == "e_detail_title" else el.get("text")}
                    for el in detail_skeleton
                ],
            },
            "scr_c": {
                "screen_id": "scr_c",
                "activity_name": ".DetailActivity",
                "metadata": {"page_title": "Detail C"},
                "interactable_elements": [
                    {**el, "text": "C" if el["element_id"] == "e_detail_title" else el.get("text")}
                    for el in detail_skeleton
                ],
            },
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_home",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_a"},
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_home",
                "target_screen_id": "scr_b",
                "action": {"action_type": "click", "target_element_id": "e_b"},
            },
            {
                "step_number": 3,
                "source_screen_id": "scr_home",
                "target_screen_id": "scr_c",
                "action": {"action_type": "click", "target_element_id": "e_c"},
            },
        ]
        return _write_trace(tmp_path, screens, traces)

    def _build_post_collapse_conflict_trace(self, tmp_path: Path) -> Path:
        row_a = {
            "element_id": "e_a",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/shared_row",
            "text": "Row A",
            "depth": 2,
            "is_clickable": True,
        }
        row_b = {
            "element_id": "e_b",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/shared_row",
            "text": "Row B",
            "depth": 2,
            "is_clickable": True,
        }
        scrollable = {
            "element_id": "e_list",
            "class_name": "android.widget.ScrollView",
            "resource_id": "com.test:id/list",
            "depth": 1,
            "is_scrollable": True,
        }
        more_button = {
            "element_id": "e_more",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/more",
            "text": "More",
            "depth": 2,
            "is_clickable": True,
        }
        screens = {
            "scr_home": _screen(
                "scr_home",
                ".HomeActivity",
                "Home",
                extra=[scrollable, row_a, row_b],
            ),
            "scr_a": _screen(
                "scr_a",
                ".DetailActivity",
                "Detail A",
                extra=[more_button],
            ),
            "scr_b": _screen(
                "scr_b",
                ".DetailActivity",
                "Detail B",
                extra=[more_button],
            ),
            "scr_down_a": _screen("scr_down_a", ".TargetAActivity", "Target A"),
            "scr_down_b": _screen("scr_down_b", ".TargetBActivity", "Target B"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_home",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_a"},
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_home",
                "target_screen_id": "scr_b",
                "action": {"action_type": "click", "target_element_id": "e_b"},
            },
            {
                "step_number": 3,
                "source_screen_id": "scr_a",
                "target_screen_id": "scr_down_a",
                "action": {"action_type": "click", "target_element_id": "e_more"},
            },
            {
                "step_number": 4,
                "source_screen_id": "scr_b",
                "target_screen_id": "scr_down_b",
                "action": {"action_type": "click", "target_element_id": "e_more"},
            },
        ]
        return _write_trace(tmp_path, screens, traces)

    def test_collapse_preserves_raw_screens(self, tmp_path: Path) -> None:
        trace = self._build_dynamic_list_trace(tmp_path)
        fsm = FsmBuilder("com.test.app").build_from_trace(trace)
        # If a template was created, the representative absorbed the others.
        templates = list(fsm.sub_fsm_templates.values())
        if not templates:
            pytest.skip("no template produced by builder for this fixture")
        tmpl = templates[0]
        rep = fsm.states[tmpl.source_state_id]
        # The container's outgoing click edges remain, each carrying provenance.
        click_edges = [
            t
            for t in fsm.transitions
            if t.source == tmpl.source_state_id and t.action.get("type") == "click"
        ]
        assert click_edges, "container has no outgoing clicks after collapse"
        # All target detail screens must be bound to SOME existing FSM state
        # (validator inverts raw_screens, so unbound screens would surface as
        # state_not_found).
        rep_screens: set[str] = set()
        for state in fsm.states.values():
            rep_screens.update(state.raw_screens)
        # Every detail screen the trace observed must be reachable through
        # raw_screens of some surviving state.
        for sid in ("scr_a", "scr_b", "scr_c"):
            assert sid in rep_screens, f"detail screen {sid!r} lost during collapse"
        # No transition should reference a deleted state.
        live_state_ids = set(fsm.states.keys())
        for t in fsm.transitions:
            assert t.source in live_state_ids
            assert t.target in live_state_ids
        # And the rep is one of the surviving states.
        assert rep.state_id in live_state_ids

    def test_post_collapse_conflicting_outgoing_edges_are_downgraded(self, tmp_path: Path) -> None:
        trace = self._build_post_collapse_conflict_trace(tmp_path)
        fsm = FsmBuilder("com.test.app").build_from_trace(trace)
        downgrade_entries = [
            entry
            for entry in fsm.evolution_log
            if entry.get("action") == "downgrade"
            and entry.get("reason") == "post_collapse_conflict"
        ]
        assert downgrade_entries, f"evolution_log={fsm.evolution_log}"

        source_id = downgrade_entries[0]["source_state"]
        conflict_groups: dict[tuple[tuple[str, object], ...], list[Transition]] = {}
        for t in fsm.transitions:
            if t.source != source_id:
                continue
            conflict_groups.setdefault(canonical_action_key(t.action), []).append(t)
        post_collapse_conflicts = [
            group for group in conflict_groups.values() if len({t.target for t in group}) > 1
        ]
        assert post_collapse_conflicts
        for group in post_collapse_conflicts:
            for transition in group:
                assert transition.low_trust is True
                assert transition.confidence <= 0.5


class TestResolveTemplateBindingMissing:
    """resolve_transition must never return MATCH with no transition."""

    @staticmethod
    def _make_template_binding_fsm() -> AppFSM:
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
        for suffix in ("a", "b"):
            fsm.add_state(
                AbstractState(
                    state_id=f"s_detail_{suffix}",
                    name=f"Detail {suffix}",
                    fingerprint=f"fp_detail_{suffix}",
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                )
            )
        fsm.sub_fsm_templates["tmpl_1"] = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
            states={
                "s_detail_a": fsm.states["s_detail_a"],
                "s_detail_b": fsm.states["s_detail_b"],
            },
        )
        for suffix, label in (("a", "Item A"), ("b", "Item B")):
            fsm.add_transition(
                Transition(
                    source="s_list",
                    target=f"s_detail_{suffix}",
                    action={
                        "type": "click",
                        "target_resource_id": "com.test:id/shared_row",
                        "target_class": "android.widget.TextView",
                        "target_text": label,
                        "target_selector": {
                            "resource_id": "com.test:id/shared_row",
                            "text": label,
                            "class_name": "android.widget.TextView",
                        },
                    },
                    confidence=0.9,
                )
            )
        return fsm

    def test_bare_click_on_template_state_is_uncertain(self) -> None:
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
        fsm.add_state(
            AbstractState(
                state_id="s_detail",
                name="Detail",
                fingerprint="fp_detail",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
        fsm.sub_fsm_templates["tmpl_1"] = SubFsmTemplate(
            template_id="tmpl_1",
            source_state_id="s_list",
            entry_fingerprint="fp_detail",
            states={"s_detail": fsm.states["s_detail"]},
        )

        # Bare click → UNCERTAIN, never MATCH-with-None.
        lookup = fsm.resolve_transition("s_list", {"type": "click"})

        assert lookup.status is TransitionLookupStatus.UNCERTAIN
        assert "template_binding_missing" in lookup.details
        assert lookup.transition is None
        assert lookup.target_state_id is None

        # Identity that matches no concrete template edge → UNCERTAIN.
        lookup = fsm.resolve_transition("s_list", {"type": "click", "target_text": "Mystery"})
        assert lookup.status is TransitionLookupStatus.UNCERTAIN
        assert "template_binding_missing" in lookup.details

    def test_class_only_template_click_is_binding_missing(self) -> None:
        fsm = self._make_template_binding_fsm()
        lookup = fsm.resolve_transition(
            "s_list",
            {"type": "click", "target_class": "android.widget.TextView"},
        )
        assert lookup.status is TransitionLookupStatus.UNCERTAIN
        assert lookup.details == "template_binding_missing"

    def test_shared_resource_only_template_click_is_binding_missing(self) -> None:
        fsm = self._make_template_binding_fsm()
        lookup = fsm.resolve_transition(
            "s_list",
            {"type": "click", "target_resource_id": "com.test:id/shared_row"},
        )
        assert lookup.status is TransitionLookupStatus.UNCERTAIN
        assert lookup.details == "template_binding_missing"

    def test_target_text_template_click_matches_one_concrete_edge(self) -> None:
        fsm = self._make_template_binding_fsm()
        lookup = fsm.resolve_transition(
            "s_list",
            {"type": "click", "target_text": "Item A"},
        )
        assert lookup.status is TransitionLookupStatus.MATCH
        assert lookup.target_state_id == "s_detail_a"

    def test_target_selector_template_click_matches_one_concrete_edge(self) -> None:
        fsm = self._make_template_binding_fsm()
        lookup = fsm.resolve_transition(
            "s_list",
            {
                "type": "click",
                "target_selector": {
                    "resource_id": "com.test:id/shared_row",
                    "text": "Item B",
                    "class_name": "android.widget.TextView",
                },
            },
        )
        assert lookup.status is TransitionLookupStatus.MATCH
        assert lookup.target_state_id == "s_detail_b"

    def test_built_fsm_resolve_never_returns_match_with_none(self, tmp_path: Path) -> None:
        # On the real builder output, no reachable (state, action) should give
        # MATCH with no Transition.
        builder = FsmBuilder("com.test.app")
        # Reuse the lossless-collapse fixture which exercises template paths.
        trace = TestLosslessTemplateCollapse()._build_dynamic_list_trace(tmp_path)
        fsm = builder.build_from_trace(trace)

        for state_id in fsm.states:
            for t in fsm.transitions:
                if t.source != state_id:
                    continue
                lookup = fsm.resolve_transition(state_id, t.action)
                if lookup.status is TransitionLookupStatus.MATCH:
                    assert lookup.transition is not None
                    assert lookup.target_state_id is not None


class TestStateLocatorSecondarySafety:
    """Refined-secondary index must require base fingerprint compatibility."""

    def test_same_secondary_hash_different_base_does_not_match(self) -> None:
        # Hand-build an FSM with one refined sibling whose base fp is B1.
        fsm = AppFSM(app_package="com.test.app")
        # Use a string that mimics a 12-char base hash so live screens that
        # genuinely correspond can match.
        base_b1 = "b1aaaaaaaaaa"
        secondary_h = "deadbeefcafe"
        from vigil.symbolic.state_locator import _REFINED_SECONDARY_MARKER

        fsm.add_state(
            AbstractState(
                state_id="s_refined",
                name="Refined",
                fingerprint=f"{base_b1}{_REFINED_SECONDARY_MARKER}{secondary_h}",
                structural_fingerprint=f"{base_b1}{_REFINED_SECONDARY_MARKER}{secondary_h}",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )

        locator = StateLocator(fsm)

        # A live screen with a DIFFERENT primary structure but contrived to
        # produce the same secondary hash must NOT match. We exercise the
        # index directly because synthesizing a live RawScreen with a precise
        # secondary hash is fragile; the index is the safety boundary.
        assert ("b2ZZZZZZZZZZ", secondary_h) not in locator._refined_fp_index
        # And the indexed key uses the 12-char-truncated base.
        assert (base_b1, secondary_h) in locator._refined_fp_index
        assert locator._refined_fp_index[(base_b1, secondary_h)] == "s_refined"


class TestApeRefinementMultiSignatureDowngrade:
    """If any target's screens span multiple secondary signatures, downgrade."""

    def test_target_with_multiple_signatures_is_downgraded(self, tmp_path: Path) -> None:
        click_elem = {
            "element_id": "e_go",
            "class_name": "android.widget.Button",
            "resource_id": "com.test:id/btn_go",
            "text": "Go",
            "depth": 2,
            "is_clickable": True,
        }
        anchor_alpha = {
            "element_id": "e_alpha",
            "class_name": "android.widget.TextView",
            "resource_id": "",
            "text": "Alpha",
            "depth": 1,
            "is_clickable": False,
        }
        anchor_beta = {
            "element_id": "e_beta",
            "class_name": "android.widget.TextView",
            "resource_id": "",
            "text": "Beta",
            "depth": 1,
            "is_clickable": False,
        }
        anchor_gamma = {
            "element_id": "e_gamma",
            "class_name": "android.widget.TextView",
            "resource_id": "",
            "text": "Gamma",
            "depth": 1,
            "is_clickable": False,
        }
        # Same primary fingerprint across h1/h2/h3 (page_title="Home").
        # h1 and h2 both go to scr_a but have DIFFERENT secondary signatures
        # (Alpha vs Beta anchor). h3 goes to scr_b with anchor Gamma.
        # Target "scr_a" therefore spans two secondary signatures → downgrade,
        # not split.
        screens = {
            "scr_h1": _screen("scr_h1", ".MainActivity", "Home", extra=[click_elem, anchor_alpha]),
            "scr_h2": _screen("scr_h2", ".MainActivity", "Home", extra=[click_elem, anchor_beta]),
            "scr_h3": _screen("scr_h3", ".MainActivity", "Home", extra=[click_elem, anchor_gamma]),
            "scr_a": _screen("scr_a", ".PageA", "PageA"),
            "scr_b": _screen("scr_b", ".PageB", "PageB"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_h1",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
            {
                "step_number": 2,
                "source_screen_id": "scr_h2",
                "target_screen_id": "scr_a",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
            {
                "step_number": 3,
                "source_screen_id": "scr_h3",
                "target_screen_id": "scr_b",
                "action": {"action_type": "click", "target_element_id": "e_go"},
            },
        ]
        trace = _write_trace(tmp_path, screens, traces)
        fsm = FsmBuilder("com.test.app").build_from_trace(trace)
        log_actions = [(e.get("action"), e.get("reason")) for e in fsm.evolution_log]
        # Must be a downgrade with the new multi-signature reason — not a split.
        assert any(
            action == "downgrade" and reason == "target_spans_multiple_secondary_signatures"
            for action, reason in log_actions
        ), f"evolution_log={fsm.evolution_log}"


# ── Pass 3: integrity + identity preservation ──


_SETTINGS_TRACE = (
    Path(__file__).parent.parent
    / "data/apps/com_android_settings/traces/exploration_20260420_164556.json"
)


def _settings_trace_available() -> bool:
    return _SETTINGS_TRACE.exists()


class TestRealTraceIntegrity:
    """After building the Settings FSM, no template artifact may reference a
    deleted state id, and the safety-net dropped_count must be zero (the
    cumulative redirect map is the primary mechanism)."""

    @pytest.mark.skipif(
        not _settings_trace_available(),
        reason="Settings exploration trace fixture not present",
    )
    def test_no_dangling_state_references_after_real_trace_build(self) -> None:
        builder = FsmBuilder("com.android.settings")
        fsm = builder.build_from_trace(_SETTINGS_TRACE)
        live = set(fsm.states.keys())

        # 1. fsm.transitions
        for t in fsm.transitions:
            assert t.source in live, f"transition source {t.source!r} not in fsm.states"
            assert t.target in live, f"transition target {t.target!r} not in fsm.states"

        # 2. fsm.graph nodes
        for node in fsm.graph.nodes():
            assert node in live, f"graph node {node!r} not in fsm.states"

        # 3. SubFsmTemplate.source_state_id
        for tid, tmpl in fsm.sub_fsm_templates.items():
            assert (
                tmpl.source_state_id in live
            ), f"template {tid!r}: source_state_id {tmpl.source_state_id!r} not in fsm.states"

        # 4. SubFsmTemplate.states
        for tid, tmpl in fsm.sub_fsm_templates.items():
            for sid in tmpl.states:
                assert sid in live, f"template {tid!r}: states[{sid!r}] not in fsm.states"

        # 5. SubFsmTemplate.transitions
        for tid, tmpl in fsm.sub_fsm_templates.items():
            for t in tmpl.transitions:
                assert (
                    t.source in live
                ), f"template {tid!r}: transition source {t.source!r} not in fsm.states"
                assert (
                    t.target in live
                ), f"template {tid!r}: transition target {t.target!r} not in fsm.states"

        # Safety-net dropped count: must be 0 (correct redirect logic leaves
        # nothing dangling; the sweep is only a defensive backstop).
        assert getattr(builder, "_template_collapse_dropped_count", 0) == 0


class TestCascadeCollapseUpdatesPriorTemplates:
    """When template B collapses a state that template A previously held as
    its representative, template A must no longer reference the deleted id."""

    def test_cascade_collapse_updates_prior_templates(self, tmp_path: Path) -> None:
        # Two DYNAMIC containers, each with two identical-skeleton detail
        # pages. We arrange the detail-page skeletons so the FIRST container's
        # representative is itself a row of the SECOND container — triggering
        # a cascade. This is engineered via shared structural fingerprints.
        from vigil.models.fsm import (
            AbstractState,
            AppFSM,
            ContainerType,
            HierarchyLevel,
            Transition,
        )
        from vigil.neuro.fsm_builder import FsmBuilder

        fsm = AppFSM(app_package="com.test.app")
        # Container A and its three rows (all share structural_fingerprint=sfp_row).
        fsm.add_state(
            AbstractState(
                state_id="cA",
                name="ContainerA",
                fingerprint="fp_a",
                structural_fingerprint="sfp_a",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                container_type=ContainerType.DYNAMIC,
            )
        )
        for sid in ("rA1", "rA2", "rA3"):
            fsm.add_state(
                AbstractState(
                    state_id=sid,
                    name=sid,
                    fingerprint=f"fp_{sid}",
                    structural_fingerprint="sfp_row",
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                )
            )
        for sid in ("rA1", "rA2", "rA3"):
            fsm.add_transition(
                Transition(
                    source="cA",
                    target=sid,
                    action={"type": "click", "target_text": sid},
                    confidence=1.0,
                )
            )

        # Container B and its rows: one of them is rA1 (the kept rep of A).
        fsm.add_state(
            AbstractState(
                state_id="cB",
                name="ContainerB",
                fingerprint="fp_b",
                structural_fingerprint="sfp_b",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                container_type=ContainerType.DYNAMIC,
            )
        )
        fsm.add_state(
            AbstractState(
                state_id="rB1",
                name="rB1",
                fingerprint="fp_rB1",
                structural_fingerprint="sfp_row",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
        fsm.add_transition(
            Transition(
                source="cB",
                target="rA1",
                action={"type": "click", "target_text": "rA1"},
                confidence=1.0,
            )
        )
        fsm.add_transition(
            Transition(
                source="cB",
                target="rB1",
                action={"type": "click", "target_text": "rB1"},
                confidence=1.0,
            )
        )

        builder = FsmBuilder("com.test.app")
        templates_created = builder._build_sub_fsm_templates(fsm)
        assert templates_created >= 1

        live = set(fsm.states.keys())
        for tid, tmpl in fsm.sub_fsm_templates.items():
            assert (
                tmpl.source_state_id in live
            ), f"template {tid!r}.source_state_id={tmpl.source_state_id!r} dangling"
            for sid in tmpl.states:
                assert sid in live, f"template {tid!r}.states[{sid!r}] dangling"
            for t in tmpl.transitions:
                assert (
                    t.source in live and t.target in live
                ), f"template {tid!r} has a dangling transition"
        for t in fsm.transitions:
            assert t.source in live and t.target in live


class TestObservedTargetTextPreserved:
    """_build_transitions must never overwrite a populated identity field
    from the raw trace action with an empty element-derived value."""

    def test_observed_target_text_preserved_when_element_text_empty(self, tmp_path: Path) -> None:
        # Action carries target_text="Network & internet"; the looked-up
        # element has empty text and content_description. The stored
        # transition action must keep target_text intact.
        screens = {
            "scr_001": {
                "screen_id": "scr_001",
                "activity_name": ".MainActivity",
                "metadata": {"page_title": "Settings"},
                "interactable_elements": [
                    {
                        "element_id": "e_row",
                        "class_name": "android.widget.LinearLayout",
                        "resource_id": "com.test:id/row",
                        # Deliberately empty text / content_description.
                        "text": "",
                        "content_description": "",
                        "depth": 2,
                        "is_clickable": True,
                    }
                ],
            },
            "scr_002": _screen("scr_002", ".DetailActivity", "Network details"),
        }
        traces = [
            {
                "step_number": 1,
                "source_screen_id": "scr_001",
                "target_screen_id": "scr_002",
                "action": {
                    "action_type": "click",
                    "target_element_id": "e_row",
                    "target_text": "Network & internet",
                },
            }
        ]
        path = _write_trace(tmp_path, screens, traces)
        fsm = FsmBuilder("com.test.app").build_from_trace(path)
        observed = [t for t in fsm.transitions if t.action.get("type") == "click"]
        assert observed, "expected one observed click transition"
        assert (
            observed[0].action.get("target_text") == "Network & internet"
        ), f"target_text was wiped: {observed[0].action!r}"
