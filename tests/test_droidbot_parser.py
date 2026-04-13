"""Tests for DroidBot output parser and integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigil.models.action import ActionType
from vigil.neuro.droidbot_parser import DroidBotParser


@pytest.fixture
def droidbot_output(tmp_path: Path) -> Path:
    """Create a mock DroidBot output directory."""
    out = tmp_path / "droidbot_output"
    out.mkdir()
    (out / "states").mkdir()
    (out / "events").mkdir()

    utg_data = {
        "nodes": ["state_hash_001", "state_hash_002"],
        "edges": [{"from": "state_hash_001", "to": "state_hash_002", "events": [0]}],
        "num_nodes": 2,
        "num_edges": 1,
    }
    (out / "utg.js").write_text(f"var defined = {json.dumps(utg_data)};", encoding="utf-8")

    state_001 = {
        "state_str": "state_hash_001",
        "foreground_activity": "com.android.settings.Settings",
        "views": [
            {
                "class": "android.widget.FrameLayout",
                "resource_id": "",
                "text": "",
                "content_description": "",
                "bounds": [[0, 0], [1080, 2340]],
                "clickable": False,
                "long_clickable": False,
                "scrollable": False,
                "checkable": False,
                "checked": False,
                "enabled": True,
                "focusable": False,
                "package": "com.android.settings",
                "children": [1, 2],
                "parent": -1,
                "temp_id": 0,
            },
            {
                "class": "android.widget.TextView",
                "resource_id": "android:id/title",
                "text": "Wi-Fi",
                "content_description": "",
                "bounds": [[40, 110], [500, 170]],
                "clickable": True,
                "long_clickable": False,
                "scrollable": False,
                "checkable": False,
                "checked": False,
                "enabled": True,
                "focusable": True,
                "package": "com.android.settings",
                "children": [],
                "parent": 0,
                "temp_id": 1,
            },
            {
                "class": "android.widget.Switch",
                "resource_id": "com.android.settings:id/switchWidget",
                "text": "",
                "content_description": "Bluetooth",
                "bounds": [[900, 300], [1040, 440]],
                "clickable": True,
                "long_clickable": False,
                "scrollable": False,
                "checkable": True,
                "checked": True,
                "enabled": True,
                "focusable": False,
                "package": "com.android.settings",
                "children": [],
                "parent": 0,
                "temp_id": 2,
            },
        ],
    }
    (out / "states" / "state_hash_001.json").write_text(json.dumps(state_001), encoding="utf-8")

    state_002 = {
        "state_str": "state_hash_002",
        "foreground_activity": "com.android.settings.wifi.WifiSettings",
        "views": [
            {
                "class": "android.widget.TextView",
                "resource_id": "android:id/title",
                "text": "Wi-Fi Settings",
                "bounds": [[40, 50], [500, 110]],
                "clickable": False,
                "enabled": True,
                "package": "com.android.settings",
                "children": [],
                "parent": -1,
                "temp_id": 0,
            },
        ],
    }
    (out / "states" / "state_hash_002.json").write_text(json.dumps(state_002), encoding="utf-8")

    event_0 = {
        "tag": "2026-04-13T12:00:00",
        "event": {
            "event_type": "touch",
            "view": {
                "class": "android.widget.TextView",
                "resource_id": "android:id/title",
                "text": "Wi-Fi",
                "bounds": [[40, 110], [500, 170]],
                "temp_id": 1,
            },
        },
        "start_state": "state_hash_001",
        "stop_state": "state_hash_002",
    }
    (out / "events" / "event_00000.json").write_text(json.dumps(event_0), encoding="utf-8")

    return out


class TestDroidBotParser:
    def test_parse_screens(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        result = parser.parse()
        assert result.unique_screens == 2

    def test_parse_traces(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        result = parser.parse()
        assert len(result.traces) == 1
        assert result.traces[0].action.action_type == ActionType.CLICK
        assert result.traces[0].action.target_resource_id == "android:id/title"

    def test_screen_elements(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        result = parser.parse()
        screen = list(result.screens.values())[0]
        wifi_els = [e for e in screen.elements if e.text == "Wi-Fi"]
        assert len(wifi_els) == 1
        assert wifi_els[0].is_clickable is True
        assert wifi_els[0].resource_id == "android:id/title"

    def test_checkable_elements(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        result = parser.parse()
        screen = list(result.screens.values())[0]
        switches = [e for e in screen.elements if e.is_checkable]
        assert len(switches) == 1
        assert switches[0].is_checked is True

    def test_activity_names(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        result = parser.parse()
        activities = {s.activity_name for s in result.screens.values() if s.activity_name}
        assert "com.android.settings.Settings" in activities
        assert "com.android.settings.wifi.WifiSettings" in activities

    def test_metadata_has_state_str(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        result = parser.parse()
        for screen in result.screens.values():
            assert screen.metadata.get("source") == "droidbot"
            assert "state_str" in screen.metadata

    def test_bounds_nested_format(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        result = parser.parse()
        screen = list(result.screens.values())[0]
        wifi = [e for e in screen.elements if e.text == "Wi-Fi"][0]
        assert wifi.bounds == [40, 110, 500, 170]


class TestDroidBotBoundsParser:
    def test_nested_list(self) -> None:
        assert DroidBotParser._parse_droidbot_bounds([[100, 200], [300, 400]]) == [
            100,
            200,
            300,
            400,
        ]

    def test_flat_list(self) -> None:
        assert DroidBotParser._parse_droidbot_bounds([100, 200, 300, 400]) == [100, 200, 300, 400]

    def test_none(self) -> None:
        assert DroidBotParser._parse_droidbot_bounds(None) == [0, 0, 0, 0]

    def test_empty(self) -> None:
        assert DroidBotParser._parse_droidbot_bounds([]) == [0, 0, 0, 0]


class TestUtgJsParsing:
    def test_parse_utg_js(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        utg = parser._parse_utg_js()
        assert utg["num_nodes"] == 2

    def test_parse_utg_js_missing(self, tmp_path: Path) -> None:
        parser = DroidBotParser(tmp_path, "com.android.settings")
        assert parser._parse_utg_js() == {}


class TestEventConversion:
    def test_touch_event(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        event = {
            "event_type": "touch",
            "view": {
                "resource_id": "android:id/title",
                "bounds": [[40, 110], [500, 170]],
                "temp_id": 1,
            },
        }
        action = parser._convert_event_to_action(event, "touch")
        assert action is not None
        assert action.action_type == ActionType.CLICK

    def test_key_back(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        action = parser._convert_event_to_action({"name": "BACK"}, "key")
        assert action is not None
        assert action.action_type == ActionType.NAVIGATE_BACK

    def test_key_home(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        action = parser._convert_event_to_action({"name": "HOME"}, "key")
        assert action is not None
        assert action.action_type == ActionType.NAVIGATE_HOME

    def test_scroll(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        event = {"direction": "DOWN", "view": {"bounds": [[0, 0], [1080, 2200]], "temp_id": 0}}
        action = parser._convert_event_to_action(event, "scroll")
        assert action is not None
        assert action.action_type == ActionType.SCROLL_DOWN

    def test_unknown_returns_none(self, droidbot_output: Path) -> None:
        parser = DroidBotParser(droidbot_output, "com.android.settings")
        assert parser._convert_event_to_action({}, "intent") is None


class TestFsmBuilderCompatibility:
    def test_trace_to_fsm(self, droidbot_output: Path, tmp_path: Path) -> None:
        from vigil.core.config import VigilConfig
        from vigil.neuro.droidbot_explorer import DroidBotExplorer
        from vigil.neuro.fsm_builder import FsmBuilder

        parser = DroidBotParser(droidbot_output, "com.android.settings")
        result = parser.parse()

        saver = DroidBotExplorer.__new__(DroidBotExplorer)
        saver._serial = "test"
        saver._app_package = "com.android.settings"
        saver._config = VigilConfig()
        saver._output_dir = tmp_path
        (tmp_path / "traces").mkdir()
        saver._save_trace(result)

        trace_files = sorted((tmp_path / "traces").glob("exploration_*.json"))
        assert len(trace_files) == 1

        builder = FsmBuilder("com.android.settings")
        fsm = builder.build_from_trace(trace_files[0])

        assert len(fsm.states) >= 1
        assert fsm.initial_state is not None
