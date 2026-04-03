"""Stage 3: Hierarchical FSM Construction.

Builds an AppFSM from exploration traces and abstract states. Organizes states
into a hierarchy (App > Activity > Fragment > Component) using Android Activity
names from the accessibility tree. Built on networkx.DiGraph.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

from vigil.core.ui_parser import parse_hierarchy_xml
from vigil.models.action import Action
from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.models.state import RawScreen
from vigil.neuro.state_abstractor import StateAbstractor


class FsmBuilder:
    """Build an AppFSM from an exploration trace JSON file.

    Args:
        app_package: Android package name.
    """

    def __init__(self, app_package: str) -> None:
        self._app_package = app_package

    def build_from_trace(
        self,
        trace_path: Path,
        include_self_loops: bool = False,
        classify_containers: bool = True,
    ) -> AppFSM:
        """Build an FSM from a serialized exploration trace.

        Args:
            trace_path: Path to the exploration JSON file.
            include_self_loops: Whether to include transitions where source == target.
            classify_containers: Whether to classify scrollable containers in each
                state as STRUCTURAL or CONTENT. Requires xml_tree_path in trace data.

        Returns:
            A fully constructed AppFSM.
        """
        data = json.loads(trace_path.read_text(encoding="utf-8"))
        raw_screens = data.get("screens", {})
        raw_traces = data.get("traces", [])

        # Step 1: Deduplicate screens by fingerprint → canonical state mapping
        fp_to_state_id, states = self._build_states(raw_screens)
        sid_to_state_id = self._build_screen_mapping(raw_screens, fp_to_state_id)

        # Step 2: Build transitions from traces
        transitions = self._build_transitions(raw_traces, sid_to_state_id, include_self_loops)

        # Step 3: Merge duplicate transitions
        transitions = self._merge_transitions(transitions)

        # Step 4: Detect initial state
        initial_state = self._detect_initial_state(raw_traces, sid_to_state_id)

        # Step 5: Disambiguate duplicate state names
        self._disambiguate_names(states)

        # Step 6: Infer hierarchy from activity names
        self._infer_hierarchy(states)

        # Step 6: Assemble FSM
        fsm = AppFSM(app_package=self._app_package)
        fsm.initial_state = initial_state

        for state in states.values():
            fsm.add_state(state)
        for t in transitions:
            fsm.add_transition(t)

        # Step 7: Container classification
        if classify_containers:
            self._classify_containers(fsm, raw_screens, trace_path.parent)

        logger.info(
            f"FSM built: {len(states)} states, {len(transitions)} transitions, "
            f"initial_state={initial_state}"
        )
        return fsm

    def _classify_containers(
        self,
        fsm: AppFSM,
        raw_screens: dict[str, Any],
        trace_dir: Path,
    ) -> None:
        """Classify scrollable containers by parsing XML tree files.

        Reads the full accessibility tree XML for each raw screen (via
        xml_tree_path in the trace data) to get the complete element hierarchy
        needed for container analysis.
        """
        screens: dict[str, RawScreen] = {}
        missing_count = 0

        for screen_id, screen_data in raw_screens.items():
            xml_rel_path = screen_data.get("xml_tree_path")
            if not xml_rel_path:
                missing_count += 1
                continue

            # Resolve path: try relative to trace dir, then relative to project root
            xml_path = trace_dir / xml_rel_path
            if not xml_path.exists():
                # Try as project-relative path
                project_root = trace_dir
                while project_root.parent != project_root:
                    candidate = project_root / xml_rel_path
                    if candidate.exists():
                        xml_path = candidate
                        break
                    project_root = project_root.parent

            if not xml_path.exists():
                missing_count += 1
                continue

            xml_content = xml_path.read_text(encoding="utf-8")
            elements = parse_hierarchy_xml(xml_content)
            if not elements:
                continue

            screens[screen_id] = RawScreen(
                screen_id=screen_id,
                activity_name=screen_data.get("activity_name"),
                elements=elements,
            )

        if not screens:
            logger.warning(
                f"Container classification skipped: no XML files found "
                f"({missing_count} screens missing xml_tree_path)"
            )
            return

        abstractor = StateAbstractor()
        abstractor.annotate_fsm_states(fsm, screens)
        classified = sum(1 for s in fsm.states.values() if s.container_type.value != "none")
        logger.info(
            f"Container classification: {classified}/{len(fsm.states)} states classified "
            f"({len(screens)} XML files parsed)"
        )

    def _build_states(
        self, raw_screens: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, AbstractState]]:
        """Build AbstractStates from screens, deduplicating by scroll-aware fingerprint.

        Scroll-aware fingerprinting excludes children of scrollable containers so
        that the same screen at different scroll positions maps to one state.

        Returns:
            fp_to_state_id: fingerprint → state_id mapping
            states: state_id → AbstractState mapping
        """
        fp_to_state_id: dict[str, str] = {}
        states: dict[str, AbstractState] = {}
        state_counter = 0

        for screen_id, screen in raw_screens.items():
            fp = self._compute_functional_fingerprint(screen)
            if not fp:
                continue

            if fp in fp_to_state_id:
                existing_sid = fp_to_state_id[fp]
                states[existing_sid].raw_screens.append(screen_id)
                continue

            state_counter += 1
            state_id = f"s_{state_counter:03d}"
            name = self._derive_state_name(screen, state_id)

            state = AbstractState(
                state_id=state_id,
                name=name,
                fingerprint=fp,
                hierarchy_level=HierarchyLevel.ACTIVITY,
                activity_name=screen.get("activity_name"),
                raw_screens=[screen_id],
            )

            fp_to_state_id[fp] = state_id
            states[state_id] = state

        logger.info(f"Built {len(states)} abstract states from {len(raw_screens)} raw screens")
        return fp_to_state_id, states

    @staticmethod
    def _compute_functional_fingerprint(screen: dict[str, Any]) -> str:
        """Compute a functional fingerprint based on page identity.

        Fingerprint priority:
        1. If page_title exists: (title, modal) — title is the primary page identity.
           Container signature is ignored because scrolling changes visible containers.
        2. If no title but container_sig is specific (≥2 classes): (container_sig, modal).
        3. Otherwise: fall back to scroll-aware structural fingerprint.
        """
        metadata = screen.get("metadata", {})
        page_title = metadata.get("page_title", "")
        container_sig = metadata.get("container_signature", "")
        has_modal = metadata.get("has_modal", False)

        if page_title:
            # Title is the primary identity — ignore container (scroll-volatile)
            fp_input = (page_title, has_modal)
            return hashlib.sha256(str(fp_input).encode()).hexdigest()[:16]

        if container_sig:
            # No title — use container sig, but only if specific enough
            num_classes = len(container_sig.split(","))
            if num_classes >= 2:
                fp_input = (container_sig, has_modal)
                return hashlib.sha256(str(fp_input).encode()).hexdigest()[:16]

        # Generic or missing metadata — fall back to structural fingerprint
        return FsmBuilder._compute_structural_fingerprint(screen)

    @staticmethod
    def _compute_structural_fingerprint(screen: dict[str, Any]) -> str:
        """Fallback structural fingerprint excluding scroll-volatile children."""
        elements = screen.get("interactable_elements", screen.get("elements", []))
        if not elements:
            return ""

        scrollable_depths: set[int] = set()
        for e in elements:
            if e.get("is_scrollable"):
                scrollable_depths.add(e.get("depth", 0))

        components = []
        for e in elements:
            depth = e.get("depth", 0)
            if (
                scrollable_depths
                and not e.get("is_scrollable")
                and any(depth > sd for sd in scrollable_depths)
            ):
                continue

            interactability = (
                e.get("is_clickable", False),
                e.get("is_long_clickable", False),
                e.get("is_scrollable", False),
                e.get("is_editable", False),
                e.get("is_checkable", False),
            )
            components.append(
                (
                    e.get("class_name", ""),
                    e.get("resource_id", "") or "",
                    depth,
                    interactability,
                )
            )

        components.sort()
        fingerprint_input = (screen.get("activity_name", "") or "", tuple(components))
        return hashlib.sha256(str(fingerprint_input).encode()).hexdigest()[:16]

    def _build_screen_mapping(
        self,
        raw_screens: dict[str, Any],
        fp_to_state_id: dict[str, str],
    ) -> dict[str, str]:
        """Map raw screen IDs to canonical state IDs via scroll-aware fingerprint."""
        sid_to_state_id: dict[str, str] = {}
        for screen_id, screen in raw_screens.items():
            fp = self._compute_functional_fingerprint(screen)
            if fp in fp_to_state_id:
                sid_to_state_id[screen_id] = fp_to_state_id[fp]
        return sid_to_state_id

    def _build_transitions(
        self,
        raw_traces: list[dict[str, Any]],
        sid_to_state_id: dict[str, str],
        include_self_loops: bool,
    ) -> list[Transition]:
        """Convert exploration traces into FSM transitions."""
        transitions: list[Transition] = []
        skipped_self_loops = 0

        for trace in raw_traces:
            source_sid = trace.get("source_screen_id", "")
            target_sid = trace.get("target_screen_id", "")

            source_state = sid_to_state_id.get(source_sid)
            target_state = sid_to_state_id.get(target_sid)

            if source_state is None or target_state is None:
                continue

            if not include_self_loops and source_state == target_state:
                skipped_self_loops += 1
                continue

            action_data = trace.get("action", {})
            action = Action(**action_data)
            fsm_action = action.to_fsm_dict()

            transitions.append(
                Transition(
                    source=source_state,
                    target=target_state,
                    action=fsm_action,
                    observed_count=1,
                )
            )

        if skipped_self_loops:
            logger.debug(f"Skipped {skipped_self_loops} self-loop transitions")
        return transitions

    def _merge_transitions(self, transitions: list[Transition]) -> list[Transition]:
        """Merge duplicate transitions by (source, target, action_type).

        Sums observed_count for duplicates.
        """
        key_to_trans: dict[tuple[str, str, str], Transition] = {}

        for t in transitions:
            action_type = t.action.get("type", "")
            key = (t.source, t.target, action_type)

            if key in key_to_trans:
                key_to_trans[key].observed_count += t.observed_count
            else:
                key_to_trans[key] = t

        merged = list(key_to_trans.values())
        if len(transitions) != len(merged):
            logger.debug(f"Merged {len(transitions)} transitions → {len(merged)} unique")
        return merged

    def _detect_initial_state(
        self,
        raw_traces: list[dict[str, Any]],
        sid_to_state_id: dict[str, str],
    ) -> str | None:
        """Detect the initial state from the first trace step."""
        if not raw_traces:
            return None
        # Sort by step_number and take the source of the first step
        sorted_traces = sorted(raw_traces, key=lambda t: t.get("step_number", 0))
        first_source = sorted_traces[0].get("source_screen_id", "")
        return sid_to_state_id.get(first_source)

    @staticmethod
    def _disambiguate_names(states: dict[str, AbstractState]) -> None:
        """Append numeric suffixes to duplicate state names."""
        name_counts: dict[str, list[str]] = defaultdict(list)
        for state in states.values():
            name_counts[state.name].append(state.state_id)

        for name, state_ids in name_counts.items():
            if len(state_ids) <= 1:
                continue
            for i, sid in enumerate(state_ids, start=1):
                states[sid].name = f"{name} #{i}"

    def _infer_hierarchy(self, states: dict[str, AbstractState]) -> None:
        """Set hierarchy levels based on activity names.

        States with the same activity_name are FRAGMENT level under
        a shared ACTIVITY parent. States with unique or None activity
        default to ACTIVITY level.
        """
        activity_groups: dict[str | None, list[str]] = defaultdict(list)
        for state in states.values():
            activity_groups[state.activity_name].append(state.state_id)

        for activity_name, state_ids in activity_groups.items():
            if activity_name is None:
                # No activity info — keep as ACTIVITY level
                continue
            if len(state_ids) > 1:
                # Multiple states share an activity — mark as FRAGMENT
                for sid in state_ids:
                    states[sid].hierarchy_level = HierarchyLevel.FRAGMENT

    def _derive_state_name(self, screen: dict[str, Any], fallback_id: str) -> str:
        """Derive a human-readable state name from screen metadata."""
        # Try to find a title element
        elements = screen.get("interactable_elements", screen.get("elements", []))
        for el in elements:
            rid = el.get("resource_id", "") or ""
            if "title" in rid.lower() or "action_bar_title" in rid.lower():
                text = el.get("text")
                if text and text.strip():
                    return text.strip()

        # Try content_description of first element
        for el in elements:
            cd = el.get("content_description")
            if cd and cd.strip() and len(cd.strip()) > 2:
                return cd.strip()

        # Fallback to first non-empty text
        for el in elements:
            text = el.get("text")
            if text and text.strip() and len(text.strip()) > 2:
                return text.strip()

        return fallback_id
