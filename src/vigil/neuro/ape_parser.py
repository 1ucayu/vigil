"""APE output parser.

Parses APE's output directory (step-N.xml, step-N.png, action-history.log)
into Vigil's ExplorationResult format for downstream FSM construction.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from loguru import logger

from vigil.core.ape_ui_parser import parse_ape_bounds, parse_ape_xml
from vigil.models.action import Action, ActionType
from vigil.models.state import RawScreen
from vigil.neuro.explorer import ExplorationResult, ExplorationTrace

# APE action type -> Vigil ActionType mapping.
APE_ACTION_MAP: dict[str, ActionType] = {
    "MODEL_CLICK": ActionType.CLICK,
    "MODEL_LONG_CLICK": ActionType.LONG_PRESS,
    "MODEL_SCROLL_TOP_DOWN": ActionType.SCROLL_DOWN,
    "MODEL_SCROLL_BOTTOM_UP": ActionType.SCROLL_UP,
    "MODEL_SCROLL_LEFT_RIGHT": ActionType.SCROLL_UP,
    "MODEL_SCROLL_RIGHT_LEFT": ActionType.SCROLL_DOWN,
    "MODEL_BACK": ActionType.NAVIGATE_BACK,
}

# APE action types that don't map to exploration actions (skip silently).
APE_SKIP_ACTIONS: set[str] = {
    "PHANTOM_CRASH",
    "FUZZ",
    "EVENT_START",
    "EVENT_RESTART",
    "EVENT_CLEAN_RESTART",
    "EVENT_NOP",
    "EVENT_ACTIVATE",
}


class ApeActionEntry:
    """A single parsed entry from APE's action-history.log."""

    __slots__ = ("step", "action_type_str", "vigil_action", "bounds_str", "target_xpath", "raw")

    def __init__(
        self,
        step: int,
        action_type_str: str,
        vigil_action: Action | None,
        bounds_str: str | None,
        target_xpath: str | None,
        raw: dict,
    ) -> None:
        self.step = step
        self.action_type_str = action_type_str
        self.vigil_action = vigil_action
        self.bounds_str = bounds_str
        self.target_xpath = target_xpath
        self.raw = raw


class ApeOutputParser:
    """Parse an APE output directory into ExplorationResult.

    Expected directory structure:
        <output_dir>/
            step-0.xml, step-0.png
            step-1.xml, step-1.png
            ...
            action-history.log
    """

    def __init__(self, output_dir: Path, app_package: str) -> None:
        self._output_dir = output_dir
        self._app_package = app_package

    def parse(self) -> ExplorationResult:
        """Parse all APE output files into an ExplorationResult."""
        # Discover step files
        step_xmls = sorted(self._output_dir.glob("step-*.xml"))
        if not step_xmls:
            logger.warning(f"No step XML files found in {self._output_dir}")
            return ExplorationResult(app_package=self._app_package)

        step_nums = []
        for p in step_xmls:
            m = re.match(r"step-(\d+)\.xml", p.name)
            if m:
                step_nums.append(int(m.group(1)))
        step_nums.sort()

        logger.info(f"Found {len(step_nums)} APE steps to parse")

        # Parse each step's XML into a RawScreen
        screens: dict[str, RawScreen] = {}
        step_to_sid: dict[int, str] = {}
        fp_to_sid: dict[str, str] = {}

        for n in step_nums:
            screen = self._parse_step(n)
            if screen is None:
                continue

            fp = screen.get_structural_fingerprint()
            if fp not in fp_to_sid:
                fp_to_sid[fp] = screen.screen_id
                screens[screen.screen_id] = screen

            # Map step to canonical screen_id (first seen with this fingerprint)
            step_to_sid[n] = fp_to_sid[fp]

        # Parse action history
        actions = self._parse_action_history()

        # Build traces by correlating steps with actions
        traces = self._build_traces(step_to_sid, actions)

        # Enrich element bounds from action log where available
        self._enrich_bounds(screens, actions, step_to_sid)

        result = ExplorationResult(
            app_package=self._app_package,
            screens=screens,
            traces=traces,
            total_steps=len(step_nums),
            unique_screens=len(screens),
            output_dir=str(self._output_dir),
        )

        logger.info(
            f"APE output parsed: {len(step_nums)} steps, "
            f"{len(screens)} unique screens, {len(traces)} traces"
        )
        return result

    def _parse_step(self, step_num: int) -> RawScreen | None:
        """Parse step-N.xml + step-N.png into a RawScreen."""
        xml_path = self._output_dir / f"step-{step_num}.xml"
        png_path = self._output_dir / f"step-{step_num}.png"

        if not xml_path.exists():
            return None

        xml_string = xml_path.read_text(encoding="utf-8")
        elements = parse_ape_xml(xml_string, app_package=self._app_package)

        if not elements:
            return None

        # Extract package from root element for activity detection
        package_name = None
        for e in elements:
            if e.resource_id and ":" in e.resource_id:
                package_name = e.resource_id.split(":")[0]
                break

        # Extract functional page identity from raw XML
        page_title, container_sig, has_modal = self._extract_page_identity(xml_string)

        screen_id = f"scr_{step_num:04d}"
        return RawScreen(
            screen_id=screen_id,
            activity_name=None,
            package_name=package_name or self._app_package,
            screenshot_path=str(png_path) if png_path.exists() else None,
            xml_tree_path=str(xml_path),
            elements=elements,
            metadata={
                "source": "ape",
                "ape_step": step_num,
                "page_title": page_title,
                "container_signature": container_sig,
                "has_modal": has_modal,
            },
        )

    def _extract_page_identity(self, xml_string: str) -> tuple[str, str, bool]:
        """Extract functional page identity from raw APE XML.

        Returns:
            page_title: Action bar title text (functional page name).
            container_signature: Sorted top-level container class names
                (e.g., "FrameLayout,RecyclerView,ViewGroup").
            has_modal: Whether a dialog/popup overlay is present.
        """
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError:
            return "", "", False

        # Find title from action_bar_title or first clickable title element
        page_title = ""
        for node in root.iter("node"):
            rid = node.get("resource-id", "")
            if "action_bar_title" in rid:
                page_title = node.get("text", "").strip()
                break

        # Top-level container classes (direct children of root, in app package)
        app_pkg = root.get("package", "")
        container_classes = []
        has_modal = False
        for child in root:
            child_cls = (child.get("class", "") or "").rsplit(".", 1)[-1]
            child_pkg = child.get("package", "")

            # Detect modal overlay (AlertDialog, Dialog, PopupWindow)
            if any(kw in child_cls for kw in ("Dialog", "Popup", "BottomSheet")):
                has_modal = True

            # Only include app-package containers
            if child_pkg == app_pkg or not child_pkg:
                container_classes.append(child_cls)

        # Also detect modal from button patterns (button1/button2 = AlertDialog)
        for node in root.iter("node"):
            rid = node.get("resource-id", "")
            if rid in ("android:id/button1", "android:id/button2"):
                has_modal = True
                break

        container_sig = ",".join(sorted(set(container_classes)))
        return page_title, container_sig, has_modal

    def _parse_action_history(self) -> list[ApeActionEntry]:
        """Parse action-history.log into structured entries."""
        log_path = self._output_dir / "action-history.log"
        if not log_path.exists():
            logger.warning("action-history.log not found")
            return []

        entries: list[ApeActionEntry] = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue

            entry = self._parse_log_line(line)
            if entry is not None:
                entries.append(entry)

        logger.debug(f"Parsed {len(entries)} action entries from action-history.log")
        return entries

    def _parse_log_line(self, line: str) -> ApeActionEntry | None:
        """Parse a single line from action-history.log.

        Format: <clock_timestamp_ms> <JSON_object>
        The JSON has "timestamp" (step number), "actionType", optional "bounds", "target".
        """
        # Find the JSON object (first '{' to end)
        idx = line.find("{")
        if idx < 0:
            return None

        try:
            data = json.loads(line[idx:])
        except json.JSONDecodeError:
            return None

        action_type_str = data.get("actionType", "")
        step = data.get("timestamp")
        if step is None:
            return None

        # Skip non-exploration actions
        if action_type_str in APE_SKIP_ACTIONS:
            return None

        # Map to Vigil action type
        vigil_type = APE_ACTION_MAP.get(action_type_str)
        vigil_action = None
        bounds_str = data.get("bounds")

        if vigil_type is not None:
            target_bounds = parse_ape_bounds(bounds_str) if bounds_str else None
            vigil_action = Action(
                action_type=vigil_type,
                target_bounds=target_bounds,
            )

        return ApeActionEntry(
            step=int(step),
            action_type_str=action_type_str,
            vigil_action=vigil_action,
            bounds_str=bounds_str,
            target_xpath=data.get("target"),
            raw=data,
        )

    def _build_traces(
        self,
        step_to_sid: dict[int, str],
        actions: list[ApeActionEntry],
    ) -> list[ExplorationTrace]:
        """Build ExplorationTrace list by correlating actions with step screens."""
        traces: list[ExplorationTrace] = []

        for entry in actions:
            if entry.vigil_action is None:
                continue

            source_sid = step_to_sid.get(entry.step)
            target_sid = step_to_sid.get(entry.step + 1)

            if source_sid is None or target_sid is None:
                continue

            trace = ExplorationTrace(
                step_number=entry.step,
                source_screen_id=source_sid,
                action=entry.vigil_action,
                target_screen_id=target_sid,
                timestamp="",
            )
            traces.append(trace)

        return traces

    def _enrich_bounds(
        self,
        screens: dict[str, RawScreen],
        actions: list[ApeActionEntry],
        step_to_sid: dict[int, str],
    ) -> None:
        """Backfill element bounds from action-history.log targeted actions.

        When APE clicks/scrolls an element, the log includes bounds for that
        element. We use the target XPath to match elements and set their bounds.
        """
        for entry in actions:
            if not entry.bounds_str or not entry.target_xpath:
                continue

            sid = step_to_sid.get(entry.step)
            if sid is None or sid not in screens:
                continue

            bounds = parse_ape_bounds(entry.bounds_str)
            if bounds == [0, 0, 0, 0]:
                continue

            # Match element by resource-id extracted from XPath
            rid_match = re.search(r"@resource-id='([^']*)'", entry.target_xpath)
            if not rid_match:
                continue
            target_rid = rid_match.group(1)

            for element in screens[sid].elements:
                if element.resource_id == target_rid and element.bounds == [0, 0, 0, 0]:
                    element.bounds = bounds
                    break
