"""Stage 2: State Abstraction — container classification.

Classifies scrollable containers as STRUCTURAL (fixed menu) or CONTENT
(dynamic list of homogeneous items). Uses multi-signal analysis of child
widget skeletons to handle real-world edge cases: heterogeneous content
lists, section headers, mixed containers.
"""

from __future__ import annotations

import hashlib
from collections import Counter

from loguru import logger
from pydantic import BaseModel

from vigil.models.fsm import AppFSM, ContainerType
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
