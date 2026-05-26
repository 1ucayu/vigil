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
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


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


class StateKind(StrEnum):
    """Policy-relevant category of an abstract state.

    Derived passively from ``HierarchyLevel`` for now: ``COMPONENT`` maps to
    ``DIALOG``, everything else defaults to ``NORMAL``. Verifier policy is not
    routed off of ``StateKind`` in this phase.
    """

    NORMAL = "normal"
    DIALOG = "dialog"
    TERMINAL = "terminal"
    ERROR = "error"
    SYSTEM = "system"
    EXTERNAL = "external"


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
_STABLE_TARGET_IDENTITY_FIELDS = frozenset(
    {
        "resource_id",
        "target_resource_id",
        "target_text",
        "target_content_desc",
    }
)
_TEMPLATE_ITEM_IDENTITY_FIELDS = frozenset(
    {
        "target",
        "target_text",
        "target_content_desc",
        "target_selector",
    }
)
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


class StateIdentity(BaseModel):
    """Deterministic identity for an abstract state.

    Replaces the ambiguous flat ``fingerprint`` field with explicit functional
    vs. structural hashes plus algorithm/version provenance so future identity
    algorithm changes do not silently break stored FSMs.
    """

    functional_hash: str
    structural_hash: str | None = None
    secondary_hash: str | None = None
    identity_version: str = "v1"
    algorithm: str = "hybrid_ui_identity"


class AndroidStateContext(BaseModel):
    """Android-specific observation context, kept separate from abstract identity."""

    activity_name: str | None = None
    package_name: str | None = None
    window_type: str | None = None


class StateEvidence(BaseModel):
    """Trace-derived evidence supporting that this state exists.

    XML/runtime traces are the deterministic source of truth — this is where
    the supporting screen ids live.
    """

    raw_screen_ids: list[str] = Field(default_factory=list)
    observation_count: int = 0
    construction_source: str = "observed_trace"
    first_seen_trace: str | None = None
    trust_level: str = "observed"

    @model_validator(mode="after")
    def _default_observation_count(self) -> StateEvidence:
        if self.observation_count == 0 and self.raw_screen_ids:
            object.__setattr__(self, "observation_count", len(self.raw_screen_ids))
        return self


class StateAbstraction(BaseModel):
    """Dynamic container / template metadata for this state."""

    container_type: ContainerType = ContainerType.NONE
    container_selector: dict[str, Any] = Field(default_factory=dict)
    template_id: str | None = None
    template_role: str = "normal"
    parameter_schema: dict[str, str] = Field(default_factory=dict)
    parameter_bindings: dict[str, str] = Field(default_factory=dict)


class StateInvariant(BaseModel):
    """A single runtime-checkable invariant with its own confidence + provenance.

    Replaces the coarse per-state scalar ``invariant_confidence`` so each
    predicate carries its own evidence.
    """

    expr: str
    confidence: float = 0.0
    source: str = "unknown"
    evidence_count: int = 0


def _model_dump_for_compare(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _nested_metadata_differs(model: BaseModel, default_model: BaseModel) -> bool:
    return _model_dump_for_compare(model) != _model_dump_for_compare(default_model)


def _invariant_expr(value: Any) -> str:
    if isinstance(value, StateInvariant):
        return value.expr
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, dict):
        return str(value.get("expr", ""))
    return str(value)


def _as_invariant_items(values: Any) -> list[Any]:
    if values is None:
        return []
    if isinstance(values, str | dict | BaseModel):
        return [values]
    return list(values)


def _invariant_expr_set(values: Any) -> set[str]:
    return {_invariant_expr(value) for value in _as_invariant_items(values)}


def _dedupe_invariant_texts(values: Any) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    for value in _as_invariant_items(values):
        text = _invariant_expr(value)
        if text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


def _state_invariant_specs(values: Any, confidence: float, source: str) -> list[Any]:
    specs: list[Any] = []
    seen_exprs: set[str] = set()
    for value in _as_invariant_items(values):
        expr = _invariant_expr(value)
        if expr in seen_exprs:
            continue
        seen_exprs.add(expr)
        if isinstance(value, StateInvariant | dict):
            specs.append(value)
        else:
            specs.append(
                {
                    "expr": expr,
                    "confidence": confidence,
                    "source": source,
                    "evidence_count": 0,
                }
            )
    return specs


class StateAnnotations(BaseModel):
    """LLM-derived, non-authoritative annotations.

    Per project rules these never decide state equality, edges, replay
    confidence, or runtime verdicts. They are kept distinct from identity and
    evidence on purpose.
    """

    display_name: str = ""
    alt_text: str = ""
    page_function: str = ""
    expected_actions: list[str] = Field(default_factory=list)
    widget_aliases: list[dict[str, Any]] = Field(default_factory=list)
    generation_confidence: float = 0.0


class AbstractState(BaseModel):
    """An abstract UI state in the FSM.

    The schema is logically partitioned into:
    - Deterministic identity (``identity`` view over ``fingerprint`` /
      ``structural_fingerprint``).
    - Android observation context (``android_context`` view over
      ``activity_name``).
    - Trace evidence (``evidence`` view over ``raw_screens``).
    - Dynamic abstraction (``abstraction`` view over ``container_type``,
      ``container_resource_id``, ``sub_fsm_template_id``).
    - Runtime-checkable invariants (``invariant_specs`` is canonical; the
      flat ``state_invariants`` + ``invariant_confidence`` aliases are kept
      for backward compatibility).
    - LLM annotations (``annotations`` view; legacy ``semantic_profile`` is
      preserved verbatim).

    Old flat kwargs and old serialized JSON continue to construct cleanly via
    ``model_validator(mode="before")``.
    """

    model_config = ConfigDict(extra="ignore")

    state_id: str
    name: str
    fingerprint: str
    structural_fingerprint: str | None = None
    hierarchy_level: HierarchyLevel
    parent_state: str | None = None
    activity_name: str | None = None
    raw_screens: list[str] = Field(default_factory=list)
    container_type: ContainerType = ContainerType.NONE
    container_resource_id: str | None = None
    semantic_profile: StateSemanticProfile | None = None
    sub_fsm_template_id: str | None = None
    invariant_specs: list[StateInvariant] = Field(default_factory=list)
    legacy_invariants: list[str] = Field(default_factory=list)
    identity_meta: StateIdentity | None = Field(default=None, exclude=True, repr=False)
    evidence_meta: StateEvidence | None = Field(default=None, exclude=True, repr=False)
    abstraction_meta: StateAbstraction | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_and_nested(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # Nested → flat (new schema input)
        identity = data.pop("identity", None)
        if identity is not None:
            if isinstance(identity, BaseModel):
                identity = identity.model_dump()
            identity_payload = dict(identity)
            if (
                identity_payload.get("functional_hash") is None
                and data.get("fingerprint") is not None
            ):
                identity_payload["functional_hash"] = data.get("fingerprint")
            if (
                identity_payload.get("structural_hash") is None
                and data.get("structural_fingerprint") is not None
            ):
                identity_payload["structural_hash"] = data.get("structural_fingerprint")
            identity_model = StateIdentity(**identity_payload)
            data.setdefault("fingerprint", identity_model.functional_hash)
            data.setdefault("structural_fingerprint", identity_model.structural_hash)
            default_identity = StateIdentity(
                functional_hash=data["fingerprint"],
                structural_hash=data.get("structural_fingerprint"),
            )
            if _nested_metadata_differs(identity_model, default_identity):
                data["identity_meta"] = identity_model

        android_context = data.pop("android_context", None)
        if android_context is not None:
            if isinstance(android_context, BaseModel):
                android_context = android_context.model_dump()
            data.setdefault("activity_name", android_context.get("activity_name"))

        evidence = data.pop("evidence", None)
        if evidence is not None:
            if isinstance(evidence, BaseModel):
                evidence = evidence.model_dump()
            evidence_model = StateEvidence(**dict(evidence))
            data.setdefault("raw_screens", list(evidence_model.raw_screen_ids))
            raw_screens = list(data.get("raw_screens", []))
            default_evidence = StateEvidence(
                raw_screen_ids=raw_screens,
                observation_count=len(raw_screens),
            )
            if _nested_metadata_differs(evidence_model, default_evidence):
                data["evidence_meta"] = evidence_model

        abstraction = data.pop("abstraction", None)
        if abstraction is not None:
            if isinstance(abstraction, BaseModel):
                abstraction = abstraction.model_dump()
            abstraction_model = StateAbstraction(**dict(abstraction))
            data.setdefault("container_type", abstraction_model.container_type)
            selector = abstraction_model.container_selector or {}
            if isinstance(selector, dict) and selector.get("resource_id") is not None:
                data.setdefault("container_resource_id", selector.get("resource_id"))
            data.setdefault("sub_fsm_template_id", abstraction_model.template_id)
            default_selector: dict[str, Any] = {}
            if data.get("container_resource_id") is not None:
                default_selector["resource_id"] = data.get("container_resource_id")
            default_abstraction = StateAbstraction(
                container_type=data.get("container_type", ContainerType.NONE),
                container_selector=default_selector,
                template_id=data.get("sub_fsm_template_id"),
            )
            if _nested_metadata_differs(abstraction_model, default_abstraction):
                data["abstraction_meta"] = abstraction_model

        annotations = data.pop("annotations", None)
        if annotations is not None and data.get("semantic_profile") is None:
            if isinstance(annotations, BaseModel):
                annotations = annotations.model_dump()
            data["semantic_profile"] = StateSemanticProfile(
                alt_text=annotations.get("alt_text", ""),
                page_function=annotations.get("page_function", ""),
                expected_actions=list(annotations.get("expected_actions", [])),
                generation_confidence=float(annotations.get("generation_confidence", 0.0)),
            )

        # ``kind`` is computed; ignore an explicit value on input.
        data.pop("kind", None)

        # Invariant handling. Three possible input keys:
        #   - ``invariant_specs`` (new canonical form, list[StateInvariant|dict])
        #   - ``state_invariants`` (list[str]) + ``invariant_confidence`` (float)
        #   - ``invariants`` (legacy list[str]; deprecated and kept separate
        #     from runtime-enforced invariant_specs)
        legacy_state_invariants = data.pop("state_invariants", None)
        legacy_confidence = data.pop("invariant_confidence", None)
        legacy_invariants = data.pop("invariants", None)
        if legacy_invariants is not None or data.get("legacy_invariants") is not None:
            combined_legacy: list[str] = []
            combined_legacy.extend(_dedupe_invariant_texts(data.get("legacy_invariants")))
            combined_legacy.extend(_dedupe_invariant_texts(legacy_invariants))
            data["legacy_invariants"] = _dedupe_invariant_texts(combined_legacy)

        if "invariant_specs" in data:
            if legacy_state_invariants is not None:
                spec_exprs = _invariant_expr_set(data.get("invariant_specs"))
                flat_exprs = _invariant_expr_set(legacy_state_invariants)
                if spec_exprs != flat_exprs:
                    raise ValueError(
                        "Conflicting invariant_specs and state_invariants: "
                        f"spec exprs={sorted(spec_exprs)!r}, "
                        f"state_invariants={sorted(flat_exprs)!r}"
                    )
        else:
            confidence = float(legacy_confidence) if legacy_confidence is not None else 0.0
            source = "mined_multivisit" if legacy_confidence is not None else "unknown"
            specs = _state_invariant_specs(legacy_state_invariants, confidence, source)
            if specs:
                data["invariant_specs"] = specs
        return data

    # --- Computed nested views (deterministic over flat fields) ---

    @computed_field  # type: ignore[prop-decorator]
    @property
    def identity(self) -> StateIdentity:
        if self.identity_meta is not None:
            return self.identity_meta
        return StateIdentity(
            functional_hash=self.fingerprint,
            structural_hash=self.structural_fingerprint,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kind(self) -> StateKind:
        if self.hierarchy_level == HierarchyLevel.COMPONENT:
            return StateKind.DIALOG
        return StateKind.NORMAL

    @computed_field  # type: ignore[prop-decorator]
    @property
    def android_context(self) -> AndroidStateContext:
        return AndroidStateContext(activity_name=self.activity_name)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def evidence(self) -> StateEvidence:
        if self.evidence_meta is not None:
            return self.evidence_meta
        return StateEvidence(
            raw_screen_ids=list(self.raw_screens),
            observation_count=len(self.raw_screens),
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def abstraction(self) -> StateAbstraction:
        if self.abstraction_meta is not None:
            return self.abstraction_meta
        selector: dict[str, Any] = {}
        if self.container_resource_id is not None:
            selector["resource_id"] = self.container_resource_id
        return StateAbstraction(
            container_type=self.container_type,
            container_selector=selector,
            template_id=self.sub_fsm_template_id,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def annotations(self) -> StateAnnotations:
        sp = self.semantic_profile
        widget_aliases: list[dict[str, Any]] = []
        if sp is not None:
            # Mirror icon_labels (keyed by element id) into widget_aliases without
            # removing the original — icon_labels stays canonical on
            # ``semantic_profile`` for this migration phase.
            for element_id, label in sp.icon_labels.items():
                widget_aliases.append({"element_id": element_id, "label": label})
        return StateAnnotations(
            display_name=self.name,
            alt_text=sp.alt_text if sp else "",
            page_function=sp.page_function if sp else "",
            expected_actions=list(sp.expected_actions) if sp else [],
            widget_aliases=widget_aliases,
            generation_confidence=sp.generation_confidence if sp else 0.0,
        )

    # --- Backward-compatible flat aliases for invariant storage ---

    @property
    def state_invariants(self) -> list[str]:
        return [spec.expr for spec in self.invariant_specs]

    @state_invariants.setter
    def state_invariants(self, value: list[str]) -> None:
        confidence = self.invariant_confidence
        new_specs = [
            StateInvariant(
                expr=str(expr),
                confidence=confidence,
                source="mined_multivisit" if confidence > 0.0 else "unknown",
            )
            for expr in value
        ]
        object.__setattr__(self, "invariant_specs", new_specs)

    @property
    def invariant_confidence(self) -> float:
        if not self.invariant_specs:
            return 0.0
        return max(spec.confidence for spec in self.invariant_specs)

    @invariant_confidence.setter
    def invariant_confidence(self, value: float) -> None:
        value = float(value)
        for spec in self.invariant_specs:
            object.__setattr__(spec, "confidence", value)
            if spec.source == "unknown" and value > 0.0:
                object.__setattr__(spec, "source", "mined_multivisit")

    @property
    def invariants(self) -> list[str]:
        """Deprecated legacy alias kept out of runtime-enforced invariants."""
        return list(self.legacy_invariants)

    @invariants.setter
    def invariants(self, value: list[str]) -> None:
        object.__setattr__(self, "legacy_invariants", [str(expr) for expr in value])

    # --- Serialization helpers ---

    def model_dump_compat(self, **kwargs: Any) -> dict[str, Any]:
        """Dump in the new nested shape plus flat compatibility mirrors.

        Existing tooling and on-disk FSM JSON consumers still see the flat
        keys ``fingerprint``, ``structural_fingerprint``, ``activity_name``,
        ``raw_screens``, ``container_type``, ``container_resource_id``,
        ``sub_fsm_template_id``, ``state_invariants``, ``invariant_confidence``,
        and ``semantic_profile``. The new ``identity`` / ``android_context`` /
        ``evidence`` / ``abstraction`` / ``annotations`` / ``invariant_specs``
        / ``kind`` keys are emitted alongside.
        """
        dumped = self.model_dump(**kwargs)
        dumped["state_invariants"] = list(self.state_invariants)
        dumped["invariant_confidence"] = self.invariant_confidence
        dumped["invariants"] = list(self.legacy_invariants)
        return dumped


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
        fields = proposed_fields
        stable_fields = fields & _STABLE_TARGET_IDENTITY_FIELDS
        if "target" in fields and stable_fields:
            # ``target`` is the capture-local element_id. Ignore churn in that
            # handle only when stable text/resource/content-desc identity also
            # binds the proposal to the stored action.
            fields = fields - {"target"}
        return all(stored_key.get(field) == proposed_key.get(field) for field in fields)

    @staticmethod
    def _item_specific_identity_fields(proposed_key: dict[str, Hashable | None]) -> set[str]:
        """Identity fields strong enough to bind a dynamic-template item."""
        return {
            field for field in _TEMPLATE_ITEM_IDENTITY_FIELDS if proposed_key.get(field) is not None
        }

    @staticmethod
    def _template_binding_missing_lookup() -> TransitionLookup:
        return TransitionLookup(
            status=TransitionLookupStatus.UNCERTAIN,
            details="template_binding_missing",
        )

    def _template_conflict_lookup(
        self,
        candidates: list[Transition],
        proposed_key: dict[str, Hashable | None],
        item_fields: set[str],
    ) -> TransitionLookup:
        for field in item_fields:
            selected = [
                t
                for t in candidates
                if _canonical_action_mapping(t.action).get(field) == proposed_key.get(field)
            ]
            if len(selected) == 1:
                t = selected[0]
                return TransitionLookup(
                    status=TransitionLookupStatus.MATCH,
                    transition=t,
                    target_state_id=t.target,
                )
        return self._template_binding_missing_lookup()

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
        state = self.states.get(from_state)
        template = None
        template_state_ids: set[str] = set()
        is_template_click = False
        item_fields: set[str] = set()
        has_template_entry_edge = False
        if (
            state
            and state.sub_fsm_template_id
            and state.container_type == ContainerType.DYNAMIC
            and proposed_type == "click"
        ):
            template = self.sub_fsm_templates.get(state.sub_fsm_template_id)
            if template is not None:
                template_state_ids = set(template.states)
                # A click on this source is a template-binding attempt only if
                # the source actually has at least one outgoing click edge
                # *that is not a self-loop* whose target is inside the template
                # subgraph. Otherwise the click is chrome (toolbar Navigate up,
                # switch, etc.). Self-loops never count as template-entry
                # edges — entering a template item means leaving the source.
                has_template_entry_edge = any(
                    t.target in template_state_ids and t.target != from_state for t in same_type
                )
                is_template_click = has_template_entry_edge
                if is_template_click:
                    item_fields = self._item_specific_identity_fields(proposed_key)

        # On dynamic-template sources, resolve exact chrome clicks before
        # template-binding fallback. On ordinary states, the no-identity
        # ambiguity guard below must run first.
        exact_matches = [
            t for t in same_type if canonical_action_key(t.action) == canonical_action_key(action)
        ]
        if is_template_click or from_state in template_state_ids:
            nontemplate_exact = [t for t in exact_matches if t.target not in template_state_ids]
            if len(nontemplate_exact) == 1:
                t = nontemplate_exact[0]
                return TransitionLookup(
                    status=TransitionLookupStatus.MATCH,
                    transition=t,
                    target_state_id=t.target,
                )

        if is_template_click and not item_fields:
            return self._template_binding_missing_lookup()

        if not proposed_fields and len(same_type) > 1 and not is_global:
            return TransitionLookup(
                status=TransitionLookupStatus.UNCERTAIN,
                details=(
                    f"Action type {proposed_type!r} lacks target identity; "
                    f"{len(same_type)} outgoing transitions share that type"
                ),
            )

        if len(exact_matches) == 1:
            t = exact_matches[0]
            if is_template_click and t.target in template_state_ids and not item_fields:
                return self._template_binding_missing_lookup()
            return TransitionLookup(
                status=TransitionLookupStatus.MATCH,
                transition=t,
                target_state_id=t.target,
            )
        if len(exact_matches) > 1:
            if is_template_click and any(t.target in template_state_ids for t in exact_matches):
                return self._template_conflict_lookup(exact_matches, proposed_key, item_fields)
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
                if is_template_click and t.target in template_state_ids and not item_fields:
                    return self._template_binding_missing_lookup()
                return TransitionLookup(
                    status=TransitionLookupStatus.MATCH,
                    transition=t,
                    target_state_id=t.target,
                )
            if len(compatible) > 1:
                if is_template_click and any(t.target in template_state_ids for t in compatible):
                    return self._template_conflict_lookup(compatible, proposed_key, item_fields)
                return TransitionLookup(
                    status=TransitionLookupStatus.UNCERTAIN,
                    details=(
                        f"Action identity for type {proposed_type!r} matches "
                        f"{len(compatible)} outgoing transitions"
                    ),
                )

        if is_template_click and template is not None:
            # Scan for a concrete outgoing edge from this container to any
            # state inside the template subgraph whose canonical action
            # identity is compatible with the proposal. Identity-compatible
            # means every proposed identity field equals the stored one.
            identity_compatible_edges = [
                t
                for t in self.transitions
                if t.source == from_state
                and t.target in template.states
                and _canonical_action_mapping(t.action).get("type") == proposed_type
                and self._compatible_with_proposed_identity(
                    _canonical_action_mapping(t.action), proposed_key, proposed_fields
                )
            ]
            if len(identity_compatible_edges) == 1:
                t = identity_compatible_edges[0]
                return TransitionLookup(
                    status=TransitionLookupStatus.MATCH,
                    transition=t,
                    target_state_id=t.target,
                )
            if len(identity_compatible_edges) > 1:
                return self._template_conflict_lookup(
                    identity_compatible_edges, proposed_key, item_fields
                )
            # No identity-compatible concrete edge exists; the action
            # claims to bind to the template but the FSM has no record
            # of an item-level edge with that identity.
            return self._template_binding_missing_lookup()

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
            "schema_version": "2",
            "initial_state": self.initial_state,
            "states": {sid: s.model_dump_compat() for sid, s in self.states.items()},
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
