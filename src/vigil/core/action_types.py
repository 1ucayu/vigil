"""Action templates and enumeration helpers.

Given a screen's interactable elements, enumerates all candidate actions using
ACTION_TEMPLATES. Reference: V-Droid action enumeration for element properties
to candidate actions mapping.
"""

from __future__ import annotations

from vigil.models.action import ACTION_TEMPLATES, GLOBAL_ACTIONS, Action, ActionType
from vigil.models.state import RawScreen, UIElement


def enumerate_actions(
    screen: RawScreen,
    exclude: set[ActionType] | None = None,
) -> list[Action]:
    """Enumerate all candidate actions for a given screen.

    For each interactable element, generates actions based on ACTION_TEMPLATES.
    Also includes GLOBAL_ACTIONS (navigate_back, navigate_home).

    Args:
        screen: The current screen with parsed UI elements.
        exclude: Action types to skip (e.g., {ActionType.INPUT_TEXT} during
            exploration, since typing doesn't discover new screens).

    Returns:
        List of candidate Action objects to try.
    """
    actions: list[Action] = []

    for element in screen.get_interactable_elements():
        actions.extend(enumerate_element_actions(element, exclude=exclude))

    # Add global actions (back, home) — these don't target a specific element
    for action_type in GLOBAL_ACTIONS:
        if exclude and action_type in exclude:
            continue
        actions.append(Action(action_type=action_type))

    return actions


def enumerate_element_actions(
    element: UIElement,
    exclude: set[ActionType] | None = None,
) -> list[Action]:
    """Enumerate candidate actions for a single UI element.

    Maps element properties (is_clickable, is_scrollable, etc.) to
    action types via ACTION_TEMPLATES.

    Args:
        element: A single interactable UI element.
        exclude: Action types to skip.

    Returns:
        List of Action objects targeting this element.
    """
    actions: list[Action] = []

    property_map: dict[str, bool] = {
        "clickable": element.is_clickable,
        "long_clickable": element.is_long_clickable,
        "editable": element.is_editable,
        "scrollable": element.is_scrollable,
        "checkable": element.is_checkable,
    }

    for prop_name, is_set in property_map.items():
        if is_set and prop_name in ACTION_TEMPLATES:
            for action_type in ACTION_TEMPLATES[prop_name]:
                if exclude and action_type in exclude:
                    continue
                # Skip click/long_press on editable elements to avoid
                # triggering the soft keyboard during exploration
                if element.is_editable and action_type in (
                    ActionType.CLICK,
                    ActionType.LONG_PRESS,
                ):
                    continue
                action = Action(
                    action_type=action_type,
                    target_element_id=element.element_id,
                    target_bounds=element.bounds,
                    input_text="test input" if action_type == ActionType.INPUT_TEXT else None,
                )
                actions.append(action)

    return actions
