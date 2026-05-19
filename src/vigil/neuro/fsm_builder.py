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
    ProvenanceEntry,
    SubFsmTemplate,
    Transition,
    canonical_action_key,
)
from vigil.neuro.app_prior import AppPrior

_REFINED_SECONDARY_MARKER = "::secondary:"


class FsmBuilder:
    """Build an AppFSM from an exploration trace JSON file.

    Args:
        app_package: Android package name.
    """

    def __init__(self, app_package: str) -> None:
        self._app_package = app_package

    @staticmethod
    def _trace_transition_trust(metadata: dict[str, Any]) -> tuple[bool, float, bool]:
        """Return (skip, confidence, low_trust) for a trace metadata dict."""
        if not metadata:
            return False, 1.0, False

        scope_pre = metadata.get("scope_pre")
        scope_post = metadata.get("scope_post")
        scope_pre_value = str(scope_pre) if scope_pre is not None else None
        scope_post_value = str(scope_post) if scope_post is not None else None

        if scope_pre_value == "android_system" or scope_post_value == "android_system":
            return True, 0.0, False

        if metadata.get("low_trust_scope") is True:
            if scope_pre_value == "in_app" and scope_post_value == "in_app":
                return False, 0.5, True
            return True, 0.0, False

        return False, 1.0, False

    @staticmethod
    def _merge_transition_trust(existing: Transition, incoming: Transition) -> None:
        """Combine duplicate-edge confidence without promoting low-trust-only edges."""
        existing.observed_count += incoming.observed_count
        # Provenance lists are append-only; preserve every supporting record so the
        # validator (and future replay aggregation) can reconstruct evidence.
        if incoming.provenance:
            existing.provenance.extend(incoming.provenance)
        if existing.low_trust and incoming.low_trust:
            existing.confidence = min(existing.confidence, incoming.confidence)
            return

        if existing.low_trust != incoming.low_trust:
            existing.low_trust = False
            existing.confidence = max(existing.confidence, incoming.confidence)
            return

        existing.confidence = max(existing.confidence, incoming.confidence)

    def build_from_trace(
        self,
        trace_path: Path,
        include_self_loops: bool = False,
        app_prior: AppPrior | None = None,
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
        # Drop sentinel traces emitted by the explorer when cold-start, replay,
        # or action execution failed — their target_state_id is a reserved
        # uppercase label, not a real state hash.
        _SENTINELS = frozenset({"COLD_START_FAILED", "ACTION_FAILED", "LEFT_APP"})  # noqa: N806
        raw_traces = [
            t for t in data.get("traces", []) if t.get("target_state_id") not in _SENTINELS
        ]
        # Make raw_screens available to downstream helpers (e.g.,
        # _classify_containers_structural) without threading it as a parameter.
        self._raw_screens = raw_screens

        # Step 1: Build canonical states. If the trace ships ``state_id`` per
        # screen (new explorer format), trust it directly — it's a functional,
        # text-anchored identity already. Otherwise fall back to the original
        # fingerprint-based dedup for backward compatibility with older traces.
        if self._trace_has_state_ids(raw_screens):
            sid_to_state_id, states = self._build_states_from_state_ids(
                raw_screens, trace_path.parent, app_prior
            )
            fp_to_state_id = {}
        else:
            fp_to_state_id, states = self._build_states(raw_screens, trace_path.parent, app_prior)
            sid_to_state_id = self._build_screen_mapping(raw_screens, fp_to_state_id)

        # Step 2: Build transitions from traces
        transitions = self._build_transitions(
            raw_traces, sid_to_state_id, include_self_loops, raw_screens
        )

        # Step 3: Merge duplicate transitions
        transitions = self._merge_transitions(transitions)

        # Step 3.5: APE-style refinement — split or downgrade conflicting successors
        transitions = self._refine_conflicting_successors(
            states, sid_to_state_id, transitions, raw_screens
        )

        # Step 4: Detect initial state (prefers manifest launcher activity)
        initial_state = self._detect_initial_state(
            raw_traces, sid_to_state_id, states=states, app_prior=app_prior
        )

        # Step 5: Disambiguate duplicate state names
        self._disambiguate_names(states)

        # Step 6: Infer hierarchy from activity names
        self._infer_hierarchy(states)

        # Step 7: Assemble FSM
        fsm = AppFSM(app_package=self._app_package)
        fsm.initial_state = initial_state

        for state in states.values():
            fsm.add_state(state)
        for t in transitions:
            fsm.add_transition(t)

        # Flush refinement diagnostics buffered on the builder into the FSM.
        refinement_log = getattr(self, "_refinement_log", None)
        if refinement_log:
            fsm.evolution_log.extend(refinement_log)

        # Step 8: Post-processing — merge duplicates and remove error states
        merged = self._merge_scroll_duplicates(fsm)
        removed = self._remove_error_states(fsm)
        if merged or removed:
            logger.info(
                f"Post-processing: merged {merged} duplicate states, removed {removed} error states"
            )

        # Step 9: Build Sub-FSM templates for dynamic containers
        structurally_labeled = self._classify_containers_structural(fsm)
        if structurally_labeled:
            logger.info(
                f"Structural container classification: {structurally_labeled} DYNAMIC states"
            )
        templates_created = self._build_sub_fsm_templates(fsm)
        if templates_created:
            logger.info(f"Created {templates_created} Sub-FSM templates")

        # Step 10: Detect dialog states and assign hierarchy
        dialogs = self._detect_dialog_states(fsm, raw_screens, sid_to_state_id)
        if dialogs:
            logger.info(f"Detected {dialogs} dialog states (COMPONENT level)")

        # Step 11: Add inferred dismiss transitions for dialogs
        dismiss = self._add_dialog_dismiss_transitions(fsm, raw_screens, sid_to_state_id)
        if dismiss:
            logger.info(f"Added {dismiss} inferred dialog dismiss transitions")

        # Step 12: Complete tab navigation transitions
        tabs = self._complete_tab_transitions(fsm, raw_screens, sid_to_state_id)
        if tabs:
            logger.info(f"Added {tabs} tab navigation transitions")

        logger.info(
            f"FSM built: {len(fsm.states)} states, {len(fsm.transitions)} transitions, "
            f"initial_state={initial_state}"
        )
        return fsm

    # --- Post-processing: duplicate/error state cleanup ---

    def _merge_scroll_duplicates(self, fsm: AppFSM) -> int:
        """Merge states that share (activity_name, base_name) AND are
        structurally compatible.

        Earlier behavior merged every same-name pair, which silently
        collapsed semantically distinct screens that happened to share a
        toolbar title. The hardened policy requires:

          1. Same ``activity_name``.
          2. Same ``base_name`` (after stripping any ``#N`` scroll suffix).
          3. Compatible structural skeletons: either identical structural
             fingerprints, or fingerprints that agree on the non-scrollable
             skeleton (so the only differences live inside scrollable
             subtrees, which is the genuine scroll-duplicate signal).

        Same-name candidates with incompatible skeletons are kept separate
        and recorded as builder diagnostics so the validator surfaces them.
        Self-loops produced by redirecting transitions are dropped only
        when they are CLICK no-ops; SCROLL_UP / SCROLL_DOWN / INPUT_TEXT
        and toggle self-loops are preserved because they remain legal
        affordances after the merge.

        Returns:
            Number of states merged away.
        """
        import re

        groups: dict[tuple[str | None, str], list[str]] = defaultdict(list)
        for state in fsm.states.values():
            base_name = re.sub(r"\s*#\d+$", "", state.name)
            key = (state.activity_name, base_name)
            groups[key].append(state.state_id)

        merged_count = 0
        diagnostics: list[dict[str, Any]] = []
        _MEANINGFUL_SELF_LOOPS = {"scroll_up", "scroll_down", "input_text"}  # noqa: N806

        def _fp(state_id: str) -> str:
            return fsm.states[state_id].fingerprint or ""

        for (_activity, base_name), state_ids in groups.items():
            if len(state_ids) <= 1:
                continue

            # Partition into compatibility clusters by structural fingerprint.
            # States with identical fingerprints are presumed merge-safe;
            # states with distinct fingerprints under the same name are kept
            # apart and reported as diagnostics.
            clusters: dict[str, list[str]] = defaultdict(list)
            for sid in state_ids:
                clusters[_fp(sid)].append(sid)

            if len(clusters) > 1:
                logger.warning(
                    f"Skipping merge for '{base_name}' in activity {_activity}: "
                    f"{len(clusters)} incompatible structural fingerprints "
                    f"({sum(len(v) for v in clusters.values())} states)"
                )
                diagnostics.append(
                    {
                        "activity": _activity,
                        "base_name": base_name,
                        "clusters": {fp: list(ids) for fp, ids in clusters.items()},
                    }
                )

            for fp_key, cluster_ids in clusters.items():
                if len(cluster_ids) <= 1:
                    continue
                canonical_id = cluster_ids[0]
                duplicates = cluster_ids[1:]

                for dup_id in duplicates:
                    dup_state = fsm.states[dup_id]
                    fsm.states[canonical_id].raw_screens.extend(dup_state.raw_screens)

                fsm.states[canonical_id].name = base_name

                redirect_map = {dup_id: canonical_id for dup_id in duplicates}
                new_transitions: list[Transition] = []
                seen_keys: set[tuple[str, str, tuple[tuple[str, object], ...]]] = set()

                for t in fsm.transitions:
                    source = redirect_map.get(t.source, t.source)
                    target = redirect_map.get(t.target, t.target)
                    if source == target:
                        action_dict = t.action if isinstance(t.action, dict) else {}
                        atype = (
                            action_dict.get("type") or action_dict.get("action_type") or ""
                        ).lower()
                        if atype not in _MEANINGFUL_SELF_LOOPS:
                            # Drop ordinary CLICK no-op self-loops. Keep
                            # scroll / input / toggle self-loops below.
                            continue
                    key = (source, target, canonical_action_key(t.action))
                    if key in seen_keys:
                        for existing in new_transitions:
                            e_src = existing.source
                            e_tgt = existing.target
                            e_key = canonical_action_key(existing.action)
                            if (e_src, e_tgt, e_key) == key:
                                self._merge_transition_trust(existing, t)
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
                                low_trust=t.low_trust,
                                observed_count=t.observed_count,
                                provenance=list(t.provenance),
                            )
                        )

                for dup_id in duplicates:
                    if dup_id in fsm.states:
                        del fsm.states[dup_id]
                    if dup_id in fsm.graph:
                        fsm.graph.remove_node(dup_id)

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
                            low_trust=t.low_trust,
                            observed_count=t.observed_count,
                        )

                if fsm.initial_state in redirect_map:
                    fsm.initial_state = redirect_map[fsm.initial_state]

                merged_count += len(duplicates)
                logger.debug(
                    f"Merged {len(duplicates)} duplicates of '{base_name}' "
                    f"(fp={fp_key[:6]}) into {canonical_id}"
                )

        if diagnostics:
            self._merge_diagnostics = diagnostics  # type: ignore[attr-defined]
        return merged_count

    def _remove_error_states(self, fsm: AppFSM) -> int:
        """Remove transient error/system states from the FSM."""
        from vigil.core.platform_priors import get_error_patterns

        patterns = get_error_patterns()
        to_remove: list[str] = []
        for state in fsm.states.values():
            name_lower = state.name.lower()
            for pattern in patterns:
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

    def _detect_dialog_states(
        self,
        fsm: AppFSM,
        raw_screens: dict[str, Any],
        sid_to_state_id: dict[str, str],
    ) -> int:
        """Detect dialog states and set hierarchy_level=COMPONENT with parent."""
        detected = 0

        screen_elements_cache: dict[str, list[dict[str, Any]]] = {}
        for sid, screen in raw_screens.items():
            screen_elements_cache[sid] = screen.get(
                "interactable_elements", screen.get("elements", [])
            )

        for state_id, state in fsm.states.items():
            if self._is_dialog_state(state, raw_screens, screen_elements_cache):
                state.hierarchy_level = HierarchyLevel.COMPONENT

                parent_id = self._find_dialog_parent(state_id, fsm)
                if parent_id:
                    state.parent_state = parent_id

                detected += 1

        return detected

    def _is_dialog_state(
        self,
        state: AbstractState,
        raw_screens: dict[str, Any],
        screen_elements_cache: dict[str, list[dict[str, Any]]],
    ) -> bool:
        """Check if a state represents a dialog/picker overlay."""
        from vigil.core.platform_priors import get_dialog_indicators

        indicators = get_dialog_indicators()
        dialog_classes = set(indicators.get("classes", []))
        dialog_rids = {rid.lower() for rid in indicators.get("resource_ids", [])}

        for sid in state.raw_screens:
            screen = raw_screens.get(sid, {})
            metadata = screen.get("metadata", {})
            if metadata.get("has_modal"):
                return True

            elements = screen_elements_cache.get(sid, [])
            rids = {(el.get("resource_id") or "").lower() for el in elements}
            if rids & dialog_rids:
                return True

            classes = {(el.get("class_name") or "").rsplit(".", 1)[-1] for el in elements}
            if classes & dialog_classes:
                return True

        return False

    @staticmethod
    def _find_dialog_parent(dialog_state_id: str, fsm: AppFSM) -> str | None:
        """Find the most likely parent state for a dialog."""
        source_counts: dict[str, int] = defaultdict(int)
        for t in fsm.transitions:
            if t.target == dialog_state_id and t.source != dialog_state_id:
                source_counts[t.source] += 1
        if not source_counts:
            return None
        return max(source_counts, key=source_counts.get)  # type: ignore[arg-type]

    def _add_dialog_dismiss_transitions(
        self,
        fsm: AppFSM,
        raw_screens: dict[str, Any],
        sid_to_state_id: dict[str, str],
    ) -> int:
        """Add inferred dismiss transitions for dialog states without them."""
        added = 0

        for state_id, state in fsm.states.items():
            if state.hierarchy_level != HierarchyLevel.COMPONENT:
                continue
            parent_id = state.parent_state
            if not parent_id:
                continue

            has_dismiss = any(
                t.source == state_id and t.target == parent_id for t in fsm.transitions
            )
            if has_dismiss:
                continue

            elements: list[dict[str, Any]] = []
            for sid in state.raw_screens:
                screen = raw_screens.get(sid, {})
                elements = screen.get("interactable_elements", screen.get("elements", []))
                if elements:
                    break

            rids = {(el.get("resource_id") or "").lower() for el in elements}
            if "android:id/button1" in rids or "android:id/button2" in rids:
                t = Transition(
                    source=state_id,
                    target=parent_id,
                    action={"type": "click", "target_text": "OK/Cancel"},
                    confidence=0.5,
                    observed_count=0,
                    provenance=[
                        ProvenanceEntry(
                            trace_step_index=-1,
                            confidence_source="inferred_dialog",
                        )
                    ],
                )
                fsm.add_transition(t)
                added += 1
            else:
                t = Transition(
                    source=state_id,
                    target=parent_id,
                    action={"type": "navigate_back"},
                    confidence=0.5,
                    observed_count=0,
                    provenance=[
                        ProvenanceEntry(
                            trace_step_index=-1,
                            confidence_source="inferred_dialog",
                        )
                    ],
                )
                fsm.add_transition(t)
                added += 1

        return added

    def _complete_tab_transitions(
        self,
        fsm: AppFSM,
        raw_screens: dict[str, Any],
        sid_to_state_id: dict[str, str],
    ) -> int:
        """Add missing bidirectional transitions between tab-navigable states."""
        from vigil.core.platform_priors import get_tab_indicators

        tab_classes = set(get_tab_indicators())

        activity_groups: dict[str, list[str]] = defaultdict(list)
        for state in fsm.states.values():
            if state.activity_name and state.hierarchy_level == HierarchyLevel.FRAGMENT:
                activity_groups[state.activity_name].append(state.state_id)

        tab_groups: list[list[str]] = []
        for _activity, state_ids in activity_groups.items():
            if len(state_ids) < 2:
                continue
            has_tabs = False
            for sid in state_ids:
                state = fsm.states[sid]
                for raw_sid in state.raw_screens:
                    screen = raw_screens.get(raw_sid, {})
                    elements = screen.get("interactable_elements", screen.get("elements", []))
                    for el in elements:
                        short_cls = (el.get("class_name") or "").rsplit(".", 1)[-1]
                        if short_cls in tab_classes:
                            has_tabs = True
                            break
                    if has_tabs:
                        break
                if has_tabs:
                    break
            if has_tabs:
                tab_groups.append(state_ids)

        added = 0
        existing = {(t.source, t.target) for t in fsm.transitions}

        for group in tab_groups:
            for i, sid_a in enumerate(group):
                for sid_b in group[i + 1 :]:
                    if (sid_a, sid_b) not in existing:
                        fsm.add_transition(
                            Transition(
                                source=sid_a,
                                target=sid_b,
                                action={
                                    "type": "click",
                                    "target_text": fsm.states[sid_b].name,
                                },
                                confidence=0.5,
                                observed_count=0,
                                provenance=[
                                    ProvenanceEntry(
                                        trace_step_index=-1,
                                        confidence_source="inferred_tab",
                                    )
                                ],
                            )
                        )
                        added += 1
                    if (sid_b, sid_a) not in existing:
                        fsm.add_transition(
                            Transition(
                                source=sid_b,
                                target=sid_a,
                                action={
                                    "type": "click",
                                    "target_text": fsm.states[sid_a].name,
                                },
                                confidence=0.5,
                                observed_count=0,
                                provenance=[
                                    ProvenanceEntry(
                                        trace_step_index=-1,
                                        confidence_source="inferred_tab",
                                    )
                                ],
                            )
                        )
                        added += 1

        return added

    def _classify_containers_structural(self, fsm: AppFSM) -> int:
        """Fallback container classification when the grounder wasn't run.

        Labels a state ``DYNAMIC`` when it (a) contains a scrollable element
        and (b) has at least two outgoing click transitions whose targets
        share the same functional fingerprint — the structural signature of
        a list-of-items page. Only runs on states still labeled ``NONE``
        so grounder decisions are preserved.

        Returns:
            Number of states newly labeled DYNAMIC.
        """
        raw_screens = getattr(self, "_raw_screens", {}) or {}
        labeled = 0

        for sid, state in fsm.states.items():
            if state.container_type != ContainerType.NONE:
                continue

            has_scrollable = False
            for rsid in state.raw_screens:
                screen = raw_screens.get(rsid, {})
                elements = screen.get("interactable_elements", screen.get("elements", []))
                if any(e.get("is_scrollable") for e in elements):
                    has_scrollable = True
                    break
            if not has_scrollable:
                continue

            targets = self._find_same_fingerprint_targets(fsm, sid)
            if len(targets) >= 2:
                state.container_type = ContainerType.DYNAMIC
                labeled += 1

        return labeled

    def _build_sub_fsm_templates(self, fsm: AppFSM) -> int:
        """Create Sub-FSM templates for verified dynamic containers.

        Detects states with container_type=DYNAMIC that have multiple outgoing
        click transitions whose targets share the same ``structural_fingerprint``
        (the text-agnostic structural skeleton — the correct identity for
        "list of structurally-identical detail pages" patterns like an email
        inbox or a Wi-Fi network list).

        Collapses those N transitions into a single SubFsmTemplate reference:
        keeps one representative target state (with its descendant subgraph
        up to depth 5), removes the N-1 duplicates, redirects their incoming
        transitions to the representative, and stamps
        ``sub_fsm_template_id`` on the container state.

        Emits a diagnostic log at INFO when 0 templates are created but
        DYNAMIC containers exist — separates a data-coverage gap (few
        items observed per container) from a code bug.

        Returns:
            Number of templates created.
        """
        templates_created = 0
        dynamic_state_ids = [
            sid for sid, s in fsm.states.items() if s.container_type == ContainerType.DYNAMIC
        ]

        for state_id in list(dynamic_state_ids):
            state = fsm.states.get(state_id)
            if state is None:
                continue

            click_targets = self._find_same_fingerprint_targets(fsm, state_id)
            if len(click_targets) < 2:
                continue

            shared_fp = click_targets[0][1]
            representative_target_id = click_targets[0][0]
            if representative_target_id == fsm.initial_state:
                # Defensive: never collapse the entry state away.
                continue

            template_id = f"tmpl_{state_id}"
            rep_state = fsm.states.get(representative_target_id)

            # Template subgraph: representative + DFS descendants (bounded
            # depth 5) excluding any transition that returns to the container
            # source state. Prevents the whole FSM from being swallowed into
            # one template when navigate-back forms a back-edge.
            template_states: dict[str, AbstractState] = {}
            if rep_state is not None:
                template_states[representative_target_id] = rep_state
                self._collect_template_subgraph(
                    fsm,
                    root_id=representative_target_id,
                    exclude_id=state_id,
                    max_depth=5,
                    out=template_states,
                )
            template_transitions: list[Transition] = [
                t
                for t in fsm.transitions
                if t.source in template_states and t.target in template_states
            ]

            collapsed_target_id_set = {tid for tid, _ in click_targets}
            parameter_schema = self._infer_parameter_schema(fsm, state_id, collapsed_target_id_set)
            item_skeleton = rep_state.structural_fingerprint or "" if rep_state else ""

            tmpl = SubFsmTemplate(
                template_id=template_id,
                source_state_id=state_id,
                entry_fingerprint=shared_fp,
                states=template_states,
                transitions=template_transitions,
                parameter_schema=parameter_schema,
                item_skeleton=item_skeleton,
            )
            fsm.sub_fsm_templates[template_id] = tmpl
            state.sub_fsm_template_id = template_id
            templates_created += 1

            # Collapse: redirect source→collapsed transitions to the
            # representative, then remove non-representative states from the
            # FSM (states dict + graph nodes). Their non-source-originating
            # transitions are pruned during the post-pass below.
            collapsed_others = {tid for tid, _ in click_targets[1:]}
            for t in fsm.transitions:
                if t.source == state_id and t.target in collapsed_others:
                    t.target = representative_target_id

            for tid in collapsed_others:
                if tid in fsm.states:
                    del fsm.states[tid]
                if tid in fsm.graph:
                    fsm.graph.remove_node(tid)

            # Drop any lingering transitions that still touch removed states,
            # then rebuild graph edges from the authoritative transitions
            # list (same post-pass pattern used by _merge_scroll_duplicates).
            fsm.transitions = [
                t
                for t in fsm.transitions
                if t.source not in collapsed_others and t.target not in collapsed_others
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
                        low_trust=t.low_trust,
                        observed_count=t.observed_count,
                    )

            logger.debug(
                f"Template {template_id}: collapsed {len(click_targets)} "
                f"transitions from {state_id} (kept {representative_target_id})"
            )

        if templates_created == 0 and dynamic_state_ids:
            # Diagnostic: show per-container click-target structural_fp
            # distribution so the operator can tell coverage gap from a
            # matching bug.
            logger.info(
                f"No sub-FSM templates created despite "
                f"{len(dynamic_state_ids)} DYNAMIC container(s). "
                "Per-container click-target structural fingerprint counts:"
            )
            for sid in dynamic_state_ids:
                counts: dict[str, int] = defaultdict(int)
                for t in fsm.transitions:
                    if t.source != sid or t.action.get("type") != "click":
                        continue
                    tgt = fsm.states.get(t.target)
                    if tgt is None:
                        continue
                    sfp = tgt.structural_fingerprint or "<none>"
                    counts[sfp] += 1
                summary = ", ".join(f"{k[:8]}:{v}" for k, v in counts.items())
                logger.info(f"  {sid[:6]}  {summary or '<no click targets>'}")

        return templates_created

    @staticmethod
    def _collect_template_subgraph(
        fsm: AppFSM,
        *,
        root_id: str,
        exclude_id: str,
        max_depth: int,
        out: dict[str, AbstractState],
    ) -> None:
        """DFS from ``root_id`` up to ``max_depth`` hops, skipping any
        transition whose target is ``exclude_id`` (the container we came
        from — a navigate-back edge would otherwise swallow the whole FSM).
        Populates ``out`` in place."""
        stack: list[tuple[str, int]] = [(root_id, 0)]
        while stack:
            sid, depth = stack.pop()
            if depth >= max_depth:
                continue
            for t in fsm.transitions:
                if t.source != sid:
                    continue
                if t.target == exclude_id or t.target == sid:
                    continue
                child = fsm.states.get(t.target)
                if child is None or t.target in out:
                    continue
                out[t.target] = child
                stack.append((t.target, depth + 1))

    @staticmethod
    def _infer_parameter_schema(
        fsm: AppFSM,
        source_state_id: str,
        collapsed_target_ids: set[str],
    ) -> dict[str, str]:
        """If the collapsed source-transitions click different
        ``target_text`` values, emit ``{"item_name": "string"}``. Otherwise
        return ``{}`` — an empty schema means the template carries a single
        shape with no varying parameter."""
        texts: set[str] = set()
        for t in fsm.transitions:
            if t.source != source_state_id:
                continue
            if t.action.get("type") != "click":
                continue
            if t.target not in collapsed_target_ids:
                continue
            txt = (t.action.get("target_text") or "").strip()
            if txt:
                texts.add(txt)
        return {"item_name": "string"} if len(texts) >= 2 else {}

    @staticmethod
    def _find_same_fingerprint_targets(fsm: AppFSM, source_id: str) -> list[tuple[str, str]]:
        """Find click transitions from ``source_id`` whose targets share a
        ``structural_fingerprint`` (the text-agnostic structural skeleton).

        Grouping on ``structural_fingerprint`` rather than the text-anchored
        ``fingerprint`` is the correct use-case for list-like containers:
        every row clicks into a structurally-identical detail page but with
        distinct row text, so text-anchored identities diverge while the
        structural one collapses them.

        Returns:
            List of (target_state_id, structural_fingerprint) for the
            largest group of same-structural-fingerprint targets. Empty if
            no group has >= 2 members.
        """
        fp_groups: dict[str, list[str]] = defaultdict(list)
        for t in fsm.transitions:
            if t.source != source_id:
                continue
            if t.action.get("type") != "click":
                continue
            target = fsm.states.get(t.target)
            if target is None:
                continue
            sfp = target.structural_fingerprint
            if not sfp:
                continue
            fp_groups[sfp].append(t.target)

        best_group: list[str] = []
        best_fp = ""
        for fp, targets in fp_groups.items():
            if len(targets) > len(best_group):
                best_group = targets
                best_fp = fp

        if len(best_group) < 2:
            return []
        return [(tid, best_fp) for tid in best_group]

    # --- APE-style refinement ---

    def _refine_conflicting_successors(
        self,
        states: dict[str, AbstractState],
        sid_to_state_id: dict[str, str],
        transitions: list[Transition],
        raw_screens: dict[str, Any],
    ) -> list[Transition]:
        """Split or downgrade states where the same canonical action yields
        conflicting successors (the APE-style refinement step).

        After ``_merge_transitions``, two outgoing edges of the same source
        with the same ``canonical_action_key`` but different targets prove the
        abstract state actually represents two distinct concrete situations.
        We try to split, but only conservatively:

          1. Every conflicting edge must carry provenance pointing at a real
             ``source_screen_id``; otherwise we cannot attribute the conflict
             to specific raw screens and we downgrade.
          2. The raw source screens that lead to each target must be
             distinguishable by stable *secondary* features (activity,
             modal flag, non-editable text anchors, content descriptions)
             that are NOT already captured by the primary fingerprint. If the
             screens are indistinguishable, we keep both edges, downgrade
             their confidence to ``min(current, 0.5)`` and set
             ``low_trust=True``.

        Diagnostics are buffered on the builder as ``self._refinement_log``;
        the caller copies them into ``AppFSM.evolution_log`` after the FSM is
        assembled (this method runs before the AppFSM exists).
        """
        self._refinement_log = []

        conflict_groups: dict[tuple[str, tuple[tuple[str, Any], ...]], list[Transition]] = (
            defaultdict(list)
        )
        for t in transitions:
            conflict_groups[(t.source, canonical_action_key(t.action))].append(t)

        working = list(transitions)

        for (source_id, action_key), conflicting in conflict_groups.items():
            distinct_targets = {t.target for t in conflicting}
            if len(distinct_targets) <= 1:
                continue
            source_state = states.get(source_id)
            if source_state is None:
                continue

            target_to_screens: dict[str, set[str]] = defaultdict(set)
            attribution_complete = True
            for t in conflicting:
                screens_for_t = {p.source_screen_id for p in t.provenance if p.source_screen_id}
                if not screens_for_t:
                    attribution_complete = False
                    break
                target_to_screens[t.target].update(screens_for_t)

            if not attribution_complete:
                self._downgrade_conflict(conflicting, source_id, action_key, "missing_provenance")
                continue

            sig_to_target: dict[Any, str] = {}
            ambiguous = False
            for tgt, screens in target_to_screens.items():
                for sid in screens:
                    sig = self._secondary_feature_signature(raw_screens.get(sid, {}))
                    seen = sig_to_target.get(sig)
                    if seen is not None and seen != tgt:
                        ambiguous = True
                        break
                    sig_to_target[sig] = tgt
                if ambiguous:
                    break

            if ambiguous:
                self._downgrade_conflict(
                    conflicting, source_id, action_key, "indistinguishable_secondary_features"
                )
                continue

            working = self._split_state_for_conflict(
                states=states,
                sid_to_state_id=sid_to_state_id,
                transitions=working,
                raw_screens=raw_screens,
                source_state=source_state,
                conflicting=conflicting,
                target_to_screens=target_to_screens,
                action_key=action_key,
            )

        return working

    def _downgrade_conflict(
        self,
        conflicting: list[Transition],
        source_id: str,
        action_key: tuple[tuple[str, Any], ...],
        reason: str,
    ) -> None:
        """Mark all conflicting edges low-trust and cap their confidence at 0.5."""
        for t in conflicting:
            t.confidence = min(t.confidence, 0.5)
            t.low_trust = True
        self._refinement_log.append(
            {
                "action": "downgrade",
                "source_state": source_id,
                "canonical_action_key": [list(item) for item in action_key],
                "targets": sorted({t.target for t in conflicting}),
                "reason": reason,
            }
        )

    def _split_state_for_conflict(
        self,
        *,
        states: dict[str, AbstractState],
        sid_to_state_id: dict[str, str],
        transitions: list[Transition],
        raw_screens: dict[str, Any],
        source_state: AbstractState,
        conflicting: list[Transition],
        target_to_screens: dict[str, set[str]],
        action_key: tuple[tuple[str, Any], ...],
    ) -> list[Transition]:
        """Split ``source_state`` so that each conflicting target gets its own
        specialized source state. Keeps the first target on the original state
        and creates ``s_xxx__refined_N`` siblings for the rest.

        The split must keep three things in sync:
          - ``states`` (new entries added)
          - ``sid_to_state_id`` (raw screens re-attributed)
          - ``transitions`` (conflicting edges re-sourced, other outgoing
            edges replicated, incoming edges re-targeted via provenance)
        """
        source_id = source_state.state_id
        target_order = sorted(target_to_screens.keys())
        keep_target = target_order[0]
        new_state_ids: dict[str, str] = {}
        base_fingerprint = source_state.fingerprint
        base_structural_fingerprint = source_state.structural_fingerprint
        target_secondary_hashes = {
            tgt: self._secondary_feature_group_hash(screens, raw_screens)
            for tgt, screens in target_to_screens.items()
        }

        keep_hash = target_secondary_hashes[keep_target]
        source_state.fingerprint = self._with_secondary_feature_hash(base_fingerprint, keep_hash)
        source_state.structural_fingerprint = self._with_secondary_feature_hash(
            base_structural_fingerprint, keep_hash
        )

        for idx, tgt in enumerate(target_order[1:], start=1):
            base = f"{source_id}__refined_{idx}"
            new_state_id = base
            collision = 1
            while new_state_id in states:
                collision += 1
                new_state_id = f"{base}_{collision}"
            screens_for_new = target_to_screens[tgt]
            secondary_hash = target_secondary_hashes[tgt]
            new_state = AbstractState(
                state_id=new_state_id,
                name=f"{source_state.name} #refined-{idx}",
                fingerprint=self._with_secondary_feature_hash(base_fingerprint, secondary_hash),
                structural_fingerprint=self._with_secondary_feature_hash(
                    base_structural_fingerprint, secondary_hash
                ),
                hierarchy_level=source_state.hierarchy_level,
                parent_state=source_state.parent_state,
                activity_name=source_state.activity_name,
                invariants=list(source_state.invariants),
                raw_screens=sorted(screens_for_new),
                container_type=source_state.container_type,
                container_resource_id=source_state.container_resource_id,
                semantic_profile=source_state.semantic_profile,
                state_invariants=list(source_state.state_invariants),
                invariant_confidence=source_state.invariant_confidence,
                sub_fsm_template_id=source_state.sub_fsm_template_id,
            )
            states[new_state_id] = new_state
            new_state_ids[tgt] = new_state_id
            for sid in screens_for_new:
                sid_to_state_id[sid] = new_state_id

        relocated_screens = {s for tgt in target_order[1:] for s in target_to_screens[tgt]}
        source_state.raw_screens = [
            s for s in source_state.raw_screens if s not in relocated_screens
        ]

        # 1. Update conflicting transitions: re-source the ones whose target is not the kept one.
        for t in conflicting:
            if t.target == keep_target:
                continue
            new_src = new_state_ids.get(t.target)
            if new_src:
                t.source = new_src

        # 2. Partition other outgoing transitions by provenance source screen.
        target_to_state_id = {keep_target: source_id, **new_state_ids}
        state_to_raw_screens = {
            target_to_state_id[tgt]: set(screens) for tgt, screens in target_to_screens.items()
        }
        other_outgoing = [t for t in transitions if t.source == source_id and t not in conflicting]
        rebuilt_outgoing: list[Transition] = []
        for t in other_outgoing:
            source_screen_ids = {p.source_screen_id for p in t.provenance if p.source_screen_id}
            matched_states = [
                state_id
                for state_id, screens in state_to_raw_screens.items()
                if source_screen_ids & screens
            ]
            unattributed = not source_screen_ids
            destinations = list(state_to_raw_screens) if unattributed else matched_states
            spans_multiple_siblings = len(matched_states) > 1

            for state_id in destinations:
                if unattributed:
                    provenance = list(t.provenance)
                else:
                    provenance = [
                        p
                        for p in t.provenance
                        if p.source_screen_id in state_to_raw_screens[state_id]
                    ]
                low_trust_partition = unattributed or spans_multiple_siblings
                # Low-trust partitions keep inferred/broad evidence usable without
                # preserving the original high-confidence edge on every sibling.
                rebuilt_outgoing.append(
                    Transition(
                        source=state_id,
                        target=t.target,
                        action=dict(t.action),
                        guard=t.guard,
                        confidence=min(t.confidence, 0.5) if low_trust_partition else t.confidence,
                        low_trust=t.low_trust or low_trust_partition,
                        observed_count=len(provenance) if provenance else t.observed_count,
                        provenance=provenance,
                    )
                )

        transitions = [t for t in transitions if t not in other_outgoing] + rebuilt_outgoing

        # 3. Redirect incoming transitions based on provenance.target_screen_id.
        for t in transitions:
            if t.target != source_id or not t.provenance:
                continue
            redirects = set()
            for entry in t.provenance:
                if entry.target_screen_id and entry.target_screen_id in sid_to_state_id:
                    redirects.add(sid_to_state_id[entry.target_screen_id])
            redirects.discard(source_id)
            if len(redirects) == 1:
                t.target = next(iter(redirects))

        self._refinement_log.append(
            {
                "action": "split",
                "source_state": source_id,
                "new_states": list(new_state_ids.values()),
                "canonical_action_key": [list(item) for item in action_key],
                "targets": target_order,
            }
        )
        return transitions

    @staticmethod
    def _with_secondary_feature_hash(fingerprint: str | None, secondary_hash: str) -> str | None:
        """Attach a split-specific secondary hash to a state fingerprint."""
        if not fingerprint:
            return fingerprint
        base = fingerprint.split(_REFINED_SECONDARY_MARKER, 1)[0]
        return f"{base}{_REFINED_SECONDARY_MARKER}{secondary_hash}"

    @staticmethod
    def _secondary_feature_signature_hash(signature: tuple[Any, ...]) -> str:
        return hashlib.sha256(repr(signature).encode()).hexdigest()[:12]

    @classmethod
    def _secondary_feature_group_hash(
        cls, screen_ids: set[str], raw_screens: dict[str, Any]
    ) -> str:
        signature_hashes = sorted(
            cls._secondary_feature_signature_hash(
                cls._secondary_feature_signature(raw_screens.get(sid, {}))
            )
            for sid in screen_ids
        )
        if len(signature_hashes) == 1:
            return signature_hashes[0]
        return hashlib.sha256(repr(tuple(signature_hashes)).encode()).hexdigest()[:12]

    @staticmethod
    def _secondary_feature_signature(screen: dict[str, Any]) -> tuple[Any, ...]:
        """Stable secondary features that can distinguish raw screens with the
        same primary fingerprint.

        Combines activity, modal-flag, sorted non-editable text anchors, and
        sorted content-description anchors. Text from editable fields is
        excluded because user input is volatile.
        """
        metadata = screen.get("metadata", {}) if isinstance(screen, dict) else {}
        elements = (
            screen.get("interactable_elements", screen.get("elements", []))
            if isinstance(screen, dict)
            else []
        )
        text_anchors = sorted(
            {
                (el.get("text") or "").strip()
                for el in elements
                if (el.get("text") or "").strip() and not el.get("is_editable")
            }
        )
        desc_anchors = sorted(
            {
                (el.get("content_description") or "").strip()
                for el in elements
                if (el.get("content_description") or "").strip()
            }
        )
        return (
            (screen.get("activity_name") or "") if isinstance(screen, dict) else "",
            bool(metadata.get("has_modal")),
            tuple(text_anchors),
            tuple(desc_anchors),
        )

    def _build_states(
        self,
        raw_screens: dict[str, Any],
        trace_dir: Path | None = None,
        app_prior: AppPrior | None = None,
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
            name = self._derive_state_name(screen, state_id, trace_dir, app_prior)
            structural_fp = self._compute_structural_fingerprint(screen)

            state = AbstractState(
                state_id=state_id,
                name=name,
                fingerprint=fp,
                structural_fingerprint=structural_fp or None,
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

    @staticmethod
    def _trace_has_state_ids(raw_screens: dict[str, Any]) -> bool:
        """Return True iff every screen in the trace carries a non-empty ``state_id``."""
        if not raw_screens:
            return False
        for screen in raw_screens.values():
            sid = screen.get("state_id") if isinstance(screen, dict) else None
            if not sid:
                return False
        return True

    def _build_states_from_state_ids(
        self,
        raw_screens: dict[str, Any],
        trace_dir: Path | None = None,
        app_prior: AppPrior | None = None,
    ) -> tuple[dict[str, str], dict[str, AbstractState]]:
        """Build AbstractStates directly from per-screen ``state_id``.

        This is the new-explorer path: the ``state_id`` is already a functional,
        text-anchored identity, so no fingerprint computation is needed. Each
        unique ``state_id`` in the trace becomes one AbstractState.

        Returns:
            (screen_id -> canonical_state_id, canonical_state_id -> AbstractState).
        """
        sid_to_state_id: dict[str, str] = {}
        states: dict[str, AbstractState] = {}
        state_id_to_canonical: dict[str, str] = {}
        state_counter = 0

        for screen_id, screen in raw_screens.items():
            text_sid: str = screen.get("state_id") or ""
            if not text_sid:
                continue
            canonical = state_id_to_canonical.get(text_sid)
            if canonical is None:
                state_counter += 1
                canonical = f"s_{state_counter:03d}"
                state_id_to_canonical[text_sid] = canonical
                structural_fp = self._compute_structural_fingerprint(screen)
                states[canonical] = AbstractState(
                    state_id=canonical,
                    name=self._derive_state_name(screen, canonical, trace_dir, app_prior),
                    fingerprint=text_sid,
                    structural_fingerprint=structural_fp or None,
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                    activity_name=screen.get("activity_name"),
                    raw_screens=[screen_id],
                )
            else:
                states[canonical].raw_screens.append(screen_id)
            sid_to_state_id[screen_id] = canonical

        logger.info(
            f"Built {len(states)} abstract states from {len(raw_screens)} raw screens "
            "(state_id path)"
        )
        return sid_to_state_id, states

    def _build_transitions(
        self,
        raw_traces: list[dict[str, Any]],
        sid_to_state_id: dict[str, str],
        include_self_loops: bool,
        raw_screens: dict[str, Any] | None = None,
    ) -> list[Transition]:
        """Convert exploration traces into FSM transitions.

        Self-loop policy: SCROLL_UP / SCROLL_DOWN / INPUT_TEXT self-loops
        and toggle (is_checkable) clicks are preserved unconditionally,
        because the FSM must represent these as legal affordances even
        when the captured pre/post screens collapsed to the same abstract
        state. Plain CLICK no-op self-loops continue to be dropped unless
        ``include_self_loops`` is set.
        """
        transitions: list[Transition] = []
        skipped_self_loops = 0
        skipped_low_trust_scope = 0
        downgraded_low_trust_scope = 0
        _MEANINGFUL_SELF_LOOPS = {  # noqa: N806
            "scroll_up",
            "scroll_down",
            "input_text",
        }

        for loop_index, trace in enumerate(raw_traces):
            source_sid = trace.get("source_screen_id", "")
            target_sid = trace.get("target_screen_id", "")

            source_state = sid_to_state_id.get(source_sid)
            target_state = sid_to_state_id.get(target_sid)

            if source_state is None or target_state is None:
                continue

            metadata = trace.get("metadata") or {}
            skip_trace, confidence, low_trust = self._trace_transition_trust(metadata)
            if skip_trace:
                skipped_low_trust_scope += 1
                continue
            if low_trust:
                downgraded_low_trust_scope += 1

            action_data = trace.get("action", {})
            action_type = (action_data.get("action_type") or action_data.get("type") or "").lower()
            is_meaningful_self_loop = action_type in _MEANINGFUL_SELF_LOOPS

            if (
                not include_self_loops
                and source_state == target_state
                and not is_meaningful_self_loop
                and not (raw_screens and self._is_toggle_action(trace, raw_screens))
            ):
                skipped_self_loops += 1
                continue

            action = Action(**action_data)
            fsm_action = action.to_fsm_dict()

            # Enrich action dict with target element metadata
            if raw_screens and action.target_element_id:
                source_screen = raw_screens.get(source_sid, {})
                elements = source_screen.get(
                    "interactable_elements", source_screen.get("elements", [])
                )
                for el in elements:
                    if el.get("element_id") == action.target_element_id:
                        text = el.get("text") or ""
                        desc = el.get("content_description") or ""
                        fsm_action["target_text"] = text or desc
                        fsm_action["target_resource_id"] = el.get("resource_id") or ""
                        fsm_action["target_class"] = el.get("class_name") or ""
                        fsm_action["target_content_desc"] = desc
                        break

            step_index = trace.get("step_number")
            if not isinstance(step_index, int):
                step_index = loop_index

            transitions.append(
                Transition(
                    source=source_state,
                    target=target_state,
                    action=fsm_action,
                    # 1.0 = observed during exploration (pre-replay).
                    # Stage 5 replay will override with success_count/total_trials.
                    # Auto-inferred transitions (dialog dismiss, tab) use 0.5.
                    confidence=confidence,
                    low_trust=low_trust,
                    observed_count=1,
                    provenance=[
                        ProvenanceEntry(
                            trace_step_index=step_index,
                            source_screen_id=source_sid or None,
                            target_screen_id=target_sid or None,
                            confidence_source="observed",
                        )
                    ],
                )
            )

        if skipped_self_loops:
            logger.debug(f"Skipped {skipped_self_loops} self-loop transitions")
        if skipped_low_trust_scope:
            logger.debug(f"Skipped {skipped_low_trust_scope} low-trust scope traces")
        if downgraded_low_trust_scope:
            logger.debug(f"Downgraded {downgraded_low_trust_scope} low-trust in-app traces")
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
        """Merge duplicate transitions by (source, target, canonical action key).

        Sums observed_count for duplicates.
        """
        key_to_trans: dict[tuple[str, str, tuple[tuple[str, object], ...]], Transition] = {}

        for t in transitions:
            key = (t.source, t.target, canonical_action_key(t.action))

            if key in key_to_trans:
                self._merge_transition_trust(key_to_trans[key], t)
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
        states: dict[str, AbstractState] | None = None,
        app_prior: AppPrior | None = None,
    ) -> str | None:
        """Detect the initial state.

        Preference order:
          1. The state whose ``activity_name`` matches the launcher activity
             declared in ``AppPrior`` (manifest ``MAIN/LAUNCHER`` intent
             filter). Matching tolerates short-class-name equality so the
             launcher ``.MainActivity`` notation in the manifest aligns with
             the fully-qualified runtime ``com.example.app.MainActivity``.
          2. Fallback: the source of the earliest-numbered trace step.
        """
        entry_activity = app_prior.entry_activity if app_prior else None
        if entry_activity and states:
            entry_short = entry_activity.rsplit(".", 1)[-1]
            for state in states.values():
                activity_name = state.activity_name
                if not activity_name:
                    continue
                if activity_name == entry_activity:
                    return state.state_id
                if activity_name.rsplit(".", 1)[-1] == entry_short:
                    return state.state_id

        if not raw_traces:
            return None
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
        self,
        screen: dict[str, Any],
        fallback_id: str,
        trace_dir: Path | None = None,
        app_prior: AppPrior | None = None,
    ) -> str:
        """Derive a human-readable state name from screen metadata.

        Priority: Activity label → page_title from XML → first heading → fallback.
        """
        activity_name = screen.get("activity_name") or ""

        # Strategy 1: Manifest Activity label
        if app_prior and activity_name:
            for act in app_prior.activities:
                name_match = act.name == activity_name or (
                    activity_name.rsplit(".", 1)[-1] == act.name.rsplit(".", 1)[-1]
                )
                if name_match and act.label:
                    return act.label

        # Strategy 2: page_title from XML (action_bar_title)
        all_elements = self._get_all_elements(screen, trace_dir)

        for el in all_elements:
            rid = el.get("resource_id", "") or ""
            if "action_bar_title" in rid.lower():
                text = el.get("text")
                if text and text.strip():
                    return text.strip()

        for el in all_elements:
            rid = el.get("resource_id", "") or ""
            if rid and "title" in rid.lower() and "subtitle" not in rid.lower():
                text = el.get("text")
                if text and text.strip() and len(text.strip()) > 1:
                    return text.strip()

        # Strategy 3: Short activity class name
        if activity_name:
            short = activity_name.rsplit(".", 1)[-1]
            if short and short not in ("SubSettings", "Settings", "Activity"):
                return short

        # Strategy 4: first non-empty text from interactable elements
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
