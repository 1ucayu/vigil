"""Stage 1: UI Exploration via uiautomator2.

BFS/DFS traversal of Android app screens. At each screen, enumerates interactable
elements, executes each action, and records the resulting screen (accessibility tree
XML + screenshot PNG + element list).
"""

from __future__ import annotations

import json
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uiautomator2 as u2
from loguru import logger
from pydantic import BaseModel, Field

from vigil.core.action_types import enumerate_actions
from vigil.core.config import VigilConfig
from vigil.core.ui_parser import parse_hierarchy_xml
from vigil.models.action import Action, ActionType
from vigil.models.state import RawScreen


class ExplorationTrace(BaseModel):
    """A single exploration step: source screen -> action -> target screen."""

    step_number: int
    source_screen_id: str
    action: Action
    target_screen_id: str
    timestamp: str


class ExplorationResult(BaseModel):
    """Output of a complete app exploration run."""

    app_package: str
    screens: dict[str, RawScreen] = Field(default_factory=dict)
    traces: list[ExplorationTrace] = Field(default_factory=list)
    total_steps: int = 0
    unique_screens: int = 0
    duration_seconds: float = 0.0
    output_dir: str = ""


class AppExplorer:
    """BFS/DFS exploration engine for Android apps via uiautomator2.

    Connects to an Android device, traverses app screens by exercising
    interactable elements, captures accessibility trees + screenshots,
    and records exploration traces.

    Args:
        device_serial: ADB serial of the target device.
        app_package: Android package name to explore.
        config: Vigil configuration.
        output_dir: Base output directory (default: data/apps/<app_name>/).
    """

    # Max back-presses to attempt when navigating to a source screen
    MAX_BACK_PRESSES = 10
    # Max scroll attempts per scrollable element
    MAX_SCROLLS_PER_ELEMENT = 3
    # Seconds to wait after an action for the screen to stabilize
    STABILITY_WAIT = 1.5
    # Max retries for device calls
    DEVICE_RETRIES = 3

    def __init__(
        self,
        device_serial: str,
        app_package: str,
        config: VigilConfig,
        output_dir: Path | None = None,
    ) -> None:
        self._serial = device_serial
        self._app_package = app_package
        self._config = config
        self._device: u2.Device | None = None
        self._screen_counter = 0

        if output_dir is None:
            # Extract short app name from package (last segment)
            app_name = app_package.rsplit(".", maxsplit=1)[-1]
            self._output_dir = Path(f"data/apps/{app_name}")
        else:
            self._output_dir = output_dir

        # Ensure output directories exist
        (self._output_dir / "screens").mkdir(parents=True, exist_ok=True)
        (self._output_dir / "trees").mkdir(parents=True, exist_ok=True)
        (self._output_dir / "traces").mkdir(parents=True, exist_ok=True)

    def explore(self) -> ExplorationResult:
        """Run the exploration and return structured results.

        Returns:
            ExplorationResult containing all discovered screens and traces.
        """
        start_time = time.monotonic()
        self._connect_device()
        assert self._device is not None

        # Start the target app and wait for it to come to foreground
        logger.info(f"Starting app: {self._app_package}")
        self._device.app_start(self._app_package, stop=True)
        if not self._wait_for_app_foreground():
            logger.error("App did not come to foreground")
            return ExplorationResult(app_package=self._app_package)

        # Capture initial screen
        initial_screen = self._capture_screen()
        if initial_screen is None:
            logger.error("Failed to capture initial screen")
            return ExplorationResult(app_package=self._app_package)

        # Validate the initial screen has meaningful content (not just
        # system chrome). If the app is still loading, retry.
        min_elements = 3
        if len(initial_screen.get_interactable_elements()) < min_elements:
            logger.warning(
                f"Initial screen has only {len(initial_screen.get_interactable_elements())} "
                f"interactable elements, waiting for app to fully load..."
            )
            for _ in range(3):
                time.sleep(2.0)
                initial_screen = self._capture_screen()
                if (
                    initial_screen is not None
                    and len(initial_screen.get_interactable_elements()) >= min_elements
                ):
                    break
            else:
                if initial_screen is None:
                    logger.error("Failed to capture initial screen after retries")
                    return ExplorationResult(app_package=self._app_package)

        initial_fp = initial_screen.get_structural_fingerprint()

        visited: set[str] = {initial_fp}
        screens: dict[str, RawScreen] = {initial_screen.screen_id: initial_screen}
        traces: list[ExplorationTrace] = []

        # fingerprint -> canonical screen_id (first time we saw this fingerprint)
        fp_to_sid: dict[str, str] = {initial_fp: initial_screen.screen_id}
        # screen_id -> list of (action, target_screen_id) describing how to reach it
        # from the initial screen (empty list = initial screen itself)
        nav_paths: dict[str, list[tuple[Action, str]]] = {initial_screen.screen_id: []}
        nav_failures: dict[str, int] = {}  # screen_id -> consecutive failure count
        max_nav_failures = 3

        # During exploration, text input doesn't discover new screens and
        # pollutes text fields — exclude it from candidate actions.
        skip_actions: set[ActionType] = {
            ActionType.INPUT_TEXT,
            ActionType.NAVIGATE_HOME,
            ActionType.LONG_PRESS,
        }

        # Build frontier: (screen_id, action) pairs.
        # Exclude navigate_back for initial screen — it exits the app.
        frontier: deque[tuple[str, Action]] = deque()
        for action in enumerate_actions(initial_screen, exclude=skip_actions):
            if action.action_type == ActionType.NAVIGATE_BACK:
                continue
            frontier.append((initial_screen.screen_id, action))

        max_steps = self._config.app.max_exploration_steps
        step = 0
        current_fp = initial_fp

        logger.info(
            f"Starting {self._config.app.exploration_strategy} exploration "
            f"(max {max_steps} steps, {len(frontier)} initial actions)"
        )

        while frontier and step < max_steps:
            # Pop next item, preferring actions from the current screen
            source_screen_id, action = self._pop_frontier_prefer_current(
                frontier, fp_to_sid.get(current_fp, ""), step, max_steps
            )

            # Navigate to source screen if we're not already there
            source_fp = screens[source_screen_id].get_structural_fingerprint()
            if current_fp != source_fp:
                nav_ok = self._navigate_to_screen_via_replay(source_screen_id, screens, nav_paths)
                if nav_ok:
                    current_fp = source_fp
                    nav_failures.pop(source_screen_id, None)
                else:
                    current_fp = self._identify_current_fp()
                    nav_failures[source_screen_id] = nav_failures.get(source_screen_id, 0) + 1
                    if nav_failures[source_screen_id] >= max_nav_failures:
                        before = len(frontier)
                        frontier = deque(
                            (sid, act) for sid, act in frontier if sid != source_screen_id
                        )
                        drained = before - len(frontier)
                        logger.warning(
                            f"Screen {source_screen_id} unreachable after "
                            f"{max_nav_failures} attempts, "
                            f"draining {drained} remaining actions"
                        )
                    else:
                        logger.debug(
                            f"Navigation to {source_screen_id} failed "
                            f"(attempt {nav_failures[source_screen_id]}"
                            f"/{max_nav_failures})"
                        )
                    continue

            # Execute the action
            logger.debug(
                f"Step {step + 1}/{max_steps}: "
                f"{action.action_type.value} on {action.target_element_id or 'global'} "
                f"from {source_screen_id}"
            )
            self._execute_action(action)
            self._wait_for_stability()
            step += 1

            # Dismiss keyboard if it popped up
            self._dismiss_keyboard_if_showing()

            # Check if we're still in the target app
            if not self._is_within_app():
                logger.debug("Left target app, recovering")
                self._restart_app()
                current_fp = self._identify_current_fp()
                continue

            # Capture the resulting screen
            target_screen = self._capture_screen()
            if target_screen is None:
                continue

            target_fp = target_screen.get_structural_fingerprint()

            # Resolve to canonical screen_id for trace recording
            canonical_target_id = fp_to_sid.get(target_fp, target_screen.screen_id)

            # Record trace
            trace = ExplorationTrace(
                step_number=step,
                source_screen_id=source_screen_id,
                action=action,
                target_screen_id=canonical_target_id,
                timestamp=_now_iso(),
            )
            traces.append(trace)
            current_fp = target_fp

            # Check if this is a new screen
            if target_fp not in visited:
                visited.add(target_fp)
                screens[target_screen.screen_id] = target_screen
                fp_to_sid[target_fp] = target_screen.screen_id

                # Record navigation path: path to source + this action
                source_path = nav_paths.get(source_screen_id, [])
                nav_paths[target_screen.screen_id] = source_path + [
                    (action, target_screen.screen_id)
                ]

                logger.info(
                    f"New screen discovered: {target_screen.screen_id} "
                    f"(activity={target_screen.activity_name}, "
                    f"total={len(screens)})"
                )

                # Enumerate actions for the new screen and add to frontier
                for new_action in enumerate_actions(target_screen, exclude=skip_actions):
                    frontier.append((target_screen.screen_id, new_action))

                # Handle scrollable content
                scroll_screens = self._handle_scroll_discovery(target_screen)
                for ss in scroll_screens:
                    ss_fp = ss.get_structural_fingerprint()
                    if ss_fp not in visited:
                        visited.add(ss_fp)
                        screens[ss.screen_id] = ss
                        fp_to_sid[ss_fp] = ss.screen_id
                        # Add actions from scroll-revealed elements to frontier.
                        # Use the original screen_id as source since we're still
                        # on the same logical page, just scrolled down.
                        new_actions = enumerate_actions(ss, exclude=skip_actions)
                        for new_action in new_actions:
                            frontier.append((target_screen.screen_id, new_action))
                        logger.info(
                            f"Scroll revealed {ss.screen_id}, "
                            f"added {len(new_actions)} actions to frontier"
                        )

                # Update current_fp after scroll discovery may have changed screen
                if scroll_screens:
                    current_fp = self._identify_current_fp()

        elapsed = time.monotonic() - start_time
        result = ExplorationResult(
            app_package=self._app_package,
            screens=screens,
            traces=traces,
            total_steps=step,
            unique_screens=len(screens),
            duration_seconds=round(elapsed, 2),
            output_dir=str(self._output_dir),
        )

        self._save_result(result)

        logger.info(
            f"Exploration complete: {step} steps, {len(screens)} unique screens, {elapsed:.1f}s"
        )
        return result

    # --- Device interaction ---

    def _connect_device(self) -> None:
        """Establish connection to the Android device."""
        logger.info(f"Connecting to device: {self._serial}")
        self._device = u2.connect(self._serial)
        info = self._device.info
        logger.info(
            f"Connected: {info.get('productName', 'unknown')} (SDK {info.get('sdkInt', '?')})"
        )

    def _capture_screen(self) -> RawScreen | None:
        """Capture the current screen state (hierarchy + screenshot).

        Returns:
            RawScreen with parsed elements, or None on failure.
        """
        assert self._device is not None
        self._screen_counter += 1
        screen_id = f"scr_{self._screen_counter:04d}"

        try:
            # Get current activity info
            current = self._device.app_current()
            activity_name = current.get("activity", "")
            package_name = current.get("package", "")

            # Dump accessibility tree
            xml_string = self._device.dump_hierarchy()

            # Take screenshot
            screenshot_path = self._output_dir / "screens" / f"{screen_id}.png"
            self._device.screenshot(str(screenshot_path))

            # Save XML
            xml_path = self._output_dir / "trees" / f"{screen_id}.xml"
            xml_path.write_text(xml_string, encoding="utf-8")

            # Parse hierarchy into UIElements (filter out system UI)
            elements = parse_hierarchy_xml(xml_string, app_package=self._app_package)

            screen = RawScreen(
                screen_id=screen_id,
                activity_name=activity_name,
                package_name=package_name,
                screenshot_path=str(screenshot_path),
                xml_tree_path=str(xml_path),
                elements=elements,
                timestamp=_now_iso(),
                metadata={
                    "device_serial": self._serial,
                },
            )
            return screen

        except Exception:
            logger.exception(f"Failed to capture screen {screen_id}")
            return None

    def _execute_action(self, action: Action) -> None:
        """Execute an action on the device."""
        assert self._device is not None

        try:
            if action.action_type == ActionType.CLICK:
                if action.target_bounds:
                    cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                    cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                    self._device.click(cx, cy)

            elif action.action_type == ActionType.LONG_PRESS:
                if action.target_bounds:
                    cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                    cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                    self._device.long_click(cx, cy)

            elif action.action_type == ActionType.INPUT_TEXT:
                if action.target_bounds:
                    cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                    cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                    self._device.click(cx, cy)  # focus the field first
                    time.sleep(0.3)
                if action.input_text:
                    self._device.send_keys(action.input_text)

            elif action.action_type == ActionType.SCROLL_UP:
                if action.target_bounds:
                    cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                    cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                    h = action.target_bounds[3] - action.target_bounds[1]
                    self._device.swipe(cx, cy, cx, cy - h // 3, duration=0.3)
                else:
                    self._device.swipe_ext("up")

            elif action.action_type == ActionType.SCROLL_DOWN:
                if action.target_bounds:
                    cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                    cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                    h = action.target_bounds[3] - action.target_bounds[1]
                    self._device.swipe(cx, cy, cx, cy + h // 3, duration=0.3)
                else:
                    self._device.swipe_ext("down")

            elif action.action_type == ActionType.NAVIGATE_BACK:
                self._device.press("back")

            elif action.action_type == ActionType.NAVIGATE_HOME:
                self._device.press("home")

        except Exception:
            logger.warning(f"Failed to execute action: {action.action_type.value}")

    def _wait_for_stability(self) -> None:
        """Wait for the screen to stabilize after an action."""
        time.sleep(self.STABILITY_WAIT)

    def _dismiss_keyboard_if_showing(self) -> None:
        """Detect and dismiss soft keyboard to prevent fingerprint corruption."""
        assert self._device is not None
        try:
            current_xml = self._device.dump_hierarchy()
            ime_indicators = [
                "com.google.android.inputmethod",
                "com.android.inputmethod",
                "com.sohu.inputmethod",
                "com.baidu.input",
                "com.iflytek.inputmethod",
                "com.miui.contentcatcher",
            ]
            if any(indicator in current_xml for indicator in ime_indicators):
                logger.debug("Keyboard detected, dismissing")
                self._device.press("back")
                time.sleep(0.5)
        except Exception:
            pass

    def _wait_for_app_foreground(self, timeout: float = 10.0) -> bool:
        """Poll until the target app is in the foreground.

        Args:
            timeout: Max seconds to wait.

        Returns:
            True if the app reached foreground within the timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_within_app():
                return True
            time.sleep(1.0)
        return False

    def _is_within_app(self) -> bool:
        """Check if the target app is still in the foreground."""
        assert self._device is not None
        try:
            current = self._device.app_current()
            return current.get("package", "") == self._app_package
        except Exception:
            return False

    def _recover_from_crash(self) -> bool:
        """Try to return to the target app after leaving it.

        Returns:
            True if successfully returned to the app.
        """
        assert self._device is not None

        # Try pressing back a few times
        for _ in range(3):
            self._device.press("back")
            time.sleep(0.5)
            if self._is_within_app():
                return True

        # Restart the app
        try:
            self._device.app_start(self._app_package)
            time.sleep(2.0)
            return self._is_within_app()
        except Exception:
            return False

    def _navigate_to_screen_via_replay(
        self,
        target_screen_id: str,
        screens: dict[str, RawScreen],
        nav_paths: dict[str, list[tuple[Action, str]]],
    ) -> bool:
        """Navigate to a target screen by restarting the app and replaying actions.

        Instead of pressing back (which can only go up the stack), we restart
        the app to get to the initial screen, then replay the recorded action
        path that leads to the target.

        Returns:
            True if we reached the target screen.
        """
        assert self._device is not None
        target_screen = screens.get(target_screen_id)
        if target_screen is None:
            return False

        target_fp = target_screen.get_structural_fingerprint()

        # Check if the target is the initial screen — just restart
        path = nav_paths.get(target_screen_id)
        if path is None:
            logger.debug(f"No nav path for {target_screen_id}")
            return False

        # Restart app to get to initial screen
        logger.debug(f"Navigating to {target_screen_id} via replay ({len(path)} steps)")
        self._restart_app()

        if not path:
            # Target IS the initial screen — just check we're there
            screen = self._capture_screen()
            if screen is not None:
                fp = screen.get_structural_fingerprint()
                if fp == target_fp:
                    return True
            return False

        # Replay each action in the path
        for action, _ in path:
            if not self._is_within_app():
                logger.debug("Left app during replay")
                return False
            self._execute_action(action)
            self._wait_for_stability()

        # Verify we arrived at the target
        screen = self._capture_screen()
        if screen is not None:
            fp = screen.get_structural_fingerprint()
            if fp == target_fp:
                return True
            logger.debug(f"Replay ended at wrong screen (expected {target_fp[:12]}, got {fp[:12]})")
        return False

    def _handle_scroll_discovery(self, screen: RawScreen) -> list[RawScreen]:
        """Scroll scrollable elements to discover hidden content.

        For each scrollable element, scrolls down up to MAX_SCROLLS_PER_ELEMENT
        times. Stops when the fingerprint stabilizes (no new content).

        Returns:
            List of additional RawScreen objects discovered via scrolling.
        """
        assert self._device is not None
        discovered: list[RawScreen] = []

        scrollable_elements = [e for e in screen.elements if e.is_scrollable and e.is_enabled]

        if not scrollable_elements:
            return discovered

        prev_fp = screen.get_structural_fingerprint()

        for element in scrollable_elements:
            for _ in range(self.MAX_SCROLLS_PER_ELEMENT):
                cx = (element.bounds[0] + element.bounds[2]) // 2
                cy = (element.bounds[1] + element.bounds[3]) // 2
                h = element.bounds[3] - element.bounds[1]
                self._device.swipe(cx, cy, cx, cy - h // 3, duration=0.3)
                time.sleep(0.5)

                # Check if scroll accidentally exited the app (e.g. MIUI edge gestures)
                if not self._is_within_app():
                    logger.debug("Scroll exited the app, stopping scroll discovery")
                    self._recover_from_crash()
                    return discovered

                new_screen = self._capture_screen()
                if new_screen is None:
                    break

                new_fp = new_screen.get_structural_fingerprint()
                if new_fp == prev_fp:
                    break  # no new content

                discovered.append(new_screen)
                prev_fp = new_fp

        return discovered

    def _restart_app(self) -> None:
        """Restart the target app (stop + start) and wait for it to load."""
        assert self._device is not None
        self._device.app_start(self._app_package, stop=True)
        self._wait_for_app_foreground()

    def _identify_current_fp(self) -> str:
        """Capture the current screen and return its fingerprint.

        If outside the app, restarts it first. Returns empty string on failure.
        """
        if not self._is_within_app():
            self._restart_app()
        screen = self._capture_screen()
        if screen is None:
            return ""
        return screen.get_structural_fingerprint()

    # --- Frontier management ---

    def _pop_frontier_prefer_current(
        self,
        frontier: deque[tuple[str, Action]],
        current_screen_id: str,
        current_step: int,
        max_steps: int,
    ) -> tuple[str, Action]:
        """Pop from frontier, preferring actions from the current screen.

        Scans the frontier for an action matching the current screen to avoid
        unnecessary navigation. Falls back to strategy-based popping.
        """
        # First, try to find an action from the current screen
        if current_screen_id:
            for i, (sid, act) in enumerate(frontier):
                if sid == current_screen_id:
                    del frontier[i]
                    return (sid, act)

        # No match — fall back to strategy-based pop
        strategy = self._config.app.exploration_strategy

        if strategy == "bfs":
            return frontier.popleft()
        elif strategy == "dfs":
            return frontier.pop()
        elif strategy == "hybrid":
            # BFS for first 60% of budget, DFS for the rest
            if current_step < int(max_steps * 0.6):
                return frontier.popleft()
            else:
                return frontier.pop()
        else:
            return frontier.popleft()  # default to BFS

    # --- Persistence ---

    def _save_result(self, result: ExplorationResult) -> None:
        """Save the full exploration result as a JSON trace file."""
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        trace_path = self._output_dir / "traces" / f"exploration_{timestamp}.json"

        # Build compact screen data for FSM construction:
        # - full screen metadata (activity, package, paths, fingerprint)
        # - only interactable elements inline (they define FSM transitions)
        # - element_summary with total count (non-interactable still in XML files)
        compact_screens: dict[str, Any] = {}
        for sid, s in result.screens.items():
            interactable = s.get_interactable_elements()
            compact_screens[sid] = {
                "screen_id": s.screen_id,
                "activity_name": s.activity_name,
                "package_name": s.package_name,
                "screenshot_path": s.screenshot_path,
                "xml_tree_path": s.xml_tree_path,
                "fingerprint": s.get_structural_fingerprint(),
                "total_elements": len(s.elements),
                "interactable_elements": [e.model_dump(mode="json") for e in interactable],
                "timestamp": s.timestamp,
                "metadata": self._extract_metadata(s),
            }

        data: dict[str, Any] = {
            "app_package": result.app_package,
            "device_serial": self._serial,
            "exploration_strategy": self._config.app.exploration_strategy,
            "max_steps": self._config.app.max_exploration_steps,
            "total_steps": result.total_steps,
            "unique_screens": result.unique_screens,
            "duration_seconds": result.duration_seconds,
            "timestamp": _now_iso(),
            "screens": compact_screens,
            "traces": [t.model_dump(mode="json") for t in result.traces],
        }

        trace_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info(f"Exploration trace saved to {trace_path}")

    @staticmethod
    def _extract_metadata(screen: RawScreen) -> dict[str, Any]:
        """Extract page_title and other metadata from a screen's elements.

        Scans all elements (not just interactable) for title resource IDs
        so that FSM construction can use page_title for fingerprinting and
        state naming.
        """
        metadata: dict[str, Any] = {}

        for e in screen.elements:
            rid = e.resource_id or ""
            if "action_bar_title" in rid.lower() and e.text and e.text.strip():
                metadata["page_title"] = e.text.strip()
                return metadata

        # Broader title search
        for e in screen.elements:
            rid = e.resource_id or ""
            if (
                rid
                and "title" in rid.lower()
                and "subtitle" not in rid.lower()
                and e.text
                and e.text.strip()
                and len(e.text.strip()) > 1
            ):
                metadata["page_title"] = e.text.strip()
                return metadata

        return metadata


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(tz=UTC).isoformat()
