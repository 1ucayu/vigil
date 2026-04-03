"""AppFSM: Per-app hierarchical Finite State Machine with DSL guard annotations.

This is the central data structure of Vigil. It wraps a networkx DiGraph and provides
methods for state/transition management, structural verification, and serialization.
"""

from __future__ import annotations

import json
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

    Attributes:
        STRUCTURAL: Fixed-structure container (e.g., settings menu — items don't change).
        CONTENT: Dynamic-content container (e.g., WiFi list — items vary at runtime).
        NONE: State does not contain a classified scrollable container.
    """

    STRUCTURAL = "structural"
    CONTENT = "content"
    NONE = "none"


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
    """

    state_id: str
    name: str
    fingerprint: str
    hierarchy_level: HierarchyLevel
    parent_state: str | None = None
    activity_name: str | None = None
    invariants: list[str] = Field(default_factory=list)
    raw_screens: list[str] = Field(default_factory=list)
    container_type: ContainerType = ContainerType.NONE
    container_resource_id: str | None = None
    item_skeleton_hash: str | None = None
    sub_fsm_template_id: str | None = None


class Transition(BaseModel):
    """A transition between two abstract states in the FSM.

    Attributes:
        source: Source state ID.
        target: Target state ID.
        action: Action that triggers this transition (e.g., {"type": "click", "target": ...}).
        guard: Optional DSL guard expression that must evaluate to true.
        confidence: Replay confidence score (success_count / total_trials).
        observed_count: Number of times this transition was observed during exploration.
    """

    source: str
    target: str
    action: dict[str, Any]
    guard: str | None = None
    confidence: float = 0.0
    observed_count: int = 0


class SubFsmTemplate(BaseModel):
    """A parameterized sub-FSM discovered by exploring one representative content item.

    When a CONTENT container's items are clicked, they lead to the same structural
    sub-tree (e.g., any WiFi network → WiFi Detail → Advanced Settings). This template
    captures that sub-tree so it can be instantiated with different parameters at runtime.

    Attributes:
        template_id: Unique identifier (e.g., "tmpl_001").
        entry_action: Action that enters this sub-FSM from the container state.
        states: States in the sub-tree.
        transitions: Transitions in the sub-tree.
        parameters: Parameter names that vary per item (e.g., ["$item.name"]).
        source_container_state_id: FSM state ID of the container this was extracted from.
        item_skeleton_hash: Skeleton hash of the item type this template handles.
    """

    template_id: str
    entry_action: dict[str, Any]
    states: dict[str, AbstractState]
    transitions: list[Transition]
    parameters: list[str] = Field(default_factory=list)
    source_container_state_id: str
    item_skeleton_hash: str


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

    def add_sub_fsm_template(self, template: SubFsmTemplate) -> None:
        """Register a sub-FSM template for container content."""
        self.sub_fsm_templates[template.template_id] = template

    def add_transition(self, transition: Transition) -> None:
        """Add a transition between two states."""
        self.transitions.append(transition)
        self.graph.add_edge(
            transition.source,
            transition.target,
            action=transition.action,
            guard=transition.guard,
            confidence=transition.confidence,
            observed_count=transition.observed_count,
        )

    def is_valid_transition(self, from_state: str, action: dict[str, Any]) -> bool:
        """Check if an action is a valid transition from the given state (Tier 1)."""
        if from_state not in self.graph:
            return False
        action_type = action.get("type")
        for _, _, edge_data in self.graph.out_edges(from_state, data=True):
            if edge_data.get("action", {}).get("type") == action_type:
                return True
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
        if from_state not in self.graph:
            return None
        action_type = action.get("type")
        for _, target, edge_data in self.graph.out_edges(from_state, data=True):
            if edge_data.get("action", {}).get("type") == action_type:
                return target
        return None

    def get_transition(self, from_state: str, action: dict[str, Any]) -> Transition | None:
        """Get the Transition object for a given action from a state."""
        action_type = action.get("type")
        for t in self.transitions:
            if t.source == from_state and t.action.get("type") == action_type:
                return t
        return None

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
            fsm.add_sub_fsm_template(SubFsmTemplate.model_validate(tmpl_data))

        return fsm

    def __repr__(self) -> str:
        return (
            f"AppFSM(app={self.app_package!r}, "
            f"states={len(self.states)}, "
            f"transitions={len(self.transitions)})"
        )
