"""Stage 2: State Abstraction — container classification and sub-FSM templates.

Classifies scrollable containers as STRUCTURAL (fixed menu) or CONTENT
(dynamic list of homogeneous items). Uses multi-signal analysis of child
widget skeletons to handle real-world edge cases: heterogeneous content
lists, section headers, mixed containers.

For CONTENT containers, extracts parameterized sub-FSM templates from
exploration traces — the structural sub-tree behind a representative item click.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter
from typing import Any

from loguru import logger
from pydantic import BaseModel

from vigil.models.fsm import (
    AbstractState,
    AppFSM,
    ContainerType,
    SubFsmTemplate,
    Transition,
)
from vigil.models.state import RawScreen, UIElement


class ContainerClassification(BaseModel):
    """Result of classifying a scrollable container."""

    container_element_id: str
    container_resource_id: str | None
    container_class_name: str
    container_type: ContainerType
    num_children: int
    num_core_children: int
    num_unique_skeletons: int
    dominant_skeleton_ratio: float
    max_segment_size: int
    representative_skeleton: str | None = None
    stripped_header: bool = False
    stripped_footer: bool = False


class StateAbstractor:
    """Classifies scrollable containers and annotates FSM states.

    Uses multi-signal analysis of child widget skeletons. Works across app types:
    - Settings menus (diverse skeletons, <8 items) -> STRUCTURAL
    - WiFi lists (homogeneous items) -> CONTENT
    - Food delivery lists (2-3 view types, dominant >60%) -> CONTENT
    - Chat lists with date headers (dominant + headers stripped) -> CONTENT
    - E-commerce home (banner + nav + products) -> STRUCTURAL
    """

    MIN_ITEMS_FOR_CLASSIFICATION: int = 3
    CONTENT_DOMINANT_RATIO: float = 0.6
    CONTENT_MIN_ITEMS: int = 5
    CONTENT_SEGMENT_MIN: int = 5
    STRUCTURAL_MAX_ITEMS: int = 8
    CONTENT_TWO_TYPE_RATIO: float = 0.7

    def classify_container(
        self,
        container: UIElement,
        children: list[UIElement],
        elements_by_id: dict[str, UIElement] | None = None,
    ) -> ContainerClassification:
        """Classify a scrollable container using multi-signal analysis."""
        skeleton_hashes = self._compute_child_skeletons(children, elements_by_id)

        # Base result for early returns
        base = {
            "container_element_id": container.element_id,
            "container_resource_id": container.resource_id,
            "container_class_name": container.class_name,
            "num_children": len(children),
        }

        # Too few children to classify
        if len(skeleton_hashes) < self.MIN_ITEMS_FOR_CLASSIFICATION:
            return ContainerClassification(
                **base,
                container_type=ContainerType.NONE,
                num_core_children=len(skeleton_hashes),
                num_unique_skeletons=len(set(skeleton_hashes)) if skeleton_hashes else 0,
                dominant_skeleton_ratio=0.0,
                max_segment_size=0,
            )

        # Strip headers/footers
        core, had_header, had_footer = self._strip_headers_footers(skeleton_hashes)

        if len(core) < self.MIN_ITEMS_FOR_CLASSIFICATION:
            return ContainerClassification(
                **base,
                container_type=ContainerType.NONE,
                num_core_children=len(core),
                num_unique_skeletons=len(set(core)) if core else 0,
                dominant_skeleton_ratio=0.0,
                max_segment_size=0,
                stripped_header=had_header,
                stripped_footer=had_footer,
            )

        # Compute signals
        counts = Counter(core)
        dominant_hash, dominant_count = counts.most_common(1)[0]
        dominant_ratio = dominant_count / len(core)
        num_unique = len(counts)
        segments = self._compute_segments(core)
        max_segment_size = max(size for _, size in segments)

        result_base = {
            **base,
            "num_core_children": len(core),
            "num_unique_skeletons": num_unique,
            "dominant_skeleton_ratio": dominant_ratio,
            "max_segment_size": max_segment_size,
            "stripped_header": had_header,
            "stripped_footer": had_footer,
        }

        # Rule b: Pure homogeneous list
        if num_unique == 1:
            return ContainerClassification(
                **result_base,
                container_type=ContainerType.CONTENT,
                representative_skeleton=dominant_hash,
            )

        # Rule c: Dominant skeleton with enough items (heterogeneous content)
        if dominant_ratio >= self.CONTENT_DOMINANT_RATIO and len(core) >= self.CONTENT_MIN_ITEMS:
            return ContainerClassification(
                **result_base,
                container_type=ContainerType.CONTENT,
                representative_skeleton=dominant_hash,
            )

        # Rule d: Large homogeneous segment
        if max_segment_size >= self.CONTENT_SEGMENT_MIN:
            return ContainerClassification(
                **result_base,
                container_type=ContainerType.CONTENT,
                representative_skeleton=dominant_hash,
            )

        # Rule e: Diverse small list (Settings-style)
        if num_unique >= 3 and len(core) < self.STRUCTURAL_MAX_ITEMS:
            return ContainerClassification(
                **result_base,
                container_type=ContainerType.STRUCTURAL,
            )

        # Rule f: Two view types, one dominant
        if num_unique == 2 and dominant_ratio >= self.CONTENT_TWO_TYPE_RATIO:
            return ContainerClassification(
                **result_base,
                container_type=ContainerType.CONTENT,
                representative_skeleton=dominant_hash,
            )

        # Rule g: Default to structural
        return ContainerClassification(
            **result_base,
            container_type=ContainerType.STRUCTURAL,
        )

    def _compute_child_skeletons(
        self,
        children: list[UIElement],
        elements_by_id: dict[str, UIElement] | None = None,
    ) -> list[str]:
        """Compute skeleton hash for each child element."""
        hashes = []
        for child in children:
            skeleton = child.get_skeleton(elements_by_id)
            h = hashlib.sha256(str(skeleton).encode()).hexdigest()[:16]
            hashes.append(h)
        return hashes

    def _strip_headers_footers(
        self,
        skeletons: list[str],
    ) -> tuple[list[str], bool, bool]:
        """Remove likely header/footer items from the skeleton list.

        Header: first item whose skeleton differs from all items at positions 1..N-1.
        Footer: last item whose skeleton differs from all items at positions 0..N-2.

        Returns:
            Tuple of (core_skeletons, had_header, had_footer).
        """
        if len(skeletons) <= 2:
            return skeletons, False, False

        had_header = False
        had_footer = False
        start = 0
        end = len(skeletons)

        # Only strip if the remaining items have some homogeneity.
        # If all items are unique, stripping header/footer is meaningless.
        unique_count = len(set(skeletons))
        if unique_count >= len(skeletons):
            return skeletons, False, False

        # Check header: first item's skeleton not found in rest
        if skeletons[0] not in skeletons[1:]:
            had_header = True
            start = 1

        # Check footer: last item's skeleton not found in preceding items
        check_range = skeletons[start : end - 1]
        if check_range and skeletons[end - 1] not in check_range:
            had_footer = True
            end -= 1

        return skeletons[start:end], had_header, had_footer

    def _compute_segments(self, skeletons: list[str]) -> list[tuple[str, int]]:
        """Group consecutive identical skeletons into (hash, count) segments.

        Example: [A, A, A, B, A, A] -> [(A, 3), (B, 1), (A, 2)]
        """
        if not skeletons:
            return []

        segments: list[tuple[str, int]] = []
        current = skeletons[0]
        count = 1

        for s in skeletons[1:]:
            if s == current:
                count += 1
            else:
                segments.append((current, count))
                current = s
                count = 1
        segments.append((current, count))

        return segments

    def classify_screen_containers(
        self,
        screen: RawScreen,
    ) -> list[ContainerClassification]:
        """Find and classify all scrollable containers in a screen."""
        elements_by_id = {e.element_id: e for e in screen.elements}
        containers = screen.find_scrollable_containers()
        results = []

        for container in containers:
            children = screen.get_container_children(container)
            classification = self.classify_container(container, children, elements_by_id)
            results.append(classification)

        return results

    def annotate_fsm_states(
        self,
        fsm: AppFSM,
        screens: dict[str, RawScreen],
    ) -> None:
        """Annotate each FSM state with container classification info.

        For each state, classifies its raw screens' containers and uses
        majority vote if multiple raw screens have conflicting classifications.
        """
        for state in fsm.states.values():
            type_votes: list[ContainerType] = []
            resource_ids: list[str | None] = []
            skeleton_hashes: list[str | None] = []

            for screen_id in state.raw_screens:
                screen = screens.get(screen_id)
                if screen is None:
                    continue

                classifications = self.classify_screen_containers(screen)
                for c in classifications:
                    if c.container_type != ContainerType.NONE:
                        type_votes.append(c.container_type)
                        resource_ids.append(c.container_resource_id)
                        skeleton_hashes.append(c.representative_skeleton)

            if not type_votes:
                continue

            # Majority vote
            vote_counts = Counter(type_votes)
            winner = vote_counts.most_common(1)[0][0]
            state.container_type = winner

            # Use the first matching resource_id and skeleton_hash
            for i, t in enumerate(type_votes):
                if t == winner:
                    state.container_resource_id = resource_ids[i]
                    if winner == ContainerType.CONTENT:
                        state.item_skeleton_hash = skeleton_hashes[i]
                    break

            logger.debug(
                f"State {state.state_id} ({state.name}): "
                f"container_type={winner}, "
                f"resource_id={state.container_resource_id}"
            )

    # ------------------------------------------------------------------
    # Sub-FSM template extraction
    # ------------------------------------------------------------------

    def build_sub_fsm_templates(
        self,
        fsm: AppFSM,
        traces: list[dict[str, Any]],
        sid_to_state_id: dict[str, str],
        screens: dict[str, dict[str, Any]],
    ) -> list[SubFsmTemplate]:
        """Extract sub-FSM templates from exploration traces for CONTENT containers.

        For each CONTENT state, follows trace chains starting from click
        transitions to discover the structural sub-tree behind list items.
        Multiple items leading to the same structure produce one template.

        Args:
            fsm: The constructed FSM with container annotations.
            traces: Raw trace dicts from the exploration JSON.
            sid_to_state_id: Mapping from raw screen_id to abstract state_id.
            screens: Raw screen dicts from the exploration JSON.

        Returns:
            List of extracted SubFsmTemplate objects (also added to fsm).
        """
        templates: list[SubFsmTemplate] = []
        template_counter = 0

        content_states = [
            s for s in fsm.states.values() if s.container_type == ContainerType.CONTENT
        ]

        if not content_states:
            return templates

        for state in content_states:
            sub_tree_ids, sub_tree_traces, entry_actions = self._extract_sub_tree_from_traces(
                state.state_id, traces, sid_to_state_id
            )

            if not sub_tree_ids:
                logger.debug(f"No sub-tree found for CONTENT state {state.state_id} ({state.name})")
                continue

            # Build template states and transitions from the sub-tree
            tmpl_states: dict[str, AbstractState] = {}
            for sid in sub_tree_ids:
                if sid in fsm.states:
                    tmpl_states[sid] = copy.deepcopy(fsm.states[sid])

            tmpl_transitions: list[Transition] = []
            for t in fsm.transitions:
                in_sub = t.source in sub_tree_ids or t.source == state.state_id
                out_sub = t.target in sub_tree_ids or t.target == state.state_id
                if in_sub and out_sub:
                    tmpl_transitions.append(copy.deepcopy(t))

            if not tmpl_states:
                continue

            # Extract clicked item text for parameterization
            clicked_text = self._get_clicked_item_text(entry_actions, screens, sid_to_state_id)

            # Parameterize
            tmpl_states, tmpl_transitions, params = self._parameterize_template(
                tmpl_states, tmpl_transitions, clicked_text
            )

            template_counter += 1
            template_id = f"tmpl_{template_counter:03d}"
            entry_action = entry_actions[0] if entry_actions else {}

            template = SubFsmTemplate(
                template_id=template_id,
                entry_action=entry_action,
                states=tmpl_states,
                transitions=tmpl_transitions,
                parameters=params,
                source_container_state_id=state.state_id,
                item_skeleton_hash=state.item_skeleton_hash or "",
            )
            templates.append(template)
            fsm.add_sub_fsm_template(template)
            state.sub_fsm_template_id = template_id

            logger.info(
                f"Sub-FSM template {template_id} for state "
                f"{state.state_id} ({state.name}): "
                f"{len(tmpl_states)} states, {len(tmpl_transitions)} transitions, "
                f"params={params}"
            )

        return templates

    def _extract_sub_tree_from_traces(
        self,
        container_state_id: str,
        traces: list[dict[str, Any]],
        sid_to_state_id: dict[str, str],
    ) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
        """Follow trace chains from a CONTENT container's click transitions.

        Starts from click actions leaving the container state and follows
        the chain until returning to the container or reaching a known
        non-sub-tree state.

        Returns:
            sub_tree_state_ids: Set of state IDs forming the sub-tree.
            sub_tree_traces: Trace dicts within the sub-tree.
            entry_actions: Action dicts that entered the sub-tree.
        """
        # Sort traces by step number for sequential following
        sorted_traces = sorted(traces, key=lambda t: t.get("step_number", 0))

        sub_tree_ids: set[str] = set()
        sub_tree_traces: list[dict[str, Any]] = []
        entry_actions: list[dict[str, Any]] = []

        # Find click transitions leaving the container state
        i = 0
        while i < len(sorted_traces):
            trace = sorted_traces[i]
            source_sid = trace.get("source_screen_id", "")
            source_state = sid_to_state_id.get(source_sid)
            action = trace.get("action", {})
            action_type = action.get("action_type", action.get("type", ""))

            if source_state == container_state_id and action_type == "click":
                target_sid = trace.get("target_screen_id", "")
                target_state = sid_to_state_id.get(target_sid)

                # Skip self-loops
                if target_state is None or target_state == container_state_id:
                    i += 1
                    continue

                # Follow the chain from this click
                chain_ids: set[str] = set()
                chain_traces: list[dict[str, Any]] = []
                chain_ids.add(target_state)
                chain_traces.append(trace)

                # Walk forward through subsequent traces
                j = i + 1
                while j < len(sorted_traces):
                    next_trace = sorted_traces[j]
                    next_source_sid = next_trace.get("source_screen_id", "")
                    next_source = sid_to_state_id.get(next_source_sid)
                    next_target_sid = next_trace.get("target_screen_id", "")
                    next_target = sid_to_state_id.get(next_target_sid)

                    if next_source is None or next_target is None:
                        j += 1
                        continue

                    # Still within the sub-tree
                    if next_source in chain_ids:
                        chain_traces.append(next_trace)
                        if next_target == container_state_id:
                            break  # Returned to container — chain complete
                        chain_ids.add(next_target)
                        j += 1
                    else:
                        break  # Left the sub-tree
                    if next_target == container_state_id:
                        break

                if chain_ids:
                    sub_tree_ids |= chain_ids
                    sub_tree_traces.extend(chain_traces)
                    entry_actions.append(action)

            i += 1

        return sub_tree_ids, sub_tree_traces, entry_actions

    def _get_clicked_item_text(
        self,
        entry_actions: list[dict[str, Any]],
        screens: dict[str, dict[str, Any]],
        sid_to_state_id: dict[str, str],
    ) -> str | None:
        """Find the text of the clicked item from the entry action.

        Looks up the target element in the source screen's elements to find
        the text associated with the clicked list item.
        """
        if not entry_actions:
            return None

        action = entry_actions[0]
        target_el_id = action.get("target_element_id")
        if not target_el_id:
            return None

        # Search all screens for this element
        for screen_data in screens.values():
            elements = screen_data.get("interactable_elements", [])
            for el in elements:
                if el.get("element_id") == target_el_id:
                    text = el.get("text")
                    if text and text.strip():
                        return text.strip()
                    cd = el.get("content_description")
                    if cd and cd.strip():
                        return cd.strip()

        return None

    def _parameterize_template(
        self,
        template_states: dict[str, AbstractState],
        template_transitions: list[Transition],
        clicked_item_text: str | None,
    ) -> tuple[dict[str, AbstractState], list[Transition], list[str]]:
        """Replace content-specific values with $item.* parameter placeholders.

        Scans state names and transition metadata for occurrences of the clicked
        item's text and replaces them with parameter references.

        Returns:
            parameterized_states, parameterized_transitions, parameter_names
        """
        params: list[str] = []

        if not clicked_item_text or len(clicked_item_text) < 2:
            return template_states, template_transitions, params

        param_name = "$item.name"
        params.append(param_name)
        pattern = re.compile(re.escape(clicked_item_text), re.IGNORECASE)

        # Parameterize state names
        for state in template_states.values():
            if pattern.search(state.name):
                state.name = pattern.sub(param_name, state.name)

        # Parameterize transition action metadata
        for t in template_transitions:
            for key in ("target", "text"):
                val = t.action.get(key)
                if isinstance(val, str) and pattern.search(val):
                    t.action[key] = pattern.sub(param_name, val)

        return template_states, template_transitions, params
