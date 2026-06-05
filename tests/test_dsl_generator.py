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
    HierarchyLevel,
    Transition,
)
from vigil.neuro.dsl_generator import DslGenerator


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
# Test: direct generate_guard calls
# -----------------------------------------------------------------------


class TestGenerateGuard:
    """Test single guard generation."""

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
# Test: skip actions
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
    """Test bulk guard generation."""

    def test_click_transitions_get_guards(self, synthetic_fsm_and_trace: tuple) -> None:
        """All click transitions call LLM for guard generation."""
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate_with_images.return_value = "action(target_text) == $intent.target_setting"

        result = gen.generate_all_guards(trace_path, use_images=True)

        assert result is fsm
        click_trans = [t for t in fsm.transitions if t.action.get("type") == "click"]
        # Both click transitions should have guards
        for t in click_trans:
            assert t.guard is not None

    def test_all_clicks_call_llm(self, synthetic_fsm_and_trace: tuple) -> None:
        """2 click transitions → 2 LLM calls."""
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate_with_images.return_value = "action(target_text) == $intent.target_setting"

        gen.generate_all_guards(trace_path, use_images=True)

        assert gen._llm.generate_with_images.call_count == 2

    def test_fallback_to_text_only(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate.return_value = "action(target_text) == $intent.target_setting"

        gen.generate_all_guards(trace_path, use_images=False)

        # 2 click transitions → 2 text-only LLM calls
        assert gen._llm.generate.call_count == 2
        gen._llm.generate_with_images.assert_not_called()

    def test_back_and_scroll_skipped(self, synthetic_fsm_and_trace: tuple) -> None:
        """navigate_back and scroll_down get guard=None without LLM."""
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        gen._llm = MagicMock()
        gen._llm.generate_with_images.return_value = "null"

        gen.generate_all_guards(trace_path)

        back = [t for t in fsm.transitions if t.action.get("type") == "navigate_back"]
        scroll = [t for t in fsm.transitions if t.action.get("type") == "scroll_down"]
        assert back[0].guard is None
        assert scroll[0].guard is None


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
        fsm.states["s1"].evidence.raw_screen_ids = []

        path = gen._resolve_screenshot(fsm.states["s1"], trace_data)
        assert path is None


# -----------------------------------------------------------------------
# Test: guard validation pipeline
# -----------------------------------------------------------------------


class TestGuardValidationPipeline:
    """Test the multi-stage guard validation."""

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
# Test: element reference table
# -----------------------------------------------------------------------


class TestElementReferenceTable:
    """Test _build_element_reference_table alias generation."""

    def test_element_with_resource_id(self) -> None:
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
        elements = [
            {"element_id": "e_001", "class_name": "android.widget.Switch"},
        ]
        result = DslGenerator._build_element_reference_table(elements)
        assert result[0]["_alias"] == "Switch_0"

    def test_multiple_same_class(self) -> None:
        elements = [
            {"element_id": "e_001", "class_name": "android.widget.Switch"},
            {"element_id": "e_002", "class_name": "android.widget.Switch"},
        ]
        result = DslGenerator._build_element_reference_table(elements)
        assert result[0]["_alias"] == "Switch_0"
        assert result[1]["_alias"] == "Switch_1"

    def test_mixed_resource_and_synthesized(self) -> None:
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
# Test: compute diff
# -----------------------------------------------------------------------


class TestComputeDiff:
    """Test _compute_diff helper."""

    def test_diff_detects_text_change(self) -> None:
        source = [{"resource_id": "com.test:id/title", "text": "Hello"}]
        target = [{"resource_id": "com.test:id/title", "text": "Goodbye"}]
        diff = DslGenerator._compute_diff(source, target)
        assert "text changed" in diff
        assert "Hello" in diff
        assert "Goodbye" in diff

    def test_diff_detects_checked_change(self) -> None:
        source = [{"resource_id": "com.test:id/switch", "is_checked": False}]
        target = [{"resource_id": "com.test:id/switch", "is_checked": True}]
        diff = DslGenerator._compute_diff(source, target)
        assert "is_checked changed" in diff

    def test_diff_no_changes(self) -> None:
        source = [{"resource_id": "com.test:id/btn", "text": "OK"}]
        target = [{"resource_id": "com.test:id/btn", "text": "OK"}]
        diff = DslGenerator._compute_diff(source, target)
        assert diff == "(no significant changes)"

    def test_diff_element_removed(self) -> None:
        source = [{"resource_id": "com.test:id/btn", "text": "OK"}]
        target: list[dict[str, str]] = []
        diff = DslGenerator._compute_diff(source, target)
        assert "removed" in diff

    def test_diff_element_added(self) -> None:
        source: list[dict[str, str]] = []
        target = [{"resource_id": "com.test:id/btn", "text": "OK"}]
        diff = DslGenerator._compute_diff(source, target)
        assert "new in target" in diff


# -----------------------------------------------------------------------
# Test: collect sibling transitions
# -----------------------------------------------------------------------


class TestCollectSiblingTransitions:
    """Test _collect_sibling_transitions helper."""

    def test_collects_siblings(self, synthetic_fsm_and_trace: tuple) -> None:
        """s1 has 2 click transitions: e_001→s2 and scroll. Only click siblings count."""
        fsm, trace_path = synthetic_fsm_and_trace
        # Add another click from s1
        fsm.add_transition(
            Transition(
                source="s1",
                target="s3",
                action={"type": "click", "target": "e_002"},
                confidence=1.0,
            )
        )
        gen = _make_generator(fsm)
        trace_data = json.loads(trace_path.read_text())
        raw_screens = trace_data["screens"]

        t1 = fsm.transitions[0]  # s1→s2 click
        result = gen._collect_sibling_transitions(t1, fsm.states["s1"], raw_screens)

        assert "Other click targets" in result
        assert "WiFiDetail" in result  # s3's name

    def test_no_siblings_returns_empty(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        gen = _make_generator(fsm)
        trace_data = json.loads(trace_path.read_text())
        raw_screens = trace_data["screens"]

        t2 = fsm.transitions[1]  # s2→s3 click (only click from s2)
        result = gen._collect_sibling_transitions(t2, fsm.states["s2"], raw_screens)

        assert result == ""

    def test_includes_all_siblings(self, synthetic_fsm_and_trace: tuple) -> None:
        fsm, trace_path = synthetic_fsm_and_trace
        # Add 7 click transitions from s1
        for i in range(7):
            sid = f"sx{i}"
            fsm.add_state(
                AbstractState(
                    state_id=sid,
                    name=f"Extra{i}",
                    fingerprint=f"fp_x{i}",
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                )
            )
            fsm.add_transition(
                Transition(
                    source="s1",
                    target=sid,
                    action={"type": "click", "target": f"e_x{i}"},
                    confidence=1.0,
                )
            )
        gen = _make_generator(fsm)
        trace_data = json.loads(trace_path.read_text())
        raw_screens = trace_data["screens"]

        t1 = fsm.transitions[0]
        result = gen._collect_sibling_transitions(t1, fsm.states["s1"], raw_screens)

        assert result.count("Click on") == 7


# -----------------------------------------------------------------------
# Test: integration — generate_all_guards with mixed transitions
# -----------------------------------------------------------------------


class TestGenerateAllGuardsIntegration:
    """Test generate_all_guards with mixed transition types."""

    def test_five_transitions(self, tmp_path: Path) -> None:
        """5 transitions: 2 clicks, 1 toggle, 1 back, 1 scroll.
        3 get LLM calls, 2 skipped."""
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
            # click: menu navigation
            Transition(
                source="s1",
                target="s2",
                action={"type": "click", "target": "e_001"},
                confidence=1.0,
            ),
            # click: list item
            Transition(
                source="s2",
                target="s3",
                action={"type": "click", "target": "e_010"},
                confidence=1.0,
            ),
            # click: toggle switch
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
        # 3 LLM calls: 2 menu clicks + 1 toggle
        gen._llm.generate_with_images.side_effect = [
            "action(target_text) == $intent.target_setting",
            "action(target_text) == $intent.wifi_name",
            "read(Switch_0, is_checked) == false",
        ]

        result = gen.generate_all_guards(trace_path, use_images=True)

        assert result is fsm
        guards = [t.guard for t in fsm.transitions]
        # click 1: menu nav → guard
        assert guards[0] == "action(target_text) == $intent.target_setting"
        # click 2: list item → guard
        assert guards[1] == "action(target_text) == $intent.wifi_name"
        # click 3: toggle → guard
        assert guards[2] == "read(Switch_0, is_checked) == false"
        # back nav → None
        assert guards[3] is None
        # scroll → None
        assert guards[4] is None
        # 3 LLM calls total
        assert gen._llm.generate_with_images.call_count == 3
