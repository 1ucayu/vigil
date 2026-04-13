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
from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    HierarchyLevel,
    SubFsmTemplate,
    Transition,
)


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
    ) -> AppFSM:
        """Build an FSM from a serialized exploration trace.

        Args:
            trace_path: Path to the exploration JSON file.
            include_self_loops: Whether to include transitions where source == target.

        Returns:
            A fully constructed AppFSM.
        """
        data = json.loads(trace_path.read_text(encoding="utf-8"))
        raw_screens = data.get("screens", {})
        raw_traces = data.get("traces", [])

        # Step 1: Deduplicate screens by fingerprint → canonical state mapping
        fp_to_state_id, states = self._build_states(raw_screens, trace_path.parent)
        sid_to_state_id = self._build_screen_mapping(raw_screens, fp_to_state_id)

        # Step 2: Build transitions from traces
        transitions = self._build_transitions(
            raw_traces, sid_to_state_id, include_self_loops, raw_screens
        )

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

        # Step 7: Post-processing — merge duplicates and remove error states
        merged = self._merge_scroll_duplicates(fsm)
        removed = self._remove_error_states(fsm)
        if merged or removed:
            logger.info(
                f"Post-processing: merged {merged} duplicate states, removed {removed} error states"
            )

        # Step 8: Build Sub-FSM templates for dynamic containers
        templates_created = self._build_sub_fsm_templates(fsm)
        if templates_created:
            logger.info(f"Created {templates_created} Sub-FSM templates")

        logger.info(
            f"FSM built: {len(fsm.states)} states, {len(fsm.transitions)} transitions, "
            f"initial_state={initial_state}"
        )
        return fsm

    # --- Post-processing: duplicate/error state cleanup ---

    ERROR_PAGE_PATTERNS: list[str] = [
        "Webpage not available",
        "Android System notif",
        "App isn't responding",
        "has stopped",
        "isn't responding",
        "Keep waiting",
    ]

    def _merge_scroll_duplicates(self, fsm: AppFSM) -> int:
        """Merge states that share the same (activity_name, page_title).

        Scroll-induced duplicates (e.g., "官方音效 #1", "官方音效 #2") share the
        same activity and title but have different fingerprints because scrolling
        changes visible elements. Merges them into one canonical state.

        Returns:
            Number of states merged away.
        """
        # Group states by (activity_name, base_name) — strip "#N" suffixes
        import re

        groups: dict[tuple[str | None, str], list[str]] = defaultdict(list)
        for state in fsm.states.values():
            base_name = re.sub(r"\s*#\d+$", "", state.name)
            key = (state.activity_name, base_name)
            groups[key].append(state.state_id)

        merged_count = 0
        for (_activity, base_name), state_ids in groups.items():
            if len(state_ids) <= 1:
                continue

            # Keep first as canonical, merge others into it
            canonical_id = state_ids[0]
            duplicates = state_ids[1:]

            # Collect raw_screens from duplicates
            for dup_id in duplicates:
                dup_state = fsm.states[dup_id]
                fsm.states[canonical_id].raw_screens.extend(dup_state.raw_screens)

            # Strip "#N" suffix from canonical state name
            fsm.states[canonical_id].name = base_name

            # Redirect transitions
            redirect_map = {dup_id: canonical_id for dup_id in duplicates}
            new_transitions: list[Transition] = []
            seen_keys: set[tuple[str, str, str]] = set()

            for t in fsm.transitions:
                source = redirect_map.get(t.source, t.source)
                target = redirect_map.get(t.target, t.target)
                # Skip self-loops created by merging
                if source == target:
                    continue
                action_type = t.action.get("type", "")
                key = (source, target, action_type)
                if key in seen_keys:
                    # Find existing and increment count
                    for existing in new_transitions:
                        e_src = existing.source
                        e_tgt = existing.target
                        e_act = existing.action.get("type", "")
                        if (e_src, e_tgt, e_act) == key:
                            existing.observed_count += t.observed_count
                            break
                else:
                    seen_keys.add(key)
                    new_transitions.append(
                        Transition(
                            source=source,
                            target=target,
                            action=t.action,
                            guard=t.guard,
                            confidence=t.confidence,
                            observed_count=t.observed_count,
                        )
                    )

            # Remove duplicate states from graph and dict
            for dup_id in duplicates:
                if dup_id in fsm.states:
                    del fsm.states[dup_id]
                if dup_id in fsm.graph:
                    fsm.graph.remove_node(dup_id)

            # Rebuild graph edges
            fsm.graph.remove_edges_from(list(fsm.graph.edges))
            fsm.transitions = new_transitions
            for t in new_transitions:
                if t.source in fsm.graph and t.target in fsm.graph:
                    fsm.graph.add_edge(
                        t.source,
                        t.target,
                        action=t.action,
                        guard=t.guard,
                        confidence=t.confidence,
                        observed_count=t.observed_count,
                    )

            # Update initial_state if it was a duplicate
            if fsm.initial_state in redirect_map:
                fsm.initial_state = redirect_map[fsm.initial_state]

            merged_count += len(duplicates)
            logger.debug(
                f"Merged {len(duplicates)} duplicates of '{base_name}' into {canonical_id}"
            )

        return merged_count

    def _remove_error_states(self, fsm: AppFSM) -> int:
        """Remove transient error/system states from the FSM.

        Matches state names against ERROR_PAGE_PATTERNS (substring match).

        Returns:
            Number of states removed.
        """
        to_remove: list[str] = []
        for state in fsm.states.values():
            name_lower = state.name.lower()
            for pattern in self.ERROR_PAGE_PATTERNS:
                if pattern.lower() in name_lower:
                    to_remove.append(state.state_id)
                    break

        for sid in to_remove:
            # Remove transitions involving this state
            fsm.transitions = [t for t in fsm.transitions if t.source != sid and t.target != sid]
            # Remove from graph
            if sid in fsm.graph:
                fsm.graph.remove_node(sid)
            # Remove from states dict
            del fsm.states[sid]
            # Update initial_state if needed
            if fsm.initial_state == sid:
                fsm.initial_state = None

        if to_remove:
            logger.debug(f"Removed error states: {to_remove}")

        return len(to_remove)

    def _build_sub_fsm_templates(self, fsm: AppFSM) -> int:
        """Create Sub-FSM templates for verified dynamic containers.

        Detects states with container_type=DYNAMIC that have multiple outgoing
        click transitions whose targets share the same structural fingerprint.
        Collapses those N transitions into a single SubFsmTemplate reference.

        Returns:
            Number of templates created.
        """
        templates_created = 0

        for state_id, state in list(fsm.states.items()):
            if state.container_type != ContainerType.DYNAMIC:
                continue

            click_targets = self._find_same_fingerprint_targets(fsm, state_id)
            if len(click_targets) < 2:
                continue

            target_fp = click_targets[0][1]
            representative_target_id = click_targets[0][0]

            templates_created += 1
            template_id = f"tmpl_{state_id}"

            rep_state = fsm.states.get(representative_target_id)
            template_states: dict[str, AbstractState] = {}
            template_transitions: list[Transition] = []

            if rep_state:
                template_states[representative_target_id] = rep_state
                for t in fsm.transitions:
                    if t.source == representative_target_id:
                        template_transitions.append(t)

            tmpl = SubFsmTemplate(
                template_id=template_id,
                source_state_id=state_id,
                entry_fingerprint=target_fp,
                states=template_states,
                transitions=template_transitions,
                parameter_schema={"selected_item": "string"},
            )
            fsm.sub_fsm_templates[template_id] = tmpl
            state.sub_fsm_template_id = template_id

            collapsed_target_ids = {tid for tid, _ in click_targets[1:]}
            for tid in collapsed_target_ids:
                if tid in fsm.states and tid != representative_target_id:
                    del fsm.states[tid]
                if tid in fsm.graph:
                    fsm.graph.remove_node(tid)

            fsm.transitions = [
                t
                for t in fsm.transitions
                if t.target not in collapsed_target_ids and t.source not in collapsed_target_ids
            ]

            fsm.graph.remove_edges_from(list(fsm.graph.edges))
            for t in fsm.transitions:
                if t.source in fsm.graph and t.target in fsm.graph:
                    fsm.graph.add_edge(
                        t.source,
                        t.target,
                        action=t.action,
                        guard=t.guard,
                        confidence=t.confidence,
                        observed_count=t.observed_count,
                    )

            logger.debug(
                f"Template {template_id}: collapsed {len(click_targets)} transitions "
                f"from {state_id} (kept {representative_target_id})"
            )

        return templates_created

    @staticmethod
    def _find_same_fingerprint_targets(fsm: AppFSM, source_id: str) -> list[tuple[str, str]]:
        """Find click transitions from source whose targets share a fingerprint.

        Returns:
            List of (target_state_id, fingerprint) for the largest group of
            same-fingerprint targets. Empty if no group has >= 2 members.
        """
        fp_groups: dict[str, list[str]] = defaultdict(list)
        for t in fsm.transitions:
            if t.source != source_id:
                continue
            if t.action.get("type") != "click":
                continue
            target = fsm.states.get(t.target)
            if target:
                fp_groups[target.fingerprint].append(t.target)

        best_group: list[str] = []
        best_fp = ""
        for fp, targets in fp_groups.items():
            if len(targets) > len(best_group):
                best_group = targets
                best_fp = fp

        if len(best_group) < 2:
            return []
        return [(tid, best_fp) for tid in best_group]

    def _build_states(
        self,
        raw_screens: dict[str, Any],
        trace_dir: Path | None = None,
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
            name = self._derive_state_name(screen, state_id, trace_dir)

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
        raw_screens: dict[str, Any] | None = None,
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

            if (
                not include_self_loops
                and source_state == target_state
                and not (raw_screens and self._is_toggle_action(trace, raw_screens))
            ):
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
                    confidence=1.0,
                    observed_count=1,
                )
            )

        if skipped_self_loops:
            logger.debug(f"Skipped {skipped_self_loops} self-loop transitions")
        return transitions

    @staticmethod
    def _is_toggle_action(trace: dict[str, Any], raw_screens: dict[str, Any]) -> bool:
        """Check if a trace step targets a checkable element (toggle/switch)."""
        action_data = trace.get("action", {})
        target_eid = action_data.get("target_element_id")
        if not target_eid:
            return False

        source_sid = trace.get("source_screen_id", "")
        screen = raw_screens.get(source_sid, {})
        elements = screen.get("interactable_elements", screen.get("elements", []))

        for el in elements:
            if el.get("element_id") == target_eid:
                return el.get("is_checkable", False)

        return False

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

    def _derive_state_name(
        self, screen: dict[str, Any], fallback_id: str, trace_dir: Path | None = None
    ) -> str:
        """Derive a human-readable state name from screen metadata.

        Reads the full XML accessibility tree (not just interactable elements)
        to find title elements like action_bar_title, which are typically
        non-clickable TextViews containing the page name.
        """
        # Strategy 1: Parse full XML tree for title elements
        all_elements = self._get_all_elements(screen, trace_dir)

        # Look for title resource IDs in all elements (including non-interactable)
        for el in all_elements:
            rid = el.get("resource_id", "") or ""
            if "action_bar_title" in rid.lower():
                text = el.get("text")
                if text and text.strip():
                    return text.strip()

        # Look for broader title patterns
        for el in all_elements:
            rid = el.get("resource_id", "") or ""
            if rid and "title" in rid.lower() and "subtitle" not in rid.lower():
                text = el.get("text")
                if text and text.strip() and len(text.strip()) > 1:
                    return text.strip()

        # Strategy 2: content_description from all elements
        for el in all_elements:
            cd = el.get("content_description")
            if cd and cd.strip() and len(cd.strip()) > 2:
                return cd.strip()

        # Strategy 3: first non-empty text from interactable elements
        interactable = screen.get("interactable_elements", screen.get("elements", []))
        for el in interactable:
            text = el.get("text")
            if text and text.strip() and len(text.strip()) > 2:
                return text.strip()

        return fallback_id

    def _get_all_elements(
        self, screen: dict[str, Any], trace_dir: Path | None = None
    ) -> list[dict[str, Any]]:
        """Get all elements for a screen, parsing XML if available.

        Falls back to interactable_elements if XML is not found.
        """
        xml_rel_path = screen.get("xml_tree_path")
        if xml_rel_path and trace_dir is not None:
            xml_path = self._resolve_path(xml_rel_path, trace_dir)
            if xml_path is not None:
                xml_content = xml_path.read_text(encoding="utf-8")
                elements = parse_hierarchy_xml(xml_content)
                if elements:
                    return [e.model_dump() for e in elements]

        return screen.get("interactable_elements", screen.get("elements", []))

    @staticmethod
    def _resolve_path(rel_path: str, trace_dir: Path) -> Path | None:
        """Resolve a path, trying multiple strategies.

        Order: absolute → CWD-relative → trace_dir-relative → trees/ sibling.
        """
        p = Path(rel_path)
        # 1. Absolute path
        if p.is_absolute() and p.exists():
            return p
        # 2. Relative to CWD (covers project-root-relative paths like
        #    "data/apps/settings/trees/scr_0001.xml")
        if p.exists():
            return p
        # 3. Relative to trace dir
        candidate = trace_dir / rel_path
        if candidate.exists():
            return candidate
        # 4. Try resolving just the filename in the trees/ sibling directory
        trees_dir = trace_dir.parent / "trees"
        if trees_dir.is_dir():
            candidate = trees_dir / p.name
            if candidate.exists():
                return candidate
        return None
