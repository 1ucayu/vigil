"""APE accessibility tree XML parser.

Parses APE's step-N.xml output into structured UIElement representations.
APE XML differs from uiautomator2 dump_hierarchy(): no bounds attribute,
no <hierarchy> wrapper, has scroll-type attribute, text truncated to 8 chars.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from loguru import logger

from vigil.models.state import UIElement

# System packages to filter out (same as ui_parser.py).
SYSTEM_PACKAGES: set[str] = {
    "com.android.systemui",
}


def parse_ape_xml(
    xml_string: str,
    app_package: str | None = None,
) -> list[UIElement]:
    """Parse APE step-N.xml into a flat list of UIElements.

    APE XML format differences from uiautomator2:
    - Root is <node> directly (no <hierarchy> wrapper)
    - No bounds attribute on nodes
    - Has scroll-type attribute
    - Text may be truncated to 8 characters

    Args:
        xml_string: Raw XML string from APE step-N.xml file.
        app_package: If provided, filter out elements from system packages.

    Returns:
        Flat list of UIElement objects. Returns empty list on parse failure.
    """
    if not xml_string or not xml_string.strip():
        return []

    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError:
        logger.warning("Failed to parse APE XML")
        return []

    elements: list[UIElement] = []
    counter = [0]

    # APE XML root is <node> directly (not <hierarchy>)
    if root.tag == "node":
        _parse_node(root, depth=0, counter=counter, elements=elements, app_package=app_package)
    else:
        # Fallback: might have child <node> elements
        for child in root:
            if child.tag == "node":
                _parse_node(
                    child, depth=0, counter=counter, elements=elements, app_package=app_package
                )

    logger.debug(f"Parsed {len(elements)} UI elements from APE XML")
    return elements


def parse_ape_bounds(bounds_str: str) -> list[int]:
    """Parse bounds from APE action-history.log format.

    APE uses comma-separated format: "left,top,right,bottom" (no brackets)
    or the standard "[left,top][right,bottom]" format.

    Returns:
        [left, top, right, bottom] or [0, 0, 0, 0] on failure.
    """
    matches = re.findall(r"\d+", bounds_str)
    if len(matches) == 4:
        return [int(x) for x in matches]
    return [0, 0, 0, 0]


def _parse_node(
    node: ET.Element,
    depth: int,
    counter: list[int],
    elements: list[UIElement],
    app_package: str | None = None,
    parent_id: str | None = None,
) -> str | None:
    """Recursively parse an APE <node> element into UIElement(s).

    Returns:
        The element_id of the parsed node, or None if filtered.
    """
    if app_package:
        pkg = node.attrib.get("package", "")
        if pkg in SYSTEM_PACKAGES:
            return None

    element_id = f"e_{counter[0]:04d}"
    counter[0] += 1

    child_ids = []
    for child in node:
        if child.tag == "node":
            child_id = _parse_node(
                child, depth + 1, counter, elements, app_package, parent_id=element_id
            )
            if child_id is not None:
                child_ids.append(child_id)

    def _bool(attr_name: str) -> bool:
        return node.attrib.get(attr_name, "false") == "true"

    class_name = node.attrib.get("class", "")

    scroll_type = node.attrib.get("scroll-type", "none")
    is_scrollable = _bool("scrollable") or scroll_type != "none"

    is_editable = _bool("focusable") and class_name.endswith("EditText")

    element = UIElement(
        element_id=element_id,
        class_name=class_name,
        resource_id=node.attrib.get("resource-id") or None,
        text=node.attrib.get("text") or None,
        content_description=node.attrib.get("content-desc") or None,
        bounds=[0, 0, 0, 0],
        is_clickable=_bool("clickable"),
        is_long_clickable=_bool("long-clickable"),
        is_scrollable=is_scrollable,
        is_editable=is_editable,
        is_checkable=_bool("checkable"),
        is_checked=_bool("checked"),
        is_enabled=_bool("enabled"),
        depth=depth,
        children=child_ids,
        parent_id=parent_id,
    )
    elements.append(element)
    return element_id
