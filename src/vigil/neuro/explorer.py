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

import hashlib
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
from vigil.core.ui_compressor import compact_ui_tree_text
from vigil.core.ui_parser import parse_hierarchy_xml
from vigil.core.ui_selectors import (
    build_component_selector,
    find_element_by_selector,
    selector_has_stable_identity,
    selector_identity,
)
from vigil.models.action import Action, ActionType
from vigil.models.state import RawScreen, UIElement, _normalize_dynamic
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

# Safety blacklist: substrings (case-insensitive) that indicate dangerous or
# dead-end UI paths the explorer must never click. These lead into system
# setup wizards in other packages (face/fingerprint enrollment, factory
# reset) or destructive confirmations; reachable from Settings but not part
# of com.android.settings, so they trigger LEFT_APP cascades that waste
# budget on retries.
DANGEROUS_TEXT_PATTERNS: frozenset[str] = frozenset(
    {
        "face unlock",
        "fingerprint",
        "factory reset",
        "erase all data",
        "set up face",
        "face & fingerprint",
        "enroll face",
        "screen lock",
        "confirm your pin",
        "confirm your pattern",
        "encrypt",
        "wipe",
        "reset phone",
    }
)


def _is_dangerous_element(text: str | None, content_desc: str | None) -> str | None:
    """Return the matching blacklist pattern if element text/content-desc
    hits it, else None."""
    for raw in (text, content_desc):
        if not raw:
            continue
        lowered = raw.lower()
        for pattern in DANGEROUS_TEXT_PATTERNS:
            if pattern in lowered:
                return pattern
    return None


def action_key(action: Action) -> str:
    """Stable descriptor-based identity.

    When ``target_selector`` has stable identity (rid / text / content-desc /
    nearby-text), key = ``action_type|selector_identity``. Otherwise falls
    back to the legacy descriptor tuple (rid, text, content-desc, class) so
    actions loaded from older traces without selectors still deduplicate
    consistently. Bounds are always excluded — the same logical element at
    different scroll offsets has different bounds in different captures.
    Use :func:`is_action_identifiable` to check whether an action is safe
    to enumerate (at least one selector or descriptor field non-empty).
    """
    if selector_has_stable_identity(action.target_selector):
        return f"{action.action_type.value}|{selector_identity(action.target_selector)}"
    return "|".join(
        [
            action.action_type.value,
            action.target_resource_id or "",
            action.target_text or "",
            action.target_content_desc or "",
            action.target_class_name or "",
        ]
    )


def is_action_identifiable(action: Action) -> bool:
    """True iff at least one descriptor or selector field is populated.

    Global actions (NAVIGATE_BACK, NAVIGATE_HOME) are always identifiable
    by type alone — they have no target and trivially deduplicate on
    action_type + empty descriptor.
    """
    if action.action_type in (ActionType.NAVIGATE_BACK, ActionType.NAVIGATE_HOME):
        return True
    if selector_has_stable_identity(action.target_selector):
        return True
    return bool(
        action.target_resource_id
        or action.target_text
        or action.target_content_desc
        or action.target_class_name
    )


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _short_target(action: Action) -> str:
    if action.target_resource_id:
        rid = action.target_resource_id
        return f"rid={rid.split(':id/', 1)[1] if ':id/' in rid else rid}"
    if action.target_text:
        t = action.target_text
        return f'text="{t[:30]}"'
    if action.target_content_desc:
        return f'cd="{action.target_content_desc[:30]}"'
    if action.target_class_name:
        return f"cls={action.target_class_name.rsplit('.', 1)[-1]}"
    if action.target_bounds:
        return f"bounds={action.target_bounds}"
    return "-"


def _descendant_title_text(element: UIElement, elements: list[UIElement]) -> str | None:
    """Return the normalized text of the first ``android:id/title`` descendant.

    Used to resolve Preference-row clickables whose label lives in a child
    TextView rather than on the clickable itself. Returns None if no such
    descendant exists.
    """
    by_id = {e.element_id: e for e in elements}
    stack = list(element.children)
    seen: set[str] = set()
    depth_guard = 0
    while stack and depth_guard < 200:
        depth_guard += 1
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        child = by_id.get(cid)
        if child is None:
            continue
        if child.resource_id == "android:id/title" and child.text:
            return _normalize_dynamic(child.text)
        stack.extend(child.children)
    return None


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


def _build_interact_action(element: UIElement, elements: list[UIElement]) -> Action:
    """Build an interaction action for an interactable element.

    Descriptor population:
      - ``target_resource_id`` from the element.
      - ``target_text`` from the element's own text, normalized. If the
        element has no rid/text/content_desc, borrow the first
        ``android:id/title`` descendant's text (Preference-row pattern).
      - ``target_content_desc`` from the element.
      - ``target_class_name`` from the element.

    Priority: INPUT_TEXT for EditText, LONG_PRESS for long-click-only
    elements, otherwise CLICK.
    """
    own_text = _normalize_dynamic(element.text) if element.text else ""
    cd = element.content_description or ""
    rid = element.resource_id or ""
    target_text = own_text
    if not rid and not own_text and not cd:
        borrowed = _descendant_title_text(element, elements)
        if borrowed:
            target_text = borrowed

    if _is_edit_text(element):
        return Action(
            action_type=ActionType.INPUT_TEXT,
            target_element_id=element.element_id,
            target_bounds=element.bounds,
            target_resource_id=element.resource_id,
            target_text=target_text or None,
            target_content_desc=cd or None,
            target_class_name=element.class_name or None,
            target_selector=build_component_selector(element, elements),
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
        target_text=target_text or None,
        target_content_desc=cd or None,
        target_class_name=element.class_name or None,
        target_selector=build_component_selector(element, elements),
    )


def _build_scroll_down(screen: RawScreen) -> Action | None:
    """First scrollable element's descriptor drives the gesture. None if no scrollable."""
    for e in screen.elements:
        if e.is_scrollable and e.bounds and (e.bounds[3] - e.bounds[1]) > 0:
            return Action(
                action_type=ActionType.SCROLL_DOWN,
                target_element_id=e.element_id,
                target_bounds=e.bounds,
                target_resource_id=e.resource_id,
                target_class_name=e.class_name or None,
                target_selector=build_component_selector(e, screen.elements),
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


class ScrollObservation(BaseModel):
    """Diagnostic record of one scroll gesture during enumeration or resolution.

    Captures the anchor / structural fingerprint of the screen before and
    after a scroll, plus any action keys newly discovered as a result. The
    ``plateau`` flag is True when the scroll did not change the anchor set
    — the marker the enumeration / resolution loops use to bail out.
    """

    phase: str  # "enumerate" | "resolve"
    source_state_id: str | None = None
    screen_id_before: str = ""
    screen_id_after: str = ""
    action_type: str = "scroll_down"
    container_selector: dict[str, Any] = Field(default_factory=dict)
    before_anchor_hash: str = ""
    after_anchor_hash: str = ""
    before_structural_fingerprint: str = ""
    after_structural_fingerprint: str = ""
    newly_discovered_action_keys: list[str] = Field(default_factory=list)
    plateau: bool = False
    timestamp: str = ""


def _anchor_hash(anchors: frozenset[tuple[str, str]]) -> str:
    """Stable short hash of an anchor set (sorted ``rid=text`` tuples)."""
    if not anchors:
        return ""
    items = sorted(f"{r}={t}" for r, t in anchors)
    return hashlib.sha256("|".join(items).encode()).hexdigest()[:12]


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
    POST_LAUNCH_WAIT: float = 5.0
    WAIT_FOR_FOREGROUND: float = 10.0
    COLD_START_HOME_WAIT: float = 0.5
    COLD_START_STOP_WAIT: float = 1.0
    MAX_SCROLLS: int = 8
    MAX_SCROLL_TO_FIND: int = 10
    MAX_CONSECUTIVE_COLD_START_FAILURES: int = 5

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
        # Change 3 / 4: current-state tracker + emulator-health counter.
        # _current_state_id is None iff the device is in an unknown / non-app
        # state (initial, post-LEFT_APP, post-COLD_START_FAILED). When
        # non-None, the device is *believed* to be at that state_id — every
        # observation verifies this via pre-capture before executing.
        self._current_state_id: str | None = None
        self._consecutive_cold_start_failures: int = 0
        # Track the last observation's intended source so the health-guard
        # counter can distinguish "this one state is cursed" (keep counting)
        # from "different state, transient emulator sluggishness" (reset).
        # A state change resets the counter before any increment, so abort
        # only fires on 5 consecutive fails AT THE SAME source.
        self._last_intended_source_id: str | None = None
        # States permanently skipped because they hit the cold-start-fail
        # threshold. The scheduler filters them out of both locality and
        # global argmax. A second round of ``MAX_CONSECUTIVE_COLD_START_FAILURES``
        # consecutive fails (tracked via ``_state_retirements``) triggers a
        # hard abort — one bad state is recoverable, two is an emulator
        # problem.
        self._retired_states: set[str] = set()
        self._state_retirements: int = 0
        # Diagnostic record of every scroll gesture the explorer fires
        # (enumeration sweep + offscreen resolution). Persisted in the v2
        # trace under "scroll_observations" for downstream analysis.
        self._scroll_observations: list[ScrollObservation] = []

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
        entry_sid = entry.get_hybrid_state_id(self._app_package)
        self._current_state_id = entry_sid
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
                # Enumeration leaves the device scroll-offset at an unknown
                # position (possibly the bottom of a scroll sweep, whose
                # state_id differs from the entry). Invalidate the current
                # tracker so the next observation force-navigates; locality
                # only kicks in during the observe loop proper.
                self._current_state_id = None
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
                # If the cold-start-failed streak was accumulated against a
                # different source state, restart counting here: the current
                # source hasn't actually failed repeatedly — the prior one
                # did. This keeps transient emulator-wide sluggishness from
                # cascading across unrelated states and tripping retire.
                if state_id != self._last_intended_source_id:
                    self._consecutive_cold_start_failures = 0
                self._consecutive_cold_start_failures += 1
                self._last_intended_source_id = state_id
                if (
                    self._consecutive_cold_start_failures
                    >= self.MAX_CONSECUTIVE_COLD_START_FAILURES
                ):
                    # First cascade → retire the state and continue. Second
                    # cascade → hard abort (something deeper is wrong with
                    # the emulator, not just one cursed source).
                    self._state_retirements += 1
                    if self._state_retirements >= 2:
                        logger.error(
                            f"Aborting exploration: second cascade of "
                            f"{self._consecutive_cold_start_failures} "
                            f"consecutive COLD_START_FAILED (this one at "
                            f"source {state_id[:6]}). Device is "
                            f"persistently unresponsive. Unused budget: "
                            f"{budget - done} observations."
                        )
                        counts["aborted_on_cold_start_failures"] = 1
                        break
                    logger.warning(
                        f"Retiring source state {state_id[:6]} after "
                        f"{self._consecutive_cold_start_failures} consecutive "
                        "COLD_START_FAILED observations. Continuing with "
                        "remaining frontier states."
                    )
                    self._retired_states.add(state_id)
                    self._consecutive_cold_start_failures = 0
                    self._last_intended_source_id = None
                    # If no non-retired frontier states have unexplored
                    # actions left, the explore loop will exit naturally
                    # on the next _pick_next_action → None.
            elif trace.target_state_id == SENTINEL_LEFT_APP:
                counts["left_app"] += 1
                self._blacklisted_per_state[state_id].add(key)
                self._consecutive_cold_start_failures = 0
                self._last_intended_source_id = state_id
            elif trace.target_state_id == SENTINEL_ACTION_FAILED:
                counts["action_failures"] += 1
                self._consecutive_cold_start_failures = 0
                self._last_intended_source_id = state_id
            else:
                # Any successful observation resets the consecutive counter.
                # Keep this explicit so the invariant is obvious on inspection.
                self._consecutive_cold_start_failures = 0
                self._last_intended_source_id = state_id
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
        """Higher is better. Depth × type weight × log-frequency decay."""
        nav_depth = len(self._nav_paths.get(state_id, []))
        type_weight = ACTION_TYPE_WEIGHT.get(action.action_type, 1.0)
        global_freq = self._global_action_type_count[action.action_type]
        depth_factor = 1.0 / (1.0 + nav_depth)
        freq_factor = 1.0 / (1.0 + math.log1p(global_freq))
        return depth_factor * type_weight * freq_factor

    def _pick_next_action(self) -> tuple[str, Action] | None:
        """Locality preference + global argmax fallback.

        If ``self._current_state_id`` has an unexplored non-blacklisted
        action, pick the argmax *within* that state — skips a cold-start
        + nav-path replay next turn. Otherwise fall through to the global
        argmax across all enumerated states.
        """
        sid = self._current_state_id
        if (
            sid is not None
            and sid in self._all_actions_per_state
            and sid not in self._retired_states
        ):
            local = self._pick_best_in_state(sid)
            if local is not None:
                return (sid, local)
        return self._global_argmax()

    def _pick_best_in_state(self, state_id: str) -> Action | None:
        """Argmax of _priority_score within a single state's unexplored,
        non-blacklisted outgoing actions. Returns None if empty or retired."""
        if state_id in self._retired_states:
            return None
        actions = self._all_actions_per_state.get(state_id, {})
        if not actions:
            return None
        explored = self._explored_per_state[state_id]
        blacklisted = self._blacklisted_per_state[state_id]
        best: Action | None = None
        best_score = 0.0
        for key, action in actions.items():
            if key in explored or key in blacklisted:
                continue
            score = self._priority_score(state_id, action)
            if score > best_score:
                best_score = score
                best = action
        return best

    def _global_argmax(self) -> tuple[str, Action] | None:
        """Argmax across all enumerated states' unexplored non-blacklisted
        outgoing actions. Retired states are skipped entirely."""
        best: tuple[str, Action] | None = None
        best_score = 0.0
        for state_id, actions in self._all_actions_per_state.items():
            if state_id in self._retired_states:
                continue
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
        if self._retired_states:
            logger.info(
                f"Summary: {len(self._retired_states)} state(s) retired after "
                f"cold-start cascades: "
                f"{', '.join(sid[:6] for sid in self._retired_states)}"
            )

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
        prev_screen: RawScreen | None = None
        prev_anchor_h: str = ""
        prev_struct_fp: str = ""
        prev_keys: set[str] = set()
        prev_container_selector: dict[str, Any] = {}

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
                hit = _is_dangerous_element(e.text, e.content_description)
                if hit is not None:
                    logger.debug(
                        f"Skipping dangerous element {e.element_id} (matched pattern {hit!r})"
                    )
                    continue
                action = _build_interact_action(e, screen.elements)
                if not is_action_identifiable(action):
                    logger.debug(
                        f"Dropping unidentifiable action on element "
                        f"{e.element_id} (no rid/text/content_desc/class_name)"
                    )
                    continue
                collected.setdefault(action_key(action), action)

            _, anchors = screen.get_functional_state_key(self._app_package)
            cur_anchor_h = _anchor_hash(anchors)
            cur_struct_fp = screen.get_structural_fingerprint()

            # If this is a post-scroll capture, record the observation.
            if prev_screen is not None:
                new_keys = sorted(set(collected.keys()) - prev_keys)
                plateau = (last_anchors is not None) and (anchors == last_anchors)
                self._scroll_observations.append(
                    ScrollObservation(
                        phase="enumerate",
                        source_state_id=state_id,
                        screen_id_before=prev_screen.screen_id,
                        screen_id_after=screen.screen_id,
                        action_type=ActionType.SCROLL_DOWN.value,
                        container_selector=prev_container_selector,
                        before_anchor_hash=prev_anchor_h,
                        after_anchor_hash=cur_anchor_h,
                        before_structural_fingerprint=prev_struct_fp,
                        after_structural_fingerprint=cur_struct_fp,
                        newly_discovered_action_keys=new_keys,
                        plateau=plateau,
                        timestamp=_now_iso(),
                    )
                )

            if last_anchors is not None and anchors == last_anchors:
                break
            last_anchors = anchors
            scroll = _build_scroll_down(screen)
            if scroll is None:
                break
            # Bypass _execute_action's descriptor-resolve path during
            # enumeration — we already have a live scroll container, no
            # need to re-capture and re-match inside execute_scroll.
            prev_screen = screen
            prev_anchor_h = cur_anchor_h
            prev_struct_fp = cur_struct_fp
            prev_keys = set(collected.keys())
            prev_container_selector = dict(scroll.target_selector or {})
            self._swipe_scroll(ActionType.SCROLL_DOWN, scroll)
            time.sleep(self.POST_ACTION_WAIT)

        return list(collected.values())

    def _perform_one_observation(
        self,
        intended_source_state_id: str,
        intended_nav_path: list[Action],
        action: Action,
        step: int,
    ) -> tuple[ExplorationTrace, RawScreen | None, RawScreen | None]:
        """One observation. Skips cold-start + replay when the device is
        already at the intended source state; otherwise performs the full
        cycle. See module docstring for sentinels and the ``_current_state_id``
        invariant.
        """
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

        needs_nav = self._current_state_id != intended_source_state_id
        if needs_nav:
            if not self._cold_start_app():
                self._current_state_id = None
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
            self._current_state_id = None
            return _trace(src=intended_source_state_id, target=SENTINEL_LEFT_APP), None, None
        actual_src = pre.get_hybrid_state_id(self._app_package)

        # Locality drift fallback: we thought we were at intended but the
        # live capture says otherwise (e.g., a system dialog surfaced).
        # Re-run this observation via the full cold-start path for
        # correctness. Log as INFO, not failure.
        if not needs_nav and actual_src != intended_source_state_id:
            cur_tag = self._current_state_id[:6] if self._current_state_id else "-"
            logger.info(
                f"locality drift: current={cur_tag} actual={actual_src[:6]} "
                f"intended={intended_source_state_id[:6]} — falling back to cold-start"
            )
            self._current_state_id = None
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
                return (
                    _trace(src=intended_source_state_id, target=SENTINEL_LEFT_APP),
                    None,
                    None,
                )
            actual_src = pre.get_hybrid_state_id(self._app_package)

        # Feature A: count attempts per action_type (not successes), so the
        # frequency decay can't be gamed by a misbehaving type that always
        # fails.
        self._global_action_type_count[action.action_type] += 1

        if not self._execute_action(action):
            # For CLICK/LONG_PRESS/INPUT_TEXT, _resolve_action_target may
            # have scrolled up to MAX_SCROLL_TO_FIND times while searching.
            # Even if we started at ``actual_src`` pre-capture, the device
            # is now at an unknown scroll offset — don't pin
            # _current_state_id to the stale pre-capture id, or the next
            # observation will take the locality branch, see drift, and
            # cold-start unnecessarily. Non-scrolling fail cases (e.g.,
            # NAVIGATE_BACK raised) land on the same None and force a
            # clean cold-start next iteration, which is the safe default.
            self._current_state_id = None
            return (
                _trace(src=actual_src, src_screen_id=pre.screen_id, target=SENTINEL_ACTION_FAILED),
                pre,
                None,
            )
        time.sleep(self.POST_ACTION_WAIT)

        if not self._is_within_app():
            self._current_state_id = None
            return (
                _trace(src=actual_src, src_screen_id=pre.screen_id, target=SENTINEL_LEFT_APP),
                pre,
                None,
            )
        post = self._capture_screen()
        if post is None:
            self._current_state_id = None
            return (
                _trace(src=actual_src, src_screen_id=pre.screen_id, target=SENTINEL_LEFT_APP),
                pre,
                None,
            )
        self._current_state_id = post.get_hybrid_state_id(self._app_package)
        return (
            _trace(
                src=actual_src,
                src_screen_id=pre.screen_id,
                target=self._current_state_id,
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
        """``app_stop`` → home → ``app_start(stop=True)`` → wait for foreground.

        If the initial attempt doesn't land the target app in the foreground
        within ``WAIT_FOR_FOREGROUND`` seconds, run one recovery pass: if a
        foreign app is sitting in the foreground (emulator sluggishness
        from Files / Gallery / etc. launched in a prior step), force-stop
        it, sleep 3 s, then retry the stop→home→start sequence once more.
        This absorbs transient emulator contention that otherwise triggers
        premature COLD_START_FAILED cascades.
        """
        assert self._device is not None
        if self._cold_start_attempt():
            return True

        foreground_pkg = ""
        try:
            current = self._device.app_current()
            foreground_pkg = (current.get("package", "") or "").strip()
        except Exception:
            logger.debug("cold_start recovery: app_current() raised", exc_info=True)

        if foreground_pkg and foreground_pkg != self._app_package:
            logger.warning(
                f"cold_start recovery: foreground is {foreground_pkg!r}, "
                "force-stopping and retrying"
            )
            try:
                self._device.app_stop(foreground_pkg)
            except Exception:
                logger.debug(f"force-stop {foreground_pkg} raised", exc_info=True)
        else:
            logger.warning(
                "cold_start recovery: no foreign foreground detected, "
                "waiting 3s for emulator to settle and retrying"
            )
        time.sleep(3.0)
        return self._cold_start_attempt()

    def _cold_start_attempt(self) -> bool:
        """One ``app_stop`` → home → ``app_start(stop=True)`` cycle."""
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

    # ------------------------- prior-guided intent launch ------------------

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
        scr = RawScreen(
            screen_id=screen_id,
            activity_name=activity_name or None,
            package_name=package_name or None,
            screenshot_path=str(screenshot_path),
            xml_tree_path=str(xml_path),
            elements=elements,
            timestamp=_now_iso(),
            metadata={"device_serial": self._serial},
        )
        # Stash the extracted toolbar title in metadata so FSM builder and
        # other downstream passes can read it without re-walking the tree.
        scr.metadata["page_title"] = scr.extract_page_title()
        return scr

    def _execute_action(self, action: Action) -> bool:
        """Dispatch. Coordinates are resolved live — stored bounds are a hint.

        For CLICK / LONG_PRESS / INPUT_TEXT: resolve the target by descriptor
        on the current screen (scrolling to find if needed). Click at the
        *live* bounds, not whatever was stored on the Action at enumeration.
        For SCROLL: resolve the scroll container by descriptor. For
        NAVIGATE_BACK / NAVIGATE_HOME: just fire the key.

        Returns False when the resolver cannot locate the target or when
        the device API raises.
        """
        assert self._device is not None
        try:
            t = action.action_type
            if t == ActionType.NAVIGATE_BACK:
                self._device.press("back")
                return True
            if t == ActionType.NAVIGATE_HOME:
                self._device.press("home")
                return True
            if t in (ActionType.SCROLL_UP, ActionType.SCROLL_DOWN):
                return self._execute_scroll(action)
            if t in (ActionType.CLICK, ActionType.LONG_PRESS, ActionType.INPUT_TEXT):
                element = self._resolve_action_target(action, scroll_to_find=True)
                if element is None:
                    logger.debug(f"resolve failed for {t.value} {_short_target(action)}")
                    return False
                if not element.bounds:
                    return False
                cx = (element.bounds[0] + element.bounds[2]) // 2
                cy = (element.bounds[1] + element.bounds[3]) // 2
                if t == ActionType.CLICK:
                    self._device.click(cx, cy)
                    return True
                if t == ActionType.LONG_PRESS:
                    self._device.long_click(cx, cy)
                    return True
                # INPUT_TEXT
                self._device.click(cx, cy)
                time.sleep(0.3)
                if action.input_text:
                    self._device.send_keys(action.input_text)
                try:
                    if hasattr(self._device, "hide_keyboard"):
                        self._device.hide_keyboard()
                    else:
                        self._device.press("back")
                except Exception:
                    logger.debug("keyboard dismiss failed", exc_info=True)
                time.sleep(0.3)
                return True
        except Exception:
            logger.exception(f"execute_action({action.action_type.value}) raised")
            return False
        return False

    def _match_descriptor(self, screen: RawScreen, action: Action) -> UIElement | None:
        """Find the element whose descriptor matches ``action``.

        Resolution order:
          1. If the action carries a ``target_selector`` with stable identity,
             try :func:`vigil.core.ui_selectors.find_element_by_selector`.
          2. Fall back to descriptor matching on (rid, text, content-desc,
             class) so traces saved before selectors were introduced still
             replay correctly.

        Descriptor matching rules (all specified fields must be compatible):
          1. If ``rid`` is specified in the action, the element's rid must match.
          2. If ``class_name`` is specified, must match.
          3. If ``text`` is specified:
               - match if element's own normalized text equals it, OR
               - (Preference-row fallback) the element has a direct child
                 with ``resource_id == "android:id/title"`` whose normalized
                 text matches.
          4. If ``content_desc`` is specified, the element's content_desc
             must match.
          5. If only ``rid`` is specified (no text / cd), any rid match wins.
          6. If none of ``rid`` / ``text`` / ``cd`` / ``cls`` is specified,
             no match (caller should have rejected the action upstream).

        Returns the first matching element, or None.
        """
        if selector_has_stable_identity(action.target_selector):
            found = find_element_by_selector(action.target_selector, screen.elements)
            if found is not None:
                return found

        rid = action.target_resource_id or ""
        text = _normalize_dynamic(action.target_text or "") if action.target_text else ""
        cd = action.target_content_desc or ""
        cls = action.target_class_name or ""
        if not (rid or text or cd or cls):
            return None

        by_id = {e.element_id: e for e in screen.elements}

        def title_in_subtree(e: UIElement) -> bool:
            """Deep DFS for an ``android:id/title`` descendant whose normalized
            text equals the target. Mirrors ``_descendant_title_text`` used at
            enumeration time — direct-child-only matching would miss
            Preference-row patterns where the title TextView lives two or
            three layouts down from the clickable parent.
            """
            if not text:
                return False
            stack = list(e.children)
            seen: set[str] = set()
            guard = 0
            while stack and guard < 200:
                guard += 1
                cid = stack.pop()
                if cid in seen:
                    continue
                seen.add(cid)
                child = by_id.get(cid)
                if child is None:
                    continue
                if child.resource_id == "android:id/title" and (
                    _normalize_dynamic(child.text or "") == text
                ):
                    return True
                stack.extend(child.children)
            return False

        for e in screen.elements:
            e_rid = e.resource_id or ""
            e_text = _normalize_dynamic(e.text or "") if e.text else ""
            e_cd = e.content_description or ""
            e_cls = e.class_name or ""

            if rid and rid != e_rid:
                continue
            if cls and cls != e_cls:
                continue
            if cd and e_cd and cd != e_cd:
                continue
            if text:
                if e_text == text:
                    return e
                if not e_text and title_in_subtree(e):
                    return e
                continue
            if cd:
                if e_cd == cd:
                    return e
                continue
            if rid:
                # rid+class (or rid alone) with no text/cd specified.
                return e
            if cls and e_cls == cls and not text and not cd:
                return e
        return None

    def _resolve_action_target(self, action: Action, *, scroll_to_find: bool) -> UIElement | None:
        """Capture the current screen, find the descriptor target, scroll
        to reveal it if necessary. Returns the element with live bounds,
        or None."""
        screen = self._capture_screen()
        if screen is None:
            return None
        found = self._match_descriptor(screen, action)
        if found is not None:
            return found
        if not scroll_to_find:
            return None

        last_anchors: frozenset[tuple[str, str]] | None = None
        prev_screen: RawScreen | None = None
        prev_anchor_h: str = ""
        prev_struct_fp: str = ""
        prev_container_selector: dict[str, Any] = {}
        for _ in range(self.MAX_SCROLL_TO_FIND):
            scroll_action = _build_scroll_down(screen)
            if scroll_action is None:
                return None
            _, anchors = screen.get_functional_state_key(self._app_package)
            cur_anchor_h = _anchor_hash(anchors)
            cur_struct_fp = screen.get_structural_fingerprint()
            if last_anchors is not None and anchors == last_anchors:
                if prev_screen is not None:
                    self._scroll_observations.append(
                        ScrollObservation(
                            phase="resolve",
                            source_state_id=None,
                            screen_id_before=prev_screen.screen_id,
                            screen_id_after=screen.screen_id,
                            action_type=ActionType.SCROLL_DOWN.value,
                            container_selector=prev_container_selector,
                            before_anchor_hash=prev_anchor_h,
                            after_anchor_hash=cur_anchor_h,
                            before_structural_fingerprint=prev_struct_fp,
                            after_structural_fingerprint=cur_struct_fp,
                            newly_discovered_action_keys=[],
                            plateau=True,
                            timestamp=_now_iso(),
                        )
                    )
                return None
            last_anchors = anchors
            prev_screen = screen
            prev_anchor_h = cur_anchor_h
            prev_struct_fp = cur_struct_fp
            prev_container_selector = dict(scroll_action.target_selector or {})
            if not self._swipe_scroll(ActionType.SCROLL_DOWN, scroll_action):
                return None
            time.sleep(self.POST_ACTION_WAIT)
            screen = self._capture_screen()
            if screen is None:
                return None
            _, post_anchors = screen.get_functional_state_key(self._app_package)
            post_anchor_h = _anchor_hash(post_anchors)
            post_struct_fp = screen.get_structural_fingerprint()
            self._scroll_observations.append(
                ScrollObservation(
                    phase="resolve",
                    source_state_id=None,
                    screen_id_before=prev_screen.screen_id,
                    screen_id_after=screen.screen_id,
                    action_type=ActionType.SCROLL_DOWN.value,
                    container_selector=prev_container_selector,
                    before_anchor_hash=prev_anchor_h,
                    after_anchor_hash=post_anchor_h,
                    before_structural_fingerprint=prev_struct_fp,
                    after_structural_fingerprint=post_struct_fp,
                    newly_discovered_action_keys=[],
                    plateau=(post_anchors == last_anchors),
                    timestamp=_now_iso(),
                )
            )
            found = self._match_descriptor(screen, action)
            if found is not None:
                return found
        return None

    def _execute_scroll(self, action: Action) -> bool:
        """Descriptor-based scroll: find the matching scrollable on the
        current screen, swipe within its live bounds. Never falls back to
        whole-screen bounds — that would fire on unrelated containers."""
        screen = self._capture_screen()
        if screen is None:
            return False
        container = self._match_descriptor(screen, action)
        if container is None or not container.bounds:
            return False
        live = Action(
            action_type=action.action_type,
            target_bounds=container.bounds,
        )
        return self._swipe_scroll(action.action_type, live)

    def _swipe_scroll(self, t: ActionType, action: Action) -> bool:
        """SCROLL_DOWN: finger high→low (reveal content below). SCROLL_UP: opposite.

        ``action.target_bounds`` must be populated (caller's contract;
        descriptor-resolver provides live bounds). Falls back to full
        window only when called from enumeration scroll-sweep (which
        passes the container's enumeration-time bounds).
        """
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
        declared: list[str] = []
        if self._app_prior is not None:
            for a in getattr(self._app_prior, "activities", []):
                # AppPrior.activities is list[ActivityInfo]; extract names.
                name = getattr(a, "name", None) if not isinstance(a, str) else a
                if name:
                    declared.append(name)
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
            interactable = s.get_interactable_elements()
            structural_fp = s.get_structural_fingerprint()
            hybrid_state_id = s.get_hybrid_state_id(self._app_package)
            text_state_id = s.get_state_id(self._app_package)
            page_title = s.extract_page_title()
            try:
                compact_tree_text = compact_ui_tree_text(s.elements)
            except Exception:
                logger.debug(f"compact_ui_tree_text failed for {sid}", exc_info=True)
                compact_tree_text = ""
            compact[sid] = {
                "screen_id": s.screen_id,
                "activity_name": s.activity_name,
                "package_name": s.package_name,
                "screenshot_path": s.screenshot_path,
                "xml_tree_path": s.xml_tree_path,
                # ``state_id`` is the canonical identity for downstream
                # consumers (fsm_builder dedups on this). Uses the hybrid
                # key — structural fingerprint + activity + page title.
                # ``structural_fingerprint``, ``functional_fingerprint``,
                # ``text_state_id`` are preserved as secondary labels for
                # post-processing / naming / debugging. ``functional_fingerprint``
                # is the hybrid state id (alias of ``state_id``) so newer
                # consumers can read it directly without re-computing.
                "fingerprint": structural_fp,
                "structural_fingerprint": structural_fp,
                "functional_fingerprint": hybrid_state_id,
                "state_id": hybrid_state_id,
                "text_state_id": text_state_id,
                "page_title": page_title,
                "state_key_anchor_container": container,
                "state_key_anchors": sorted([list(t) for t in anchors]),
                "total_elements": len(s.elements),
                # Canonical: full UIElement graph for downstream replay /
                # fingerprinting / DSL evaluation. Lossy ``compact_tree_text``
                # is for LLM prompts only — do NOT feed it to fingerprint or
                # replay code paths.
                "elements": [e.model_dump(mode="json") for e in s.elements],
                "interactable_elements": [e.model_dump(mode="json") for e in interactable],
                "compact_tree_text": compact_tree_text,
                "screen_quality": {
                    "total_elements": len(s.elements),
                    "interactable_count": len(interactable),
                    "has_screenshot": bool(s.screenshot_path and Path(s.screenshot_path).exists()),
                    "has_xml": bool(s.xml_tree_path and Path(s.xml_tree_path).exists()),
                },
                "timestamp": s.timestamp,
                "metadata": {"page_title": page_title},
            }
        data: dict[str, Any] = {
            "schema_version": "exploration_v2",
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
            "scroll_observations": [o.model_dump(mode="json") for o in self._scroll_observations],
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
