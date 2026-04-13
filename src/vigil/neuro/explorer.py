"""Stage 1: UI Exploration via uiautomator2.

BFS/DFS traversal of Android app screens. At each screen, enumerates interactable
elements, executes each action, and records the resulting screen (accessibility tree
XML + screenshot PNG + element list).

Smart stopping: when a scrollable container has homogeneous children (>=60% share
the same element skeleton AND >=3 such children), only 2 representative item clicks
are explored instead of all N — reducing per-container cost from O(N) to O(1).

Locality-aware scheduling: 5-tier frontier priority minimizes costly replay navigation
by preferring actions from the current screen, forward-adjacent, back-reachable,
or sibling screens before falling back to full app restart + replay.

Action dedup: tracks executed actions by (activity_name, action_type, bounds) signature
to prevent re-exploring the same action when fingerprint drift causes a revisited
screen to appear "new".
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
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
from vigil.models.state import RawScreen, UIElement
from vigil.neuro.app_prior import AppPrior

STRUCTURAL_GROUP_MIN_SIZE = 4
STRUCTURAL_GROUP_REPRESENTATIVES = 2

_ACTION_WAIT: dict[ActionType, float] = {
    ActionType.NAVIGATE_BACK: 0.3,
    ActionType.NAVIGATE_HOME: 0.3,
    ActionType.SCROLL_UP: 0.3,
    ActionType.SCROLL_DOWN: 0.3,
    ActionType.CLICK: 0.6,
    ActionType.LONG_PRESS: 0.6,
    ActionType.INPUT_TEXT: 0.5,
}


@dataclass
class StructuralGroupingContext:
    """Tracks structural equivalence groups and behavioral verification.

    Lifecycle: PENDING → CONFIRMED_EQUIVALENT or CONFIRMED_HETEROGENEOUS.
    """

    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    confirmed_equivalent: set[str] = field(default_factory=set)
    confirmed_heterogeneous: set[str] = field(default_factory=set)
    deferred_actions: dict[str, list[tuple[str, Action]]] = field(default_factory=dict)


def apply_structural_grouping(
    screen: RawScreen,
    actions: list[Action],
    ctx: StructuralGroupingContext,
    screen_id: str,
) -> list[Action]:
    """Layer 1: Structural Equivalence Prediction.

    Groups click actions by (parent_id, grouping_skeleton). Large groups
    keep only 2 representatives; rest are deferred for behavioral verification.
    Subsumes smart stopping + toggle filtering under one principle.
    """
    elements_by_id = {e.element_id: e for e in screen.elements}

    groups: dict[str, list[tuple[Action, UIElement]]] = defaultdict(list)
    ungrouped: list[Action] = []

    for action in actions:
        if action.action_type != ActionType.CLICK or not action.target_element_id:
            ungrouped.append(action)
            continue

        element = elements_by_id.get(action.target_element_id)
        if element is None or element.parent_id is None:
            ungrouped.append(action)
            continue

        skeleton = element.get_grouping_skeleton()
        group_key = f"{element.parent_id}|{skeleton}"

        if group_key in ctx.confirmed_equivalent:
            continue

        groups[group_key].append((action, element))

    kept: list[Action] = list(ungrouped)

    for group_key, members in groups.items():
        if len(members) < STRUCTURAL_GROUP_MIN_SIZE:
            kept.extend(a for a, _ in members)
            continue

        if group_key in ctx.confirmed_heterogeneous:
            kept.extend(a for a, _ in members)
            continue

        members.sort(key=lambda pair: pair[1].element_id)
        reps = [members[0], members[-1]]
        rep_ids = {el.element_id for _, el in reps}

        deferred: list[tuple[str, Action]] = []
        for action, el in members:
            if el.element_id in rep_ids:
                kept.append(action)
            else:
                deferred.append((screen_id, action))

        ctx.pending[group_key] = {
            "source_screen_id": screen_id,
            "representative_element_ids": [el.element_id for _, el in reps],
            "total_members": len(members),
            "detail_fingerprints": [],
        }
        ctx.deferred_actions[group_key] = deferred

    return kept


def record_behavioral_result(
    ctx: StructuralGroupingContext,
    source_screen_id: str,
    target_element_id: str,
    target_fingerprint: str,
) -> list[tuple[str, Action]]:
    """Layer 2: Behavioral Equivalence Verification.

    After a representative is executed, record its target fingerprint.
    When all representatives are done: same fp → EQUIVALENT (skip rest),
    different fps → HETEROGENEOUS (replenish rest).
    """
    replenish: list[tuple[str, Action]] = []

    for group_key, info in list(ctx.pending.items()):
        if source_screen_id != info["source_screen_id"]:
            continue
        if target_element_id not in info["representative_element_ids"]:
            continue

        info["detail_fingerprints"].append(target_fingerprint)

        if len(info["detail_fingerprints"]) >= STRUCTURAL_GROUP_REPRESENTATIVES:
            unique_fps = set(info["detail_fingerprints"])

            if len(unique_fps) == 1:
                ctx.confirmed_equivalent.add(group_key)
                logger.info(f"Group {group_key[:30]} EQUIVALENT ({info['total_members']} members)")
            else:
                ctx.confirmed_heterogeneous.add(group_key)
                replenish.extend(ctx.deferred_actions.get(group_key, []))
                logger.info(
                    f"Group {group_key[:30]} HETEROGENEOUS — replenishing {len(replenish)} actions"
                )

            del ctx.pending[group_key]
            ctx.deferred_actions.pop(group_key, None)

        return replenish

    return replenish


def _match_activity(activity_name: str, declared: set[str]) -> str | None:
    """Match an observed activity_name against declared Activity names."""
    if activity_name in declared:
        return activity_name
    for d in declared:
        if d.endswith(activity_name) or activity_name.endswith(d):
            return d
        short = d.rsplit(".", 1)[-1]
        obs_short = activity_name.rsplit(".", 1)[-1]
        if short == obs_short:
            return d
    return None


def _action_signature(action: Action) -> str:
    """Compute a stable signature for dedup.

    Does NOT include activity_name — on MIUI the same page can report
    different activity names across captures, breaking dedup.
    """
    if action.target_resource_id:
        return f"{action.action_type.value}|rid:{action.target_resource_id}"

    if action.target_bounds:
        qb = [round(b / 50) * 50 for b in action.target_bounds]
        bounds_str = ",".join(str(b) for b in qb)
        return f"{action.action_type.value}|qb:{bounds_str}"

    return f"{action.action_type.value}|global"


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
    declared_activities: list[str] = Field(default_factory=list)
    covered_activities: list[str] = Field(default_factory=list)
    nav_stats: dict[str, int] = Field(default_factory=dict)


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
        app_prior: Optional AppPrior for Activity coverage guidance.
    """

    MAX_BACK_PRESSES = 10
    MAX_SCROLLS_PER_ELEMENT = 8
    STABILITY_WAIT = 0.8
    DEVICE_RETRIES = 3

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
        self._device: u2.Device | None = None
        self._screen_counter = 0
        self._app_prior = app_prior

        if output_dir is None:
            app_name = app_package.replace(".", "_")
            self._output_dir = Path(f"data/apps/{app_name}")
        else:
            self._output_dir = output_dir

        (self._output_dir / "screens").mkdir(parents=True, exist_ok=True)
        (self._output_dir / "trees").mkdir(parents=True, exist_ok=True)
        (self._output_dir / "traces").mkdir(parents=True, exist_ok=True)

    def explore(self) -> ExplorationResult:
        """Run the exploration and return structured results."""
        start_time = time.monotonic()
        self._connect_device()
        assert self._device is not None

        logger.info(f"Starting app: {self._app_package}")
        self._device.app_start(self._app_package, stop=True)
        if not self._wait_for_app_foreground():
            logger.error("App did not come to foreground")
            return ExplorationResult(app_package=self._app_package)

        initial_screen = self._capture_screen()
        if initial_screen is None:
            logger.error("Failed to capture initial screen")
            return ExplorationResult(app_package=self._app_package)

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

        # Activity coverage tracking
        declared_activities: set[str] = set()
        if self._app_prior:
            declared_activities = {a.name for a in self._app_prior.activities}
            logger.info(
                f"Activity prior loaded: {len(declared_activities)} declared Activities, "
                f"entry={self._app_prior.entry_activity}"
            )
        covered_activities: set[str] = set()
        if initial_screen.activity_name and declared_activities:
            matched = _match_activity(initial_screen.activity_name, declared_activities)
            if matched:
                covered_activities.add(matched)

        fp_to_sid: dict[str, str] = {initial_fp: initial_screen.screen_id}
        nav_paths: dict[str, list[tuple[Action, str]]] = {initial_screen.screen_id: []}
        nav_failures: dict[str, int] = {}
        max_nav_failures = 3

        # Action dedup: track executed actions by stable signature
        executed_actions: set[str] = set()

        # Track action signatures currently in the frontier to avoid duplicates
        frontier_sigs: set[str] = set()

        # Actions that caused the app to exit — permanently skip
        leave_app_blacklist: set[str] = set()

        # Adjacency graph for local navigation
        forward_edges: dict[str, list[tuple[Action, str]]] = {}
        back_edges: dict[str, str] = {}

        # Navigation statistics
        nav_stats: dict[str, int] = {
            "p1_current": 0,
            "p2_forward": 0,
            "p3_back": 0,
            "p4_sibling": 0,
            "p5_replay": 0,
            "local_nav_ok": 0,
            "local_nav_fail": 0,
            "replay_ok": 0,
            "replay_fail": 0,
            "actions_deduped": 0,
            "blacklisted_skips": 0,
            "deferred_actions": 0,
            "replenished_actions": 0,
            "depth_skips": 0,
            "fuzzy_matches": 0,
            "groups_formed": 0,
            "groups_equivalent": 0,
            "groups_heterogeneous": 0,
            "back_chain_ok": 0,
            "back_chain_fail": 0,
            "keyboard_checks": 0,
        }

        skip_actions: set[ActionType] = {
            ActionType.INPUT_TEXT,
            ActionType.NAVIGATE_HOME,
            ActionType.SCROLL_UP,
            ActionType.LONG_PRESS,
        }

        # Screen depth tracking
        screen_depth: dict[str, int] = {initial_screen.screen_id: 0}
        max_depth = 4

        # Deferred frontier for nav-failed screens
        deferred_frontier: deque[tuple[str, Action]] = deque()

        # Build initial frontier
        frontier: deque[tuple[str, Action]] = deque()
        grouping_ctx = StructuralGroupingContext()
        initial_actions = enumerate_actions(initial_screen, exclude=skip_actions)
        initial_actions = [a for a in initial_actions if a.action_type != ActionType.NAVIGATE_BACK]
        initial_actions = apply_structural_grouping(
            initial_screen, initial_actions, grouping_ctx, initial_screen.screen_id
        )
        for action in initial_actions:
            sig = _action_signature(action)
            frontier.append((initial_screen.screen_id, action))
            frontier_sigs.add(sig)

        # Discover hidden content via scrolling on initial screen
        scroll_screens = self._handle_scroll_discovery(initial_screen)
        for ss in scroll_screens:
            ss_fp = ss.get_structural_fingerprint()
            if ss_fp not in visited:
                visited.add(ss_fp)
                screens[ss.screen_id] = ss
                fp_to_sid[ss_fp] = ss.screen_id
                for new_action in enumerate_actions(ss, exclude=skip_actions):
                    sig = _action_signature(new_action)
                    if sig not in executed_actions and sig not in frontier_sigs:
                        frontier.append((initial_screen.screen_id, new_action))
                        frontier_sigs.add(sig)

        if scroll_screens:
            logger.info(
                f"Initial screen scroll discovery: found {len(scroll_screens)} "
                f"additional scroll positions, frontier now has {len(frontier)} actions"
            )
            self._restart_app()
            current_fp = initial_fp

        max_steps = self._config.app.max_exploration_steps
        step = 0
        current_fp = initial_fp

        logger.info(
            f"Starting {self._config.app.exploration_strategy} exploration "
            f"(max {max_steps} steps, {len(frontier)} initial actions)"
        )

        while frontier and step < max_steps:
            # Replenish from deferred if main frontier is low
            if len(frontier) < 5 and deferred_frontier:
                batch = min(10, len(deferred_frontier))
                retried_sids: set[str] = set()
                for _ in range(batch):
                    item = deferred_frontier.popleft()
                    frontier.append(item)
                    retried_sids.add(item[0])
                    frontier_sigs.add(_action_signature(item[1]))
                for sid in retried_sids:
                    nav_failures.pop(sid, None)
                nav_stats["replenished_actions"] += batch

            source_screen_id, action, pop_tier = self._pop_frontier_prefer_current(
                frontier,
                fp_to_sid.get(current_fp, ""),
                step,
                max_steps,
                forward_edges=forward_edges,
                back_edges=back_edges,
            )
            tier_keys = {
                1: "p1_current",
                2: "p2_forward",
                3: "p3_back",
                4: "p4_sibling",
                5: "p5_replay",
            }
            nav_stats[tier_keys[pop_tier]] += 1

            # Remove from frontier membership tracking
            if source_screen_id in screens:
                pop_sig = _action_signature(action)
                frontier_sigs.discard(pop_sig)

            # Skip blacklisted actions
            bl_sig = _action_signature(action)
            if bl_sig in leave_app_blacklist:
                nav_stats["blacklisted_skips"] += 1
                continue

            # Navigate to source screen if we're not already there
            source_fp = screens[source_screen_id].get_structural_fingerprint()
            if current_fp != source_fp:
                nav_ok = False
                current_sid = fp_to_sid.get(current_fp, "")

                if pop_tier in (2, 3, 4):
                    nav_ok = self._try_local_navigation(
                        source_screen_id=source_screen_id,
                        current_screen_id=current_sid,
                        screens=screens,
                        forward_edges=forward_edges,
                        back_edges=back_edges,
                    )
                    if nav_ok:
                        nav_stats["local_nav_ok"] += 1
                    else:
                        nav_stats["local_nav_fail"] += 1

                if not nav_ok:
                    nav_ok = self._try_back_to_screen(source_screen_id, screens)
                    if nav_ok:
                        nav_stats["back_chain_ok"] += 1
                    else:
                        nav_stats["back_chain_fail"] += 1

                if not nav_ok:
                    nav_ok = self._navigate_to_screen_via_replay(
                        source_screen_id, screens, nav_paths
                    )
                    if nav_ok:
                        nav_stats["replay_ok"] += 1
                    else:
                        nav_stats["replay_fail"] += 1

                if nav_ok:
                    current_fp = source_fp
                    nav_failures.pop(source_screen_id, None)
                else:
                    current_fp = self._identify_current_fp()
                    nav_failures[source_screen_id] = nav_failures.get(source_screen_id, 0) + 1
                    if nav_failures[source_screen_id] >= max_nav_failures:
                        deferred = [(sid, act) for sid, act in frontier if sid == source_screen_id]
                        for sid, act in deferred:
                            if sid in screens:
                                d_sig = _action_signature(act)
                                frontier_sigs.discard(d_sig)
                        frontier = deque(
                            (sid, act) for sid, act in frontier if sid != source_screen_id
                        )
                        if deferred:
                            deferred_frontier.extend(deferred)
                            nav_stats["deferred_actions"] += len(deferred)
                            logger.info(
                                f"Deferred {len(deferred)} actions for "
                                f"{source_screen_id} (will retry later)"
                            )
                    continue

            # Execute the action
            logger.debug(
                f"Step {step + 1}/{max_steps}: "
                f"{action.action_type.value} on {action.target_element_id or 'global'} "
                f"from {source_screen_id}"
            )
            executed = self._execute_action(action)
            self._wait_for_stability(action.action_type)
            step += 1

            if not executed:
                continue

            # Only check keyboard after actions that could trigger it
            if (
                action.action_type in (ActionType.CLICK, ActionType.INPUT_TEXT)
                and action.target_element_id
                and source_screen_id in screens
            ):
                el_map = {e.element_id: e for e in screens[source_screen_id].elements}
                target_el = el_map.get(action.target_element_id)
                if target_el and (
                    target_el.is_editable or "EditText" in (target_el.class_name or "")
                ):
                    self._dismiss_keyboard_if_showing()
                    nav_stats["keyboard_checks"] += 1

            if not self._is_within_app():
                logger.debug("Left target app, recovering")
                bl_act_sig = _action_signature(action)
                leave_app_blacklist.add(bl_act_sig)
                if not self._restart_app():
                    logger.error("Cannot recover app, stopping exploration")
                    break
                current_fp = self._identify_current_fp()
                continue

            target_screen = self._capture_screen()
            if target_screen is None:
                continue

            target_fp = target_screen.get_structural_fingerprint()
            canonical_target_id = fp_to_sid.get(target_fp, target_screen.screen_id)

            # Ensure canonical screen is in screens dict
            if canonical_target_id not in screens:
                screens[canonical_target_id] = target_screen

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

            # Record executed action signature for dedup
            exec_sig = _action_signature(action)
            executed_actions.add(exec_sig)

            # Update adjacency graph
            if source_screen_id not in forward_edges:
                forward_edges[source_screen_id] = []
            if not any(
                a.action_type == action.action_type
                and a.target_bounds == action.target_bounds
                and tid == canonical_target_id
                for a, tid in forward_edges[source_screen_id]
            ):
                forward_edges[source_screen_id].append((action, canonical_target_id))
            if action.action_type == ActionType.CLICK and canonical_target_id != source_screen_id:
                back_edges[canonical_target_id] = source_screen_id

            # Behavioral verification for structural grouping
            replenished = record_behavioral_result(
                grouping_ctx, source_screen_id, action.target_element_id or "", target_fp
            )
            if replenished:
                for r_sid, r_action in replenished:
                    sig = _action_signature(r_action)
                    if sig not in executed_actions and sig not in frontier_sigs:
                        frontier.append((r_sid, r_action))
                        frontier_sigs.add(sig)
                nav_stats["groups_heterogeneous"] += 1
                nav_stats["replenished_actions"] += len(replenished)

            # --- Screen registration (first visit only) ---
            if target_fp not in visited:
                visited.add(target_fp)
                screens[target_screen.screen_id] = target_screen
                fp_to_sid[target_fp] = target_screen.screen_id

                source_path = nav_paths.get(source_screen_id, [])
                nav_paths[canonical_target_id] = source_path + [(action, canonical_target_id)]
                screen_depth[canonical_target_id] = screen_depth.get(source_screen_id, 0) + 1

                logger.info(
                    f"New screen discovered: {target_screen.screen_id} "
                    f"(activity={target_screen.activity_name}, "
                    f"total={len(screens)})"
                )

                # Track Activity coverage
                if target_screen.activity_name and declared_activities:
                    matched = _match_activity(target_screen.activity_name, declared_activities)
                    if matched and matched not in covered_activities:
                        covered_activities.add(matched)
                        remaining = len(declared_activities) - len(covered_activities)
                        logger.info(
                            f"Activity covered: {matched} "
                            f"({len(covered_activities)}/{len(declared_activities)}, "
                            f"{remaining} remaining)"
                        )

                # Handle scrollable content (only on first visit)
                scroll_screens = self._handle_scroll_discovery(target_screen)
                for ss in scroll_screens:
                    ss_fp = ss.get_structural_fingerprint()
                    if ss_fp not in visited:
                        visited.add(ss_fp)
                        screens[ss.screen_id] = ss
                        fp_to_sid[ss_fp] = ss.screen_id
                        for scroll_action in enumerate_actions(ss, exclude=skip_actions):
                            sig = _action_signature(scroll_action)
                            if sig not in executed_actions and sig not in frontier_sigs:
                                frontier.append((target_screen.screen_id, scroll_action))
                                frontier_sigs.add(sig)

                if scroll_screens:
                    current_fp = self._identify_current_fp()

            # --- Action enumeration (every visit, dedup prevents re-execution) ---
            depth = screen_depth.get(canonical_target_id, 0)
            if depth > max_depth:
                nav_stats["depth_skips"] += 1
            else:
                new_actions_list = list(enumerate_actions(target_screen, exclude=skip_actions))
                new_actions_list = apply_structural_grouping(
                    target_screen, new_actions_list, grouping_ctx, canonical_target_id
                )
                added = 0
                for new_action in new_actions_list:
                    sig = _action_signature(new_action)
                    if (
                        sig not in executed_actions
                        and sig not in frontier_sigs
                        and sig not in leave_app_blacklist
                    ):
                        frontier.append((canonical_target_id, new_action))
                        frontier_sigs.add(sig)
                        added += 1
                skipped = len(new_actions_list) - added
                if skipped > 0:
                    nav_stats["actions_deduped"] += skipped

        elapsed = time.monotonic() - start_time

        # Log navigation statistics
        total_pops = sum(nav_stats[k] for k in tier_keys.values())
        local_keys = ["p1_current", "p2_forward", "p3_back", "p4_sibling"]
        local_pops = sum(nav_stats[k] for k in local_keys)
        if total_pops > 0:
            logger.info(
                f"Nav stats: {total_pops} pops — "
                f"P1={nav_stats['p1_current']} P2={nav_stats['p2_forward']} "
                f"P3={nav_stats['p3_back']} P4={nav_stats['p4_sibling']} "
                f"P5={nav_stats['p5_replay']} | "
                f"deduped={nav_stats['actions_deduped']} actions"
            )
            pct = local_pops / total_pops * 100
            logger.info(f"Locality ratio: {pct:.0f}% of pops avoided replay")

        if declared_activities:
            uncovered = declared_activities - covered_activities
            logger.info(
                f"Activity coverage: {len(covered_activities)}/{len(declared_activities)} "
                f"({len(covered_activities) / len(declared_activities) * 100:.0f}%)"
            )
            if uncovered:
                logger.warning(f"Uncovered Activities: {sorted(uncovered)}")

        result = ExplorationResult(
            app_package=self._app_package,
            screens=screens,
            traces=traces,
            total_steps=step,
            unique_screens=len(screens),
            duration_seconds=round(elapsed, 2),
            output_dir=str(self._output_dir),
            declared_activities=sorted(declared_activities),
            covered_activities=sorted(covered_activities),
            nav_stats=nav_stats,
        )

        self._save_result(result)
        if self._app_prior:
            self._save_prior()

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
        """Capture the current screen state (hierarchy + screenshot)."""
        assert self._device is not None
        self._screen_counter += 1
        screen_id = f"scr_{self._screen_counter:04d}"

        try:
            current = self._device.app_current()
            activity_name = current.get("activity", "")
            package_name = current.get("package", "")

            xml_string = self._device.dump_hierarchy()

            screenshot_path = self._output_dir / "screens" / f"{screen_id}.png"
            self._device.screenshot(str(screenshot_path))

            xml_path = self._output_dir / "trees" / f"{screen_id}.xml"
            xml_path.write_text(xml_string, encoding="utf-8")

            elements = parse_hierarchy_xml(xml_string, app_package=self._app_package)

            screen = RawScreen(
                screen_id=screen_id,
                activity_name=activity_name,
                package_name=package_name,
                screenshot_path=str(screenshot_path),
                xml_tree_path=str(xml_path),
                elements=elements,
                timestamp=_now_iso(),
                metadata={"device_serial": self._serial},
            )
            return screen

        except Exception:
            logger.exception(f"Failed to capture screen {screen_id}")
            return None

    def _execute_action(self, action: Action) -> bool:
        """Execute an action on the device.

        Returns:
            True if executed, False if it couldn't be performed.
        """
        assert self._device is not None

        try:
            if action.action_type == ActionType.CLICK:
                if not action.target_bounds:
                    logger.debug(f"Skipping click: no bounds for {action.target_element_id}")
                    return False
                cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                self._device.click(cx, cy)

            elif action.action_type == ActionType.LONG_PRESS:
                if not action.target_bounds:
                    return False
                cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                self._device.long_click(cx, cy)

            elif action.action_type == ActionType.INPUT_TEXT:
                if action.target_bounds:
                    cx = (action.target_bounds[0] + action.target_bounds[2]) // 2
                    cy = (action.target_bounds[1] + action.target_bounds[3]) // 2
                    self._device.click(cx, cy)
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

            return True

        except Exception:
            logger.warning(f"Failed to execute action: {action.action_type.value}")
            return False

    def _wait_for_stability(self, action_type: ActionType | None = None) -> None:
        """Wait for the screen to stabilize after an action."""
        wait = (
            _ACTION_WAIT.get(action_type, self.STABILITY_WAIT)
            if action_type
            else self.STABILITY_WAIT
        )
        time.sleep(wait)

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
        """Poll until the target app is in the foreground."""
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
        """Try to return to the target app after leaving it."""
        assert self._device is not None
        for _ in range(3):
            self._device.press("back")
            time.sleep(0.5)
            if self._is_within_app():
                return True
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
        """Navigate to a target screen by restarting the app and replaying actions."""
        assert self._device is not None
        target_screen = screens.get(target_screen_id)
        if target_screen is None:
            return False

        target_fp = target_screen.get_structural_fingerprint()
        path = nav_paths.get(target_screen_id)
        if path is None:
            return False

        logger.debug(f"Navigating to {target_screen_id} via replay ({len(path)} steps)")
        if not self._restart_app():
            return False

        if not path:
            return self._verify_arrived_at(target_fp, target_screen)

        for replay_action, _ in path:
            if not self._is_within_app():
                logger.debug("Left app during replay")
                return False
            self._execute_action(replay_action)
            self._wait_for_stability(replay_action.action_type)

        arrived = self._verify_arrived_at(target_fp, target_screen)
        if not arrived:
            logger.debug(f"Replay ended at wrong screen (expected {target_fp[:12]})")
        return arrived

    def _try_local_navigation(
        self,
        source_screen_id: str,
        current_screen_id: str,
        screens: dict[str, RawScreen],
        forward_edges: dict[str, list[tuple[Action, str]]],
        back_edges: dict[str, str],
    ) -> bool:
        """Try to reach source_screen_id without full replay.

        Attempts: forward click, back press, or back + forward (sibling).
        """
        assert self._device is not None
        source_screen = screens[source_screen_id]
        source_fp = source_screen.get_structural_fingerprint()

        # Forward click
        if current_screen_id in forward_edges:
            for edge_action, edge_target in forward_edges[current_screen_id]:
                if edge_target == source_screen_id:
                    logger.debug(f"Local nav: forward to {source_screen_id}")
                    self._execute_action(edge_action)
                    self._wait_for_stability(edge_action.action_type)
                    return self._verify_arrived_at(source_fp, source_screen)

        # Back press to parent
        parent = back_edges.get(current_screen_id, "")
        if parent == source_screen_id:
            logger.debug(f"Local nav: back to {source_screen_id}")
            self._execute_action(Action(action_type=ActionType.NAVIGATE_BACK))
            self._wait_for_stability(ActionType.NAVIGATE_BACK)
            return self._verify_arrived_at(source_fp, source_screen)

        # Back to parent + forward to sibling
        if parent and parent in forward_edges:
            for edge_action, edge_target in forward_edges[parent]:
                if edge_target == source_screen_id:
                    logger.debug(f"Local nav: back to {parent} then forward to {source_screen_id}")
                    self._execute_action(Action(action_type=ActionType.NAVIGATE_BACK))
                    self._wait_for_stability(ActionType.NAVIGATE_BACK)

                    if parent in screens:
                        parent_screen = screens[parent]
                        parent_fp = parent_screen.get_structural_fingerprint()
                        if not self._verify_arrived_at(parent_fp, parent_screen):
                            return False

                    self._execute_action(edge_action)
                    self._wait_for_stability(edge_action.action_type)
                    return self._verify_arrived_at(source_fp, source_screen)

        return False

    def _verify_arrived_at(
        self,
        expected_fp: str,
        target_screen: RawScreen | None = None,
    ) -> bool:
        """Check if current screen matches expected, with fuzzy fallback."""
        screen = self._capture_screen()
        if screen is None:
            return False
        fp = screen.get_structural_fingerprint()
        if fp == expected_fp:
            return True
        if target_screen is not None:
            same_activity = (
                screen.activity_name and screen.activity_name == target_screen.activity_name
            )
            if same_activity and self._fingerprint_similarity(screen, target_screen) >= 0.75:
                return True
        return False

    @staticmethod
    def _fingerprint_similarity(screen_a: RawScreen, screen_b: RawScreen) -> float:
        """Jaccard similarity of structural element sets between two screens."""

        def _structural_set(scr: RawScreen) -> set[str]:
            return {f"{e.class_name}|{e.resource_id or ''}" for e in scr.elements}

        set_a = _structural_set(screen_a)
        set_b = _structural_set(screen_b)
        if not set_a and not set_b:
            return 1.0
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    def _handle_scroll_discovery(self, screen: RawScreen) -> list[RawScreen]:
        """Scroll scrollable elements to discover hidden content."""
        assert self._device is not None
        discovered: list[RawScreen] = []

        scrollable_elements = [e for e in screen.elements if e.is_scrollable and e.is_enabled]
        if not scrollable_elements:
            return discovered

        def _element_ids(scr: RawScreen) -> set[str]:
            ids: set[str] = set()
            for e in scr.elements:
                if e.resource_id:
                    ids.add(f"rid:{e.resource_id}")
                elif e.bounds:
                    qb = tuple(round(b / 50) * 50 for b in e.bounds)
                    ids.add(f"{e.class_name}:{qb}")
            return ids

        prev_elements = _element_ids(screen)

        for element in scrollable_elements:
            for _ in range(self.MAX_SCROLLS_PER_ELEMENT):
                cx = (element.bounds[0] + element.bounds[2]) // 2
                cy = (element.bounds[1] + element.bounds[3]) // 2
                h = element.bounds[3] - element.bounds[1]
                self._device.swipe(cx, cy, cx, cy - h // 3, duration=0.3)
                time.sleep(0.5)

                if not self._is_within_app():
                    logger.debug("Scroll exited the app, stopping scroll discovery")
                    self._recover_from_crash()
                    return discovered

                new_screen = self._capture_screen()
                if new_screen is None:
                    break

                new_elements = _element_ids(new_screen)
                if not (new_elements - prev_elements):
                    break

                discovered.append(new_screen)
                prev_elements = prev_elements | new_elements

        return discovered

    def _restart_app(self) -> bool:
        """Restart the target app and wait for it to load.

        Tries soft restart first (bring to foreground without killing),
        then falls back to hard restart (kill + relaunch).
        """
        assert self._device is not None
        self._device.app_start(self._app_package)
        if self._wait_for_app_foreground(timeout=3.0):
            return True
        self._device.app_start(self._app_package, stop=True)
        if self._wait_for_app_foreground():
            return True
        logger.warning(f"App {self._app_package} did not come to foreground, retrying")
        self._device.app_start(self._app_package, stop=True)
        time.sleep(3.0)
        return self._wait_for_app_foreground()

    def _try_back_to_screen(
        self,
        target_screen_id: str,
        screens: dict[str, RawScreen],
        max_presses: int = 3,
    ) -> bool:
        """Try pressing back up to max_presses times to reach target screen."""
        assert self._device is not None
        target_screen = screens.get(target_screen_id)
        if target_screen is None:
            return False
        target_fp = target_screen.get_structural_fingerprint()
        for _ in range(max_presses):
            self._device.press("back")
            time.sleep(0.4)
            if not self._is_within_app():
                self._restart_app()
                return False
            if self._verify_arrived_at(target_fp, target_screen):
                return True
        return False

    def _identify_current_screen(self) -> tuple[str, RawScreen | None]:
        """Capture the current screen and return (fingerprint, screen)."""
        if not self._is_within_app() and not self._restart_app():
            return "", None
        screen = self._capture_screen()
        if screen is None:
            return "", None
        return screen.get_structural_fingerprint(), screen

    def _identify_current_fp(self) -> str:
        """Capture the current screen and return its fingerprint."""
        fp, _ = self._identify_current_screen()
        return fp

    # --- Frontier management ---

    def _pop_frontier_prefer_current(
        self,
        frontier: deque[tuple[str, Action]],
        current_screen_id: str,
        current_step: int,
        max_steps: int,
        forward_edges: dict[str, list[tuple[Action, str]]] | None = None,
        back_edges: dict[str, str] | None = None,
    ) -> tuple[str, Action, int]:
        """Pop from frontier with 5-tier locality-aware priority.

        P1: Current screen (cost=0)
        P2: Forward-adjacent via observed transition (cost=1)
        P3: Back-reachable parent (cost=1)
        P4: Sibling via back+forward (cost=2)
        P5: Fallback — full replay required
        """
        forward_edges = forward_edges or {}
        back_edges = back_edges or {}

        # P1: Current screen
        if current_screen_id:
            for i, (sid, act) in enumerate(frontier):
                if sid == current_screen_id:
                    del frontier[i]
                    return (sid, act, 1)

        # P2: Forward-adjacent
        if current_screen_id and current_screen_id in forward_edges:
            adjacent_sids = {tid for _, tid in forward_edges[current_screen_id]}
            for i, (sid, act) in enumerate(frontier):
                if sid in adjacent_sids:
                    del frontier[i]
                    return (sid, act, 2)

        # P3: Back-reachable parent
        parent_sid = back_edges.get(current_screen_id, "") if current_screen_id else ""
        if parent_sid:
            for i, (sid, act) in enumerate(frontier):
                if sid == parent_sid:
                    del frontier[i]
                    return (sid, act, 3)

        # P4: Sibling (other children of same parent)
        if parent_sid and parent_sid in forward_edges:
            sibling_sids = {tid for _, tid in forward_edges[parent_sid]}
            sibling_sids.discard(current_screen_id)
            if sibling_sids:
                for i, (sid, act) in enumerate(frontier):
                    if sid in sibling_sids:
                        del frontier[i]
                        return (sid, act, 4)

        # P5: Fallback
        strategy = self._config.app.exploration_strategy
        if strategy == "dfs":
            return (*frontier.pop(), 5)
        elif strategy == "hybrid":
            if current_step < int(max_steps * 0.6):
                return (*frontier.popleft(), 5)
            else:
                return (*frontier.pop(), 5)
        else:
            return (*frontier.popleft(), 5)

    # --- Persistence ---

    def _save_result(self, result: ExplorationResult) -> None:
        """Save the full exploration result as a JSON trace file."""
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        trace_path = self._output_dir / "traces" / f"exploration_{timestamp}.json"

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

        if result.declared_activities:
            data["activity_coverage"] = {
                "declared": result.declared_activities,
                "covered": result.covered_activities,
                "coverage_ratio": (
                    len(result.covered_activities) / len(result.declared_activities)
                    if result.declared_activities
                    else 0
                ),
            }

        if result.nav_stats:
            data["nav_stats"] = result.nav_stats

        trace_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info(f"Exploration trace saved to {trace_path}")

    def _save_prior(self) -> None:
        """Save the app prior as JSON alongside exploration data."""
        assert self._app_prior is not None
        prior_path = self._output_dir / "prior.json"
        prior_path.write_text(
            json.dumps(self._app_prior.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(f"App prior saved to {prior_path}")

    @staticmethod
    def _extract_metadata(screen: RawScreen) -> dict[str, Any]:
        """Extract page_title and other metadata from a screen's elements."""
        metadata: dict[str, Any] = {}

        for e in screen.elements:
            rid = e.resource_id or ""
            if "action_bar_title" in rid.lower() and e.text and e.text.strip():
                metadata["page_title"] = e.text.strip()
                return metadata

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
