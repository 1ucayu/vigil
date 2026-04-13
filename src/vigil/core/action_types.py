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
    """
    actions: list[Action] = []
    all_elements = screen.elements

    for element in screen.get_interactable_elements():
        actions.extend(
            enumerate_element_actions(element, exclude=exclude, all_elements=all_elements)
        )

    for action_type in GLOBAL_ACTIONS:
        if exclude and action_type in exclude:
            continue
        actions.append(Action(action_type=action_type))

    return actions


def enumerate_element_actions(
    element: UIElement,
    exclude: set[ActionType] | None = None,
    all_elements: list[UIElement] | None = None,
) -> list[Action]:
    """Enumerate candidate actions for a single UI element.

    Args:
        element: A single interactable UI element.
        exclude: Action types to skip.
        all_elements: Full element list from the screen. When provided,
            scroll actions are suppressed for containers with <5 children.
    """
    actions: list[Action] = []

    child_count = 0
    if all_elements is not None and element.is_scrollable:
        child_ids = set(element.children)
        child_count = sum(1 for e in all_elements if e.element_id in child_ids)

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
                if element.is_editable and action_type in (
                    ActionType.CLICK,
                    ActionType.LONG_PRESS,
                ):
                    continue
                if (
                    action_type in (ActionType.SCROLL_UP, ActionType.SCROLL_DOWN)
                    and all_elements is not None
                    and child_count < 5
                ):
                    continue
                action = Action(
                    action_type=action_type,
                    target_element_id=element.element_id,
                    target_bounds=element.bounds,
                    target_resource_id=element.resource_id,
                    input_text="test input" if action_type == ActionType.INPUT_TEXT else None,
                )
                actions.append(action)

    return actions
