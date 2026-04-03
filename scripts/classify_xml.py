#!/usr/bin/env python3
"""Classify scrollable containers in a single accessibility tree XML file.

Usage:
    python scripts/classify_xml.py <xml_file>
    python scripts/classify_xml.py tests/fixtures/settings_main.xml
    python scripts/classify_xml.py /path/to/step-42.xml --verbose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vigil.core.ui_parser import parse_hierarchy_xml
from vigil.models.state import RawScreen
from vigil.neuro.state_abstractor import StateAbstractor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify scrollable containers in an XML accessibility tree.",
    )
    parser.add_argument("xml_file", help="Path to a uiautomator2-style XML file")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show per-child skeleton details"
    )
    args = parser.parse_args()

    xml_path = Path(args.xml_file)
    if not xml_path.exists():
        print(f"Error: {xml_path} not found")
        sys.exit(1)

    xml = xml_path.read_text()
    elements = parse_hierarchy_xml(xml)
    if not elements:
        print("No elements parsed from XML.")
        sys.exit(1)

    screen = RawScreen(screen_id="input", elements=elements)
    elements_by_id = {e.element_id: e for e in elements}

    print(f"Parsed {len(elements)} elements from {xml_path.name}")
    print()

    abstractor = StateAbstractor()
    containers = screen.find_scrollable_containers()

    if not containers:
        print("No scrollable containers found.")
        sys.exit(0)

    for container in containers:
        children = screen.get_container_children(container)
        result = abstractor.classify_container(container, children, elements_by_id)

        print(f"Container: {container.class_name}")
        print(f"  element_id:    {container.element_id}")
        print(f"  resource_id:   {container.resource_id or '(none)'}")
        print(f"  children:      {result.num_children}")
        print(f"  core children: {result.num_core_children} (after header/footer strip)")
        print(f"  header stripped: {result.stripped_header}")
        print(f"  footer stripped: {result.stripped_footer}")
        print(f"  unique skeletons: {result.num_unique_skeletons}")
        print(f"  dominant ratio:   {result.dominant_skeleton_ratio:.2f}")
        print(f"  max segment size: {result.max_segment_size}")
        print(f"  >>> TYPE: {result.container_type.value.upper()}")
        if result.representative_skeleton:
            print(f"  representative skeleton hash: {result.representative_skeleton}")
        print()

        if args.verbose and children:
            skeletons = abstractor._compute_child_skeletons(children, elements_by_id)
            print("  Children detail:")
            for i, (child, skel) in enumerate(zip(children, skeletons, strict=False)):
                text = child.text or child.content_description or ""
                if text:
                    text = f' "{text[:30]}"'
                print(f"    [{i}] {child.class_name}{text}  skeleton={skel}")
            print()


if __name__ == "__main__":
    main()
