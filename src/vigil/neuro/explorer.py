"""Stage 1: UI Exploration as plain BFS with scroll-to-enumerate and restart-per-action.

Algorithm:

    pop state_id from queue
    cold-start + navigate to state_id
    enumerate clickables by scrolling until anchor set stops changing
    for each enumerated clickable:
        cold-start + navigate + click + capture post screen
        record whatever state we actually saw (may drift from intended)
        queue novel target states

Drift is not a failure. If replay lands somewhere other than the
intended source state, we record the actual source plus the intended
one and move on. The FSM builder aggregates facts; the explorer does
not promise nav_path precision.

Sentinel ``target_state_id`` values flag non-transitional outcomes for
downstream filters: ``COLD_START_FAILED`` (couldn't foreground the
app), ``ACTION_FAILED`` (device API raised), ``LEFT_APP`` (ended up
outside the target package).
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uiautomator2 as u2
from loguru import logger
from pydantic import BaseModel, Field

from vigil.core.config import VigilConfig
from vigil.core.ui_parser import parse_hierarchy_xml
from vigil.models.action import Action, ActionType
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.app_prior import AppPrior

SENTINEL_COLD_START_FAILED = "COLD_START_FAILED"
SENTINEL_ACTION_FAILED = "ACTION_FAILED"
SENTINEL_LEFT_APP = "LEFT_APP"
SENTINEL_TARGETS = frozenset(
    {SENTINEL_COLD_START_FAILED, SENTINEL_ACTION_FAILED, SENTINEL_LEFT_APP}
)

# Feature A: priority weights. Click-equivalent actions default to 1.0;
# navigation / scroll actions are deprioritized because they tend to revisit
# already-known states rather than discover new ones.
ACTION_TYPE_WEIGHT: dict[ActionType, float] = {
    ActionType.CLICK: 1.0,
    ActionType.LONG_PRESS: 1.0,
    ActionType.INPUT_TEXT: 1.0,
    ActionType.NAVIGATE_BACK: 0.3,
    ActionType.NAVIGATE_HOME: 0.2,
    ActionType.SCROLL_DOWN: 0.5,
    ActionType.SCROLL_UP: 0.5,
}


def action_key(action: Action) -> str:
    """In-state dedup key. Resource-id primary; 50-px bounds bucket fallback."""
    if action.target_resource_id:
        return f"{action.action_type.value}|{action.target_resource_id}"
    bounds = action.target_bounds or [0, 0, 0, 0]
    qb = ",".join(str(round(x / 50) * 50) for x in bounds)
    return f"{action.action_type.value}|qb:{qb}"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _short_target(action: Action) -> str:
    if action.target_resource_id:
        rid = action.target_resource_id
        return f"rid={rid.split(':id/', 1)[1] if ':id/' in rid else rid}"
    if action.target_bounds:
        return f"bounds={action.target_bounds}"
    return "-"


def _is_edit_text(element: UIElement) -> bool:
    """Heuristic: is this element an Android EditText / text-input widget?"""
    if element.is_editable:
        return True
    return "EditText" in (element.class_name or "")


def _generate_edit_value(element: UIElement) -> str:
    """Pick a plausible text value for an EditText.

    Priority:
      1. Hint / content-desc keyword matching (email, password, phone, ...).
      2. ``android:inputType`` bitmask inference.
      3. Generic ``test123`` fallback.

    Always returns a non-empty ASCII string.
    """
    hint = (element.content_description or "").lower()
    if any(kw in hint for kw in ("email", "e-mail")):
        return "test@example.com"
    if any(kw in hint for kw in ("password", "pwd", "pass")):
        return "TestPass123!"
    if any(kw in hint for kw in ("phone", "mobile", "tel")):
        return "5551234567"
    if any(kw in hint for kw in ("search", "query", "find")):
        return "test"
    if any(kw in hint for kw in ("url", "website", "link")):
        return "https://example.com"
    if "name" in hint:
        return "Test User"

    it = element.input_type or 0
    type_class = it & 0x0F
    type_variation = it & 0xFF0
    if type_class == 0x02:  # TYPE_CLASS_NUMBER
        return "12345"
    if type_class == 0x03:  # TYPE_CLASS_PHONE
        return "5551234567"
    if type_class == 0x04:  # TYPE_CLASS_DATETIME
        return "2026-01-01"
    if type_class == 0x01:  # TYPE_CLASS_TEXT
        if type_variation == 0x20:  # EMAIL
            return "test@example.com"
        if type_variation in (0x80, 0xE0):  # PASSWORD / WEB_PASSWORD
            return "TestPass123!"
        if type_variation == 0x10:  # URI
            return "https://example.com"
    return "test123"


def _build_interact_action(element: UIElement) -> Action:
    """Build a single interaction action for an interactable element.

    Priority: INPUT_TEXT for EditText (with generated text), LONG_PRESS
    for long-click-only elements, otherwise CLICK.
    """
    if _is_edit_text(element):
        return Action(
            action_type=ActionType.INPUT_TEXT,
            target_element_id=element.element_id,
            target_bounds=element.bounds,
            target_resource_id=element.resource_id,
            input_text=_generate_edit_value(element),
        )
    at = ActionType.CLICK
    if element.is_long_clickable and not element.is_clickable:
        at = ActionType.LONG_PRESS
    return Action(
        action_type=at,
        target_element_id=element.element_id,
        target_bounds=element.bounds,
        target_resource_id=element.resource_id,
    )


def _build_scroll_down(screen: RawScreen) -> Action | None:
    """First scrollable element's bounds drive the gesture. None if no scrollable."""
    for e in screen.elements:
        if e.is_scrollable and e.bounds and (e.bounds[3] - e.bounds[1]) > 0:
            return Action(
                action_type=ActionType.SCROLL_DOWN,
                target_element_id=e.element_id,
                target_bounds=e.bounds,
                target_resource_id=e.resource_id,
            )
    return None


class ExplorationTrace(BaseModel):
    """One observation record. Compatible with the previous trace schema;
    adds ``intended_source_state_id`` and drops ``failure_reason``."""

    step_number: int
    source_state_id: str = ""
    intended_source_state_id: str = ""
    source_screen_id: str = ""
    action: Action
    target_state_id: str = ""
    target_screen_id: str = ""
    timestamp: str


class ExplorationResult(BaseModel):
    app_package: str
    screens: dict[str, RawScreen] = Field(default_factory=dict)
    traces: list[ExplorationTrace] = Field(default_factory=list)
    total_steps: int = 0
    unique_screens: int = 0
    duration_seconds: float = 0.0
    output_dir: str = ""
    declared_activities: list[str] = Field(default_factory=list)
    covered_activities: list[str] = Field(default_factory=list)
    nav_stats: dict[str, int] = Field(default_factory=dict)


class AppExplorer:
    """Plain BFS explorer. See module docstring for rationale."""

    POST_ACTION_WAIT: float = 1.0
    POST_LAUNCH_WAIT: float = 2.5
    WAIT_FOR_FOREGROUND: float = 10.0
    COLD_START_HOME_WAIT: float = 0.5
    COLD_START_STOP_WAIT: float = 1.0
    MAX_SCROLLS: int = 8

    def __init__(
        self,
        device_serial: str,
        app_package: str,
        config: VigilConfig,
        output_dir: Path | None = None,
        app_prior: AppPrior | None = None,
    ) -> None:
        self._serial = device_serial
        self._app_package = app_package
        self._config = config
        self._app_prior = app_prior
        self._device: u2.Device | None = None
        self._screen_counter = 0
        if output_dir is None:
            self._output_dir = Path(f"data/apps/{app_package.replace('.', '_')}")
        else:
            self._output_dir = output_dir
        for sub in ("screens", "trees", "traces"):
            (self._output_dir / sub).mkdir(parents=True, exist_ok=True)

        # Feature A/B instance state. These are attached to self so
        # _priority_score / _pick_next_action / _perform_one_observation
        # can read and mutate them without threading through arguments.
        # - _nav_paths: canonical replay nav_path from app entry per state_id
        # - _all_actions_per_state: enumerated outgoing actions, keyed by action_key
        # - _explored_per_state: action_keys already consumed (success or ACTION_FAILED)
        # - _blacklisted_per_state: action_keys skipped from future picks
        #   (LEFT_APP anchored on intended source; scroll-no-yield on actual source)
        # - _global_action_type_count: total execution attempts per ActionType
        self._nav_paths: dict[str, list[Action]] = {}
        self._all_actions_per_state: dict[str, dict[str, Action]] = {}
        self._explored_per_state: dict[str, set[str]] = defaultdict(set)
        self._blacklisted_per_state: dict[str, set[str]] = defaultdict(set)
        self._global_action_type_count: dict[ActionType, int] = defaultdict(int)

    # ------------------------------------------------------------------ public

    def explore(self) -> ExplorationResult:
        budget = self._config.app.max_exploration_steps
        logger.info(
            f"Budget: {budget} observations — est. {max(1, round(budget * 25 / 60))} min wall-clock"
        )
        started = time.monotonic()
        self._connect_device()
        if not self._cold_start_app():
            raise RuntimeError(f"Could not launch {self._app_package}")

        entry = self._capture_screen()
        if entry is None:
            raise RuntimeError("Initial capture failed after cold-start")
        entry_sid = entry.get_state_id(self._app_package)
        self._nav_paths[entry_sid] = []
        screens: dict[str, RawScreen] = {entry.screen_id: entry}
        traces: list[ExplorationTrace] = []
        counts = {"cold_start_failures": 0, "left_app": 0, "action_failures": 0}
        enumerate_queue: deque[str] = deque([entry_sid])
        done = 0

        while done < budget:
            # Enumerate one pending state per outer iteration so priority
            # selection always has up-to-date candidates. We enumerate the
            # shallowest unenumerated state (BFS-ordered discovery).
            unenumerated = [
                sid for sid in enumerate_queue if sid not in self._all_actions_per_state
            ]
            if unenumerated:
                unenumerated.sort(key=lambda s: len(self._nav_paths[s]))
                sid = unenumerated[0]
                enumerate_queue.remove(sid)
                actions = self._enumerate_all_clickables(sid, self._nav_paths[sid])
                self._all_actions_per_state[sid] = {action_key(a): a for a in actions}
                logger.info(f"state={sid[:6]} enumerated {len(actions)} actions")

            picked = self._pick_next_action()
            if picked is None:
                if enumerate_queue:
                    continue  # more states awaiting enumeration
                logger.info("Exploration complete: no more unexplored actions")
                break

            state_id, action = picked
            done += 1
            t0 = time.monotonic()
            trace, pre_screen, post_screen = self._perform_one_observation(
                intended_source_state_id=state_id,
                intended_nav_path=self._nav_paths[state_id],
                action=action,
                step=done,
            )
            elapsed = time.monotonic() - t0
            self._log_step(trace, len(self._nav_paths[state_id]), elapsed)
            traces.append(trace)

            key = action_key(action)
            self._explored_per_state[state_id].add(key)

            if trace.target_state_id == SENTINEL_COLD_START_FAILED:
                counts["cold_start_failures"] += 1
            elif trace.target_state_id == SENTINEL_LEFT_APP:
                counts["left_app"] += 1
                self._blacklisted_per_state[state_id].add(key)
            elif trace.target_state_id == SENTINEL_ACTION_FAILED:
                counts["action_failures"] += 1
            else:
                # Scroll-no-yield check: identical anchors pre/post → blacklist
                # at ACTUAL source so we don't try the dead scroll again if the
                # replay lands on a truly stable page.
                if (
                    action.action_type in (ActionType.SCROLL_UP, ActionType.SCROLL_DOWN)
                    and pre_screen is not None
                    and post_screen is not None
                ):
                    _, pre_anchors = pre_screen.get_functional_state_key(self._app_package)
                    _, post_anchors = post_screen.get_functional_state_key(self._app_package)
                    if pre_anchors == post_anchors:
                        self._blacklisted_per_state[trace.source_state_id].add(key)

            if pre_screen is not None:
                screens[pre_screen.screen_id] = pre_screen
            if post_screen is not None:
                screens[post_screen.screen_id] = post_screen

            tgt = trace.target_state_id
            if tgt and tgt not in SENTINEL_TARGETS and tgt not in self._nav_paths:
                self._nav_paths[tgt] = [*self._nav_paths[state_id], action]
                enumerate_queue.append(tgt)

        self._log_summary()
        return self._finalize(
            traces, screens, self._nav_paths, counts, done, time.monotonic() - started
        )

    # ---------------------------------------------------------- scheduling

    def _priority_score(self, state_id: str, action: Action) -> float:
        """Higher is better. Feature A: depth × type weight × log-frequency decay."""
        nav_depth = len(self._nav_paths.get(state_id, []))
        type_weight = ACTION_TYPE_WEIGHT.get(action.action_type, 1.0)
        global_freq = self._global_action_type_count[action.action_type]
        depth_factor = 1.0 / (1.0 + nav_depth)
        freq_factor = 1.0 / (1.0 + math.log1p(global_freq))
        return depth_factor * type_weight * freq_factor

    def _pick_next_action(self) -> tuple[str, Action] | None:
        """Argmax of _priority_score across all enumerated states' outgoing
        actions that are neither explored nor blacklisted. Ties break on
        insertion order (dicts are ordered)."""
        best: tuple[str, Action] | None = None
        best_score = 0.0
        for state_id, actions in self._all_actions_per_state.items():
            explored = self._explored_per_state[state_id]
            blacklisted = self._blacklisted_per_state[state_id]
            for key, action in actions.items():
                if key in explored or key in blacklisted:
                    continue
                score = self._priority_score(state_id, action)
                if score > best_score:
                    best_score = score
                    best = (state_id, action)
        return best

    def _log_summary(self) -> None:
        """Feature A/B reporting: action-type counts + top blacklisted states."""
        logger.info("Summary: action-type execution counts")
        for at, n in sorted(self._global_action_type_count.items(), key=lambda kv: -kv[1]):
            logger.info(f"  {at.value:<14} {n}")
        top = sorted(self._blacklisted_per_state.items(), key=lambda kv: -len(kv[1]))[:5]
        if top:
            logger.info("Summary: top blacklisted states")
            for sid, keys in top:
                if keys:
                    logger.info(f"  {sid[:6]}: {len(keys)} blacklisted")

    # ---------------------------------------------------------- core phases

    def _enumerate_all_clickables(self, state_id: str, nav_path: list[Action]) -> list[Action]:
        """Restart + navigate + scroll to collect all clickables on the page."""
        if not self._cold_start_app():
            logger.warning(f"cold-start failed during enumerate of {state_id[:6]}")
            return []
        for step in nav_path:
            self._execute_action(step)
            time.sleep(self.POST_ACTION_WAIT)

        collected: dict[str, Action] = {}
        last_anchors: frozenset[tuple[str, str]] | None = None

        for _ in range(self.MAX_SCROLLS):
            screen = self._capture_screen()
            if screen is None:
                break
            for e in screen.elements:
                interactable = (
                    e.is_clickable or e.is_long_clickable or e.is_checkable or e.is_editable
                )
                if not interactable:
                    continue
                pkg = (e.package or "").strip()
                if pkg and pkg != self._app_package and pkg != "android":
                    continue
                action = _build_interact_action(e)
                collected.setdefault(action_key(action), action)

            _, anchors = screen.get_functional_state_key(self._app_package)
            if last_anchors is not None and anchors == last_anchors:
                break
            last_anchors = anchors
            scroll = _build_scroll_down(screen)
            if scroll is None:
                break
            self._execute_action(scroll)
            time.sleep(self.POST_ACTION_WAIT)

        return list(collected.values())

    def _perform_one_observation(
        self,
        intended_source_state_id: str,
        intended_nav_path: list[Action],
        action: Action,
        step: int,
    ) -> tuple[ExplorationTrace, RawScreen | None, RawScreen | None]:
        """One cold-start + replay + click cycle. See module docstring for sentinels."""
        ts = _now_iso()

        def _trace(
            *,
            src: str,
            src_screen_id: str = "",
            target: str,
            target_screen_id: str = "",
        ) -> ExplorationTrace:
            return ExplorationTrace(
                step_number=step,
                intended_source_state_id=intended_source_state_id,
                source_state_id=src,
                source_screen_id=src_screen_id,
                action=action,
                target_state_id=target,
                target_screen_id=target_screen_id,
                timestamp=ts,
            )

        if not self._cold_start_app():
            return (
                _trace(src=intended_source_state_id, target=SENTINEL_COLD_START_FAILED),
                None,
                None,
            )
        for nav_step in intended_nav_path:
            self._execute_action(nav_step)
            time.sleep(self.POST_ACTION_WAIT)

        pre = self._capture_screen()
        if pre is None:
            return _trace(src=intended_source_state_id, target=SENTINEL_LEFT_APP), None, None
        actual_src = pre.get_state_id(self._app_package)

        # Feature A: count attempts per action_type (not successes), so the
        # frequency decay can't be gamed by a misbehaving type that always
        # fails.
        self._global_action_type_count[action.action_type] += 1

        if not self._execute_action(action):
            return (
                _trace(src=actual_src, src_screen_id=pre.screen_id, target=SENTINEL_ACTION_FAILED),
                pre,
                None,
            )
        time.sleep(self.POST_ACTION_WAIT)

        if not self._is_within_app():
            return (
                _trace(src=actual_src, src_screen_id=pre.screen_id, target=SENTINEL_LEFT_APP),
                pre,
                None,
            )
        post = self._capture_screen()
        if post is None:
            return (
                _trace(src=actual_src, src_screen_id=pre.screen_id, target=SENTINEL_LEFT_APP),
                pre,
                None,
            )
        return (
            _trace(
                src=actual_src,
                src_screen_id=pre.screen_id,
                target=post.get_state_id(self._app_package),
                target_screen_id=post.screen_id,
            ),
            pre,
            post,
        )

    def _log_step(self, trace: ExplorationTrace, depth: int, elapsed: float) -> None:
        drift = (
            "(as expected)"
            if trace.source_state_id == trace.intended_source_state_id
            else f"(drifted from {trace.intended_source_state_id[:6]})"
        )
        tgt = trace.target_state_id
        tgt_short = tgt if tgt in SENTINEL_TARGETS else tgt[:6]
        logger.info(
            f"step {trace.step_number} | replay({depth}) -> "
            f"src={trace.source_state_id[:6]} {drift} | "
            f"{trace.action.action_type.value} {_short_target(trace.action)} | "
            f"tgt={tgt_short} | {elapsed:.1f}s"
        )

    # ---------------------------------------------------------- device I/O

    def _connect_device(self) -> None:
        logger.info(f"Connecting to device: {self._serial}")
        self._device = u2.connect(self._serial)
        info = self._device.info
        logger.info(
            f"Connected: {info.get('productName', 'unknown')} (SDK {info.get('sdkInt', '?')})"
        )

    def _cold_start_app(self) -> bool:
        """``app_stop`` → home → ``app_start(stop=True)`` → wait for foreground."""
        assert self._device is not None
        dev, pkg = self._device, self._app_package
        steps: tuple[tuple[str, Any, float], ...] = (
            ("app_stop", lambda: dev.app_stop(pkg), self.COLD_START_STOP_WAIT),
            ("home", lambda: dev.press("home"), self.COLD_START_HOME_WAIT),
            ("app_start", lambda: dev.app_start(pkg, stop=True), self.POST_LAUNCH_WAIT),
        )
        for label, op, wait in steps:
            try:
                op()
            except Exception:
                logger.debug(f"cold_start.{label} raised", exc_info=True)
            time.sleep(wait)
        return self._wait_for_app_foreground()

    def _capture_screen(self) -> RawScreen | None:
        assert self._device is not None
        try:
            current = self._device.app_current()
            package_name = current.get("package", "")
            if package_name and package_name != self._app_package:
                return None
            activity_name = current.get("activity", "")
            xml_string = self._device.dump_hierarchy()
        except Exception:
            logger.exception("dump_hierarchy failed")
            return None
        self._screen_counter += 1
        screen_id = f"scr_{self._screen_counter:04d}"
        screenshot_path = self._output_dir / "screens" / f"{screen_id}.png"
        xml_path = self._output_dir / "trees" / f"{screen_id}.xml"
        try:
            self._device.screenshot(str(screenshot_path))
        except Exception:
            logger.debug(f"screenshot failed for {screen_id}", exc_info=True)
        try:
            xml_path.write_text(xml_string, encoding="utf-8")
        except Exception:
            logger.debug(f"xml persist failed for {screen_id}", exc_info=True)
        elements = parse_hierarchy_xml(xml_string, app_package=self._app_package)
        if not elements:
            return None
        return RawScreen(
            screen_id=screen_id,
            activity_name=activity_name or None,
            package_name=package_name or None,
            screenshot_path=str(screenshot_path),
            xml_tree_path=str(xml_path),
            elements=elements,
            timestamp=_now_iso(),
            metadata={"device_serial": self._serial},
        )

    def _execute_action(self, action: Action) -> bool:
        """Dispatch; return False iff the device API raised."""
        assert self._device is not None
        try:
            t = action.action_type
            if t in (ActionType.CLICK, ActionType.LONG_PRESS):
                if not action.target_bounds:
                    return False
                cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                if t is ActionType.LONG_PRESS:
                    self._device.long_click(cx, cy)
                else:
                    self._device.click(cx, cy)
                return True
            if t == ActionType.INPUT_TEXT:
                if action.target_bounds:
                    cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                    cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                    self._device.click(cx, cy)
                    time.sleep(0.3)
                if action.input_text:
                    self._device.send_keys(action.input_text)
                # Dismiss the soft keyboard so the post-action screen dump
                # reflects the real UI (the IME otherwise occludes ~half the
                # screen and corrupts state identity).
                try:
                    if hasattr(self._device, "hide_keyboard"):
                        self._device.hide_keyboard()
                    else:
                        self._device.press("back")
                except Exception:
                    logger.debug("keyboard dismiss failed", exc_info=True)
                time.sleep(0.3)
                return True
            if t in (ActionType.SCROLL_UP, ActionType.SCROLL_DOWN):
                return self._swipe_scroll(t, action)
            if t == ActionType.NAVIGATE_BACK:
                self._device.press("back")
                return True
            if t == ActionType.NAVIGATE_HOME:
                self._device.press("home")
                return True
        except Exception:
            logger.exception(f"execute_action({action.action_type.value}) raised")
            return False
        return False

    def _swipe_scroll(self, t: ActionType, action: Action) -> bool:
        """SCROLL_DOWN: finger high→low (reveal content below). SCROLL_UP: opposite."""
        assert self._device is not None
        if action.target_bounds:
            cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
            top, bot = action.target_bounds[1], action.target_bounds[3]
        else:
            try:
                w, h = self._device.window_size()
            except Exception:
                w, h = 1080, 2400
            cx, top, bot = w // 2, 0, h
        y_low = int(top + (bot - top) * 0.2)
        y_high = int(top + (bot - top) * 0.8)
        if t is ActionType.SCROLL_DOWN:
            self._device.swipe(cx, y_high, cx, y_low, duration=0.3)
        else:
            self._device.swipe(cx, y_low, cx, y_high, duration=0.3)
        return True

    def _wait_for_app_foreground(self, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (timeout or self.WAIT_FOR_FOREGROUND)
        while time.monotonic() < deadline:
            if self._is_within_app():
                return True
            time.sleep(0.5)
        return False

    def _is_within_app(self) -> bool:
        assert self._device is not None
        try:
            return bool(self._device.app_current().get("package", "") == self._app_package)
        except Exception:
            return False

    # ---------------------------------------------------------- finalize

    def _finalize(
        self,
        traces: list[ExplorationTrace],
        screens: dict[str, RawScreen],
        nav_paths: dict[str, list[Action]],
        counts: dict[str, int],
        total_steps: int,
        duration: float,
    ) -> ExplorationResult:
        covered: set[str] = set()
        for t in traces:
            if t.target_state_id in SENTINEL_TARGETS:
                continue
            scr = screens.get(t.source_screen_id)
            if scr is not None and scr.activity_name:
                covered.add(scr.activity_name)
        declared: list[str] = (
            list(getattr(self._app_prior, "activities", [])) if self._app_prior is not None else []
        )
        nav_stats = {
            "observations_total": total_steps,
            "states_discovered": len(nav_paths),
            **counts,
        }
        result = ExplorationResult(
            app_package=self._app_package,
            screens=screens,
            traces=traces,
            total_steps=total_steps,
            unique_screens=len(nav_paths),
            duration_seconds=round(duration, 2),
            output_dir=str(self._output_dir),
            declared_activities=declared,
            covered_activities=sorted(covered),
            nav_stats=nav_stats,
        )
        self._save_result(result)
        logger.info(
            f"Done: {total_steps} observations, {len(nav_paths)} states, "
            f"{result.duration_seconds:.1f}s — saved to {result.output_dir}"
        )
        return result

    def _save_result(self, result: ExplorationResult) -> None:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        trace_path = self._output_dir / "traces" / f"exploration_{timestamp}.json"
        compact: dict[str, Any] = {}
        for sid, s in result.screens.items():
            container, anchors = s.get_functional_state_key(self._app_package)
            compact[sid] = {
                "screen_id": s.screen_id,
                "activity_name": s.activity_name,
                "package_name": s.package_name,
                "screenshot_path": s.screenshot_path,
                "xml_tree_path": s.xml_tree_path,
                "fingerprint": s.get_structural_fingerprint(),
                "state_id": s.get_state_id(self._app_package),
                "state_key_anchor_container": container,
                "state_key_anchors": sorted([list(t) for t in anchors]),
                "total_elements": len(s.elements),
                "interactable_elements": [
                    e.model_dump(mode="json") for e in s.get_interactable_elements()
                ],
                "timestamp": s.timestamp,
                "metadata": {},
            }
        data: dict[str, Any] = {
            "app_package": result.app_package,
            "device_serial": self._serial,
            "exploration_strategy": "bfs_restart_per_action",
            "max_steps": self._config.app.max_exploration_steps,
            "total_steps": result.total_steps,
            "unique_screens": result.unique_screens,
            "duration_seconds": result.duration_seconds,
            "timestamp": _now_iso(),
            "screens": compact,
            "traces": [t.model_dump(mode="json") for t in result.traces],
            "nav_stats": result.nav_stats,
        }
        if result.declared_activities:
            data["activity_coverage"] = {
                "declared": result.declared_activities,
                "covered": result.covered_activities,
                "coverage_ratio": (
                    len(result.covered_activities) / len(result.declared_activities)
                    if result.declared_activities
                    else 0.0
                ),
            }
        trace_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info(f"Exploration trace saved to {trace_path}")
