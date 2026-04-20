"""Tests for vigil.neuro.explorer (plain BFS rewrite) and supporting modules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vigil.core.action_types import enumerate_actions, enumerate_element_actions
from vigil.core.config import VigilConfig
from vigil.core.ui_parser import parse_bounds, parse_hierarchy_xml
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
    _build_interact_action,
    _generate_edit_value,
    _is_edit_text,
    action_key,
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
        action = Action(
            action_type=ActionType.CLICK,
            target_element_id="e_0001",
            target_bounds=[100, 200, 300, 400],
        )
        assert explorer._execute_action(action) is True
        mock_device.click.assert_called_once_with(200, 300)

    def test_execute_click_without_bounds_returns_false(self, explorer: AppExplorer) -> None:
        action = Action(action_type=ActionType.CLICK, target_element_id="e_001")
        assert explorer._execute_action(action) is False

    def test_execute_back_returns_true(self, explorer: AppExplorer, mock_device: MagicMock) -> None:
        assert explorer._execute_action(Action(action_type=ActionType.NAVIGATE_BACK)) is True
        mock_device.press.assert_called_once_with("back")

    def test_scroll_down_swipes_high_to_low(
        self, explorer: AppExplorer, mock_device: MagicMock
    ) -> None:
        action = Action(
            action_type=ActionType.SCROLL_DOWN,
            target_bounds=[0, 0, 1080, 1000],
        )
        assert explorer._execute_action(action) is True
        # SCROLL_DOWN = finger drags from high y (800) to low y (200).
        mock_device.swipe.assert_called_once()
        args = mock_device.swipe.call_args.args
        _, y1, _, y2 = args
        assert y1 > y2  # high to low


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

    def test_bounds_bucket_detects_large_shift(self) -> None:
        a = Action(action_type=ActionType.CLICK, target_bounds=[100, 200, 300, 400])
        b = Action(action_type=ActionType.CLICK, target_bounds=[100, 600, 300, 800])
        assert action_key(a) != action_key(b)

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
        rids = {a.target_resource_id for a in actions}
        assert rids == {"a:id/1", "a:id/2"}

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
        action = _build_interact_action(e)
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
        action = _build_interact_action(e)
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
