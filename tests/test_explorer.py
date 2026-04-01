"""Tests for vigil.neuro.explorer and supporting modules."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vigil.core.action_types import enumerate_actions, enumerate_element_actions
from vigil.core.config import VigilConfig
from vigil.core.ui_parser import parse_bounds, parse_hierarchy_xml
from vigil.models.action import Action, ActionType
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.explorer import AppExplorer, ExplorationResult, ExplorationTrace

# === Sample XML for testing ===

SIMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="Settings" resource-id="com.android.settings:id/title"
        class="android.widget.TextView" package="com.android.settings"
        content-desc="" checkable="false" checked="false" clickable="true"
        enabled="true" focusable="true" focused="false" scrollable="false"
        long-clickable="false" password="false" selected="false"
        bounds="[0,0][1080,200]" />
</hierarchy>
"""

NESTED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id=""
        class="android.widget.FrameLayout" package="com.android.settings"
        content-desc="" checkable="false" checked="false" clickable="false"
        enabled="true" focusable="false" focused="false" scrollable="true"
        long-clickable="false" password="false" selected="false"
        bounds="[0,0][1080,1920]">
    <node index="0" text="Wi-Fi" resource-id="com.android.settings:id/wifi"
          class="android.widget.TextView" package="com.android.settings"
          content-desc="Wi-Fi settings" checkable="false" checked="false"
          clickable="true" enabled="true" focusable="true" focused="false"
          scrollable="false" long-clickable="false" password="false"
          selected="false" bounds="[0,200][1080,400]" />
    <node index="1" text="Bluetooth" resource-id="com.android.settings:id/bt"
          class="android.widget.TextView" package="com.android.settings"
          content-desc="" checkable="false" checked="false" clickable="true"
          enabled="true" focusable="true" focused="false" scrollable="false"
          long-clickable="false" password="false" selected="false"
          bounds="[0,400][1080,600]" />
  </node>
</hierarchy>
"""

EDITTEXT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="com.example:id/input"
        class="android.widget.EditText" package="com.example"
        content-desc="" checkable="false" checked="false" clickable="true"
        enabled="true" focusable="true" focused="false" scrollable="false"
        long-clickable="false" password="false" selected="false"
        bounds="[50,100][500,200]" />
</hierarchy>
"""


# ============================================================
# ui_parser tests
# ============================================================


class TestParseBounds:
    def test_standard_bounds(self) -> None:
        assert parse_bounds("[100,200][300,400]") == [100, 200, 300, 400]

    def test_zero_bounds(self) -> None:
        assert parse_bounds("[0,0][0,0]") == [0, 0, 0, 0]

    def test_large_bounds(self) -> None:
        assert parse_bounds("[0,0][1080,1920]") == [0, 0, 1080, 1920]

    def test_malformed_bounds(self) -> None:
        assert parse_bounds("invalid") == [0, 0, 0, 0]

    def test_empty_bounds(self) -> None:
        assert parse_bounds("") == [0, 0, 0, 0]


class TestParseHierarchyXml:
    def test_single_node(self) -> None:
        elements = parse_hierarchy_xml(SIMPLE_XML)
        assert len(elements) == 1
        el = elements[0]
        assert el.class_name == "android.widget.TextView"
        assert el.resource_id == "com.android.settings:id/title"
        assert el.text == "Settings"
        assert el.is_clickable is True
        assert el.is_scrollable is False
        assert el.bounds == [0, 0, 1080, 200]

    def test_nested_nodes(self) -> None:
        elements = parse_hierarchy_xml(NESTED_XML)
        assert len(elements) == 3
        # First two children, then the parent (DFS post-order due to child processing first)
        # Actually children are processed first but appended after recursion
        # The parent node is appended AFTER its children
        parent = elements[2]  # FrameLayout is last
        assert parent.class_name == "android.widget.FrameLayout"
        assert len(parent.children) == 2
        assert parent.is_scrollable is True

    def test_element_id_assignment(self) -> None:
        elements = parse_hierarchy_xml(NESTED_XML)
        ids = [e.element_id for e in elements]
        # IDs should be sequential
        assert ids == ["e_0001", "e_0002", "e_0000"]  # children first, then parent
        # Wait - let me re-check. The parent gets e_0000, then recurses:
        # Actually: _parse_node assigns counter first (e_0000 for parent),
        # then recurses into children (e_0001, e_0002)
        # But children are appended to elements list first, parent appended last.
        # So element list order is: child1(e_0001), child2(e_0002), parent(e_0000)
        assert "e_0000" in ids
        assert "e_0001" in ids
        assert "e_0002" in ids

    def test_boolean_attributes(self) -> None:
        elements = parse_hierarchy_xml(SIMPLE_XML)
        el = elements[0]
        assert el.is_enabled is True
        assert el.is_checkable is False
        assert el.is_checked is False
        assert el.is_long_clickable is False

    def test_edittext_detection(self) -> None:
        elements = parse_hierarchy_xml(EDITTEXT_XML)
        assert len(elements) == 1
        el = elements[0]
        assert el.is_editable is True
        assert el.class_name == "android.widget.EditText"

    def test_empty_xml(self) -> None:
        assert parse_hierarchy_xml("") == []
        assert parse_hierarchy_xml("   ") == []

    def test_invalid_xml(self) -> None:
        assert parse_hierarchy_xml("<not valid xml") == []

    def test_empty_text_normalized_to_none(self) -> None:
        elements = parse_hierarchy_xml(NESTED_XML)
        # The FrameLayout has text="" which should be normalized to None
        frame = [e for e in elements if e.class_name == "android.widget.FrameLayout"][0]
        assert frame.text is None
        assert frame.resource_id is None


# ============================================================
# action_types tests
# ============================================================


def _make_element(**overrides: object) -> UIElement:
    """Helper to create a UIElement with defaults."""
    defaults = {
        "element_id": "e_0001",
        "class_name": "android.widget.Button",
        "bounds": [100, 200, 300, 400],
        "is_enabled": True,
    }
    defaults.update(overrides)
    return UIElement(**defaults)  # type: ignore[arg-type]


class TestEnumerateElementActions:
    def test_clickable_element(self) -> None:
        el = _make_element(is_clickable=True)
        actions = enumerate_element_actions(el)
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.CLICK
        assert actions[0].target_element_id == "e_0001"

    def test_scrollable_element_gives_two_actions(self) -> None:
        el = _make_element(is_scrollable=True)
        actions = enumerate_element_actions(el)
        assert len(actions) == 2
        types = {a.action_type for a in actions}
        assert types == {ActionType.SCROLL_UP, ActionType.SCROLL_DOWN}

    def test_editable_element(self) -> None:
        el = _make_element(is_editable=True)
        actions = enumerate_element_actions(el)
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.INPUT_TEXT
        assert actions[0].input_text == "test input"

    def test_multi_property_element(self) -> None:
        el = _make_element(is_clickable=True, is_long_clickable=True)
        actions = enumerate_element_actions(el)
        assert len(actions) == 2
        types = {a.action_type for a in actions}
        assert types == {ActionType.CLICK, ActionType.LONG_PRESS}

    def test_non_interactable_element(self) -> None:
        el = _make_element()  # no interactable flags set
        actions = enumerate_element_actions(el)
        assert len(actions) == 0


class TestEnumerateActions:
    def test_includes_global_actions(self) -> None:
        screen = RawScreen(screen_id="s1", elements=[])
        actions = enumerate_actions(screen)
        types = {a.action_type for a in actions}
        assert ActionType.NAVIGATE_BACK in types
        assert ActionType.NAVIGATE_HOME in types

    def test_empty_screen_only_global(self) -> None:
        screen = RawScreen(screen_id="s1", elements=[])
        actions = enumerate_actions(screen)
        assert len(actions) == 2  # just back + home

    def test_full_screen(self) -> None:
        el = _make_element(is_clickable=True)
        screen = RawScreen(screen_id="s1", elements=[el])
        actions = enumerate_actions(screen)
        # 1 click + 2 global = 3
        assert len(actions) == 3


# ============================================================
# Structural fingerprint tests
# ============================================================


class TestStructuralFingerprint:
    def test_same_structure_same_fingerprint(self) -> None:
        el1 = _make_element(is_clickable=True, text="Hello")
        el2 = _make_element(is_clickable=True, text="Hello")
        s1 = RawScreen(screen_id="s1", activity_name="A", elements=[el1])
        s2 = RawScreen(screen_id="s2", activity_name="A", elements=[el2])
        assert s1.get_structural_fingerprint() == s2.get_structural_fingerprint()

    def test_different_text_same_fingerprint(self) -> None:
        el1 = _make_element(is_clickable=True, text="Hello")
        el2 = _make_element(is_clickable=True, text="World")
        s1 = RawScreen(screen_id="s1", activity_name="A", elements=[el1])
        s2 = RawScreen(screen_id="s2", activity_name="A", elements=[el2])
        assert s1.get_structural_fingerprint() == s2.get_structural_fingerprint()

    def test_different_structure_different_fingerprint(self) -> None:
        el1 = _make_element(is_clickable=True)
        el2 = _make_element(is_scrollable=True)
        s1 = RawScreen(screen_id="s1", activity_name="A", elements=[el1])
        s2 = RawScreen(screen_id="s2", activity_name="A", elements=[el2])
        assert s1.get_structural_fingerprint() != s2.get_structural_fingerprint()

    def test_different_activity_different_fingerprint(self) -> None:
        el = _make_element(is_clickable=True)
        s1 = RawScreen(screen_id="s1", activity_name="ActivityA", elements=[el])
        s2 = RawScreen(screen_id="s2", activity_name="ActivityB", elements=[el])
        assert s1.get_structural_fingerprint() != s2.get_structural_fingerprint()


# ============================================================
# Explorer model tests
# ============================================================


class TestExplorationModels:
    def test_trace_serialization(self) -> None:
        trace = ExplorationTrace(
            step_number=1,
            source_screen_id="s1",
            action=Action(action_type=ActionType.CLICK, target_element_id="e_0001"),
            target_screen_id="s2",
            timestamp="2026-03-30T12:00:00",
        )
        data = trace.model_dump()
        assert data["step_number"] == 1
        assert data["source_screen_id"] == "s1"

    def test_result_defaults(self) -> None:
        result = ExplorationResult(app_package="com.test")
        assert result.total_steps == 0
        assert result.unique_screens == 0
        assert result.screens == {}
        assert result.traces == []


# ============================================================
# AppExplorer tests (mocked device)
# ============================================================


class TestAppExplorer:
    @pytest.fixture
    def mock_device(self) -> MagicMock:
        device = MagicMock()
        device.info = {"productName": "test", "sdkInt": 30}
        device.app_current.return_value = {
            "package": "com.android.settings",
            "activity": "com.android.settings.Settings",
            "pid": 1234,
        }
        device.dump_hierarchy.return_value = SIMPLE_XML
        # screenshot returns a mock that has .save()
        device.screenshot = MagicMock()
        return device

    @pytest.fixture
    def explorer(self, mock_device: MagicMock, tmp_path: object) -> AppExplorer:
        config = VigilConfig()
        config.app.max_exploration_steps = 5

        with patch("vigil.neuro.explorer.u2") as mock_u2:
            mock_u2.connect.return_value = mock_device
            exp = AppExplorer(
                device_serial="test_serial",
                app_package="com.android.settings",
                config=config,
                output_dir=tmp_path,  # type: ignore[arg-type]
            )
            exp._connect_device()
        return exp

    def test_connect_device(self, explorer: AppExplorer) -> None:
        assert explorer._device is not None

    def test_capture_screen(self, explorer: AppExplorer) -> None:
        screen = explorer._capture_screen()
        assert screen is not None
        assert screen.screen_id.startswith("scr_")
        assert screen.activity_name == "com.android.settings.Settings"
        assert len(screen.elements) == 1

    def test_is_within_app(self, explorer: AppExplorer) -> None:
        assert explorer._is_within_app() is True

    def test_is_outside_app(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        mock_device.app_current.return_value = {"package": "com.other.app"}
        assert explorer._is_within_app() is False

    def test_execute_click(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        action = Action(
            action_type=ActionType.CLICK,
            target_element_id="e_0001",
            target_bounds=[100, 200, 300, 400],
        )
        explorer._execute_action(action)
        mock_device.click.assert_called_once_with(200, 300)

    def test_execute_back(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        action = Action(action_type=ActionType.NAVIGATE_BACK)
        explorer._execute_action(action)
        mock_device.press.assert_called_once_with("back")

    def test_max_steps_respected(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        # With max_steps=5 and a simple XML that produces actions,
        # the explorer should stop after 5 steps
        with patch("vigil.neuro.explorer.u2") as mock_u2:
            mock_u2.connect.return_value = mock_device
            result = explorer.explore()
        assert result.total_steps <= 5
