"""Tests for vigil.neuro.explorer and supporting modules."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vigil.core.action_types import enumerate_actions, enumerate_element_actions
from vigil.core.config import VigilConfig
from vigil.core.ui_parser import parse_bounds, parse_hierarchy_xml
from vigil.models.action import Action, ActionType
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.explorer import (
    AppExplorer,
    ExplorationResult,
    ExplorationTrace,
    SmartStoppingContext,
    _action_signature,
    _match_activity,
    analyze_container_homogeneity,
    apply_smart_stopping,
    pick_representatives,
    record_detail_fingerprint,
)

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


# ============================================================
# Smart Stopping tests
# ============================================================


def _make_item(eid: str, **overrides) -> UIElement:
    """Clickable list item with default skeleton."""
    defaults = {
        "element_id": eid,
        "class_name": "android.widget.LinearLayout",
        "resource_id": "com.app:id/item",
        "is_clickable": True,
        "is_enabled": True,
        "bounds": [0, 0, 1080, 100],
    }
    defaults.update(overrides)
    return UIElement(**defaults)


def _make_container_screen(
    item_count: int = 10,
    heterogeneous_indices: set[int] | None = None,
) -> RawScreen:
    """Build a screen with a scrollable container holding `item_count` clickable items.

    Items at `heterogeneous_indices` get a different class_name to break homogeneity.
    """
    children_ids = [f"e_{i:03d}" for i in range(item_count)]
    container = UIElement(
        element_id="e_container",
        class_name="android.widget.RecyclerView",
        resource_id="com.app:id/list",
        is_scrollable=True,
        is_enabled=True,
        bounds=[0, 0, 1080, 1920],
        children=children_ids,
    )
    items: list[UIElement] = []
    het = heterogeneous_indices or set()
    for i in range(item_count):
        cls = "android.widget.Button" if i in het else "android.widget.LinearLayout"
        items.append(
            _make_item(
                f"e_{i:03d}",
                class_name=cls,
                resource_id="com.app:id/item",
            )
        )

    return RawScreen(
        screen_id="scr_list",
        activity_name="com.app.ListActivity",
        elements=[container, *items],
    )


class TestAnalyzeContainerHomogeneity:
    def test_homogeneous_container(self) -> None:
        screen = _make_container_screen(10)
        ebi = {e.element_id: e for e in screen.elements}
        container = ebi["e_container"]
        dominant, ratio, matching = analyze_container_homogeneity(container, ebi)
        assert dominant is not None
        assert ratio == 1.0
        assert len(matching) == 10

    def test_heterogeneous_container(self) -> None:
        screen = _make_container_screen(10, heterogeneous_indices={0, 1, 2, 3, 4})
        ebi = {e.element_id: e for e in screen.elements}
        container = ebi["e_container"]
        _, ratio, _ = analyze_container_homogeneity(container, ebi)
        assert ratio == 0.5  # 5/10

    def test_too_few_children(self) -> None:
        screen = _make_container_screen(2)
        ebi = {e.element_id: e for e in screen.elements}
        container = ebi["e_container"]
        _, ratio, _ = analyze_container_homogeneity(container, ebi)
        assert ratio == 0.0


class TestPickRepresentatives:
    def test_picks_first_and_last(self) -> None:
        items = [_make_item(f"e_{i}") for i in range(10)]
        reps = pick_representatives(items)
        assert len(reps) == 2
        assert reps[0].element_id == "e_0"
        assert reps[1].element_id == "e_9"

    def test_returns_all_if_small(self) -> None:
        items = [_make_item("e_0"), _make_item("e_1")]
        reps = pick_representatives(items)
        assert len(reps) == 2


class TestApplySmartStopping:
    @staticmethod
    def _click(eid: str) -> Action:
        return Action(
            action_type=ActionType.CLICK,
            target_element_id=eid,
            target_bounds=[0, 0, 1, 1],
        )

    def test_homogeneous_list_filters_to_2(self) -> None:
        screen = _make_container_screen(10)
        actions = [self._click(f"e_{i:03d}") for i in range(10)]
        ctx = SmartStoppingContext()
        filtered = apply_smart_stopping(screen, actions, ctx)
        assert len(filtered) == 2
        ids = {a.target_element_id for a in filtered}
        assert "e_000" in ids
        assert "e_009" in ids

    def test_heterogeneous_keeps_all(self) -> None:
        screen = _make_container_screen(10, heterogeneous_indices={0, 1, 2, 3, 4})
        actions = [self._click(f"e_{i:03d}") for i in range(10)]
        ctx = SmartStoppingContext()
        filtered = apply_smart_stopping(screen, actions, ctx)
        assert len(filtered) == 10

    def test_verified_dynamic_skips_all(self) -> None:
        screen = _make_container_screen(5)
        ebi = {e.element_id: e for e in screen.elements}
        container = ebi["e_container"]
        from vigil.neuro.explorer import _container_fingerprint

        cfp = _container_fingerprint(screen, container)
        ctx = SmartStoppingContext(verified_dynamic={cfp})

        actions = [self._click(f"e_{i:03d}") for i in range(5)]
        filtered = apply_smart_stopping(screen, actions, ctx)
        assert len(filtered) == 0

    def test_non_click_actions_preserved(self) -> None:
        screen = _make_container_screen(10)
        actions = [self._click(f"e_{i:03d}") for i in range(10)]
        actions.append(Action(action_type=ActionType.NAVIGATE_BACK))
        actions.append(
            Action(
                action_type=ActionType.SCROLL_DOWN,
                target_element_id="e_container",
                target_bounds=[0, 0, 1, 1],
            )
        )
        ctx = SmartStoppingContext()
        filtered = apply_smart_stopping(screen, actions, ctx)
        types = {a.action_type for a in filtered}
        assert ActionType.NAVIGATE_BACK in types
        assert ActionType.SCROLL_DOWN in types


class TestVerifyDynamicContainer:
    def test_matching_fingerprints_mark_dynamic(self) -> None:
        ctx = SmartStoppingContext()
        ctx.pending["cfp_1"] = {
            "source_screen_id": "scr_list",
            "dominant_skeleton": "sk",
            "total_items": 10,
            "representative_element_ids": ["e_000", "e_009"],
            "detail_fingerprints": [],
        }
        record_detail_fingerprint(ctx, "scr_list", "e_000", "fp_detail_A")
        assert "cfp_1" in ctx.pending

        record_detail_fingerprint(ctx, "scr_list", "e_009", "fp_detail_A")
        assert "cfp_1" not in ctx.pending
        assert "cfp_1" in ctx.verified_dynamic

    def test_different_fingerprints_mark_static(self) -> None:
        ctx = SmartStoppingContext()
        ctx.pending["cfp_2"] = {
            "source_screen_id": "scr_list",
            "dominant_skeleton": "sk",
            "total_items": 10,
            "representative_element_ids": ["e_000", "e_009"],
            "detail_fingerprints": [],
        }
        record_detail_fingerprint(ctx, "scr_list", "e_000", "fp_detail_A")
        record_detail_fingerprint(ctx, "scr_list", "e_009", "fp_detail_B")
        assert "cfp_2" not in ctx.pending
        assert "cfp_2" in ctx.verified_static


# ============================================================
# Activity coverage tracking tests
# ============================================================


class TestMatchActivity:
    def test_exact_match(self) -> None:
        declared = {"com.android.settings.Settings", "com.android.settings.wifi.WifiSettings"}
        assert _match_activity("com.android.settings.Settings", declared) == (
            "com.android.settings.Settings"
        )

    def test_suffix_match(self) -> None:
        declared = {"com.android.settings.Settings"}
        assert _match_activity(".Settings", declared) is not None

    def test_short_class_match(self) -> None:
        declared = {"com.android.settings.wifi.WifiSettings"}
        assert _match_activity("com.other.WifiSettings", declared) is not None

    def test_no_match(self) -> None:
        declared = {"com.android.settings.Settings"}
        assert _match_activity("com.totally.different.Activity", declared) is None

    def test_empty_declared(self) -> None:
        assert _match_activity("com.app.Main", set()) is None


class TestExplorationResultCoverage:
    def test_coverage_fields_default_empty(self) -> None:
        result = ExplorationResult(app_package="com.test")
        assert result.declared_activities == []
        assert result.covered_activities == []

    def test_coverage_fields_populated(self) -> None:
        result = ExplorationResult(
            app_package="com.test",
            declared_activities=["A", "B", "C"],
            covered_activities=["A", "B"],
        )
        assert len(result.declared_activities) == 3
        assert len(result.covered_activities) == 2


# ============================================================
# Action signature + dedup tests
# ============================================================


class TestActionSignature:
    def test_same_action_same_activity(self) -> None:
        sig1 = _action_signature(
            "com.android.settings.WifiSettings",
            Action(action_type=ActionType.CLICK, target_bounds=[100, 200, 300, 400]),
        )
        sig2 = _action_signature(
            "com.android.settings.WifiSettings",
            Action(action_type=ActionType.CLICK, target_bounds=[100, 200, 300, 400]),
        )
        assert sig1 == sig2

    def test_different_bounds(self) -> None:
        sig1 = _action_signature(
            "com.android.settings.WifiSettings",
            Action(action_type=ActionType.CLICK, target_bounds=[100, 200, 300, 400]),
        )
        sig2 = _action_signature(
            "com.android.settings.WifiSettings",
            Action(action_type=ActionType.CLICK, target_bounds=[500, 600, 700, 800]),
        )
        assert sig1 != sig2

    def test_different_activity(self) -> None:
        sig1 = _action_signature(
            "com.android.settings.WifiSettings",
            Action(action_type=ActionType.CLICK, target_bounds=[100, 200, 300, 400]),
        )
        sig2 = _action_signature(
            "com.android.settings.BluetoothSettings",
            Action(action_type=ActionType.CLICK, target_bounds=[100, 200, 300, 400]),
        )
        assert sig1 != sig2

    def test_back_action_signature(self) -> None:
        sig = _action_signature(
            "com.android.settings.WifiSettings",
            Action(action_type=ActionType.NAVIGATE_BACK),
        )
        assert "navigate_back" in sig
        assert "global" in sig


# ============================================================
# Locality-aware frontier tests
# ============================================================


class TestLocalityAwareFrontier:
    @staticmethod
    def _make_explorer() -> AppExplorer:
        config = VigilConfig()
        config.app.max_exploration_steps = 5
        config.app.exploration_strategy = "bfs"
        with patch("vigil.neuro.explorer.u2"):
            return AppExplorer(
                device_serial="test",
                app_package="com.test",
                config=config,
                output_dir=Path("/tmp/test_explorer_locality"),
            )

    def test_p1_current_screen(self) -> None:
        explorer = self._make_explorer()
        frontier = deque(
            [
                ("other", Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10])),
                ("current", Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10])),
            ]
        )
        sid, _act, tier = explorer._pop_frontier_prefer_current(frontier, "current", 0, 100)
        assert sid == "current"
        assert tier == 1

    def test_p2_forward_adjacent(self) -> None:
        explorer = self._make_explorer()
        fwd = {
            "current": [
                (Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10]), "neighbor")
            ]
        }
        frontier = deque(
            [
                ("far_away", Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10])),
                ("neighbor", Action(action_type=ActionType.CLICK, target_bounds=[20, 20, 30, 30])),
            ]
        )
        sid, _act, tier = explorer._pop_frontier_prefer_current(
            frontier, "current", 0, 100, forward_edges=fwd
        )
        assert sid == "neighbor"
        assert tier == 2

    def test_p3_back_to_parent(self) -> None:
        explorer = self._make_explorer()
        back = {"current": "parent"}
        frontier = deque(
            [
                ("far_away", Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10])),
                ("parent", Action(action_type=ActionType.CLICK, target_bounds=[20, 20, 30, 30])),
            ]
        )
        sid, _act, tier = explorer._pop_frontier_prefer_current(
            frontier, "current", 0, 100, back_edges=back
        )
        assert sid == "parent"
        assert tier == 3

    def test_p4_sibling(self) -> None:
        explorer = self._make_explorer()
        back = {"current": "parent"}
        fwd = {
            "parent": [
                (Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10]), "current"),
                (Action(action_type=ActionType.CLICK, target_bounds=[20, 20, 30, 30]), "sibling"),
            ]
        }
        frontier = deque(
            [
                ("far_away", Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10])),
                ("sibling", Action(action_type=ActionType.CLICK, target_bounds=[50, 50, 60, 60])),
            ]
        )
        sid, _act, tier = explorer._pop_frontier_prefer_current(
            frontier, "current", 0, 100, forward_edges=fwd, back_edges=back
        )
        assert sid == "sibling"
        assert tier == 4

    def test_p5_fallback(self) -> None:
        explorer = self._make_explorer()
        frontier = deque(
            [
                ("far_away", Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10])),
            ]
        )
        sid, _act, tier = explorer._pop_frontier_prefer_current(frontier, "current", 0, 100)
        assert sid == "far_away"
        assert tier == 5

    def test_p2_skipped_if_no_forward_edges(self) -> None:
        explorer = self._make_explorer()
        fwd = {
            "other_screen": [
                (Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10]), "neighbor")
            ]
        }
        frontier = deque(
            [
                ("neighbor", Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 10, 10])),
            ]
        )
        _sid, _act, tier = explorer._pop_frontier_prefer_current(
            frontier, "current", 0, 100, forward_edges=fwd
        )
        assert tier == 5


class TestNavStats:
    def test_nav_stats_field_on_result(self) -> None:
        result = ExplorationResult(
            app_package="com.test",
            nav_stats={"p1_current": 10, "p5_replay": 2},
        )
        assert result.nav_stats["p1_current"] == 10
        assert result.nav_stats["p5_replay"] == 2

    def test_nav_stats_default_empty(self) -> None:
        result = ExplorationResult(app_package="com.test")
        assert result.nav_stats == {}


class TestFrontierReplenishment:
    def test_revisited_screen_adds_unexecuted_actions(self) -> None:
        executed: set[str] = set()
        frontier_sigs: set[str] = set()

        activity = "com.android.settings.WifiSettings"
        actions = [
            Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 100, 100]),
            Action(action_type=ActionType.CLICK, target_bounds=[0, 100, 100, 200]),
            Action(action_type=ActionType.CLICK, target_bounds=[0, 200, 100, 300]),
        ]

        # First visit: all 3 added
        added_first = 0
        for a in actions:
            sig = _action_signature(activity, a)
            if sig not in executed and sig not in frontier_sigs:
                frontier_sigs.add(sig)
                added_first += 1
        assert added_first == 3

        # Execute VPN (first action)
        vpn_sig = _action_signature(activity, actions[0])
        executed.add(vpn_sig)
        frontier_sigs.discard(vpn_sig)

        # Simulate: frontier for Bluetooth and Hotspot drained (nav failure)
        bt_sig = _action_signature(activity, actions[1])
        hs_sig = _action_signature(activity, actions[2])
        frontier_sigs.discard(bt_sig)
        frontier_sigs.discard(hs_sig)

        # Second visit: should re-add Bluetooth + Hotspot, NOT VPN
        added_second = 0
        for a in actions:
            sig = _action_signature(activity, a)
            if sig not in executed and sig not in frontier_sigs:
                frontier_sigs.add(sig)
                added_second += 1
        assert added_second == 2

    def test_frontier_sigs_prevents_duplicates(self) -> None:
        executed: set[str] = set()
        frontier_sigs: set[str] = set()

        activity = "com.android.settings.WifiSettings"
        action = Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 100, 100])
        sig = _action_signature(activity, action)

        assert sig not in executed and sig not in frontier_sigs
        frontier_sigs.add(sig)

        # Second add attempt blocked
        assert sig in frontier_sigs
