"""Action type definitions and templates for UI interaction.

Defines the vocabulary of actions that Vigil's explorer can perform on UI elements
and that the FSM records as transition labels.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ActionType(StrEnum):
    """Types of UI actions."""

    CLICK = "click"
    LONG_PRESS = "long_press"
    INPUT_TEXT = "input_text"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    NAVIGATE_BACK = "navigate_back"
    NAVIGATE_HOME = "navigate_home"


# Mapping from element properties to applicable action types
ACTION_TEMPLATES: dict[str, list[ActionType]] = {
    "clickable": [ActionType.CLICK],
    "long_clickable": [ActionType.LONG_PRESS],
    "editable": [ActionType.INPUT_TEXT],
    "scrollable": [ActionType.SCROLL_UP, ActionType.SCROLL_DOWN],
    "checkable": [ActionType.CLICK],
}

# Actions available regardless of element properties
GLOBAL_ACTIONS: list[ActionType] = [
    ActionType.NAVIGATE_BACK,
    ActionType.NAVIGATE_HOME,
]


class Action(BaseModel):
    """A concrete action to perform on the device.

    Identity lives in the descriptor triple (``target_resource_id``,
    ``target_text``, ``target_content_desc``, ``target_class_name``), which
    is stable across captures. ``target_element_id`` and ``target_bounds``
    are volatile capture-local hints used only for logging / debugging
    and must NEVER be used for equality or dedup.

    Attributes:
        action_type: The type of action to perform.
        target_resource_id: Android resource id of the target element.
        target_text: Displayed text of the target (normalized at build time).
        target_content_desc: Accessibility content-description of the target.
        target_class_name: Android widget class name (last-resort discriminator).
        target_element_id: Capture-local id (volatile hint).
        target_bounds: Capture-local bounds (volatile hint; re-resolved at exec).
        input_text: Text to input (only for INPUT_TEXT actions).
        metadata: Arbitrary action metadata.
    """

    action_type: ActionType
    target_element_id: str | None = None
    target_bounds: list[int] | None = None
    target_resource_id: str | None = None
    target_text: str | None = None
    target_content_desc: str | None = None
    target_class_name: str | None = None
    input_text: str | None = None
    metadata: dict[str, Any] = {}

    def to_fsm_dict(self) -> dict[str, Any]:
        """Convert to the dict format stored in FSM transitions."""
        result: dict[str, Any] = {"type": self.action_type.value}
        if self.target_element_id:
            result["target"] = self.target_element_id
        if self.target_bounds:
            result["bounds"] = self.target_bounds
        if self.target_resource_id:
            result["resource_id"] = self.target_resource_id
        if self.target_text:
            result["target_text"] = self.target_text
        if self.target_content_desc:
            result["target_content_desc"] = self.target_content_desc
        if self.target_class_name:
            result["target_class_name"] = self.target_class_name
        if self.input_text:
            result["text"] = self.input_text
        return result

    @classmethod
    def from_fsm_dict(cls, data: dict[str, Any]) -> Action:
        """Reconstruct an Action from an FSM transition dict."""
        return cls(
            action_type=ActionType(data["type"]),
            target_element_id=data.get("target"),
            target_bounds=data.get("bounds"),
            target_resource_id=data.get("resource_id"),
            target_text=data.get("target_text"),
            target_content_desc=data.get("target_content_desc"),
            target_class_name=data.get("target_class_name"),
            input_text=data.get("text"),
        )
