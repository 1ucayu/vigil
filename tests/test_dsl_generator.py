"""Tests for vigil.neuro.dsl_generator — Stage 4 DSL guard generation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vigil.core.config import VigilConfig
from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    HierarchyLevel,
    Transition,
)
from vigil.neuro.dsl_generator import DslGenerator, TransitionCategory


@pytest.fixture()
def synthetic_fsm_and_trace(tmp_path: Path) -> tuple[AppFSM, Path]:
    """Build a 3-state FSM with synthetic trace data."""
    fsm = AppFSM("com.test.app")

    s1 = AbstractState(
        state_id="s1",
        name="MainSettings",
        fingerprint="fp_main",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        raw_screens=["scr_001"],
    )
    s2 = AbstractState(
        state_id="s2",
        name="WiFiSettings",
        fingerprint="fp_wifi",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        raw_screens=["scr_002"],
        container_type=ContainerType.CONTENT,
    )
    s3 = AbstractState(
        state_id="s3",
        name="WiFiDetail",
        fingerprint="fp_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        raw_screens=["scr_003"],
    )
    for s in (s1, s2, s3):
        fsm.add_state(s)
    fsm.initial_state = "s1"

    t1 = Transition(
        source="s1",
        target="s2",
        action={"type": "click", "target": "e_001"},
        confidence=1.0,
        observed_count=3,
    )
    t2 = Transition(
        source="s2",
        target="s3",
        action={"type": "click", "target": "e_010"},
        confidence=1.0,
        observed_count=2,
    )
    t3 = Transition(
        source="s2",
        target="s1",
        action={"type": "navigate_back"},
        confidence=1.0,
        observed_count=2,
    )
    t4 = Transition(
        source="s1",
        target="s1",
        action={"type": "scroll_down"},
        confidence=1.0,
        observed_count=1,
    )
    for t in (t1, t2, t3, t4):
        fsm.add_transition(t)

    # Create screenshot files
    from PIL import Image

    for name in ("scr_001.png", "scr_002.png", "scr_003.png"):
        img = Image.new("RGB", (100, 100), color=(200, 200, 200))
        img.save(tmp_path / name)

    trace_data = {
        "app_package": "com.test.app",
        "screens": {
            "scr_001": {
                "screen_id": "scr_001",
                "screenshot_path": str(tmp_path / "scr_001.png"),
                "elements": [
                    {
                        "element_id": "e_001",
                        "class_name": "android.widget.TextView",
                        "resource_id": "com.test:id/wifi_item",
                        "text": "Wi-Fi",
                        "is_clickable": True,
                        "is_checkable": False,
                        "is_checked": False,
                    },
                    {
                        "element_id": "e_002",
                        "class_name": "android.widget.Switch",
                        "resource_id": "com.test:id/toggle",
                        "text": "",
                        "is_clickable": True,
                        "is_checkable": True,
                        "is_checked": False,
                    },
                ],
            },
            "scr_002": {
                "screen_id": "scr_002",
                "screenshot_path": str(tmp_path / "scr_002.png"),
                "elements": [
                    {
                        "element_id": "e_010",
                        "class_name": "android.widget.TextView",
                        "resource_id": "com.test:id/network_name",
                        "text": "HKU_WiFi",
                        "is_clickable": True,
                        "is_checkable": False,
                        "is_checked": False,
                    },
                ],
            },
            "scr_003": {
                "screen_id": "scr_003",
                "screenshot_path": str(tmp_path / "scr_003.png"),
                "elements": [
                    {
                        "element_id": "e_020",
                        "class_name": "android.widget.Button",
                        "resource_id": "com.test:id/connect_btn",
                        "text": "Connect",
                        "is_clickable": True,
                        "is_checkable": False,
                        "is_checked": False,
                    },
                ],
            },
        },
        "traces": [],
    }
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(trace_data))

    return fsm, trace_path


def _make_generator(fsm: AppFSM) -> DslGenerator:
    """Create a DslGenerator with mocked LlmClient."""
    config = VigilConfig()
    with patch("vigil.neuro.dsl_generator.LlmClient"):
        gen = DslGenerator(fsm, config)
    return gen


# -----------------------------------------------------------------------
# Test: direct generate_guard calls (backward compat, no category)
# -----------------------------------------------------------------------


class TestGenerateGuard:
    """Test single guard generation (generic, no category)."""

    def test_generate_valid_guard(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate_with_images.return_value = "read(com.test:id/toggle, is_checked) == false"

        t = fsm.transitions[0]  # s1→s2 click
        guard = gen.generate_guard(
            t,
            fsm.states["s1"],
            fsm.states["s2"],
            [
                {"element_id": "e_001", "resource_id": "com.test:id/wifi_item", "text": "Wi-Fi"},
                {"element_id": "e_002", "resource_id": "com.test:id/toggle", "text": ""},
            ],
            [],
            source_screenshot=Path(str(trace_path.parent / "scr_001.png")),
            target_screenshot=Path(str(trace_path.parent / "scr_002.png")),
        )
        assert guard == "read(com.test:id/toggle, is_checked) == false"

    def test_generate_null_guard(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "null"

        t = fsm.transitions[0]
        guard = gen.generate_guard(t, fsm.states["s1"], fsm.states["s2"], [], [])
        assert guard is None

    def test_generate_with_retry(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.side_effect = [
            "read(bad syntax",
            "read(com.test:id/toggle, is_checked) == false",
        ]

        elements = [
            {"element_id": "e_001", "resource_id": "com.test:id/toggle"},
        ]
        t = fsm.transitions[0]
        guard = gen.generate_guard(t, fsm.states["s1"], fsm.states["s2"], elements, [])
        assert guard == "read(com.test:id/toggle, is_checked) == false"
        assert gen._llm.generate.call_count == 2

    def test_all_retries_fail(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "totally invalid {{{"

        t = fsm.transitions[0]
        guard = gen.generate_guard(t, fsm.states["s1"], fsm.states["s2"], [], [])
        assert guard is None
        # 1 initial + 2 retries = 3
        assert gen._llm.generate.call_count == 3

    def test_intent_variable_guard(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "action(target_text) == $intent.wifi_name"

        t = fsm.transitions[0]
        guard = gen.generate_guard(t, fsm.states["s1"], fsm.states["s2"], [], [])
        assert guard == "action(target_text) == $intent.wifi_name"


# -----------------------------------------------------------------------
# Test: skip actions via classification
# -----------------------------------------------------------------------


class TestSkipActions:
    """Test that navigation/scroll actions are skipped."""

    def test_skip_navigate_back(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "null"
        gen._llm.generate_with_images.return_value = "null"

        gen.generate_all_guards(trace_path)

        back_trans = [t for t in fsm.transitions if t.action.get("type") == "navigate_back"]
        assert len(back_trans) == 1
        assert back_trans[0].guard is None

    def test_skip_scroll(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "null"
        gen._llm.generate_with_images.return_value = "null"

        gen.generate_all_guards(trace_path)

        scroll_trans = [t for t in fsm.transitions if t.action.get("type") == "scroll_down"]
        assert len(scroll_trans) == 1
        assert scroll_trans[0].guard is None


# -----------------------------------------------------------------------
# Test: bulk guard generation
# -----------------------------------------------------------------------


class TestGenerateAllGuards:
    """Test bulk guard generation with classification routing."""

    def test_generate_all_guards(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        # Only content_selection (t2: s2→s3 in CONTENT state) triggers LLM
        gen._llm.generate_with_images.return_value = "action(target_text) == $intent.wifi_name"

        result = gen.generate_all_guards(trace_path, use_images=True)

        assert result is fsm
        click_trans = [t for t in fsm.transitions if t.action.get("type") == "click"]
        guards = [t.guard for t in click_trans]
        # t1 (structural nav) → None, t2 (content selection) → guard
        assert "action(target_text) == $intent.wifi_name" in guards
        assert None in guards

    def test_fallback_to_text_only(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "action(target_text) == $intent.wifi_name"

        gen.generate_all_guards(trace_path, use_images=False)

        # Only content_selection transition calls LLM (1 call, text-only)
        assert gen._llm.generate.call_count == 1
        gen._llm.generate_with_images.assert_not_called()


# -----------------------------------------------------------------------
# Test: screenshot resolution
# -----------------------------------------------------------------------


class TestScreenshotResolution:
    """Test screenshot path resolution."""

    def test_screenshot_resolution(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        trace_data = json.loads(trace_path.read_text())

        path = gen._resolve_screenshot(fsm.states["s1"], trace_data)
        assert path is not None
        assert path.exists()
        assert path.name == "scr_001.png"

    def test_screenshot_missing_file(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        trace_data = json.loads(trace_path.read_text())
        trace_data["screens"]["scr_001"]["screenshot_path"] = "/nonexistent/path.png"

        path = gen._resolve_screenshot(fsm.states["s1"], trace_data)
        assert path is None

    def test_screenshot_no_raw_screens(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        trace_data = json.loads(trace_path.read_text())
        fsm.states["s1"].raw_screens = []

        path = gen._resolve_screenshot(fsm.states["s1"], trace_data)
        assert path is None


# -----------------------------------------------------------------------
# Test: guard validation pipeline (Problems 1-3)
# -----------------------------------------------------------------------


class TestGuardValidationPipeline:
    """Test the multi-stage guard validation (Problems 1-3)."""

    def test_reject_ephemeral_element_id(self, synthetic_fsm_and_trace: tuple) -> None:
        """Ephemeral e_XXXX IDs should be rejected and retried."""
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.side_effect = [
            "read(e_0037, is_checked) == false",
            "read(com.test:id/toggle, is_checked) == false",
        ]

        elements = [
            {"element_id": "e_001", "resource_id": "com.test:id/toggle"},
        ]
        t = fsm.transitions[0]
        guard = gen.generate_guard(t, fsm.states["s1"], fsm.states["s2"], elements, [])

        assert guard == "read(com.test:id/toggle, is_checked) == false"
        assert gen._llm.generate.call_count == 2

    def test_reject_invented_element_name(self, synthetic_fsm_and_trace: tuple) -> None:
        """Made-up element names not in the element list should be rejected."""
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.side_effect = [
            "value(rename_input_field) == $intent.name",
            "value(com.test:id/toggle) == $intent.name",
        ]

        elements = [
            {"element_id": "e_001", "resource_id": "com.test:id/toggle"},
        ]
        t = fsm.transitions[0]
        guard = gen.generate_guard(t, fsm.states["s1"], fsm.states["s2"], elements, [])

        assert guard == "value(com.test:id/toggle) == $intent.name"
        assert gen._llm.generate.call_count == 2

    def test_auto_fix_quoted_identifiers(self, synthetic_fsm_and_trace: tuple) -> None:
        """Quoted identifiers should be auto-stripped without retry."""
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = 'read("com.test:id/toggle", "is_checked") == false'

        elements = [
            {"element_id": "e_001", "resource_id": "com.test:id/toggle"},
        ]
        t = fsm.transitions[0]
        guard = gen.generate_guard(t, fsm.states["s1"], fsm.states["s2"], elements, [])

        assert guard == "read(com.test:id/toggle, is_checked) == false"
        assert gen._llm.generate.call_count == 1

    def test_all_validations_pass(self, synthetic_fsm_and_trace: tuple) -> None:
        """Clean guard with valid resource_id should pass all checks."""
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "read(com.test:id/toggle, is_checked) == false"

        elements = [
            {"element_id": "e_001", "resource_id": "com.test:id/toggle"},
        ]
        t = fsm.transitions[0]
        guard = gen.generate_guard(t, fsm.states["s1"], fsm.states["s2"], elements, [])

        assert guard == "read(com.test:id/toggle, is_checked) == false"
        assert gen._llm.generate.call_count == 1


# -----------------------------------------------------------------------
# Test: transition classification
# -----------------------------------------------------------------------


_DEFAULT_TARGET = AbstractState(
    state_id="s_tgt",
    name="Target",
    fingerprint="fp_tgt",
    hierarchy_level=HierarchyLevel.FRAGMENT,
)


class TestClassifyTransition:
    """Test _classify_transition logic."""

    def test_classify_back_navigation(self) -> None:
        """navigate_back action type -> BACK_NAVIGATION."""
        t = Transition(source="s1", target="s2", action={"type": "navigate_back"}, confidence=1.0)
        state = AbstractState(
            state_id="s1", name="S1", fingerprint="fp", hierarchy_level=HierarchyLevel.ACTIVITY
        )
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, [])
            == TransitionCategory.BACK_NAVIGATION
        )

    def test_classify_scroll(self) -> None:
        """scroll_up action type -> SCROLL."""
        t = Transition(source="s1", target="s1", action={"type": "scroll_up"}, confidence=1.0)
        state = AbstractState(
            state_id="s1", name="S1", fingerprint="fp", hierarchy_level=HierarchyLevel.ACTIVITY
        )
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, [])
            == TransitionCategory.SCROLL
        )

    def test_classify_content_selection(self) -> None:
        """Click in a state with container_type=CONTENT -> CONTENT_SELECTION."""
        t = Transition(
            source="s1", target="s2", action={"type": "click", "target": "e_001"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1",
            name="WiFiList",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.FRAGMENT,
            container_type=ContainerType.CONTENT,
        )
        elements = [
            {
                "element_id": "e_001",
                "class_name": "android.widget.TextView",
                "is_clickable": True,
                "bounds": [0, 400, 500, 500],
            }
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.CONTENT_SELECTION
        )

    def test_classify_state_mutation_toggle(self) -> None:
        """Click on checkable element -> STATE_MUTATION."""
        t = Transition(
            source="s1", target="s1", action={"type": "click", "target": "e_002"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1", name="S1", fingerprint="fp", hierarchy_level=HierarchyLevel.ACTIVITY
        )
        elements = [
            {
                "element_id": "e_002",
                "class_name": "android.widget.Switch",
                "is_clickable": True,
                "is_checkable": True,
            },
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.STATE_MUTATION
        )

    def test_classify_state_mutation_input(self) -> None:
        """State with EditText + Button click -> STATE_MUTATION (Rule 6)."""
        t = Transition(
            source="s1", target="s1", action={"type": "click", "target": "e_004"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1", name="Rename", fingerprint="fp", hierarchy_level=HierarchyLevel.ACTIVITY
        )
        elements = [
            {
                "element_id": "e_003",
                "class_name": "android.widget.EditText",
                "is_clickable": True,
                "is_editable": True,
            },
            {
                "element_id": "e_004",
                "class_name": "android.widget.Button",
                "is_clickable": True,
                "text": "OK",
            },
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.STATE_MUTATION
        )

    def test_classify_structural_navigation(self) -> None:
        """Click on unique menu item in non-content state -> STRUCTURAL_NAVIGATION."""
        t = Transition(
            source="s1", target="s2", action={"type": "click", "target": "e_001"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1", name="S1", fingerprint="fp", hierarchy_level=HierarchyLevel.ACTIVITY
        )
        elements = [
            {
                "element_id": "e_001",
                "class_name": "android.widget.TextView",
                "is_clickable": True,
                "text": "Settings",
            },
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.STRUCTURAL_NAVIGATION
        )

    def test_classify_back_arrow_button(self) -> None:
        """Small bounds (< 200x200), top-left (y < 300, x < 300), no text -> BACK_NAVIGATION."""
        t = Transition(
            source="s1",
            target="s2",
            action={"type": "click", "target": "e_001"},
            confidence=1.0,
        )
        state = AbstractState(
            state_id="s1", name="S1", fingerprint="fp", hierarchy_level=HierarchyLevel.ACTIVITY
        )
        elements = [
            {
                "element_id": "e_001",
                "class_name": "android.widget.ImageButton",
                "is_clickable": True,
                "text": "",
                "bounds": [0, 0, 100, 100],
            },
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.BACK_NAVIGATION
        )

    def test_classify_homogeneous_list(self) -> None:
        """5+ same-class clickable elements at same depth -> CONTENT_SELECTION."""
        t = Transition(
            source="s1", target="s2", action={"type": "click", "target": "e_001"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1", name="S1", fingerprint="fp", hierarchy_level=HierarchyLevel.ACTIVITY
        )
        elements = [
            {
                "element_id": f"e_00{i}",
                "class_name": "android.widget.TextView",
                "is_clickable": True,
                "text": f"Item {i}",
                "depth": 7,
            }
            for i in range(1, 6)
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.CONTENT_SELECTION
        )

    def test_classify_dialog_state_by_name(self) -> None:
        """State named 'Pair with X?' with button elements -> STATE_MUTATION."""
        t = Transition(
            source="s1", target="s2", action={"type": "click", "target": "e_010"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1",
            name="Pair with U-ACG0AB4?",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.FRAGMENT,
        )
        elements = [
            {
                "element_id": "e_010",
                "class_name": "android.widget.Button",
                "is_clickable": True,
                "text": "Pair",
            },
            {
                "element_id": "e_011",
                "class_name": "android.widget.Button",
                "is_clickable": True,
                "text": "Cancel",
            },
            {
                "element_id": "e_012",
                "class_name": "android.widget.CheckBox",
                "is_clickable": True,
                "is_checkable": True,
                "text": "Allow access",
            },
        ]
        # The target element (e_010) is a Button, not checkable.
        # Rule 5 (dialog by name) fires first because "pair with" is in the name.
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.STATE_MUTATION
        )

    def test_classify_dialog_state_by_buttons(self) -> None:
        """State with <=5 elements including OK/Cancel buttons -> STATE_MUTATION."""
        t = Transition(
            source="s1", target="s2", action={"type": "click", "target": "e_020"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1",
            name="SomeDialog",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.FRAGMENT,
        )
        elements = [
            {
                "element_id": "e_020",
                "class_name": "android.widget.Button",
                "is_clickable": True,
                "text": "OK",
            },
            {
                "element_id": "e_021",
                "class_name": "android.widget.Button",
                "is_clickable": True,
                "text": "Cancel",
            },
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.STATE_MUTATION
        )

    def test_classify_input_confirmation(self) -> None:
        """State with EditText + confirm Button -> STATE_MUTATION (Rule 6)."""
        t = Transition(
            source="s1", target="s2", action={"type": "click", "target": "e_031"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1",
            name="RenameDevice",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.FRAGMENT,
        )
        elements = [
            {
                "element_id": "e_030",
                "class_name": "android.widget.EditText",
                "is_clickable": True,
                "is_editable": True,
                "text": "",
            },
            {
                "element_id": "e_031",
                "class_name": "android.widget.Button",
                "is_clickable": True,
                "text": "Save",
            },
            {
                "element_id": "e_032",
                "class_name": "android.widget.Button",
                "is_clickable": True,
                "text": "Cancel",
            },
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.STATE_MUTATION
        )

    def test_classify_content_toolbar_excluded(self) -> None:
        """CONTENT container but element at y<300 -> STRUCTURAL_NAVIGATION (toolbar)."""
        t = Transition(
            source="s1", target="s2", action={"type": "click", "target": "e_040"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1",
            name="WiFiList",
            fingerprint="fp",
            hierarchy_level=HierarchyLevel.FRAGMENT,
            container_type=ContainerType.CONTENT,
        )
        elements = [
            {
                "element_id": "e_040",
                "class_name": "android.widget.ImageView",
                "is_clickable": True,
                "text": "",
                "bounds": [900, 100, 1000, 200],
            },
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.STRUCTURAL_NAVIGATION
        )

    def test_classify_few_siblings_structural(self) -> None:
        """Only 3 same-class (< min_count=5) at same depth -> STRUCTURAL_NAVIGATION."""
        t = Transition(
            source="s1", target="s2", action={"type": "click", "target": "e_001"}, confidence=1.0
        )
        state = AbstractState(
            state_id="s1", name="S1", fingerprint="fp", hierarchy_level=HierarchyLevel.ACTIVITY
        )
        elements = [
            {
                "element_id": f"e_00{i}",
                "class_name": "android.widget.TextView",
                "is_clickable": True,
                "text": f"Item {i}",
                "depth": 7,
            }
            for i in range(1, 4)
        ]
        assert (
            DslGenerator._classify_transition(t, state, _DEFAULT_TARGET, elements)
            == TransitionCategory.STRUCTURAL_NAVIGATION
        )


# -----------------------------------------------------------------------
# Test: element reference table
# -----------------------------------------------------------------------


class TestElementReferenceTable:
    """Test _build_element_reference_table alias generation."""

    def test_element_with_resource_id(self) -> None:
        """Element with resource_id → alias = resource_id."""
        elements = [
            {
                "element_id": "e_001",
                "resource_id": "com.app:id/btn",
                "class_name": "android.widget.Button",
            },
        ]
        result = DslGenerator._build_element_reference_table(elements)
        assert result[0]["_alias"] == "com.app:id/btn"

    def test_element_without_resource_id(self) -> None:
        """Element without resource_id → alias = ShortClass_0."""
        elements = [
            {"element_id": "e_001", "class_name": "android.widget.Switch"},
        ]
        result = DslGenerator._build_element_reference_table(elements)
        assert result[0]["_alias"] == "Switch_0"

    def test_multiple_same_class(self) -> None:
        """Two Switches without resource_id → Switch_0, Switch_1."""
        elements = [
            {"element_id": "e_001", "class_name": "android.widget.Switch"},
            {"element_id": "e_002", "class_name": "android.widget.Switch"},
        ]
        result = DslGenerator._build_element_reference_table(elements)
        assert result[0]["_alias"] == "Switch_0"
        assert result[1]["_alias"] == "Switch_1"

    def test_mixed_resource_and_synthesized(self) -> None:
        """Elements with resource_id don't affect synthesized alias counters."""
        elements = [
            {
                "element_id": "e_001",
                "resource_id": "com.app:id/sw1",
                "class_name": "android.widget.Switch",
            },
            {"element_id": "e_002", "class_name": "android.widget.Switch"},
            {"element_id": "e_003", "class_name": "android.widget.Switch"},
        ]
        result = DslGenerator._build_element_reference_table(elements)
        assert result[0]["_alias"] == "com.app:id/sw1"
        assert result[1]["_alias"] == "Switch_0"
        assert result[2]["_alias"] == "Switch_1"


# -----------------------------------------------------------------------
# Test: category-specific guard generation
# -----------------------------------------------------------------------


class TestContentSelectionGuard:
    """Test content selection guard generation."""

    def test_content_selection_must_have_guard(self, synthetic_fsm_and_trace: tuple) -> None:
        """Content selection returns guard even if LLM returns null."""
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "null"

        t = fsm.transitions[1]  # s2→s3 (content state)
        guard = gen.generate_guard(
            t,
            fsm.states["s2"],
            fsm.states["s3"],
            [],
            [],
            category=TransitionCategory.CONTENT_SELECTION,
        )
        assert guard == "action(target_text) == $intent.selected_item"

    def test_content_selection_llm_guard(self, synthetic_fsm_and_trace: tuple) -> None:
        """Content selection uses LLM-generated guard when valid."""
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "action(target_text) == $intent.wifi_name"

        t = fsm.transitions[1]
        guard = gen.generate_guard(
            t,
            fsm.states["s2"],
            fsm.states["s3"],
            [],
            [],
            category=TransitionCategory.CONTENT_SELECTION,
        )
        assert guard == "action(target_text) == $intent.wifi_name"


class TestStateMutationGuard:
    """Test state mutation guard generation."""

    def test_state_mutation_generates_toggle_guard(self, synthetic_fsm_and_trace: tuple) -> None:
        """State mutation with Switch_0 alias accepted."""
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "read(Switch_0, is_checked) == false"

        elements = [
            {
                "element_id": "e_002",
                "class_name": "android.widget.Switch",
                "is_clickable": True,
                "is_checkable": True,
            },
        ]
        t = Transition(
            source="s1",
            target="s1",
            action={"type": "click", "target": "e_002"},
            confidence=1.0,
        )
        guard = gen.generate_guard(
            t,
            fsm.states["s1"],
            fsm.states["s1"],
            elements,
            [],
            category=TransitionCategory.STATE_MUTATION,
        )
        assert guard == "read(Switch_0, is_checked) == false"

    def test_state_mutation_generates_input_guard(self, synthetic_fsm_and_trace: tuple) -> None:
        """State mutation with EditText_0 alias accepted."""
        fsm, _trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "value(EditText_0) == $intent.device_name"

        elements = [
            {
                "element_id": "e_003",
                "class_name": "android.widget.EditText",
                "is_clickable": True,
                "is_editable": True,
            },
        ]
        t = Transition(
            source="s1",
            target="s1",
            action={"type": "click", "target": "e_003"},
            confidence=1.0,
        )
        guard = gen.generate_guard(
            t,
            fsm.states["s1"],
            fsm.states["s1"],
            elements,
            [],
            category=TransitionCategory.STATE_MUTATION,
        )
        assert guard == "value(EditText_0) == $intent.device_name"

    def test_structural_nav_skips_llm(self, synthetic_fsm_and_trace: tuple) -> None:
        """Structural navigation via generate_all_guards skips LLM."""
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate_with_images.return_value = "null"

        gen.generate_all_guards(trace_path)

        # t1 (s1→s2 click on menu item) is structural nav → null, no LLM needed
        t1 = fsm.transitions[0]
        assert t1.guard is None


# -----------------------------------------------------------------------
# Test: integration — classification-based generate_all_guards
# -----------------------------------------------------------------------


class TestClassificationIntegration:
    """Test generate_all_guards with mixed transition categories."""

    def test_generate_all_guards_with_classification(self, tmp_path: Path) -> None:
        """5 transitions: 1 content, 1 mutation, 1 structural, 1 back, 1 scroll."""
        fsm = AppFSM("com.test.app")

        s1 = AbstractState(
            state_id="s1",
            name="Home",
            fingerprint="fp1",
            hierarchy_level=HierarchyLevel.ACTIVITY,
            raw_screens=["scr_001"],
        )
        s2 = AbstractState(
            state_id="s2",
            name="WiFiList",
            fingerprint="fp2",
            hierarchy_level=HierarchyLevel.FRAGMENT,
            raw_screens=["scr_002"],
            container_type=ContainerType.CONTENT,
        )
        s3 = AbstractState(
            state_id="s3",
            name="Detail",
            fingerprint="fp3",
            hierarchy_level=HierarchyLevel.FRAGMENT,
            raw_screens=["scr_003"],
        )
        for s in (s1, s2, s3):
            fsm.add_state(s)
        fsm.initial_state = "s1"

        transitions = [
            # structural nav: menu click (unique class in non-content state)
            Transition(
                source="s1",
                target="s2",
                action={"type": "click", "target": "e_001"},
                confidence=1.0,
            ),
            # content selection: click in CONTENT state
            Transition(
                source="s2",
                target="s3",
                action={"type": "click", "target": "e_010"},
                confidence=1.0,
            ),
            # state mutation: toggle switch
            Transition(
                source="s1",
                target="s1",
                action={"type": "click", "target": "e_002"},
                confidence=1.0,
            ),
            # back navigation
            Transition(
                source="s2",
                target="s1",
                action={"type": "navigate_back"},
                confidence=1.0,
            ),
            # scroll
            Transition(
                source="s1",
                target="s1",
                action={"type": "scroll_down"},
                confidence=1.0,
            ),
        ]
        for t in transitions:
            fsm.add_transition(t)

        # Create screenshots
        from PIL import Image

        for name in ("scr_001.png", "scr_002.png", "scr_003.png"):
            img = Image.new("RGB", (100, 100), color=(200, 200, 200))
            img.save(tmp_path / name)

        trace_data = {
            "app_package": "com.test.app",
            "screens": {
                "scr_001": {
                    "screen_id": "scr_001",
                    "screenshot_path": str(tmp_path / "scr_001.png"),
                    "elements": [
                        {
                            "element_id": "e_001",
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/wifi_item",
                            "text": "Wi-Fi",
                            "is_clickable": True,
                        },
                        {
                            "element_id": "e_002",
                            "class_name": "android.widget.Switch",
                            "resource_id": "",
                            "text": "",
                            "is_clickable": True,
                            "is_checkable": True,
                            "is_checked": False,
                        },
                    ],
                },
                "scr_002": {
                    "screen_id": "scr_002",
                    "screenshot_path": str(tmp_path / "scr_002.png"),
                    "elements": [
                        {
                            "element_id": "e_010",
                            "class_name": "android.widget.TextView",
                            "resource_id": "com.test:id/network",
                            "text": "HKU_WiFi",
                            "is_clickable": True,
                        },
                    ],
                },
                "scr_003": {
                    "screen_id": "scr_003",
                    "screenshot_path": str(tmp_path / "scr_003.png"),
                    "elements": [],
                },
            },
            "traces": [],
        }
        trace_path = tmp_path / "trace.json"
        trace_path.write_text(json.dumps(trace_data))

        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        # 2 LLM calls: content_selection, state_mutation
        gen._llm.generate_with_images.side_effect = [
            "action(target_text) == $intent.wifi_name",
            "read(Switch_0, is_checked) == false",
        ]

        result = gen.generate_all_guards(trace_path, use_images=True)

        assert result is fsm
        # Verify: 2 guards generated, 3 skipped
        guards = [t.guard for t in fsm.transitions]
        assert guards[0] is None  # structural nav
        assert guards[1] == "action(target_text) == $intent.wifi_name"  # content
        assert guards[2] == "read(Switch_0, is_checked) == false"  # mutation
        assert guards[3] is None  # back nav
        assert guards[4] is None  # scroll
        # LLM called exactly 2 times
        assert gen._llm.generate_with_images.call_count == 2
