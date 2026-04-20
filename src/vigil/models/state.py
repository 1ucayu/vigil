"""Raw UI state and element definitions.

These represent the unprocessed screen data captured during exploration (Stage 1),
before state abstraction (Stage 2) maps them to AbstractStates.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, Field

_DYNAMIC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\d+\.\d+\s*[GMK]B"), "<SIZE>"),
    (re.compile(r"\d+\s*[GMK]B"), "<SIZE>"),
    (re.compile(r"\d+%"), "<PCT>"),
    (re.compile(r"\d{1,2}:\d{2}(\s*[AP]M)?"), "<TIME>"),
    (re.compile(r"\b\d+\b"), "<N>"),
]

_TOOLBAR_ANCESTOR_TOKENS: tuple[str, ...] = ("app_bar", "toolbar", "action_bar")

EMPTY_SCREEN_ID = "EMPTY_SCREEN"


def _normalize_dynamic(text: str) -> str:
    """Apply dynamic-content normalization patterns in order."""
    t = text
    for pat, repl in _DYNAMIC_PATTERNS:
        t = pat.sub(repl, t)
    return t.strip()


def _short_rid(rid: str, class_name: str) -> str:
    """Return the short form of a resource-id, falling back to class name tail."""
    if ":id/" in rid:
        return rid.split(":id/", 1)[1]
    if rid:
        return rid
    if class_name:
        return class_name.rsplit(".", 1)[-1]
    return ""


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
    package: str = ""
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
    input_type: int = 0

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

    def _classify_scrollable(
        self, scrollable_elem: UIElement, elements_by_id: dict[str, UIElement]
    ) -> str:
        """Classify a scrollable by direct-row count (feature D).

        Row-count heuristic:
          - 0 rows                    -> "empty"
          - 1 row that is itself a wrapper -> recurse
          - 1-15 rows (after unwrap)  -> "heterogeneous" (preserve per-row anchors)
          - 16+ rows                  -> "homogeneous" (collapse to sentinel)

        Rationale: Android Preference menus in apps like Settings have 3-11
        heterogeneous rows whose titles are page identity. Content lists
        (email, contacts, file browsers) typically have 20+ homogeneous
        rows whose titles are content, not identity — collapsing prevents
        N-way state explosion.
        """
        direct_children = [e for e in self.elements if e.parent_id == scrollable_elem.element_id]
        if not direct_children:
            return "empty"
        if len(direct_children) == 1:
            return self._classify_scrollable(direct_children[0], elements_by_id)
        row_count = len(direct_children)
        if row_count <= 15:
            return "heterogeneous"
        return "homogeneous"

    def get_functional_state_key(self, app_package: str) -> tuple[str, frozenset[tuple[str, str]]]:
        """Text-anchored functional state identity.

        Returns (anchor_container_rid, frozenset of (rid_short, normalized_text)
        anchor tuples).

        anchor_container_rid is the resource-id of the first child of the element
        whose resource-id is ``android:id/content``. Empty string if not found.

        Anchors include every element with non-empty text (or content-description)
        that is:
          - interactable (click / long-click / checkable / editable), OR
          - has resource-id ``android:id/title`` (Preference list row labels), OR
          - is a TextView inside an ancestor whose resource-id contains any of
            ``app_bar`` / ``toolbar`` / ``action_bar`` (page titles).

        Explicitly excluded:
          - Elements with resource-id ``android:id/summary`` (dynamic content).
          - Elements whose package is neither ``app_package`` nor ``"android"``
            (cross-app UI injections such as com.android.systemui).
          - Elements with empty text AND empty content-description.
          - Descendants of a scrollable container classified as "homogeneous"
            (feature D: list content collapse — replaced by a single
            ``list_<rid>=non_empty|empty`` sentinel anchor per such list).

        Text is normalized (see _DYNAMIC_PATTERNS) so numeric values, sizes,
        percentages, and timestamps do not destabilize state identity.
        """
        elements_by_id: dict[str, UIElement] = {e.element_id: e for e in self.elements}

        anchor_container = ""
        for e in self.elements:
            if e.resource_id == "android:id/content":
                children = [c for c in self.elements if c.parent_id == e.element_id]
                if children:
                    children.sort(key=lambda c: c.depth)
                    anchor_container = children[0].resource_id or ""
                break

        def is_inside_toolbar(elem: UIElement) -> bool:
            cur: UIElement | None = elem
            depth_guard = 0
            while cur is not None and cur.parent_id is not None and depth_guard < 30:
                parent = elements_by_id.get(cur.parent_id)
                if parent is None:
                    return False
                rid = parent.resource_id or ""
                if any(tok in rid for tok in _TOOLBAR_ANCESTOR_TOKENS):
                    return True
                cur = parent
                depth_guard += 1
            return False

        # Feature D: classify each scrollable and mark descendants of
        # "homogeneous" ones for anchor exclusion.
        scrollable_classification: dict[str, str] = {}
        for e in self.elements:
            if e.is_scrollable:
                scrollable_classification[e.element_id] = self._classify_scrollable(
                    e, elements_by_id
                )

        def homogeneous_ancestor_id(elem: UIElement) -> str | None:
            cur: UIElement | None = elem
            depth_guard = 0
            while cur is not None and cur.parent_id is not None and depth_guard < 50:
                parent = elements_by_id.get(cur.parent_id)
                if parent is None:
                    return None
                if scrollable_classification.get(parent.element_id) == "homogeneous":
                    return parent.element_id
                cur = parent
                depth_guard += 1
            return None

        anchors: set[tuple[str, str]] = set()
        for e in self.elements:
            if e.package and e.package != app_package and e.package != "android":
                continue
            if e.resource_id == "android:id/summary":
                continue
            if homogeneous_ancestor_id(e) is not None:
                continue  # descendants of homogeneous lists collapse to sentinel

            raw = e.text or e.content_description or ""
            if not raw:
                continue
            text = _normalize_dynamic(raw)
            if not text:
                continue

            is_interactable = (
                e.is_clickable or e.is_long_clickable or e.is_checkable or e.is_editable
            )
            is_title = e.resource_id == "android:id/title"
            is_toolbar_text = "TextView" in (e.class_name or "") and is_inside_toolbar(e)
            if not (is_interactable or is_title or is_toolbar_text):
                continue

            anchors.add((_short_rid(e.resource_id or "", e.class_name or ""), text))

        # Emit list sentinels for homogeneous and empty scrollables.
        for scroll_id, classification in scrollable_classification.items():
            if classification == "heterogeneous":
                continue
            scroll_elem = elements_by_id[scroll_id]
            sentinel_rid = _short_rid(scroll_elem.resource_id or "", scroll_elem.class_name or "")
            if not sentinel_rid:
                continue
            value = "empty" if classification == "empty" else "non_empty"
            anchors.add((f"list_{sentinel_rid}", value))

        return (anchor_container, frozenset(anchors))

    def get_state_id(self, app_package: str) -> str:
        """Deterministic 12-char state id derived from the functional state key.

        Returns the literal ``EMPTY_SCREEN`` if both the anchor container is
        empty AND no anchors were collected (empty capture, crash/ANR dialog,
        fully black screen). Callers must treat this as a non-registrable
        observation.
        """
        container, anchors = self.get_functional_state_key(app_package)
        if not container and not anchors:
            return EMPTY_SCREEN_ID
        sorted_anchors = sorted(f"{r}={t}" for r, t in anchors)
        key_str = container + "||" + "|".join(sorted_anchors)
        return hashlib.sha256(key_str.encode()).hexdigest()[:12]

    def extract_page_title(self) -> str:
        """Best-effort extraction of the page title as a stable text anchor.

        Tried in order:
          1. Element with ``resource_id`` containing ``action_bar_title`` that
             has non-empty text.
          2. Element with ``resource_id`` containing ``collapsing_toolbar`` —
             its content-description (Settings' pattern), or any descendant
             TextView text.
          3. The first TextView sibling of a ``Navigate up`` ImageButton
             (generic toolbar pattern).

        Returns the empty string when no title anchor can be found. Does NOT
        normalize numeric / dynamic content — the page title on a real app
        rarely carries notification counts or timestamps, and preserving
        capitalization helps FSM state naming downstream.
        """
        # Strategy 1: action_bar_title
        for e in self.elements:
            rid = (e.resource_id or "").lower()
            if "action_bar_title" in rid:
                t = (e.text or "").strip()
                if t:
                    return t

        # Strategy 2: collapsing_toolbar (Settings' pattern — content-desc on
        # the toolbar container, or descendant TextView text).
        by_id = {e.element_id: e for e in self.elements}
        for e in self.elements:
            rid = (e.resource_id or "").lower()
            if "collapsing_toolbar" not in rid:
                continue
            cd = (e.content_description or "").strip()
            if cd:
                return cd
            # Walk descendants for the first non-empty TextView text.
            stack = list(e.children)
            seen: set[str] = set()
            depth_guard = 0
            while stack and depth_guard < 60:
                depth_guard += 1
                cid = stack.pop()
                if cid in seen:
                    continue
                seen.add(cid)
                child = by_id.get(cid)
                if child is None:
                    continue
                if "TextView" in (child.class_name or "") and child.text:
                    t = child.text.strip()
                    if t:
                        return t
                stack.extend(child.children)

        # Strategy 3: first TextView sibling of a "Navigate up" button.
        for e in self.elements:
            if (e.content_description or "").strip() != "Navigate up":
                continue
            parent = by_id.get(e.parent_id) if e.parent_id else None
            if parent is None:
                continue
            for cid in parent.children:
                if cid == e.element_id:
                    continue
                sib = by_id.get(cid)
                if sib is None:
                    continue
                if "TextView" in (sib.class_name or "") and sib.text:
                    t = sib.text.strip()
                    if t:
                        return t
        return ""

    def get_hybrid_state_id(self, app_package: str) -> str:
        """Hybrid state identity: structural fingerprint + activity + page title.

        Combines the scroll-aware structural skeleton (distinguishes pages
        with genuinely different widget trees) with the activity name and
        page title (distinguishes pages that share a skeleton but have
        different semantic roles — e.g. Settings' Internet / Battery / Apps
        / Location SubSettings all have the same Preference-framework
        skeleton but different toolbar titles).

        Intentionally *excludes* Preference row labels, list content, and
        other body text, so a page with 3 Wi-Fi networks and a page with
        5 Wi-Fi networks collapse to the same state.

        Returns the literal ``EMPTY_SCREEN`` when the screen has no
        elements. Truncated to 12 hex chars to match :meth:`get_state_id`.
        """
        if not self.elements:
            return EMPTY_SCREEN_ID
        struct_fp = self.get_structural_fingerprint()
        activity = self.activity_name or ""
        title = self.extract_page_title()
        key = f"{struct_fp}|{activity}|{title}"
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def get_structural_fingerprint(self, scroll_aware: bool = True) -> str:
        """Generate a structural fingerprint ignoring dynamic content.

        Hashes (activity_name, sorted element structure tuples) where each element
        contributes (class_name, resource_id, depth, interactability_flags).
        Text, content_description, checked state, and bounds are deliberately
        excluded — they vary across instances of the same structural screen
        (e.g., different WiFi networks, account names, notification counts).

        Truncated to 12 hex characters to match :meth:`get_state_id`'s short
        log-friendly form. Returns ``EMPTY_SCREEN`` if no elements are present
        (broken capture / black screen) so callers can detect unclassifiable
        screens with the same sentinel as ``get_state_id``.

        Args:
            scroll_aware: If True, exclude children of scrollable containers from
                the fingerprint. This merges scroll-equivalent screens (same page
                at different scroll positions) into a single state.

        Used as the primary identity for explorer scheduling — Stoat-style
        model compaction (FSE'17). Text-anchored ``get_state_id`` remains
        available as a secondary label for logging and FSM state naming.
        """
        if not self.elements:
            return EMPTY_SCREEN_ID

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
        return hashlib.sha256(str(fingerprint_input).encode()).hexdigest()[:12]
