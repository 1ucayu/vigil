"""AppFSM: Per-app hierarchical Finite State Machine with DSL guard annotations.

This is the central data structure of Vigil. It wraps a networkx DiGraph and provides
methods for state/transition management, structural verification, and serialization.
"""

from __future__ import annotations

import json
from collections.abc import Hashable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import networkx as nx
from pydantic import BaseModel, Field


class HierarchyLevel(StrEnum):
    """Hierarchy levels for FSM state abstraction.

    Inspired by "Learned Cloud Emulators" (HotNets'25):
    App > Activity > Fragment > Component.
    """

    APP = "app"
    ACTIVITY = "activity"
    FRAGMENT = "fragment"
    COMPONENT = "component"


class ContainerType(StrEnum):
    """Classification for scrollable containers within a state.

    Derived from invariant mining in Stage 2.5, not rule-based heuristics.

    STATIC: Fixed structure AND content across visits (e.g., Settings menu —
        same items every time). Determined by exact invariants from multi-visit observation.
    DYNAMIC: Structure fixed but content/count varies across visits (e.g.,
        WiFi list — different networks at different locations). Determined by
        range/pattern invariants from multi-visit observation.
    NONE: State does not contain a classified scrollable container.
    """

    STATIC = "static"
    DYNAMIC = "dynamic"
    NONE = "none"


class TransitionLookupStatus(StrEnum):
    """Result of resolving an action against a state's outgoing transitions."""

    MATCH = "match"
    NO_MATCH = "no_match"
    UNCERTAIN = "uncertain"


_GLOBAL_ACTION_TYPES = frozenset({"navigate_back", "navigate_home"})
_ACTION_KEY_FIELDS = (
    "type",
    "target",
    "resource_id",
    "target_resource_id",
    "target_text",
    "target_content_desc",
    "target_class",
    "target_class_name",
    "target_selector",
    "text",
    "value",
)
_ACTION_IDENTITY_FIELDS = tuple(k for k in _ACTION_KEY_FIELDS if k != "type")
_SELECTOR_IDENTITY_FIELDS = (
    "resource_id",
    "text",
    "content_description",
    "nearby_text",
    "class_name",
    "ancestor_chain",
)


def _normalize_action_value(value: Any) -> Hashable | None:
    """Normalize action identity values; empty strings/containers mean absent."""
    if value is None:
        return None
    if isinstance(value, str):
        return value if value else None
    if isinstance(value, list | tuple):
        normalized = tuple(_normalize_action_value(v) for v in value)
        return normalized if any(v is not None for v in normalized) else None
    if isinstance(value, dict):
        normalized_items = tuple(
            sorted((str(k), _normalize_action_value(v)) for k, v in value.items())
        )
        return normalized_items if any(v is not None for _, v in normalized_items) else None
    if isinstance(value, bool | int | float):
        return value
    return str(value)


def _selector_signature(selector: Any) -> Hashable | None:
    """Stable selector identity; debug-only bounds/depth are intentionally excluded."""
    if not isinstance(selector, dict) or not selector:
        return None
    parts = tuple(
        (field, _normalize_action_value(selector.get(field))) for field in _SELECTOR_IDENTITY_FIELDS
    )
    return parts if any(value is not None for _, value in parts) else None


def _first_present(*values: Any) -> Hashable | None:
    for value in values:
        normalized = _normalize_action_value(value)
        if normalized is not None:
            return normalized
    return None


def _canonical_action_mapping(action: dict[str, Any]) -> dict[str, Hashable | None]:
    """Normalize every stable serialized action identity field into one mapping."""
    selector = action.get("target_selector") or {}
    selector_map = selector if isinstance(selector, dict) else {}

    resource_id = _first_present(
        action.get("resource_id"),
        action.get("target_resource_id"),
        selector_map.get("resource_id"),
    )
    target_resource_id = _first_present(
        action.get("target_resource_id"),
        action.get("resource_id"),
        selector_map.get("resource_id"),
    )
    target_class = _first_present(
        action.get("target_class"),
        action.get("target_class_name"),
        action.get("class_name"),
        selector_map.get("class_name"),
    )
    target_class_name = _first_present(
        action.get("target_class_name"),
        action.get("target_class"),
        action.get("class_name"),
        selector_map.get("class_name"),
    )
    text = _first_present(action.get("text"), action.get("value"))
    value = _first_present(action.get("value"), action.get("text"))

    return {
        "type": _first_present(action.get("type"), action.get("action_type")),
        "target": _first_present(action.get("target")),
        "resource_id": resource_id,
        "target_resource_id": target_resource_id,
        "target_text": _first_present(
            action.get("target_text"),
            selector_map.get("text"),
            selector_map.get("nearby_text"),
        ),
        "target_content_desc": _first_present(
            action.get("target_content_desc"),
            selector_map.get("content_description"),
        ),
        "target_class": target_class,
        "target_class_name": target_class_name,
        "target_selector": _selector_signature(selector),
        "text": text,
        "value": value,
    }


def canonical_action_key(action: dict[str, Any]) -> tuple[tuple[str, Hashable | None], ...]:
    """Canonical signature for serialized FSM action identity.

    Bounds are excluded because ``Action`` marks them as volatile capture-local
    hints. Resource-id/text/class aliases are filled in both directions so
    actions serialized by different pipeline stages still compare by the same
    logical identity.
    """
    mapping = _canonical_action_mapping(action)
    return tuple((field, mapping[field]) for field in _ACTION_KEY_FIELDS)


def _identity_fields(action_key: dict[str, Hashable | None]) -> set[str]:
    return {field for field in _ACTION_IDENTITY_FIELDS if action_key.get(field) is not None}


class StateSemanticProfile(BaseModel):
    """LLM-generated semantic annotation for an abstract state (Stage 2.5).

    Provides semantic context beyond structural fingerprinting for:
    - State localization (distinguishing structurally isomorphic pages)
    - Guard generation (stable element aliases for icon-only buttons)
    - Container classification (invariant-derived static/dynamic)
    """

    alt_text: str = ""
    page_function: str = ""
    expected_actions: list[str] = Field(default_factory=list)
    icon_labels: dict[str, str] = Field(default_factory=dict)
    generation_confidence: float = 0.0


class AbstractState(BaseModel):
    """An abstract UI state in the FSM.

    Attributes:
        state_id: Unique identifier for this state.
        name: Human-readable name (e.g., "PaymentConfirm", "WiFiListPage").
        fingerprint: Structural fingerprint hash (class_name, resource_id, depth, interactability).
        hierarchy_level: Position in the App > Activity > Fragment > Component hierarchy.
        parent_state: Parent state ID in the hierarchy (None for APP-level).
        activity_name: Android Activity class name (from accessibility tree).
        invariants: List of invariant expressions that must hold in this state.
        raw_screens: List of raw screen IDs that map to this abstract state.
        container_type: Scrollable container classification (Stage 2.5 invariant mining).
        container_resource_id: Resource ID of the classified scrollable container.
        semantic_profile: LLM-generated semantic annotation (Stage 2.5).
        state_invariants: Goal-agnostic DSL expressions that must ALWAYS hold in this state.
        invariant_confidence: Confidence in mined invariants (from multi-visit observation).
        sub_fsm_template_id: Reference to a SubFsmTemplate for dynamic container detail pages.
    """

    state_id: str
    name: str
    fingerprint: str
    structural_fingerprint: str | None = None
    hierarchy_level: HierarchyLevel
    parent_state: str | None = None
    activity_name: str | None = None
    invariants: list[str] = Field(default_factory=list)
    raw_screens: list[str] = Field(default_factory=list)
    container_type: ContainerType = ContainerType.NONE
    container_resource_id: str | None = None
    semantic_profile: StateSemanticProfile | None = None
    state_invariants: list[str] = Field(default_factory=list)
    invariant_confidence: float = 0.0
    sub_fsm_template_id: str | None = None


class ProvenanceEntry(BaseModel):
    """Single evidence record explaining how a transition entered the FSM.

    A transition may carry multiple entries when traces are merged or when an
    inferred edge is later corroborated by an observed edge.

    Attributes:
        trace_step_index: Position in the source trace ``traces`` array; ``-1``
            for synthetic entries produced by dialog/tab inferrers.
        source_screen_id: Raw screen id captured before the action.
        target_screen_id: Raw screen id captured after the action.
        confidence_source: Where the supporting evidence came from. One of
            ``"observed" | "inferred_dialog" | "inferred_tab"``.
    """

    trace_step_index: int = -1
    source_screen_id: str | None = None
    target_screen_id: str | None = None
    confidence_source: str = "observed"


class Transition(BaseModel):
    """A transition between two abstract states in the FSM.

    Attributes:
        source: Source state ID.
        target: Target state ID.
        action: Action that triggers this transition (e.g., {"type": "click", "target": ...}).
        guard: Optional DSL guard expression that must evaluate to true.
        confidence: Replay confidence score (success_count / total_trials).
        low_trust: Whether the edge came from a low-trust observation scope.
        observed_count: Number of times this transition was observed during exploration.
        provenance: Evidence records explaining where this transition came from.
    """

    source: str
    target: str
    action: dict[str, Any]
    guard: str | None = None
    confidence: float = 0.0
    low_trust: bool = False
    observed_count: int = 0
    provenance: list[ProvenanceEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class TransitionLookup:
    """Resolved transition plus ambiguity status for action identity matching."""

    status: TransitionLookupStatus
    transition: Transition | None = None
    target_state_id: str | None = None
    details: str = ""


class SubFsmTemplate(BaseModel):
    """Parameterized sub-FSM for dynamic container item detail pages.

    When a DYNAMIC container's items all lead to structurally identical
    detail pages (verified via smart stopping in Stage 1), this template
    represents all N possible detail pages with a single parameterized state.
    """

    template_id: str
    source_state_id: str
    entry_fingerprint: str
    states: dict[str, AbstractState] = Field(default_factory=dict)
    transitions: list[Transition] = Field(default_factory=list)
    parameter_schema: dict[str, str] = Field(default_factory=dict)
    item_skeleton: str = ""


class AppFSM:
    """Per-app hierarchical FSM with DSL guard annotations.

    Wraps a networkx.DiGraph where nodes are AbstractStates and edges are Transitions.
    Provides methods for structural verification (Tier 1) and serialization.

    Args:
        app_package: Android package name (e.g., "com.android.settings").
    """

    def __init__(self, app_package: str) -> None:
        self.app_package = app_package
        self.graph: nx.DiGraph = nx.DiGraph()
        self.states: dict[str, AbstractState] = {}
        self.transitions: list[Transition] = []
        self.initial_state: str | None = None
        self.version: str = "0.1.0"
        self.evolution_log: list[dict[str, Any]] = []
        self.sub_fsm_templates: dict[str, SubFsmTemplate] = {}

    def add_state(self, state: AbstractState) -> None:
        """Add an abstract state to the FSM."""
        self.states[state.state_id] = state
        self.graph.add_node(state.state_id, **state.model_dump())

    def add_transition(self, transition: Transition) -> None:
        """Add a transition between two states."""
        self.transitions.append(transition)
        self.graph.add_edge(
            transition.source,
            transition.target,
            action=transition.action,
            guard=transition.guard,
            confidence=transition.confidence,
            low_trust=transition.low_trust,
            observed_count=transition.observed_count,
        )

    @staticmethod
    def _compatible_with_proposed_identity(
        stored_key: dict[str, Hashable | None],
        proposed_key: dict[str, Hashable | None],
        proposed_fields: set[str],
    ) -> bool:
        """True when all identity fields supplied by the proposal agree."""
        return all(stored_key.get(field) == proposed_key.get(field) for field in proposed_fields)

    def resolve_transition(self, from_state: str, action: dict[str, Any]) -> TransitionLookup:
        """Resolve an action to a transition, preserving ambiguity as UNCERTAIN."""
        if from_state not in self.graph:
            return TransitionLookup(
                status=TransitionLookupStatus.NO_MATCH,
                details=f"State {from_state} is not in the FSM",
            )

        proposed_key = _canonical_action_mapping(action)
        proposed_type = proposed_key.get("type")
        same_type = [
            t
            for t in self.transitions
            if t.source == from_state
            and _canonical_action_mapping(t.action).get("type") == proposed_type
        ]
        proposed_fields = _identity_fields(proposed_key)
        is_global = proposed_type in _GLOBAL_ACTION_TYPES
        if not proposed_fields and len(same_type) > 1 and not is_global:
            return TransitionLookup(
                status=TransitionLookupStatus.UNCERTAIN,
                details=(
                    f"Action type {proposed_type!r} lacks target identity; "
                    f"{len(same_type)} outgoing transitions share that type"
                ),
            )

        exact_matches = [
            t for t in same_type if canonical_action_key(t.action) == canonical_action_key(action)
        ]
        if len(exact_matches) == 1:
            t = exact_matches[0]
            return TransitionLookup(
                status=TransitionLookupStatus.MATCH,
                transition=t,
                target_state_id=t.target,
            )
        if len(exact_matches) > 1:
            return TransitionLookup(
                status=TransitionLookupStatus.UNCERTAIN,
                details="Multiple transitions share the same canonical action key",
            )

        if not proposed_fields and same_type and not is_global:
            return TransitionLookup(
                status=TransitionLookupStatus.UNCERTAIN,
                details=(
                    f"Action type {proposed_type!r} lacks target identity; "
                    "cannot bind it to a non-global transition"
                ),
            )

        if proposed_fields:
            compatible = [
                t
                for t in same_type
                if self._compatible_with_proposed_identity(
                    _canonical_action_mapping(t.action), proposed_key, proposed_fields
                )
            ]
            if len(compatible) == 1:
                t = compatible[0]
                return TransitionLookup(
                    status=TransitionLookupStatus.MATCH,
                    transition=t,
                    target_state_id=t.target,
                )
            if len(compatible) > 1:
                return TransitionLookup(
                    status=TransitionLookupStatus.UNCERTAIN,
                    details=(
                        f"Action identity for type {proposed_type!r} matches "
                        f"{len(compatible)} outgoing transitions"
                    ),
                )

        state = self.states.get(from_state)
        if (
            state
            and state.sub_fsm_template_id
            and state.container_type == ContainerType.DYNAMIC
            and proposed_type == "click"
            and self.sub_fsm_templates.get(state.sub_fsm_template_id)
        ):
            return TransitionLookup(
                status=TransitionLookupStatus.MATCH,
                details="Matched dynamic container sub-FSM template",
            )

        return TransitionLookup(
            status=TransitionLookupStatus.NO_MATCH,
            details=f"No transition from {from_state} matches action {action}",
        )

    def is_valid_transition(self, from_state: str, action: dict[str, Any]) -> bool | None:
        """Check if an action is a valid transition from the given state (Tier 1).

        For DYNAMIC container states with a sub_fsm_template, click actions are
        validated against the template's entry transition pattern (any click is
        valid since items are parameterized), not just exact graph edges.

        Returns True for a resolved transition, False for a proven miss, and
        None when the action identity is insufficient to choose one transition.
        """
        lookup = self.resolve_transition(from_state, action)
        if lookup.status is TransitionLookupStatus.MATCH:
            return True
        if lookup.status is TransitionLookupStatus.UNCERTAIN:
            return None
        return False

    def is_reachable(self, from_state: str, goal_state: str) -> bool:
        """Check if goal_state is reachable from from_state. O(V+E) via BFS."""
        try:
            return nx.has_path(self.graph, from_state, goal_state)
        except nx.NodeNotFound:
            return False

    def get_shortest_path(self, from_state: str, goal_state: str) -> list[str]:
        """Get the shortest path from from_state to goal_state."""
        try:
            return nx.shortest_path(self.graph, from_state, goal_state)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def get_transition_target(self, from_state: str, action: dict[str, Any]) -> str | None:
        """Get the target state for a given action from a state."""
        lookup = self.resolve_transition(from_state, action)
        return lookup.target_state_id if lookup.status is TransitionLookupStatus.MATCH else None

    def get_transition(self, from_state: str, action: dict[str, Any]) -> Transition | None:
        """Get the Transition object for a given action from a state."""
        lookup = self.resolve_transition(from_state, action)
        return lookup.transition if lookup.status is TransitionLookupStatus.MATCH else None

    def find_similar_state(self, fingerprint: str, threshold: float = 0.85) -> str | None:
        """Find the most similar existing state by fingerprint (for Tier 3 evolution).

        Currently uses exact fingerprint match. Fuzzy structural similarity
        (on raw component tuples before hashing) is a future extension for Tier 3.
        """
        for state in self.states.values():
            if state.fingerprint == fingerprint:
                return state.state_id
        return None

    def serialize(self, path: str | Path) -> None:
        """Serialize the FSM to a JSON file."""
        path = Path(path)
        data = {
            "app_package": self.app_package,
            "version": self.version,
            "initial_state": self.initial_state,
            "states": {sid: s.model_dump() for sid, s in self.states.items()},
            "transitions": [t.model_dump() for t in self.transitions],
            "evolution_log": self.evolution_log,
            "sub_fsm_templates": {tid: t.model_dump() for tid, t in self.sub_fsm_templates.items()},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str))

    @classmethod
    def deserialize(cls, path: str | Path) -> AppFSM:
        """Deserialize an FSM from a JSON file."""
        path = Path(path)
        data = json.loads(path.read_text())
        fsm = cls(app_package=data["app_package"])
        fsm.version = data.get("version", "0.1.0")
        fsm.initial_state = data.get("initial_state")
        fsm.evolution_log = data.get("evolution_log", [])

        for state_data in data.get("states", {}).values():
            fsm.add_state(AbstractState(**state_data))

        for trans_data in data.get("transitions", []):
            fsm.add_transition(Transition(**trans_data))

        for tmpl_data in data.get("sub_fsm_templates", {}).values():
            tmpl = SubFsmTemplate(**tmpl_data)
            fsm.sub_fsm_templates[tmpl.template_id] = tmpl

        return fsm

    def __repr__(self) -> str:
        return (
            f"AppFSM(app={self.app_package!r}, "
            f"states={len(self.states)}, "
            f"transitions={len(self.transitions)})"
        )
