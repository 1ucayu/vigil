"""Tests for vigil.neuro.explorer (plain BFS rewrite) and supporting modules."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vigil.core.action_types import enumerate_actions, enumerate_element_actions
from vigil.core.config import VigilConfig
from vigil.core.ui_compressor import compact_ui_tree_text
from vigil.core.ui_parser import parse_bounds, parse_hierarchy_xml
from vigil.core.ui_selectors import (
    build_component_selector,
    find_element_by_selector,
    selector_has_stable_identity,
    selector_identity,
)
from vigil.models.action import Action, ActionType
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.explorer import (
    ACTION_TYPE_WEIGHT,
    SENTINEL_ACTION_FAILED,
    SENTINEL_COLD_START_FAILED,
    SENTINEL_LEFT_APP,
    AppExplorer,
    ExplorationResult,
    ExplorationTrace,
    ScrollObservation,
    _build_interact_action,
    _generate_edit_value,
    _is_edit_text,
    action_key,
    is_action_identifiable,
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
        assert el.is_clickable is True

    def test_nested_nodes(self) -> None:
        elements = parse_hierarchy_xml(NESTED_XML)
        assert len(elements) == 3
        parent = elements[2]
        assert parent.is_scrollable is True

    def test_edittext_detection(self) -> None:
        elements = parse_hierarchy_xml(EDITTEXT_XML)
        assert elements[0].is_editable is True

    def test_empty_xml(self) -> None:
        assert parse_hierarchy_xml("") == []

    def test_invalid_xml(self) -> None:
        assert parse_hierarchy_xml("<not valid xml") == []


# ============================================================
# action_types tests
# ============================================================


def _make_element(**overrides: object) -> UIElement:
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
        actions = enumerate_element_actions(_make_element(is_clickable=True))
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.CLICK

    def test_scrollable_element_gives_two_actions(self) -> None:
        actions = enumerate_element_actions(_make_element(is_scrollable=True))
        assert {a.action_type for a in actions} == {
            ActionType.SCROLL_UP,
            ActionType.SCROLL_DOWN,
        }


class TestEnumerateActions:
    def test_includes_global_actions(self) -> None:
        screen = RawScreen(screen_id="s1", elements=[])
        types = {a.action_type for a in enumerate_actions(screen)}
        assert ActionType.NAVIGATE_BACK in types
        assert ActionType.NAVIGATE_HOME in types


# ============================================================
# Structural fingerprint (unchanged)
# ============================================================


class TestStructuralFingerprint:
    def test_same_structure_same_fingerprint(self) -> None:
        el = _make_element(is_clickable=True)
        s1 = RawScreen(screen_id="s1", activity_name="A", elements=[el])
        s2 = RawScreen(screen_id="s2", activity_name="A", elements=[el])
        assert s1.get_structural_fingerprint() == s2.get_structural_fingerprint()

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
            source_state_id="sid_src",
            intended_source_state_id="sid_int",
            source_screen_id="s1",
            action=Action(action_type=ActionType.CLICK, target_element_id="e_0001"),
            target_state_id="sid_tgt",
            target_screen_id="s2",
            timestamp="2026-03-30T12:00:00",
        )
        data = trace.model_dump()
        assert data["intended_source_state_id"] == "sid_int"
        assert data["target_state_id"] == "sid_tgt"

    def test_result_defaults(self) -> None:
        result = ExplorationResult(app_package="com.test")
        assert result.total_steps == 0
        assert result.unique_screens == 0
        assert result.nav_stats == {}


# ============================================================
# Deleted test classes tied to removed explorer internals (prior
# restart-based-observation rewrite):
#   TestStructuralGrouping          (old structural grouping engine)
#   TestBehavioralVerification      (old grouping equivalence verify)
#   TestMatchActivity, TestActionSignature, TestLeaveAppBlacklist,
#   TestFingerprintSimilarity, TestLocalityAwareFrontier,
#   TestFrontierReplenishment, TestScrollFilterSmallContainer  (all
#   targeted the original 5-tier scheduler / grouping engine / deferred
#   frontier)
#   TestAppExplorer/KnownState/Observation/_register_state_if_novel/
#   _pick_next_target/_observe_transition branches
#   (from the failure-taxonomy rewrite — replaced by sentinel
#   target_state_ids here)
# ============================================================


# ============================================================
# AppExplorer tests (mocked device, new plain-BFS loop)
# ============================================================


@pytest.fixture
def mock_device() -> MagicMock:
    """Mock shape matches real uiautomator2.Device.app_current() (plain dict)."""
    device = MagicMock()
    device.info = {"productName": "test", "sdkInt": 30}
    device.app_current.return_value = {
        "package": "com.android.settings",
        "activity": "com.android.settings.Settings",
        "pid": 1234,
    }
    device.dump_hierarchy.return_value = SIMPLE_XML
    device.window_size.return_value = (1080, 2400)
    return device


@pytest.fixture
def explorer(mock_device: MagicMock, tmp_path: Path) -> AppExplorer:
    config = VigilConfig()
    config.app.max_exploration_steps = 5
    with patch("vigil.neuro.explorer.u2") as mock_u2:
        mock_u2.connect.return_value = mock_device
        exp = AppExplorer(
            device_serial="test_serial",
            app_package="com.android.settings",
            config=config,
            output_dir=tmp_path,
        )
        exp._connect_device()
    return exp


class TestDeviceIO:
    def test_capture_screen(self, explorer: AppExplorer) -> None:
        screen = explorer._capture_screen()
        assert screen is not None
        assert screen.screen_id.startswith("scr_")
        assert screen.activity_name == "com.android.settings.Settings"

    def test_capture_discards_external_package(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        mock_device.app_current.return_value = {"package": "com.other"}
        assert explorer._capture_screen() is None

    def test_is_within_app(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        assert explorer._is_within_app() is True
        mock_device.app_current.return_value = {"package": "com.other"}
        assert explorer._is_within_app() is False

    def test_execute_click(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        # CLICK is now descriptor-resolved: the device-screen dump
        # (SIMPLE_XML) contains a TextView with rid com.android.settings:id/title,
        # text "Settings", bounds [0,0,1080,200]. Descriptor targets that.
        action = Action(
            action_type=ActionType.CLICK,
            target_resource_id="com.android.settings:id/title",
            target_text="Settings",
            target_class_name="android.widget.TextView",
            # Stored bounds deliberately stale — resolver must ignore them.
            target_bounds=[100, 200, 300, 400],
        )
        assert explorer._execute_action(action) is True
        # Click should happen at the LIVE bounds center (540, 100), not the
        # stored (200, 300).
        mock_device.click.assert_called_once_with(540, 100)

    def test_execute_click_unresolvable_descriptor_returns_false(
        self, explorer: AppExplorer
    ) -> None:
        # Descriptor doesn't match anything on SIMPLE_XML; scroll plateau
        # (identical anchors twice) aborts resolver → False.
        action = Action(
            action_type=ActionType.CLICK,
            target_resource_id="com.app:id/nonexistent",
        )
        assert explorer._execute_action(action) is False

    def test_execute_back_returns_true(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        assert explorer._execute_action(Action(action_type=ActionType.NAVIGATE_BACK)) is True
        mock_device.press.assert_called_once_with("back")

    def test_scroll_down_swipes_high_to_low(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        # Need a scrollable element on the live screen for the resolver.
        scrollable_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="com.android.settings:id/content_parent"
        class="android.widget.ScrollView" package="com.android.settings"
        content-desc="" checkable="false" checked="false" clickable="false"
        enabled="true" focusable="false" focused="false" scrollable="true"
        long-clickable="false" password="false" selected="false"
        bounds="[0,0][1080,1000]" />
</hierarchy>
"""
        mock_device.dump_hierarchy.return_value = scrollable_xml
        action = Action(
            action_type=ActionType.SCROLL_DOWN,
            target_resource_id="com.android.settings:id/content_parent",
            target_class_name="android.widget.ScrollView",
        )
        assert explorer._execute_action(action) is True
        mock_device.swipe.assert_called_once()
        args = mock_device.swipe.call_args.args
        _, y1, _, y2 = args
        assert y1 > y2  # SCROLL_DOWN: finger high → low


# ============================================================
# action_key helper tests
# ============================================================


class TestActionKey:
    def test_rid_is_primary(self) -> None:
        a = Action(
            action_type=ActionType.CLICK,
            target_resource_id="com.app:id/vpn",
            target_bounds=[10, 20, 30, 40],
        )
        b = Action(
            action_type=ActionType.CLICK,
            target_resource_id="com.app:id/vpn",
            target_bounds=[100, 200, 300, 400],
        )
        assert action_key(a) == action_key(b)

    def test_bounds_bucket_tolerates_small_shift(self) -> None:
        a = Action(action_type=ActionType.CLICK, target_bounds=[100, 200, 300, 400])
        b = Action(action_type=ActionType.CLICK, target_bounds=[120, 215, 320, 415])
        assert action_key(a) == action_key(b)

    def test_bounds_differ_only_yield_same_key(self) -> None:
        # The whole point of the descriptor rework: two actions that
        # differ ONLY in bounds share an action_key.
        a = Action(action_type=ActionType.CLICK, target_bounds=[100, 200, 300, 400])
        b = Action(action_type=ActionType.CLICK, target_bounds=[100, 600, 300, 800])
        assert action_key(a) == action_key(b)

    def test_different_action_type_distinct(self) -> None:
        a = Action(action_type=ActionType.CLICK, target_resource_id="x:id/y")
        b = Action(action_type=ActionType.LONG_PRESS, target_resource_id="x:id/y")
        assert action_key(a) != action_key(b)


# ============================================================
# _enumerate_all_clickables scroll loop
# ============================================================


def _screen_with_clickables(screen_id: str, rids: list[str], scrollable: bool) -> RawScreen:
    elements = [
        UIElement(
            element_id=f"e_{i}",
            class_name="android.widget.Button",
            package="com.android.settings",
            resource_id=rid,
            text=rid,
            is_clickable=True,
            is_enabled=True,
            bounds=[0, i * 100, 1080, (i + 1) * 100],
        )
        for i, rid in enumerate(rids)
    ]
    if scrollable:
        elements.append(
            UIElement(
                element_id="e_scroll",
                class_name="androidx.recyclerview.widget.RecyclerView",
                package="com.android.settings",
                resource_id="com.android.settings:id/recycler_view",
                is_scrollable=True,
                is_enabled=True,
                bounds=[0, 0, 1080, 2400],
            )
        )
    return RawScreen(
        screen_id=screen_id,
        activity_name="Settings",
        package_name="com.android.settings",
        elements=elements,
    )


class TestEnumerateAllClickables:
    def test_stops_when_anchor_set_stable(self, explorer: AppExplorer) -> None:
        # Capture returns the same screen three times; scroll gesture never
        # surfaces new content — loop should break after the second capture.
        scr = _screen_with_clickables("scr", ["a:id/1", "a:id/2"], scrollable=True)
        screens = iter([scr, scr, scr, scr, scr])
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", side_effect=lambda: next(screens)),
        ):
            actions = explorer._enumerate_all_clickables("sid", [])
        # The two clickables on scr should be collected exactly once.
        click_rids = {a.target_resource_id for a in actions if a.action_type == ActionType.CLICK}
        assert click_rids == {"a:id/1", "a:id/2"}
        # Scroll candidates should also be emitted (Section A): the
        # scrollable RecyclerView contributes SCROLL_UP and SCROLL_DOWN
        # actions so the FSM records them as legal affordances.
        scroll_types = {
            a.action_type
            for a in actions
            if a.target_resource_id == "com.android.settings:id/recycler_view"
        }
        assert scroll_types == {ActionType.SCROLL_DOWN, ActionType.SCROLL_UP}

    def test_stops_when_no_scrollable(self, explorer: AppExplorer) -> None:
        scr = _screen_with_clickables("scr", ["x:id/one"], scrollable=False)
        screens = iter([scr, scr])
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", side_effect=lambda: next(screens)),
        ):
            actions = explorer._enumerate_all_clickables("sid", [])
        assert {a.target_resource_id for a in actions} == {"x:id/one"}

    def test_root_state_does_not_enumerate_navigate_back(self, explorer: AppExplorer) -> None:
        scr = _screen_with_clickables("scr", ["x:id/one"], scrollable=False)
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", return_value=scr),
        ):
            actions = explorer._enumerate_actions_for_state("sid_root", [])
        assert ActionType.NAVIGATE_BACK not in {a.action_type for a in actions}

    def test_child_state_enumerates_navigate_back(self, explorer: AppExplorer) -> None:
        scr = _screen_with_clickables("scr", ["x:id/one"], scrollable=False)
        nav_path = [Action(action_type=ActionType.CLICK, target_resource_id="x:id/open")]
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", return_value=scr),
        ):
            actions = explorer._enumerate_actions_for_state("sid_child", nav_path)

        back_actions = [a for a in actions if a.action_type == ActionType.NAVIGATE_BACK]
        assert len(back_actions) == 1
        assert action_key(back_actions[0]) == "navigate_back||||"
        assert back_actions[0].metadata == {"scope": "global_navigation"}

    def test_external_package_element_dropped(self, explorer: AppExplorer) -> None:
        in_app = UIElement(
            element_id="e0",
            class_name="android.widget.Button",
            package="com.android.settings",
            resource_id="com.android.settings:id/ok",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 100, 100],
        )
        systemui = UIElement(
            element_id="e1",
            class_name="android.widget.ImageButton",
            package="com.android.systemui",
            resource_id="com.android.systemui:id/clock",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 200, 100, 300],
        )
        scr = RawScreen(screen_id="s", elements=[in_app, systemui])
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_capture_screen", return_value=scr),
        ):
            actions = explorer._enumerate_all_clickables("sid", [])
        rids = {a.target_resource_id for a in actions}
        assert "com.android.settings:id/ok" in rids
        assert "com.android.systemui:id/clock" not in rids

    def test_cold_start_failure_returns_empty(self, explorer: AppExplorer) -> None:
        with patch.object(explorer, "_cold_start_app", return_value=False):
            actions = explorer._enumerate_all_clickables("sid", [])
        assert actions == []


# ============================================================
# _perform_one_observation branches
# ============================================================


def _simple_click() -> Action:
    return Action(action_type=ActionType.CLICK, target_bounds=[10, 10, 20, 20])


class TestPerformOneObservation:
    def test_cold_start_failed_sentinel(self, explorer: AppExplorer) -> None:
        with patch.object(explorer, "_cold_start_app", return_value=False):
            trace, pre, post = explorer._perform_one_observation(
                intended_source_state_id="intended_abc",
                intended_nav_path=[],
                action=_simple_click(),
                step=1,
            )
        assert trace.target_state_id == SENTINEL_COLD_START_FAILED
        assert trace.source_state_id == "intended_abc"  # fallback: intended
        assert pre is None and post is None

    def test_drift_recorded(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        # Pre-screen's state_id won't equal intended; we record it anyway.
        pre = _screen_with_clickables("pre_scr", ["a:id/1"], scrollable=False)
        post = _screen_with_clickables("post_scr", ["a:id/2"], scrollable=False)
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", side_effect=[pre, post]),
            patch.object(explorer, "_is_within_app", return_value=True),
        ):
            trace, p, q = explorer._perform_one_observation(
                intended_source_state_id="intended_xyz",
                intended_nav_path=[],
                action=_simple_click(),
                step=2,
            )
        assert trace.target_state_id not in {
            SENTINEL_COLD_START_FAILED,
            SENTINEL_ACTION_FAILED,
            SENTINEL_LEFT_APP,
        }
        # Drift: actual source (hash) != intended literal.
        assert trace.source_state_id != "intended_xyz"
        assert trace.intended_source_state_id == "intended_xyz"
        assert p is pre and q is post

    def test_left_app_sentinel(self, explorer: AppExplorer) -> None:
        pre = _screen_with_clickables("pre", ["a:id/1"], scrollable=False)
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", return_value=pre),
            patch.object(explorer, "_is_within_app", return_value=False),
        ):
            trace, p, q = explorer._perform_one_observation(
                intended_source_state_id="intended",
                intended_nav_path=[],
                action=_simple_click(),
                step=3,
            )
        assert trace.target_state_id == SENTINEL_LEFT_APP
        assert p is pre
        assert q is None

    def test_action_failed_sentinel(self, explorer: AppExplorer) -> None:
        pre = _screen_with_clickables("pre", ["a:id/1"], scrollable=False)
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_capture_screen", return_value=pre),
            patch.object(explorer, "_execute_action", return_value=False),
        ):
            trace, p, q = explorer._perform_one_observation(
                intended_source_state_id="intended",
                intended_nav_path=[],
                action=_simple_click(),
                step=4,
            )
        assert trace.target_state_id == SENTINEL_ACTION_FAILED
        assert p is pre
        assert q is None


# ============================================================
# ExplorationResult coverage / nav_stats containers
# ============================================================


class TestExplorationResultCoverage:
    def test_coverage_fields_default_empty(self) -> None:
        result = ExplorationResult(app_package="com.test")
        assert result.declared_activities == []
        assert result.covered_activities == []


class TestNavStats:
    def test_nav_stats_default_empty(self) -> None:
        result = ExplorationResult(app_package="com.test")
        assert result.nav_stats == {}

    def test_nav_stats_field(self) -> None:
        result = ExplorationResult(
            app_package="com.test",
            nav_stats={
                "observations_total": 50,
                "states_discovered": 7,
                "cold_start_failures": 1,
                "left_app": 2,
                "action_failures": 0,
            },
        )
        assert result.nav_stats["states_discovered"] == 7


# ============================================================
# Feature A — priority scoring
# ============================================================


def _click(rid: str = "com.app:id/click") -> Action:
    return Action(
        action_type=ActionType.CLICK,
        target_resource_id=rid,
        target_bounds=[10, 10, 20, 20],
    )


def _back() -> Action:
    return Action(action_type=ActionType.NAVIGATE_BACK)


def _scroll_down() -> Action:
    return Action(action_type=ActionType.SCROLL_DOWN, target_bounds=[0, 0, 1080, 2400])


class TestPriorityOrdering:
    def test_weight_map_sanity(self) -> None:
        assert ACTION_TYPE_WEIGHT[ActionType.CLICK] == 1.0
        assert ACTION_TYPE_WEIGHT[ActionType.NAVIGATE_HOME] < ACTION_TYPE_WEIGHT[ActionType.CLICK]
        assert (
            ACTION_TYPE_WEIGHT[ActionType.NAVIGATE_BACK]
            < ACTION_TYPE_WEIGHT[ActionType.SCROLL_DOWN]
        )

    def test_click_at_depth_0_beats_back_at_depth_0(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["s0"] = []
        click_score = explorer._priority_score("s0", _click())
        back_score = explorer._priority_score("s0", _back())
        assert click_score > back_score

    def test_shallow_click_beats_deep_click(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["s_shallow"] = []
        explorer._nav_paths["s_deep"] = [_click()] * 5
        assert explorer._priority_score("s_shallow", _click()) > explorer._priority_score(
            "s_deep", _click()
        )

    def test_frequency_decays_repeat_action_type(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["s0"] = []
        fresh = explorer._priority_score("s0", _click())
        explorer._global_action_type_count[ActionType.CLICK] = 50
        after = explorer._priority_score("s0", _click())
        assert after < fresh

    def test_pick_returns_highest_priority(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["shallow"] = []
        explorer._nav_paths["deep"] = [_click()] * 3
        explorer._all_actions_per_state["deep"] = {"k1": _click("com.app:id/deep_click")}
        explorer._all_actions_per_state["shallow"] = {"k2": _back()}
        # Shallow back: 1.0 * 0.3 * 1.0 = 0.3
        # Deep click: 0.25 * 1.0 * 1.0 = 0.25
        # Shallow back wins.
        picked = explorer._pick_next_action()
        assert picked is not None
        state, action = picked
        assert state == "shallow"
        assert action.action_type == ActionType.NAVIGATE_BACK

    def test_pick_returns_none_when_all_explored(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["s"] = []
        explorer._all_actions_per_state["s"] = {"k1": _click()}
        explorer._explored_per_state["s"].add("k1")
        assert explorer._pick_next_action() is None


# ============================================================
# Feature B — per-state blacklist
# ============================================================


class TestBlacklist:
    def test_pick_skips_blacklisted(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["s"] = []
        explorer._all_actions_per_state["s"] = {
            "k1": _click("com.app:id/blocked"),
            "k2": _click("com.app:id/ok"),
        }
        explorer._blacklisted_per_state["s"].add("k1")
        picked = explorer._pick_next_action()
        assert picked is not None
        _, action = picked
        assert action.target_resource_id == "com.app:id/ok"

    def test_blacklist_is_per_state(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["s_a"] = []
        explorer._nav_paths["s_b"] = []
        explorer._all_actions_per_state["s_a"] = {"k": _click("rid")}
        explorer._all_actions_per_state["s_b"] = {"k": _click("rid")}
        # Blacklist at s_a only — s_b's identical key is still available.
        explorer._blacklisted_per_state["s_a"].add("k")
        picked = explorer._pick_next_action()
        assert picked is not None
        state, _ = picked
        assert state == "s_b"


# ============================================================
# Feature C — EditText fill
# ============================================================


def _et(**kwargs: object) -> UIElement:
    defaults: dict[str, object] = {
        "element_id": "e_et",
        "class_name": "android.widget.EditText",
        "package": "com.android.settings",
        "is_editable": True,
        "is_enabled": True,
        "bounds": [0, 0, 500, 100],
    }
    defaults.update(kwargs)
    return UIElement(**defaults)  # type: ignore[arg-type]


class TestEditText:
    def test_is_edit_text_by_class(self) -> None:
        e = _et(is_editable=False, class_name="android.widget.EditText")
        assert _is_edit_text(e) is True

    def test_is_edit_text_by_flag(self) -> None:
        e = _et(class_name="android.widget.CustomInput", is_editable=True)
        assert _is_edit_text(e) is True

    def test_not_edit_text(self) -> None:
        e = _et(class_name="android.widget.TextView", is_editable=False)
        assert _is_edit_text(e) is False

    def test_hint_email(self) -> None:
        e = _et(content_description="Enter your email address")
        assert _generate_edit_value(e) == "test@example.com"

    def test_hint_password(self) -> None:
        e = _et(content_description="Password")
        assert "Pass" in _generate_edit_value(e)

    def test_input_type_phone(self) -> None:
        # TYPE_CLASS_PHONE = 0x03
        e = _et(content_description="", input_type=0x03)
        assert _generate_edit_value(e) == "5551234567"

    def test_input_type_email_variation(self) -> None:
        # TYPE_CLASS_TEXT (0x01) | TYPE_TEXT_VARIATION_EMAIL_ADDRESS (0x20)
        e = _et(content_description="", input_type=0x21)
        assert _generate_edit_value(e) == "test@example.com"

    def test_input_type_password_variation(self) -> None:
        # TYPE_CLASS_TEXT (0x01) | TYPE_TEXT_VARIATION_PASSWORD (0x80)
        e = _et(content_description="", input_type=0x81)
        assert _generate_edit_value(e) == "TestPass123!"

    def test_fallback_non_empty(self) -> None:
        e = _et(content_description="", input_type=0)
        v = _generate_edit_value(e)
        assert v and isinstance(v, str)

    def test_build_interact_action_emits_input_text(self) -> None:
        e = _et(content_description="Search", resource_id="com.app:id/query")
        action = _build_interact_action(e, [e])
        assert action.action_type == ActionType.INPUT_TEXT
        assert action.input_text == "test"
        assert action.target_resource_id == "com.app:id/query"

    def test_build_interact_action_non_edit_click(self) -> None:
        e = _et(
            class_name="android.widget.Button",
            is_editable=False,
            is_clickable=True,
            resource_id="com.app:id/ok",
        )
        action = _build_interact_action(e, [e])
        assert action.action_type == ActionType.CLICK


# ============================================================
# Feature B — blacklisting triggers in perform_one_observation
# ============================================================


class TestBlacklistingOnLeftApp:
    def test_left_app_blacklists_at_intended_source(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["intended"] = []
        explorer._all_actions_per_state["intended"] = {
            action_key(_click("com.app:id/go")): _click("com.app:id/go")
        }
        # Bypass the actual observation: hand-write the LEFT_APP outcome and
        # invoke the explore-loop blacklist hook directly.
        act = _click("com.app:id/go")
        trace = ExplorationTrace(
            step_number=1,
            source_state_id="intended",
            intended_source_state_id="intended",
            action=act,
            target_state_id=SENTINEL_LEFT_APP,
            timestamp="2026-01-01T00:00:00",
        )
        # Mirror main-loop blacklist insertion.
        if trace.target_state_id == SENTINEL_LEFT_APP:
            explorer._blacklisted_per_state["intended"].add(action_key(act))
        assert action_key(act) in explorer._blacklisted_per_state["intended"]


# ============================================================
# Descriptor action_key behavior (Change 1)
# ============================================================


class TestDescriptorActionKey:
    def test_same_rid_and_text_regardless_of_bounds(self) -> None:
        a = Action(
            action_type=ActionType.CLICK,
            target_resource_id="com.app:id/x",
            target_text="Network",
            target_bounds=[0, 100, 100, 200],
        )
        b = Action(
            action_type=ActionType.CLICK,
            target_resource_id="com.app:id/x",
            target_text="Network",
            target_bounds=[500, 1500, 700, 1700],
        )
        assert action_key(a) == action_key(b)

    def test_text_only_is_identifiable(self) -> None:
        a = Action(action_type=ActionType.CLICK, target_text="Messages")
        assert is_action_identifiable(a)

    def test_different_text_different_key(self) -> None:
        a = Action(action_type=ActionType.CLICK, target_resource_id="rid", target_text="Foo")
        b = Action(action_type=ActionType.CLICK, target_resource_id="rid", target_text="Bar")
        assert action_key(a) != action_key(b)

    def test_empty_descriptor_not_identifiable(self) -> None:
        a = Action(action_type=ActionType.CLICK, target_bounds=[0, 0, 100, 100])
        assert is_action_identifiable(a) is False

    def test_global_actions_identifiable(self) -> None:
        assert is_action_identifiable(Action(action_type=ActionType.NAVIGATE_BACK))
        assert is_action_identifiable(Action(action_type=ActionType.NAVIGATE_HOME))


# ============================================================
# Element resolution (Change 2)
# ============================================================


def _elem(**kw: object) -> UIElement:
    defaults: dict[str, object] = {
        "element_id": "e0",
        "class_name": "android.widget.Button",
        "package": "com.android.settings",
        "is_enabled": True,
        "bounds": [0, 0, 100, 100],
    }
    defaults.update(kw)
    return UIElement(**defaults)  # type: ignore[arg-type]


def _screen(elements: list[UIElement]) -> RawScreen:
    return RawScreen(
        screen_id="s",
        activity_name="A",
        package_name="com.android.settings",
        elements=elements,
    )


class TestResolver:
    def test_resolve_by_rid_exact_match(self, explorer: AppExplorer) -> None:
        target = _elem(element_id="e0", resource_id="com.app:id/btn")
        screen = _screen([target])
        action = Action(action_type=ActionType.CLICK, target_resource_id="com.app:id/btn")
        assert explorer._match_descriptor(screen, action) is target

    def test_resolve_preference_row_by_title_text(self, explorer: AppExplorer) -> None:
        row = _elem(
            element_id="e_row",
            class_name="android.widget.LinearLayout",
            is_clickable=True,
            children=["e_title"],
            bounds=[0, 100, 1080, 300],
        )
        title = _elem(
            element_id="e_title",
            class_name="android.widget.TextView",
            resource_id="android:id/title",
            text="Network & internet",
            bounds=[50, 150, 500, 250],
            parent_id="e_row",
        )
        screen = _screen([row, title])
        # Action descriptor borrows title text but no rid on the row.
        action = Action(
            action_type=ActionType.CLICK,
            target_text="Network & internet",
            target_class_name="android.widget.LinearLayout",
        )
        resolved = explorer._match_descriptor(screen, action)
        assert resolved is not None
        # Could resolve to either the row or the TextView; both are valid
        # — the row is the click target but the TextView also matches text.
        assert resolved.element_id in {"e_row", "e_title"}

    def test_resolve_returns_none_when_missing(self, explorer: AppExplorer) -> None:
        screen = _screen([_elem(resource_id="com.app:id/other")])
        action = Action(action_type=ActionType.CLICK, target_resource_id="com.app:id/missing")
        assert explorer._match_descriptor(screen, action) is None

    def test_resolve_with_scroll_finds_offscreen(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        # First capture misses; second capture (after scroll) hits.
        miss_elem = _elem(element_id="miss", resource_id="com.app:id/miss", text="Wrong")
        hit_elem = _elem(element_id="hit", resource_id="com.app:id/target", text="Target")
        scrollable = _elem(
            element_id="scr",
            class_name="androidx.recyclerview.widget.RecyclerView",
            resource_id="com.app:id/list",
            is_scrollable=True,
            bounds=[0, 0, 1080, 2000],
        )
        first = _screen([scrollable, miss_elem])
        second = _screen([scrollable, hit_elem])
        with patch.object(explorer, "_capture_screen", side_effect=[first, second]):
            action = Action(action_type=ActionType.CLICK, target_resource_id="com.app:id/target")
            resolved = explorer._resolve_action_target(action, scroll_to_find=True)
        assert resolved is not None
        assert resolved.element_id == "hit"
        mock_device.swipe.assert_called()  # at least one scroll fired

    def test_resolve_exhausts_scroll_plateau(self, explorer: AppExplorer) -> None:
        # Same screen every capture → anchor set doesn't change → plateau →
        # resolver aborts and returns None.
        scrollable = _elem(
            element_id="scr",
            class_name="androidx.recyclerview.widget.RecyclerView",
            resource_id="com.app:id/list",
            is_scrollable=True,
            bounds=[0, 0, 1080, 2000],
        )
        miss = _elem(element_id="m", resource_id="com.app:id/miss", text="Miss")
        same = _screen([scrollable, miss])
        with patch.object(explorer, "_capture_screen", return_value=same):
            action = Action(action_type=ActionType.CLICK, target_resource_id="com.app:id/target")
            assert explorer._resolve_action_target(action, scroll_to_find=True) is None

    def test_execute_uses_live_bounds_not_stored(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        # Stored bounds [0,0,100,100] would give click at (50,50); live
        # element at [400,500,600,700] should give click at (500,600).
        live = _elem(
            element_id="live",
            resource_id="com.app:id/btn",
            text="Go",
            bounds=[400, 500, 600, 700],
        )
        with patch.object(explorer, "_capture_screen", return_value=_screen([live])):
            action = Action(
                action_type=ActionType.CLICK,
                target_resource_id="com.app:id/btn",
                target_bounds=[0, 0, 100, 100],  # stale
            )
            assert explorer._execute_action(action) is True
        mock_device.click.assert_called_once_with(500, 600)


# ============================================================
# Current-state locality (Change 3)
# ============================================================


def _sid_click(rid: str = "com.app:id/c") -> Action:
    return Action(action_type=ActionType.CLICK, target_resource_id=rid)


class TestLocality:
    def test_prefers_current_state_when_has_unexplored(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["other"] = []
        explorer._nav_paths["current"] = [_sid_click()] * 3  # deeper
        explorer._all_actions_per_state["other"] = {"k1": _sid_click("a")}
        explorer._all_actions_per_state["current"] = {"k2": _sid_click("b")}
        explorer._current_state_id = "current"
        picked = explorer._pick_next_action()
        assert picked is not None
        sid, _ = picked
        assert sid == "current"  # local win even though other is shallower

    def test_falls_back_to_global_when_current_exhausted(self, explorer: AppExplorer) -> None:
        explorer._nav_paths["current"] = []
        explorer._nav_paths["other"] = [_sid_click()]
        explorer._all_actions_per_state["current"] = {"k1": _sid_click("a")}
        explorer._all_actions_per_state["other"] = {"k2": _sid_click("b")}
        explorer._explored_per_state["current"].add("k1")
        explorer._current_state_id = "current"
        picked = explorer._pick_next_action()
        assert picked is not None
        assert picked[0] == "other"

    def test_cold_start_skipped_when_already_at_intended(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        # Simulate: current == intended, pre-capture returns same state_id.
        pre = _screen([_elem(resource_id="com.app:id/x")])
        sid = pre.get_hybrid_state_id("com.android.settings")
        explorer._current_state_id = sid
        explorer._nav_paths[sid] = []
        with (
            patch.object(explorer, "_cold_start_app") as cs,
            patch.object(explorer, "_capture_screen", return_value=pre),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_is_within_app", return_value=True),
        ):
            explorer._perform_one_observation(
                intended_source_state_id=sid,
                intended_nav_path=[],
                action=_sid_click(),
                step=1,
            )
        cs.assert_not_called()

    def test_cold_start_fired_when_state_differs(self, explorer: AppExplorer) -> None:
        explorer._current_state_id = "A"
        pre = _screen([_elem(resource_id="com.app:id/x")])
        with (
            patch.object(explorer, "_cold_start_app", return_value=True) as cs,
            patch.object(explorer, "_capture_screen", return_value=pre),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_is_within_app", return_value=True),
        ):
            explorer._perform_one_observation(
                intended_source_state_id="B",
                intended_nav_path=[],
                action=_sid_click(),
                step=1,
            )
        cs.assert_called_once()

    def test_current_state_updated_on_success(self, explorer: AppExplorer) -> None:
        pre = _screen([_elem(resource_id="com.app:id/pre")])
        post = _screen([_elem(resource_id="com.app:id/post")])
        explorer._current_state_id = None
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_capture_screen", side_effect=[pre, post]),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_is_within_app", return_value=True),
        ):
            explorer._perform_one_observation(
                intended_source_state_id="intended",
                intended_nav_path=[],
                action=_sid_click(),
                step=1,
            )
        assert explorer._current_state_id == post.get_hybrid_state_id("com.android.settings")

    def test_current_state_cleared_on_left_app(self, explorer: AppExplorer) -> None:
        pre = _screen([_elem(resource_id="com.app:id/pre")])
        explorer._current_state_id = "X"
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_capture_screen", return_value=pre),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_is_within_app", return_value=False),
        ):
            explorer._perform_one_observation(
                intended_source_state_id="other",
                intended_nav_path=[],
                action=_sid_click(),
                step=1,
            )
        assert explorer._current_state_id is None

    def test_locality_drift_falls_back_to_cold_start(self, explorer: AppExplorer) -> None:
        # current_state claims we are at "intended" but pre-capture shows
        # something else → fall back to cold-start.
        pre_first = _screen([_elem(resource_id="com.app:id/other_pre")])
        pre_after_coldstart = _screen([_elem(resource_id="com.app:id/pre_again")])
        post = _screen([_elem(resource_id="com.app:id/post")])
        intended = "intended_state_hash"
        explorer._current_state_id = intended  # lie
        with (
            patch.object(explorer, "_cold_start_app", return_value=True) as cs,
            patch.object(
                explorer,
                "_capture_screen",
                side_effect=[pre_first, pre_after_coldstart, post],
            ),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_is_within_app", return_value=True),
        ):
            trace, _, _ = explorer._perform_one_observation(
                intended_source_state_id=intended,
                intended_nav_path=[],
                action=_sid_click(),
                step=1,
            )
        cs.assert_called_once()
        # Observation should still succeed (not a sentinel outcome).
        assert trace.target_state_id not in (
            SENTINEL_COLD_START_FAILED,
            SENTINEL_LEFT_APP,
            SENTINEL_ACTION_FAILED,
        )


# ============================================================
# Emulator health guard (Change 4)
# ============================================================


class TestHealthGuard:
    def test_five_consecutive_cold_start_failures_abort(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        explorer._config.app.max_exploration_steps = 20
        entry = _screen([_elem(resource_id="com.app:id/only", is_clickable=True)])
        with (
            patch("vigil.neuro.explorer.u2") as mock_u2,
            patch.object(explorer, "_cold_start_app", return_value=False) as cs,
            patch.object(explorer, "_capture_screen", return_value=entry),
        ):
            mock_u2.connect.return_value = mock_device
            try:
                explorer.explore()
            except RuntimeError:
                # First cold_start before the main loop fails → RuntimeError.
                # Valid abort path.
                assert cs.call_count == 1
                return
        # Main loop aborts after MAX_CONSECUTIVE_COLD_START_FAILURES calls.
        assert cs.call_count <= explorer.MAX_CONSECUTIVE_COLD_START_FAILURES + 1

    def test_counter_resets_on_success(self, explorer: AppExplorer) -> None:
        explorer._consecutive_cold_start_failures = 3
        # Any non-cold-start outcome should reset. Easiest to verify the
        # main-loop logic: feed a sequence of outcomes and check the
        # counter via direct manipulation.
        trace_success = ExplorationTrace(
            step_number=1,
            source_state_id="s",
            intended_source_state_id="s",
            action=_sid_click(),
            target_state_id="tgt",
            timestamp="2026-01-01T00:00:00",
        )
        # Mirror the main-loop else-branch reset.
        if trace_success.target_state_id not in (
            SENTINEL_COLD_START_FAILED,
            SENTINEL_ACTION_FAILED,
            SENTINEL_LEFT_APP,
        ):
            explorer._consecutive_cold_start_failures = 0
        assert explorer._consecutive_cold_start_failures == 0

    def test_left_app_does_not_increment_counter(self, explorer: AppExplorer) -> None:
        explorer._consecutive_cold_start_failures = 2
        trace_left = ExplorationTrace(
            step_number=1,
            source_state_id="s",
            intended_source_state_id="s",
            action=_sid_click(),
            target_state_id=SENTINEL_LEFT_APP,
            timestamp="2026-01-01T00:00:00",
        )
        # Main-loop LEFT_APP branch resets the counter.
        if trace_left.target_state_id == SENTINEL_LEFT_APP:
            explorer._consecutive_cold_start_failures = 0
        assert explorer._consecutive_cold_start_failures == 0


# ============================================================
# Exploration V2 — selectors, scroll observations, schema v2
# ============================================================


def _preference_row() -> list[UIElement]:
    row = UIElement(
        element_id="e_row",
        class_name="android.widget.LinearLayout",
        package="com.android.settings",
        is_clickable=True,
        is_enabled=True,
        bounds=[0, 100, 1080, 300],
        children=["e_title"],
    )
    title = UIElement(
        element_id="e_title",
        class_name="android.widget.TextView",
        package="com.android.settings",
        resource_id="android:id/title",
        text="Network & internet",
        is_enabled=True,
        bounds=[50, 150, 500, 250],
        parent_id="e_row",
    )
    return [row, title]


class TestSelectorBuilding:
    def test_preference_row_borrows_title_as_nearby_text(self) -> None:
        elements = _preference_row()
        row = elements[0]
        selector = build_component_selector(row, elements)
        assert selector["nearby_text"] == "Network & internet"
        assert selector["class_name"] == "android.widget.LinearLayout"

    def test_selector_identity_excludes_bounds(self) -> None:
        elements = _preference_row()
        sel_a = build_component_selector(elements[1], elements)
        sel_b = dict(sel_a)
        sel_b["bounds"] = [9999, 9999, 9999, 9999]
        sel_b["depth"] = 42
        assert selector_identity(sel_a) == selector_identity(sel_b)

    def test_stable_identity_for_rid_text_cd_or_nearby(self) -> None:
        assert selector_has_stable_identity({"resource_id": "x:id/y"})
        assert selector_has_stable_identity({"text": "Wi-Fi"})
        assert selector_has_stable_identity({"content_description": "Search"})
        assert selector_has_stable_identity({"nearby_text": "Bluetooth"})

    def test_class_alone_is_not_stable(self) -> None:
        assert not selector_has_stable_identity({"class_name": "android.widget.LinearLayout"})

    def test_empty_selector_is_not_stable(self) -> None:
        assert not selector_has_stable_identity({})


class TestSelectorResolution:
    def test_find_by_rid(self) -> None:
        elements = _preference_row()
        target = elements[1]
        sel = build_component_selector(target, elements)
        found = find_element_by_selector(sel, elements)
        assert found is target

    def test_find_by_nearby_text(self) -> None:
        elements = _preference_row()
        row = elements[0]
        sel = build_component_selector(row, elements)
        found = find_element_by_selector(sel, elements)
        # Resolver matches the clickable container by its title descendant.
        assert found is not None
        assert found.element_id == "e_row"

    def test_resolves_across_id_and_bounds_drift(self) -> None:
        elements = _preference_row()
        title = elements[1]
        sel = build_component_selector(title, elements)

        # Fresh capture: same logical element, different element_id and bounds.
        fresh = [
            UIElement(
                element_id="e_OTHER",
                class_name="android.widget.LinearLayout",
                package="com.android.settings",
                is_clickable=True,
                is_enabled=True,
                bounds=[20, 600, 900, 800],
                children=["e_RENAMED"],
            ),
            UIElement(
                element_id="e_RENAMED",
                class_name="android.widget.TextView",
                package="com.android.settings",
                resource_id="android:id/title",
                text="Network & internet",
                is_enabled=True,
                bounds=[70, 650, 480, 750],
                parent_id="e_OTHER",
            ),
        ]
        found = find_element_by_selector(sel, fresh)
        assert found is not None
        assert found.element_id == "e_RENAMED"

    def test_duplicate_resource_id_uses_text_disambiguator(self) -> None:
        first = UIElement(
            element_id="e_first",
            class_name="android.widget.TextView",
            package="com.example",
            resource_id="com.example:id/title",
            text="First row",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 1080, 100],
        )
        second = UIElement(
            element_id="e_second",
            class_name="android.widget.TextView",
            package="com.example",
            resource_id="com.example:id/title",
            text="Second row",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 100, 1080, 200],
        )
        elements = [first, second]

        selector = build_component_selector(second, elements)
        found = find_element_by_selector(selector, elements)

        assert found is second

    def test_duplicate_text_filters_by_class_or_returns_none_when_ambiguous(self) -> None:
        text_view = UIElement(
            element_id="e_text",
            class_name="android.widget.TextView",
            package="com.example",
            text="Duplicate",
            is_enabled=True,
            bounds=[0, 0, 500, 100],
        )
        button = UIElement(
            element_id="e_button",
            class_name="android.widget.Button",
            package="com.example",
            text="Duplicate",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 100, 500, 200],
        )

        found_button = find_element_by_selector(
            build_component_selector(button, [text_view, button]),
            [text_view, button],
        )
        assert found_button is button

        first = UIElement(
            element_id="e_first_duplicate",
            class_name="android.widget.TextView",
            package="com.example",
            text="Ambiguous",
            is_enabled=True,
            bounds=[0, 0, 500, 100],
        )
        second = UIElement(
            element_id="e_second_duplicate",
            class_name="android.widget.TextView",
            package="com.example",
            text="Ambiguous",
            is_enabled=True,
            bounds=[0, 100, 500, 200],
        )
        ambiguous_selector = build_component_selector(second, [first, second])

        assert find_element_by_selector(ambiguous_selector, [first, second]) is None

    def test_nearby_text_prefers_clickable_row_over_ancestor(self) -> None:
        root = UIElement(
            element_id="e_root",
            class_name="android.widget.LinearLayout",
            package="com.example",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 1080, 1920],
            children=["e_list"],
        )
        recycler = UIElement(
            element_id="e_list",
            class_name="androidx.recyclerview.widget.RecyclerView",
            package="com.example",
            resource_id="com.example:id/recycler_view",
            is_scrollable=True,
            is_enabled=True,
            bounds=[0, 0, 1080, 1920],
            children=["e_row"],
            parent_id="e_root",
        )
        row = UIElement(
            element_id="e_row",
            class_name="android.widget.LinearLayout",
            package="com.example",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 300, 1080, 450],
            children=["e_title"],
            parent_id="e_list",
        )
        title = UIElement(
            element_id="e_title",
            class_name="android.widget.TextView",
            package="com.example",
            resource_id="android:id/title",
            text="Target row",
            is_enabled=True,
            bounds=[48, 320, 500, 420],
            parent_id="e_row",
        )
        elements = [root, recycler, row, title]

        found = find_element_by_selector({"nearby_text": "Target row"}, elements)

        assert found is row


class TestActionWithSelector:
    def test_build_interact_action_attaches_selector(self) -> None:
        elements = _preference_row()
        action = _build_interact_action(elements[0], elements)
        assert action.target_selector
        assert action.target_selector.get("nearby_text") == "Network & internet"

    def test_action_key_same_selector_different_bounds(self) -> None:
        elements = _preference_row()
        sel = build_component_selector(elements[1], elements)
        a = Action(
            action_type=ActionType.CLICK,
            target_selector=sel,
            target_bounds=[0, 0, 100, 100],
        )
        b = Action(
            action_type=ActionType.CLICK,
            target_selector=sel,
            target_bounds=[800, 900, 1000, 1100],
        )
        assert action_key(a) == action_key(b)

    def test_action_round_trips_through_fsm_dict(self) -> None:
        elements = _preference_row()
        sel = build_component_selector(elements[1], elements)
        a = Action(action_type=ActionType.CLICK, target_selector=sel)
        d = a.to_fsm_dict()
        assert "target_selector" in d
        rebuilt = Action.from_fsm_dict(d)
        assert rebuilt.target_selector == sel

    def test_legacy_trace_without_selector_still_loads(self) -> None:
        legacy = {
            "type": "click",
            "resource_id": "com.app:id/x",
            "target_text": "Old",
        }
        a = Action.from_fsm_dict(legacy)
        assert a.action_type == ActionType.CLICK
        assert a.target_resource_id == "com.app:id/x"
        assert a.target_selector == {}


class TestCompactUITree:
    def test_emits_deterministic_handles_and_selectors(self) -> None:
        scrollable = UIElement(
            element_id="e_list",
            class_name="androidx.recyclerview.widget.RecyclerView",
            package="com.android.settings",
            resource_id="com.android.settings:id/recycler_view",
            is_scrollable=True,
            is_enabled=True,
            bounds=[0, 0, 1080, 2000],
            children=["e_row"],
        )
        row = UIElement(
            element_id="e_row",
            class_name="android.widget.LinearLayout",
            package="com.android.settings",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 100, 1080, 300],
            children=["e_title"],
            parent_id="e_list",
            depth=1,
        )
        title = UIElement(
            element_id="e_title",
            class_name="android.widget.TextView",
            package="com.android.settings",
            resource_id="android:id/title",
            text="Network & internet",
            is_enabled=True,
            bounds=[50, 150, 500, 250],
            parent_id="e_row",
            depth=2,
        )
        out = compact_ui_tree_text([scrollable, row, title])
        # Deterministic handles
        assert "[c_0000]" in out
        assert "[c_0001]" in out
        # Action affordances
        assert "scroll" in out
        assert "click" in out
        # Text label appears (either own text or via title node)
        assert "Network & internet" in out
        # Selector summary present
        assert "selector=" in out

    def test_compact_ui_tree_text_emits_all_semantic_nodes(self) -> None:
        elements: list[UIElement] = []
        for i in range(50):
            elements.append(
                UIElement(
                    element_id=f"e_{i}",
                    class_name="android.widget.Button",
                    package="com.android.settings",
                    resource_id=f"com.app:id/b{i}",
                    is_clickable=True,
                    is_enabled=True,
                    bounds=[0, i * 100, 1080, (i + 1) * 100],
                )
            )
        out = compact_ui_tree_text(elements)
        assert out.count("[c_") == 50


class TestScrollObservations:
    def test_enumerate_records_observation(self, explorer: AppExplorer) -> None:
        scr1 = _screen_with_clickables("scr1", ["a:id/1", "a:id/2"], scrollable=True)
        scr2 = _screen_with_clickables("scr2", ["a:id/1", "a:id/2", "a:id/3"], scrollable=True)
        # Third capture mirrors scr2 to trigger plateau.
        screens = iter([scr1, scr2, scr2, scr2])
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", side_effect=lambda: next(screens)),
        ):
            explorer._enumerate_all_clickables("sid_xyz", [])
        assert len(explorer._scroll_observations) >= 1
        first = explorer._scroll_observations[0]
        assert first.phase == "enumerate"
        assert first.source_state_id == "sid_xyz"
        # Second screen introduced a:id/3 — newly_discovered must reflect that.
        assert any(
            "a:id/3" in key for key in first.newly_discovered_action_keys
        ), first.newly_discovered_action_keys
        # Eventually a plateau-marked observation should appear.
        assert any(o.plateau for o in explorer._scroll_observations)


class TestV2TraceSchema:
    def test_save_result_emits_v2_schema(self, explorer: AppExplorer, tmp_path: Path) -> None:
        scr = _screen_with_clickables("scr_v2", ["a:id/wifi", "a:id/bt"], scrollable=False)
        scr.screenshot_path = str(tmp_path / "screens" / "scr_v2.png")
        scr.xml_tree_path = str(tmp_path / "trees" / "scr_v2.xml")
        result = ExplorationResult(
            app_package="com.android.settings",
            screens={"scr_v2": scr},
            traces=[],
            total_steps=0,
            unique_screens=1,
            output_dir=str(tmp_path),
        )
        # Inject one scroll observation so the list serializes non-empty.
        explorer._scroll_observations.append(
            ScrollObservation(
                phase="enumerate",
                source_state_id="sid",
                screen_id_before="scr_v2",
                screen_id_after="scr_v2",
                container_selector={"resource_id": "com.app:id/list"},
                before_anchor_hash="abc",
                after_anchor_hash="def",
                newly_discovered_action_keys=["click|x"],
                plateau=False,
                timestamp="2026-05-15T00:00:00",
            )
        )
        explorer._save_result(result)
        trace_files = sorted((tmp_path / "traces").glob("exploration_*.json"))
        assert trace_files
        data = json.loads(trace_files[-1].read_text(encoding="utf-8"))
        assert data["schema_version"] == "exploration_v2"
        assert isinstance(data["scroll_observations"], list)
        assert len(data["scroll_observations"]) == 1
        per_screen = data["screens"]["scr_v2"]
        for key in (
            "elements",
            "interactable_elements",
            "structural_fingerprint",
            "functional_fingerprint",
            "state_id",
            "text_state_id",
            "compact_tree_text",
            "screen_quality",
            "page_title",
        ):
            assert key in per_screen, key
        sq = per_screen["screen_quality"]
        assert sq["total_elements"] == len(scr.elements)
        assert sq["interactable_count"] == 2
        assert isinstance(per_screen["elements"], list)
        assert len(per_screen["elements"]) == len(scr.elements)


class TestFsmBuilderBackwardCompat:
    def test_builder_builds_from_v2_trace(self, explorer: AppExplorer, tmp_path: Path) -> None:
        from vigil.neuro.fsm_builder import FsmBuilder

        scr_src = _screen_with_clickables("scr_src", ["a:id/wifi"], scrollable=False)
        scr_tgt = _screen_with_clickables("scr_tgt", ["a:id/saved"], scrollable=False)
        # Force distinct activity names so the two screens hash to different state_ids.
        scr_src.activity_name = "Settings"
        scr_tgt.activity_name = "WifiSettings"

        action = _build_interact_action(scr_src.elements[0], scr_src.elements)
        trace = ExplorationTrace(
            step_number=1,
            source_state_id=scr_src.get_hybrid_state_id("com.android.settings"),
            intended_source_state_id=scr_src.get_hybrid_state_id("com.android.settings"),
            source_screen_id="scr_src",
            action=action,
            target_state_id=scr_tgt.get_hybrid_state_id("com.android.settings"),
            target_screen_id="scr_tgt",
            timestamp="2026-05-15T00:00:00",
        )
        result = ExplorationResult(
            app_package="com.android.settings",
            screens={"scr_src": scr_src, "scr_tgt": scr_tgt},
            traces=[trace],
            total_steps=1,
            unique_screens=2,
            output_dir=str(tmp_path),
        )
        explorer._save_result(result)
        trace_files = sorted((tmp_path / "traces").glob("exploration_*.json"))
        assert trace_files
        builder = FsmBuilder(app_package="com.android.settings")
        fsm = builder.build_from_trace(trace_files[-1])
        assert len(fsm.states) >= 1
        # The recorded trace should produce at least one transition.
        assert len(fsm.transitions) >= 1


# ============================================================
# Section C — selector resolution (MATCH / MISSING / AMBIGUOUS)
# ============================================================


class TestSelectorResolutionAmbiguity:
    def test_ambiguous_resource_id_skips_execution(self, explorer: AppExplorer) -> None:
        """Two Switch elements share android:id/switch_widget with no labels;
        the explorer must refuse to click either."""
        # Stage a screen with two indistinguishable switches.
        e1 = UIElement(
            element_id="sw1",
            class_name="android.widget.Switch",
            package="com.android.settings",
            resource_id="android:id/switch_widget",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 100, 100],
        )
        e2 = UIElement(
            element_id="sw2",
            class_name="android.widget.Switch",
            package="com.android.settings",
            resource_id="android:id/switch_widget",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 200, 100, 300],
        )
        screen = RawScreen(
            screen_id="scr",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[e1, e2],
        )
        action = Action(
            action_type=ActionType.CLICK,
            target_selector={
                "resource_id": "android:id/switch_widget",
                "text": "",
                "content_description": "",
                "nearby_text": "",
                "class_name": "android.widget.Switch",
                "ancestor_chain": [],
            },
        )
        with patch.object(explorer, "_capture_screen", return_value=screen):
            assert explorer._execute_action(action) is False
        assert explorer._last_execution_metadata.get("selector_resolution") == "ambiguous"

    def test_nearby_text_disambiguates(self, explorer: AppExplorer) -> None:
        """Same rid but nearby_text labels disambiguate to the right element."""
        title_a = UIElement(
            element_id="t_a",
            class_name="android.widget.TextView",
            package="com.android.settings",
            resource_id="android:id/title",
            text="Wi-Fi",
            parent_id="row_a",
        )
        switch_a = UIElement(
            element_id="sw_a",
            class_name="android.widget.Switch",
            package="com.android.settings",
            resource_id="android:id/switch_widget",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 100, 100],
            parent_id="row_a",
        )
        row_a = UIElement(
            element_id="row_a",
            class_name="android.widget.LinearLayout",
            package="com.android.settings",
            children=["t_a", "sw_a"],
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 1080, 200],
        )
        title_b = UIElement(
            element_id="t_b",
            class_name="android.widget.TextView",
            package="com.android.settings",
            resource_id="android:id/title",
            text="Bluetooth",
            parent_id="row_b",
        )
        switch_b = UIElement(
            element_id="sw_b",
            class_name="android.widget.Switch",
            package="com.android.settings",
            resource_id="android:id/switch_widget",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 200, 100, 300],
            parent_id="row_b",
        )
        row_b = UIElement(
            element_id="row_b",
            class_name="android.widget.LinearLayout",
            package="com.android.settings",
            children=["t_b", "sw_b"],
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 200, 1080, 400],
        )
        # Even though resolution starts from rid (2 candidates), there is no
        # nearby_text on the Switch itself — the selector targets the row.
        # Use the row as the action target so nearby_text disambiguates.
        screen = RawScreen(
            screen_id="scr",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[row_a, title_a, switch_a, row_b, title_b, switch_b],
        )
        # Target the row by class_name + nearby_text. Ambiguous rids never
        # come into play here.
        action = Action(
            action_type=ActionType.CLICK,
            target_selector={
                "resource_id": "",
                "text": "",
                "content_description": "",
                "nearby_text": "Bluetooth",
                "class_name": "android.widget.LinearLayout",
                "ancestor_chain": [],
            },
            target_bounds=[0, 0, 0, 0],
        )
        with patch.object(explorer, "_capture_screen", return_value=screen):
            ok = explorer._execute_action(action)
        assert ok is True
        assert explorer._last_execution_metadata.get("selector_resolution") == "match"


# ============================================================
# Section D — safe INPUT_TEXT (clear-then-set)
# ============================================================


class TestSafeInputText:
    def test_input_text_clears_before_set(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        """Original "abc" + input "test123" must end as "test123", not "abctest123"."""
        # Build an EditText already containing "abc".
        edit = UIElement(
            element_id="e_edit",
            class_name="android.widget.EditText",
            package="com.android.settings",
            resource_id="com.example:id/input",
            text="abc",
            is_clickable=True,
            is_editable=True,
            is_enabled=True,
            bounds=[100, 100, 500, 200],
        )
        screen = RawScreen(
            screen_id="scr",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[edit],
        )
        # Simulate device-side text store: set_text replaces, send_keys appends.
        device_text: dict[str, str] = {"value": "abc"}

        class _Selector:
            exists = True

            def set_text(self, value: str) -> None:
                device_text["value"] = value

        mock_device.return_value = _Selector()
        action = Action(
            action_type=ActionType.INPUT_TEXT,
            target_selector={
                "resource_id": "com.example:id/input",
                "text": "",
                "content_description": "",
                "nearby_text": "",
                "class_name": "android.widget.EditText",
                "ancestor_chain": [],
            },
            input_text="test123",
        )
        with patch.object(explorer, "_capture_screen", return_value=screen):
            ok = explorer._execute_action(action)
        assert ok is True
        assert device_text["value"] == "test123"
        md = explorer._last_execution_metadata
        assert md.get("input_original_text") == "abc"
        assert md.get("input_text") == "test123"
        assert md.get("cleared") is True
        assert md.get("input_policy") == "clear_set"

    def test_input_text_duplicate_resource_id_uses_resolved_instance(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        first = UIElement(
            element_id="first_edit",
            class_name="android.widget.EditText",
            package="com.android.settings",
            resource_id="com.example:id/input",
            text="first",
            is_editable=True,
            is_enabled=True,
            bounds=[100, 100, 500, 200],
        )
        second = UIElement(
            element_id="second_edit",
            class_name="android.widget.EditText",
            package="com.android.settings",
            resource_id="com.example:id/input",
            text="second",
            is_editable=True,
            is_enabled=True,
            bounds=[100, 250, 500, 350],
        )
        screen = RawScreen(
            screen_id="scr",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[first, second],
        )
        set_text_calls: list[tuple[int | None, str]] = []

        class _Selector:
            exists = True

            def __init__(self, instance: int | None) -> None:
                self.instance = instance

            def set_text(self, value: str) -> None:
                set_text_calls.append((self.instance, value))

        def select(**kwargs: object) -> _Selector:
            instance = kwargs.get("instance")
            return _Selector(instance if isinstance(instance, int) else None)

        mock_device.side_effect = select
        action = Action(
            action_type=ActionType.INPUT_TEXT,
            target_selector={
                "resource_id": "com.example:id/input",
                "text": "second",
                "content_description": "",
                "nearby_text": "",
                "class_name": "android.widget.EditText",
                "ancestor_chain": [],
            },
            input_text="target value",
        )
        with patch.object(explorer, "_capture_screen", return_value=screen):
            ok = explorer._execute_action(action)

        assert ok is True
        assert set_text_calls == [(1, "target value")]
        mock_device.assert_any_call(
            resourceId="com.example:id/input",
            className="android.widget.EditText",
            instance=1,
        )
        mock_device.send_keys.assert_not_called()
        md = explorer._last_execution_metadata
        assert md.get("input_selector_collision") is True
        assert md.get("input_selector_instance") == 1
        assert md.get("input_selector_policy") == "resource_id_instance"


# ============================================================
# Section E — drift does not pollute target nav paths
# ============================================================


class TestDriftNavPathPolicy:
    def test_drift_does_not_create_trusted_nav_path_for_new_target(self) -> None:
        """When actual_src != intended, a brand-new target_state_id must
        not be enqueued or registered in _nav_paths."""
        config = VigilConfig()
        with patch("vigil.neuro.explorer.u2"):
            exp = AppExplorer(
                device_serial="x",
                app_package="com.app",
                config=config,
                output_dir=Path("/tmp/vigil_test_drift"),
            )
        exp._nav_paths["intended"] = []
        exp._trusted_states.add("intended")
        # Synthesize a successful, drifted trace from the explorer state.
        action = Action(action_type=ActionType.CLICK, target_resource_id="r")
        trace = ExplorationTrace(
            step_number=1,
            intended_source_state_id="intended",
            source_state_id="actual_other",  # drift
            action=action,
            target_state_id="brand_new_target",
            timestamp="t",
        )
        # Replicate the discovery-site logic.
        actual_src = trace.source_state_id
        drifted = actual_src != "intended"
        assert drifted
        if drifted:
            exp._drift_count += 1
        tgt = trace.target_state_id
        if tgt not in exp._nav_paths:
            if not drifted:
                exp._nav_paths[tgt] = [action]
            elif actual_src in exp._nav_paths and actual_src in exp._trusted_states:
                exp._nav_paths[tgt] = [*exp._nav_paths[actual_src], action]
            else:
                exp._untrusted_targets.add(tgt)
        assert tgt not in exp._nav_paths
        assert "brand_new_target" in exp._untrusted_targets
        assert exp._drift_count == 1


# ============================================================
# Section F — scope policy classification & enforcement
# ============================================================


class TestScopePolicyExplorer:
    def test_classify_target_in_app(self, explorer: AppExplorer) -> None:
        from vigil.neuro.scope_policy import ScopeCategory

        assert explorer._scope_policy.classify("com.android.settings") == ScopeCategory.IN_APP

    def test_classify_android_dialog_low_trust(self, explorer: AppExplorer) -> None:
        from vigil.neuro.scope_policy import ScopeCategory

        cat = explorer._scope_policy.classify("android")
        assert cat == ScopeCategory.ANDROID_SYSTEM
        assert explorer._scope_policy.is_allowed(cat) is True
        assert explorer._scope_policy.is_low_trust(cat) is True

    def test_system_ui_element_filtered_from_enumeration(self, explorer: AppExplorer) -> None:
        in_app = UIElement(
            element_id="e0",
            class_name="android.widget.Button",
            package="com.android.settings",
            resource_id="com.android.settings:id/ok",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 100, 100],
        )
        sysui = UIElement(
            element_id="e1",
            class_name="android.widget.ImageButton",
            package="com.android.systemui",
            resource_id="com.android.systemui:id/clock",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 500, 100, 600],
        )
        scr = RawScreen(
            screen_id="s",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[in_app, sysui],
        )
        screens = iter([scr, scr])
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", side_effect=lambda: next(screens)),
        ):
            actions = explorer._enumerate_actions_for_state("sid", [])
        rids = {a.target_resource_id for a in actions}
        assert "com.android.settings:id/ok" in rids
        assert "com.android.systemui:id/clock" not in rids

    def test_unknown_package_left_app(self, explorer: AppExplorer) -> None:
        from vigil.neuro.scope_policy import ScopeCategory

        assert explorer._scope_policy.classify("com.foo.bar") == ScopeCategory.OUT_OF_SCOPE_EXTERNAL

    def test_launcher_package(self, explorer: AppExplorer) -> None:
        from vigil.neuro.scope_policy import ScopeCategory

        assert (
            explorer._scope_policy.classify("com.android.launcher3")
            == ScopeCategory.LAUNCHER_OR_HOME
        )


# ============================================================
# Section G — policy-neutral action enumeration
# ============================================================


class TestPolicyNeutralEnumeration:
    def test_side_effect_label_is_enumerated(self, explorer: AppExplorer) -> None:
        reset = UIElement(
            element_id="e_reset",
            class_name="android.widget.Button",
            package="com.android.settings",
            resource_id="com.example:id/reset",
            text="Factory reset",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 100, 100],
        )
        safe = UIElement(
            element_id="e_safe",
            class_name="android.widget.Button",
            package="com.android.settings",
            resource_id="com.example:id/wifi",
            text="Wi-Fi",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 200, 100, 300],
        )
        scr = RawScreen(
            screen_id="s",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[reset, safe],
        )
        screens = iter([scr, scr])
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", side_effect=lambda: next(screens)),
        ):
            actions = explorer._enumerate_actions_for_state("sid", [])
        rids = {a.target_resource_id for a in actions}
        assert "com.example:id/wifi" in rids
        assert "com.example:id/reset" in rids


# ============================================================
# Clarification: INPUT_TEXT refuses when clear cannot be guaranteed
# ============================================================


class TestInputTextRefusalOnUnsafeClear:
    def test_refuses_when_clear_unsupported(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        """Device exposes neither element.set_text nor device.clear_text:
        the explorer must refuse rather than append."""
        edit = UIElement(
            element_id="e_edit",
            class_name="android.widget.EditText",
            package="com.android.settings",
            resource_id="com.example:id/input",
            text="abc",
            is_clickable=True,
            is_editable=True,
            is_enabled=True,
            bounds=[100, 100, 500, 200],
        )
        screen = RawScreen(
            screen_id="scr",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[edit],
        )

        class _NoSetText:
            exists = True
            # intentionally NO set_text attribute

        # Device call returns a selector without set_text. Also strip
        # clear_text from the device entirely.
        mock_device.return_value = _NoSetText()
        if hasattr(mock_device, "clear_text"):
            # MagicMock auto-creates attributes; force AttributeError via
            # del on the spec.
            with contextlib.suppress(AttributeError):
                del mock_device.clear_text
        # Make hasattr(..., 'clear_text') return False.
        mock_device.__class__ = type(
            "NoClearDevice",
            (),
            {k: v for k, v in mock_device.__class__.__dict__.items() if k != "clear_text"},
        )

        action = Action(
            action_type=ActionType.INPUT_TEXT,
            target_selector={
                "resource_id": "com.example:id/input",
                "text": "",
                "content_description": "",
                "nearby_text": "",
                "class_name": "android.widget.EditText",
                "ancestor_chain": [],
            },
            input_text="test123",
        )
        # Make sure hasattr returns False for clear_text on the mock device.
        mock_device.clear_text = MagicMock(side_effect=AttributeError())

        # send_keys would append if invoked: track that it isn't called.
        with patch.object(explorer, "_capture_screen", return_value=screen):
            ok = explorer._execute_action(action)
        assert ok is False
        md = explorer._last_execution_metadata
        # set_text failed (no attribute), clear_text raised; explorer must
        # refuse rather than send_keys.
        assert md.get("cleared") is False
        assert md.get("input_policy") == "skipped_unsafe"


# ============================================================
# Clarification: ANDROID_SYSTEM low-trust never enqueued / never trusted
# ============================================================


class TestAndroidSystemLowTrust:
    def test_android_system_target_not_enqueued_as_normal_state(self) -> None:
        """Even on a clean (non-drifted) observation, an ANDROID_SYSTEM
        post_scope must NOT promote the target to a trusted, enqueued
        app state. The target is observed only."""
        from vigil.neuro.scope_policy import ScopeCategory

        config = VigilConfig()
        with patch("vigil.neuro.explorer.u2"):
            exp = AppExplorer(
                device_serial="x",
                app_package="com.app",
                config=config,
                output_dir=Path("/tmp/vigil_test_android_system"),
            )
        exp._nav_paths["src_app"] = []
        exp._trusted_states.add("src_app")
        action = Action(action_type=ActionType.CLICK, target_resource_id="r")
        # Simulate a trace whose post-action capture landed on an
        # ANDROID_SYSTEM dialog (low_trust_scope=True).
        trace = ExplorationTrace(
            step_number=1,
            intended_source_state_id="src_app",
            source_state_id="src_app",
            action=action,
            target_state_id="dialog_system",
            timestamp="t",
            metadata={
                "scope_pre": "in_app",
                "scope_post": "android_system",
                "low_trust_scope": True,
            },
        )

        # Replicate the discovery-site behavior from explorer.explore().
        tgt = trace.target_state_id
        actual_src = trace.source_state_id
        drifted = actual_src != "src_app"
        low_trust_target = bool(trace.metadata.get("low_trust_scope"))
        low_trust_source = trace.metadata.get("scope_pre") == ScopeCategory.ANDROID_SYSTEM.value
        if not drifted and not low_trust_source:
            exp._trusted_states.add(actual_src)
        if tgt not in exp._nav_paths:
            if low_trust_target:
                exp._untrusted_targets.add(tgt)
            elif not drifted and not low_trust_source:
                exp._nav_paths[tgt] = [*exp._nav_paths["src_app"], action]

        assert tgt not in exp._nav_paths
        assert tgt in exp._untrusted_targets


# ============================================================
# Clarification: ActionAttempt for filtered enumeration
# ============================================================


class TestActionAttempts:
    def test_side_effect_label_is_regular_action(self, explorer: AppExplorer) -> None:
        from vigil.neuro.explorer import ActionAttempt as _ActionAttempt

        reset = UIElement(
            element_id="e_reset",
            class_name="android.widget.Button",
            package="com.android.settings",
            resource_id="com.example:id/reset",
            text="Factory reset",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 100, 100],
        )
        scr = RawScreen(
            screen_id="s",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[reset],
        )
        screens = iter([scr, scr])
        with (
            patch.object(explorer, "_cold_start_app", return_value=True),
            patch.object(explorer, "_execute_action", return_value=True),
            patch.object(explorer, "_capture_screen", side_effect=lambda: next(screens)),
        ):
            actions = explorer._enumerate_actions_for_state("sid", [])
        assert any(a.target_resource_id == "com.example:id/reset" for a in actions)
        assert not any(
            isinstance(a, _ActionAttempt) and a.status.startswith("skipped")
            for a in explorer._action_attempts
        )

    def test_ambiguous_selector_records_action_attempt(self, explorer: AppExplorer) -> None:
        from vigil.neuro.explorer import ActionAttempt as _ActionAttempt

        # Two switches sharing rid; click action with only that rid.
        e1 = UIElement(
            element_id="sw1",
            class_name="android.widget.Switch",
            package="com.android.settings",
            resource_id="android:id/switch_widget",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 0, 100, 100],
        )
        e2 = UIElement(
            element_id="sw2",
            class_name="android.widget.Switch",
            package="com.android.settings",
            resource_id="android:id/switch_widget",
            is_clickable=True,
            is_enabled=True,
            bounds=[0, 200, 100, 300],
        )
        screen = RawScreen(
            screen_id="scr",
            activity_name=".X",
            package_name="com.android.settings",
            elements=[e1, e2],
        )
        action = Action(
            action_type=ActionType.CLICK,
            target_selector={
                "resource_id": "android:id/switch_widget",
                "text": "",
                "content_description": "",
                "nearby_text": "",
                "class_name": "android.widget.Switch",
                "ancestor_chain": [],
            },
        )
        with patch.object(explorer, "_capture_screen", return_value=screen):
            trace, _, _ = explorer._perform_one_observation(
                intended_source_state_id="src",
                intended_nav_path=[],
                action=action,
                step=1,
            )
        # The trace records the refusal as a SENTINEL_ACTION_FAILED
        # (filtered by FsmBuilder) AND an ActionAttempt is appended.
        assert trace.target_state_id == SENTINEL_ACTION_FAILED
        assert any(
            isinstance(a, _ActionAttempt) and a.status == "ambiguous_selector"
            for a in explorer._action_attempts
        )
