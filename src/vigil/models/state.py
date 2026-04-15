"""Raw UI state and element definitions.

These represent the unprocessed screen data captured during exploration (Stage 1),
before state abstraction (Stage 2) maps them to AbstractStates.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UIElement(BaseModel):
    """A single UI element extracted from the accessibility tree.

    Attributes:
        element_id: Unique identifier assigned during parsing.
        class_name: Android widget class (e.g., "android.widget.Button").
        resource_id: Android resource ID (e.g., "com.android.settings:id/title").
        text: Displayed text content.
        content_description: Accessibility content description.
        bounds: Bounding box as [left, top, right, bottom].
        is_clickable: Whether the element responds to click events.
        is_long_clickable: Whether the element responds to long press.
        is_scrollable: Whether the element is scrollable.
        is_editable: Whether the element accepts text input.
        is_checkable: Whether the element is a checkbox/toggle.
        is_checked: Current checked state (if checkable).
        is_enabled: Whether the element is interactive.
        depth: Depth in the accessibility tree hierarchy.
        children: Child element IDs.
    """

    element_id: str
    class_name: str
    resource_id: str | None = None
    text: str | None = None
    content_description: str | None = None
    bounds: list[int] = Field(default_factory=lambda: [0, 0, 0, 0])
    is_clickable: bool = False
    is_long_clickable: bool = False
    is_scrollable: bool = False
    is_editable: bool = False
    is_checkable: bool = False
    is_checked: bool = False
    is_enabled: bool = True
    is_focusable: bool = False
    is_focused: bool = False
    is_selected: bool = False
    is_password: bool = False
    depth: int = 0
    children: list[str] = Field(default_factory=list)
    parent_id: str | None = None

    def get_grouping_skeleton(self) -> tuple:
        """Return a coarse structural skeleton for equivalence grouping.

        Unlike get_skeleton(), this EXCLUDES resource_id so that sibling
        elements with the same widget type and interactability but different
        resource_ids (e.g., digit_0 through digit_9) are grouped together.
        """
        interactability = (
            self.is_clickable,
            self.is_long_clickable,
            self.is_scrollable,
            self.is_editable,
            self.is_checkable,
        )
        return (self.class_name, self.depth, interactability)

    def get_skeleton(self, elements_by_id: dict[str, UIElement] | None = None) -> tuple:
        """Return the structural skeleton of this element.

        Excludes text, content_description, bounds — these are content properties.
        Used for skeleton homogeneity classification in container analysis.

        Args:
            elements_by_id: If provided, recursively resolves children by ID.
                Without it, returns a flat skeleton with no child info.
        """
        child_skeletons: tuple = ()
        if elements_by_id and self.children:
            child_list = []
            for cid in self.children:
                child = elements_by_id.get(cid)
                if child is not None:
                    child_list.append(child.get_skeleton(elements_by_id))
            child_skeletons = tuple(sorted(child_list))

        return (
            self.class_name,
            self.resource_id or "",
            self.is_clickable,
            self.is_long_clickable,
            self.is_scrollable,
            self.is_editable,
            self.is_checkable,
            child_skeletons,
        )


class RawScreen(BaseModel):
    """A raw screen capture from UI exploration.

    Represents a single snapshot of the device screen including the accessibility
    tree, screenshot path, and extracted elements.

    Attributes:
        screen_id: Unique identifier for this screen capture.
        activity_name: Current Android Activity class name.
        package_name: Current Android package name.
        screenshot_path: Path to the screenshot PNG file.
        xml_tree_path: Path to the accessibility tree XML file.
        elements: List of UI elements extracted from the tree.
        timestamp: ISO 8601 timestamp of capture.
        metadata: Additional metadata (device info, orientation, etc.).
    """

    screen_id: str
    activity_name: str | None = None
    package_name: str | None = None
    screenshot_path: str | None = None
    xml_tree_path: str | None = None
    elements: list[UIElement] = Field(default_factory=list)
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def get_interactable_elements(self) -> list[UIElement]:
        """Return elements that can be interacted with (clickable, scrollable, etc.)."""
        return [
            e
            for e in self.elements
            if e.is_enabled
            and (
                e.is_clickable
                or e.is_long_clickable
                or e.is_scrollable
                or e.is_editable
                or e.is_checkable
            )
        ]

    def get_structural_fingerprint(self, scroll_aware: bool = True) -> str:
        """Generate a structural fingerprint ignoring dynamic content.

        Hashes (activity_name, sorted element structure tuples) where each element
        contributes (class_name, resource_id, depth, interactability_flags).
        Text and content_description are deliberately excluded — they vary across
        instances of the same structural screen (e.g., different WiFi networks).

        Args:
            scroll_aware: If True, exclude children of scrollable containers from
                the fingerprint. This merges scroll-equivalent screens (same page
                at different scroll positions) into a single state.

        Used by Stage 2 (State Abstraction) for rule-based grouping.
        """
        import hashlib

        # Find depths of scrollable containers — their children are scroll-volatile
        scrollable_depths: set[int] = set()
        if scroll_aware:
            for e in self.elements:
                if e.is_scrollable:
                    scrollable_depths.add(e.depth)

        components = []
        for e in self.elements:
            # Skip elements that are children of a scrollable container
            if (
                scrollable_depths
                and not e.is_scrollable
                and any(e.depth > sd for sd in scrollable_depths)
            ):
                continue

            interactability = (
                e.is_clickable,
                e.is_long_clickable,
                e.is_scrollable,
                e.is_editable,
                e.is_checkable,
            )
            components.append((e.class_name, e.resource_id or "", e.depth, interactability))
        components.sort()
        fingerprint_input = (self.activity_name or "", tuple(components))
        return hashlib.sha256(str(fingerprint_input).encode()).hexdigest()[:16]
