"""DroidBot UTG output parser.

Converts DroidBot's output directory (utg.js, states/*.json, events/*.json)
into Vigil's ExplorationResult format for downstream FSM construction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from vigil.models.action import Action, ActionType
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.explorer import ExplorationResult, ExplorationTrace


class DroidBotParser:
    """Parse DroidBot output directory into Vigil ExplorationResult."""

    def __init__(self, output_dir: Path, app_package: str) -> None:
        self._output_dir = output_dir
        self._app_package = app_package

    def parse(self) -> ExplorationResult:
        """Parse all DroidBot output into an ExplorationResult."""
        utg_data = self._parse_utg_js()
        screens = self._parse_states(utg_data)
        traces = self._parse_events(screens)

        unique_activities = {s.activity_name for s in screens.values() if s.activity_name}

        result = ExplorationResult(
            app_package=self._app_package,
            screens=screens,
            traces=traces,
            total_steps=len(traces),
            unique_screens=len(screens),
            output_dir=str(self._output_dir),
            declared_activities=sorted(unique_activities),
            covered_activities=sorted(unique_activities),
        )

        logger.info(
            f"DroidBot output parsed: {len(screens)} states, "
            f"{len(traces)} transitions, {len(unique_activities)} activities"
        )
        return result

    def _parse_utg_js(self) -> dict[str, Any]:
        """Parse utg.js — extract JSON from JS variable assignment."""
        utg_path = self._output_dir / "utg.js"
        if not utg_path.exists():
            logger.error(f"utg.js not found at {utg_path}")
            return {}

        text = utg_path.read_text(encoding="utf-8")
        idx = text.find("{")
        if idx < 0:
            logger.error("No JSON object found in utg.js")
            return {}

        json_text = text[idx:].rstrip().rstrip(";")
        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse utg.js JSON: {e}")
            return {}

    def _parse_states(self, utg_data: dict[str, Any]) -> dict[str, RawScreen]:
        """Parse DroidBot state files into RawScreen objects."""
        screens: dict[str, RawScreen] = {}
        states_dir = self._output_dir / "states"

        if not states_dir.exists():
            logger.warning(f"No states directory at {states_dir}")
            return screens

        state_files = sorted(states_dir.glob("*.json"))
        for i, state_path in enumerate(state_files):
            state_str = state_path.stem
            screen = self._parse_state_file(state_path, state_str, i)
            if screen is not None:
                screens[screen.screen_id] = screen

        logger.info(f"Parsed {len(screens)} states from {len(state_files)} state files")
        return screens

    def _parse_state_file(self, path: Path, state_str: str, index: int) -> RawScreen | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to parse state file {path}: {e}")
            return None

        activity = data.get("foreground_activity", "")
        views = data.get("views", [])
        elements = self._convert_views_to_elements(views)

        screenshot_path = path.with_suffix(".png")
        screenshot_str = str(screenshot_path) if screenshot_path.exists() else None

        screen_id = f"scr_{index:04d}"

        return RawScreen(
            screen_id=screen_id,
            activity_name=activity or None,
            package_name=self._app_package,
            screenshot_path=screenshot_str,
            xml_tree_path=str(path),
            elements=elements,
            metadata={
                "source": "droidbot",
                "state_str": state_str,
            },
        )

    def _convert_views_to_elements(self, views: list[dict[str, Any]]) -> list[UIElement]:
        elements: list[UIElement] = []

        for i, view in enumerate(views):
            if view.get("package") == "com.android.systemui":
                continue

            bounds = self._parse_droidbot_bounds(view.get("bounds"))
            children_indices = view.get("children", [])
            children_ids = [f"e_{ci:04d}" for ci in children_indices if isinstance(ci, int)]

            class_name = view.get("class", "")
            is_editable = view.get("focusable", False) and class_name.endswith("EditText")

            element = UIElement(
                element_id=f"e_{i:04d}",
                class_name=class_name,
                resource_id=view.get("resource_id") or None,
                text=view.get("text") or None,
                content_description=view.get("content_description") or None,
                bounds=bounds,
                is_clickable=view.get("clickable", False),
                is_long_clickable=view.get("long_clickable", False),
                is_scrollable=view.get("scrollable", False),
                is_editable=is_editable,
                is_checkable=view.get("checkable", False),
                is_checked=view.get("checked", False),
                is_enabled=view.get("enabled", True),
                depth=self._compute_depth(views, i),
                children=children_ids,
            )
            elements.append(element)

        return elements

    @staticmethod
    def _parse_droidbot_bounds(bounds: Any) -> list[int]:
        """Parse DroidBot bounds into [left, top, right, bottom]."""
        if bounds is None:
            return [0, 0, 0, 0]
        if isinstance(bounds, list):
            if len(bounds) == 4 and all(isinstance(b, int | float) for b in bounds):
                return [int(b) for b in bounds]
            if len(bounds) == 2 and isinstance(bounds[0], list) and isinstance(bounds[1], list):
                return [
                    int(bounds[0][0]),
                    int(bounds[0][1]),
                    int(bounds[1][0]),
                    int(bounds[1][1]),
                ]
        return [0, 0, 0, 0]

    @staticmethod
    def _compute_depth(views: list[dict[str, Any]], index: int) -> int:
        depth = 0
        current = index
        seen: set[int] = set()
        while True:
            parent = views[current].get("parent", -1) if current < len(views) else -1
            if parent < 0 or parent == current or parent in seen:
                break
            seen.add(parent)
            current = parent
            depth += 1
        return depth

    def _parse_events(self, screens: dict[str, RawScreen]) -> list[ExplorationTrace]:
        events_dir = self._output_dir / "events"
        if not events_dir.exists():
            return []

        state_str_to_sid: dict[str, str] = {}
        for screen in screens.values():
            state_str = screen.metadata.get("state_str", "")
            if state_str:
                state_str_to_sid[state_str] = screen.screen_id

        traces: list[ExplorationTrace] = []
        event_files = sorted(events_dir.glob("event_*.json"))

        for step, event_path in enumerate(event_files):
            trace = self._parse_event_file(event_path, step, state_str_to_sid)
            if trace is not None:
                traces.append(trace)

        logger.info(f"Parsed {len(traces)} traces from {len(event_files)} event files")
        return traces

    def _parse_event_file(
        self,
        path: Path,
        step: int,
        state_str_to_sid: dict[str, str],
    ) -> ExplorationTrace | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        event_data = data.get("event", {})
        event_type = event_data.get("event_type", "")

        action = self._convert_event_to_action(event_data, event_type)
        if action is None:
            return None

        start_state = data.get("start_state", "")
        stop_state = data.get("stop_state", "")
        source_sid = state_str_to_sid.get(start_state, "")
        target_sid = state_str_to_sid.get(stop_state, "")

        if not source_sid or not target_sid:
            return None

        return ExplorationTrace(
            step_number=step,
            source_screen_id=source_sid,
            action=action,
            target_screen_id=target_sid,
            timestamp=data.get("tag", ""),
        )

    def _convert_event_to_action(
        self, event_data: dict[str, Any], event_type: str
    ) -> Action | None:
        if event_type == "touch":
            return self._convert_touch_event(event_data)
        if event_type == "long_touch":
            return self._convert_touch_event(event_data, long=True)
        if event_type == "key":
            return self._convert_key_event(event_data)
        if event_type == "scroll":
            return self._convert_scroll_event(event_data)
        if event_type == "set_text":
            return self._convert_set_text_event(event_data)
        return None

    def _convert_touch_event(self, event_data: dict[str, Any], long: bool = False) -> Action:
        view = event_data.get("view", {})
        bounds = self._parse_droidbot_bounds(view.get("bounds"))
        resource_id = view.get("resource_id") or None
        element_id = None
        if "temp_id" in view:
            element_id = f"e_{view['temp_id']:04d}"

        return Action(
            action_type=ActionType.LONG_PRESS if long else ActionType.CLICK,
            target_element_id=element_id,
            target_bounds=bounds if bounds != [0, 0, 0, 0] else None,
            target_resource_id=resource_id,
        )

    @staticmethod
    def _convert_key_event(event_data: dict[str, Any]) -> Action:
        key_name = event_data.get("name", "BACK")
        if key_name == "HOME":
            return Action(action_type=ActionType.NAVIGATE_HOME)
        return Action(action_type=ActionType.NAVIGATE_BACK)

    def _convert_scroll_event(self, event_data: dict[str, Any]) -> Action:
        view = event_data.get("view", {})
        bounds = self._parse_droidbot_bounds(view.get("bounds"))
        resource_id = view.get("resource_id") or None
        element_id = None
        if "temp_id" in view:
            element_id = f"e_{view['temp_id']:04d}"

        direction = event_data.get("direction", "DOWN")
        action_type = ActionType.SCROLL_UP if direction == "UP" else ActionType.SCROLL_DOWN

        return Action(
            action_type=action_type,
            target_element_id=element_id,
            target_bounds=bounds if bounds != [0, 0, 0, 0] else None,
            target_resource_id=resource_id,
        )

    def _convert_set_text_event(self, event_data: dict[str, Any]) -> Action:
        view = event_data.get("view", {})
        bounds = self._parse_droidbot_bounds(view.get("bounds"))
        resource_id = view.get("resource_id") or None
        element_id = None
        if "temp_id" in view:
            element_id = f"e_{view['temp_id']:04d}"

        return Action(
            action_type=ActionType.INPUT_TEXT,
            target_element_id=element_id,
            target_bounds=bounds if bounds != [0, 0, 0, 0] else None,
            target_resource_id=resource_id,
            input_text=event_data.get("text", ""),
        )
