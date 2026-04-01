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

    Attributes:
        action_type: The type of action to perform.
        target_element_id: ID of the target UI element (None for global actions).
        target_bounds: Bounding box of the target element [left, top, right, bottom].
        input_text: Text to input (only for INPUT_TEXT actions).
        metadata: Additional action metadata (e.g., scroll distance, coordinates).
    """

    action_type: ActionType
    target_element_id: str | None = None
    target_bounds: list[int] | None = None
    input_text: str | None = None
    metadata: dict[str, Any] = {}

    def to_fsm_dict(self) -> dict[str, Any]:
        """Convert to the dict format stored in FSM transitions."""
        result: dict[str, Any] = {"type": self.action_type.value}
        if self.target_element_id:
            result["target"] = self.target_element_id
        if self.target_bounds:
            result["bounds"] = self.target_bounds
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
            input_text=data.get("text"),
        )
