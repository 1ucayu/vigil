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
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    computed_field,
    model_validator,
)


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


def _kind_for_hierarchy_level(hierarchy_level: HierarchyLevel | str) -> StateKind:
    level = HierarchyLevel(hierarchy_level)
    return StateKind.DIALOG if level == HierarchyLevel.COMPONENT else StateKind.NORMAL


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
    """Legacy LLM-generated semantic annotation for an abstract state.

    Retained as a synthesized view rebuilt from ``StateAnnotations`` for
    callers (notably the legacy ``semantic_profile`` alias on
    ``AbstractState`` and external fixtures) that still expect the old
    shape. ``StateAnnotations`` is the canonical storage.
    """

    alt_text: str = ""
    page_function: str = ""
    expected_actions: list[str] = Field(default_factory=list)
    icon_labels: dict[str, str] = Field(default_factory=dict)
    generation_confidence: float = 0.0


class StateIdentity(BaseModel):
    """Deterministic identity for an abstract state.

    Explicit functional vs. structural hashes plus algorithm/version
    provenance so future identity algorithm changes do not silently break
    stored FSMs.
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
    the supporting screen ids live. ``observation_count`` is a derived
    field over ``raw_screen_ids`` so mutating the underlying list (e.g.
    appending newly-observed screens during builder merging) can never
    leave the count stale.
    """

    raw_screen_ids: list[str] = Field(default_factory=list)
    construction_source: str = "observed_trace"
    first_seen_trace: str | None = None
    trust_level: str = "observed"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def observation_count(self) -> int:
        return len(self.raw_screen_ids)


class StateAbstraction(BaseModel):
    """Dynamic container / template metadata for this state."""

    container_type: ContainerType = ContainerType.NONE
    container_selector: dict[str, Any] = Field(default_factory=dict)
    template_id: str | None = None
    template_role: str = "normal"
    parameter_schema: dict[str, str] = Field(default_factory=dict)
    parameter_bindings: dict[str, str] = Field(default_factory=dict)


class StateInvariant(BaseModel):
    """A single runtime-checkable invariant with its own confidence + provenance."""

    expr: str
    confidence: float = 0.0
    source: str = "unknown"
    evidence_count: int = 0


class StateAnnotations(BaseModel):
    """LLM-derived, non-authoritative annotations.

    Per project rules these never decide state equality, edges, replay
    confidence, or runtime verdicts. ``display_name`` is annotation-only —
    consumers must use ``AbstractState.name`` as the canonical state name;
    ``display_name`` does not override identity or routing.
    """

    display_name: str = ""
    alt_text: str = ""
    page_function: str = ""
    expected_actions: list[str] = Field(default_factory=list)
    widget_aliases: list[dict[str, Any]] = Field(default_factory=list)
    generation_confidence: float = 0.0


_MISSING: Any = object()


def _invariant_expr(value: Any) -> str:
    if isinstance(value, StateInvariant):
        return value.expr
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, dict):
        return str(value.get("expr", ""))
    return str(value)


def _invariant_confidence(value: Any) -> float:
    if isinstance(value, StateInvariant):
        return float(value.confidence)
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, dict):
        return float(value.get("confidence", 0.0))
    return 0.0


def _to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return None


def _annotations_from_semantic_profile(profile: Any) -> dict[str, Any]:
    sp = profile.model_dump() if isinstance(profile, BaseModel) else dict(profile or {})
    icon_labels = sp.get("icon_labels") or {}
    widget_aliases = [
        {"element_id": str(elem_id), "label": str(label)} for elem_id, label in icon_labels.items()
    ]
    return {
        "alt_text": sp.get("alt_text", "") or "",
        "page_function": sp.get("page_function", "") or "",
        "expected_actions": list(sp.get("expected_actions", []) or []),
        "widget_aliases": widget_aliases,
        "generation_confidence": float(sp.get("generation_confidence", 0.0) or 0.0),
    }


def _semantic_profile_from_annotations(annotations: StateAnnotations) -> StateSemanticProfile:
    icon_labels: dict[str, str] = {}
    for alias in annotations.widget_aliases:
        if not isinstance(alias, dict):
            continue
        elem_id = alias.get("element_id")
        label = alias.get("label")
        if elem_id is None or label is None:
            continue
        icon_labels[str(elem_id)] = str(label)
    return StateSemanticProfile(
        alt_text=annotations.alt_text,
        page_function=annotations.page_function,
        expected_actions=list(annotations.expected_actions),
        icon_labels=icon_labels,
        generation_confidence=annotations.generation_confidence,
    )


def _merge_value(
    nested: dict[str, Any],
    nested_key: str,
    flat_key: str,
    flat_value: Any,
    *,
    equals: Any = None,
) -> None:
    """Merge a flat alias into a nested dict, raising on real conflicts."""
    if flat_value is _MISSING:
        return
    existing = nested.get(nested_key)
    if existing is None:
        nested[nested_key] = flat_value
        return
    same = (equals or (lambda a, b: a == b))(existing, flat_value)
    if not same:
        raise ValueError(
            f"Conflicting {flat_key!r} (flat) vs {nested_key!r} (nested): "
            f"flat={flat_value!r}, nested={existing!r}"
        )


def _container_type_equal(a: Any, b: Any) -> bool:
    return ContainerType(a) == ContainerType(b)


class AbstractState(BaseModel):
    """An abstract UI state in the FSM.

    The schema is partitioned into nested canonical submodels:

    - ``identity`` (StateIdentity): functional / structural hashes.
    - ``android_context`` (AndroidStateContext): activity / package / window.
    - ``evidence`` (StateEvidence): trace-derived raw screen ids.
    - ``abstraction`` (StateAbstraction): container / template metadata.
    - ``invariant_specs`` (list[StateInvariant]): runtime-checkable invariants.
    - ``annotations`` (StateAnnotations): LLM-derived non-authoritative labels.

    Flat names (``fingerprint``, ``structural_fingerprint``, ``activity_name``,
    ``raw_screens``, ``container_type``, ``container_resource_id``,
    ``sub_fsm_template_id``, ``semantic_profile``, ``state_invariants``,
    ``invariant_confidence``) survive **only** as ``@property`` aliases that
    read and write the nested canonical fields — there is exactly one copy
    of every datum. Both old flat JSON / kwargs and the nested form
    construct cleanly via ``model_validator(mode="before")``. Mixed input
    that agrees is accepted; mixed input that disagrees raises.

    ``legacy_invariants`` is the one intentional flat survivor: a
    non-runtime bag of legacy invariant expressions kept distinct from
    ``invariant_specs`` so loading old FSMs cannot silently change verifier
    verdicts. The ``invariants`` alias reads/writes this bag only.

    ``name`` is the canonical state name. ``annotations.display_name`` is
    annotation-only and must not override identity or routing.
    """

    model_config = ConfigDict(extra="ignore")

    state_id: str
    name: str
    hierarchy_level: HierarchyLevel
    parent_state: str | None = None
    kind: StateKind = StateKind.NORMAL
    identity: StateIdentity
    android_context: AndroidStateContext = Field(default_factory=AndroidStateContext)
    evidence: StateEvidence = Field(default_factory=StateEvidence)
    abstraction: StateAbstraction = Field(default_factory=StateAbstraction)
    invariant_specs: list[StateInvariant] = Field(default_factory=list)
    annotations: StateAnnotations = Field(default_factory=StateAnnotations)
    legacy_invariants: list[str] = Field(default_factory=list)
    _kind_explicit_override: bool = PrivateAttr(default=False)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_and_nested(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # --- Pop legacy flat keys ---
        flat_fp = data.pop("fingerprint", _MISSING)
        flat_sfp = data.pop("structural_fingerprint", _MISSING)
        flat_act = data.pop("activity_name", _MISSING)
        flat_raw = data.pop("raw_screens", _MISSING)
        flat_ct = data.pop("container_type", _MISSING)
        flat_crid = data.pop("container_resource_id", _MISSING)
        flat_tid = data.pop("sub_fsm_template_id", _MISSING)
        flat_sp = data.pop("semantic_profile", _MISSING)
        flat_st_inv = data.pop("state_invariants", _MISSING)
        flat_inv_conf = data.pop("invariant_confidence", _MISSING)
        flat_inv_legacy = data.pop("invariants", _MISSING)

        # --- Normalise nested submodel inputs to dicts ---
        identity = _to_dict(data.get("identity")) or {}
        android = _to_dict(data.get("android_context")) or {}
        evidence = _to_dict(data.get("evidence")) or {}
        abstraction = _to_dict(data.get("abstraction")) or {}
        annotations = _to_dict(data.get("annotations")) or {}

        # --- Identity ---
        _merge_value(identity, "functional_hash", "fingerprint", flat_fp)
        _merge_value(identity, "structural_hash", "structural_fingerprint", flat_sfp)
        if "functional_hash" not in identity:
            raise ValueError(
                "AbstractState requires either 'fingerprint' or 'identity.functional_hash'"
            )
        data["identity"] = identity

        # --- Android context ---
        _merge_value(android, "activity_name", "activity_name", flat_act)
        if android:
            data["android_context"] = android

        # --- Evidence ---
        if flat_raw is not _MISSING:
            existing = evidence.get("raw_screen_ids")
            if existing is None:
                evidence["raw_screen_ids"] = list(flat_raw)
            elif list(existing) != list(flat_raw):
                raise ValueError(
                    "Conflicting 'raw_screens' (flat) vs "
                    "'evidence.raw_screen_ids' (nested): "
                    f"flat={list(flat_raw)!r}, nested={list(existing)!r}"
                )
        # Reject only when an explicit legacy observation_count disagrees
        # with the derived value; otherwise accept and let the computed
        # field redo the math.
        legacy_obs = evidence.pop("observation_count", _MISSING)
        if legacy_obs is not _MISSING:
            derived = len(evidence.get("raw_screen_ids", []) or [])
            if int(legacy_obs) != derived:
                raise ValueError(
                    "Conflicting 'observation_count' "
                    f"({legacy_obs}) vs len(raw_screen_ids)={derived}"
                )
        if evidence:
            data["evidence"] = evidence

        # --- Abstraction ---
        _merge_value(
            abstraction,
            "container_type",
            "container_type",
            flat_ct,
            equals=_container_type_equal,
        )
        _merge_value(abstraction, "template_id", "sub_fsm_template_id", flat_tid)
        if flat_crid is not _MISSING:
            selector = dict(abstraction.get("container_selector") or {})
            existing_crid = selector.get("resource_id")
            if existing_crid is None:
                if flat_crid is not None:
                    selector["resource_id"] = flat_crid
            elif existing_crid != flat_crid:
                raise ValueError(
                    "Conflicting 'container_resource_id' (flat) vs "
                    "'abstraction.container_selector.resource_id' (nested): "
                    f"flat={flat_crid!r}, nested={existing_crid!r}"
                )
            if selector:
                abstraction["container_selector"] = selector
        if abstraction:
            data["abstraction"] = abstraction

        # --- Annotations / semantic_profile ---
        if flat_sp is not _MISSING and flat_sp is not None:
            sp_annotations = _annotations_from_semantic_profile(flat_sp)
            if annotations:
                # Reconcile only meaningful overlap (alt_text, page_function,
                # expected_actions). widget_aliases is recomputed from
                # icon_labels on every load — trust the nested form when
                # both are present and non-empty.
                for key in ("alt_text", "page_function"):
                    flat_val = sp_annotations.get(key, "")
                    nested_val = annotations.get(key, "")
                    if flat_val and nested_val and flat_val != nested_val:
                        raise ValueError(
                            f"Conflicting 'semantic_profile.{key}' (flat) vs "
                            f"'annotations.{key}' (nested): "
                            f"flat={flat_val!r}, nested={nested_val!r}"
                        )
                    if not nested_val and flat_val:
                        annotations[key] = flat_val
                if not annotations.get("widget_aliases") and sp_annotations.get("widget_aliases"):
                    annotations["widget_aliases"] = sp_annotations["widget_aliases"]
                if not annotations.get("expected_actions") and sp_annotations.get(
                    "expected_actions"
                ):
                    annotations["expected_actions"] = sp_annotations["expected_actions"]
                if (
                    annotations.get("generation_confidence", 0.0) == 0.0
                    and sp_annotations.get("generation_confidence", 0.0) != 0.0
                ):
                    annotations["generation_confidence"] = sp_annotations["generation_confidence"]
            else:
                annotations = sp_annotations
        if annotations:
            data["annotations"] = annotations

        # --- Invariant specs (runtime) ---
        if "invariant_specs" in data and data["invariant_specs"] is not None:
            specs = list(data["invariant_specs"])
            if flat_st_inv is not _MISSING and flat_st_inv is not None:
                spec_exprs = [_invariant_expr(s) for s in specs]
                flat_exprs = [str(e) for e in flat_st_inv]
                if spec_exprs != flat_exprs:
                    raise ValueError(
                        "Conflicting 'invariant_specs' vs 'state_invariants': "
                        f"specs={spec_exprs!r}, state_invariants={flat_exprs!r}"
                    )
            if flat_inv_conf is not _MISSING and flat_inv_conf is not None:
                max_spec = max((_invariant_confidence(s) for s in specs), default=0.0)
                if abs(max_spec - float(flat_inv_conf)) > 1e-9:
                    raise ValueError(
                        "Conflicting 'invariant_confidence' "
                        f"({float(flat_inv_conf)}) vs "
                        f"max(invariant_specs.confidence)={max_spec}"
                    )
        elif flat_st_inv is not _MISSING and flat_st_inv is not None:
            has_conf = flat_inv_conf is not _MISSING and flat_inv_conf is not None
            confidence = float(flat_inv_conf) if has_conf else 0.0
            source = "mined_multivisit" if has_conf else "unknown"
            data["invariant_specs"] = [
                {"expr": str(e), "confidence": confidence, "source": source} for e in flat_st_inv
            ]

        # --- Legacy invariants (non-runtime; never merged into specs) ---
        if flat_inv_legacy is not _MISSING and flat_inv_legacy is not None:
            existing = list(data.get("legacy_invariants") or [])
            for expr in flat_inv_legacy:
                text = str(expr)
                if text not in existing:
                    existing.append(text)
            data["legacy_invariants"] = existing

        return data

    @model_validator(mode="after")
    def _sync_kind_from_hierarchy_level(self) -> AbstractState:
        explicit_non_default_kind = (
            "kind" in self.model_fields_set and self.kind != StateKind.NORMAL
        )
        object.__setattr__(self, "_kind_explicit_override", explicit_non_default_kind)
        if not explicit_non_default_kind:
            object.__setattr__(self, "kind", _kind_for_hierarchy_level(self.hierarchy_level))
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "hierarchy_level":
            level = HierarchyLevel(value)
            super().__setattr__(name, level)
            if not self._kind_explicit_override:
                super().__setattr__("kind", _kind_for_hierarchy_level(level))
            return
        if name == "kind":
            kind = StateKind(value)
            explicit_override = kind != StateKind.NORMAL
            object.__setattr__(self, "_kind_explicit_override", explicit_override)
            if not explicit_override:
                kind = _kind_for_hierarchy_level(self.hierarchy_level)
            super().__setattr__(name, kind)
            return
        super().__setattr__(name, value)

    # --- Backward-compatible flat aliases (no Pydantic storage) ---

    @property
    def fingerprint(self) -> str:
        return self.identity.functional_hash

    @fingerprint.setter
    def fingerprint(self, value: str) -> None:
        self.identity.functional_hash = str(value)

    @property
    def structural_fingerprint(self) -> str | None:
        return self.identity.structural_hash

    @structural_fingerprint.setter
    def structural_fingerprint(self, value: str | None) -> None:
        self.identity.structural_hash = value

    @property
    def activity_name(self) -> str | None:
        return self.android_context.activity_name

    @activity_name.setter
    def activity_name(self, value: str | None) -> None:
        self.android_context.activity_name = value

    @property
    def raw_screens(self) -> list[str]:
        # Return the live list reference so callers can ``.extend()`` / append.
        return self.evidence.raw_screen_ids

    @raw_screens.setter
    def raw_screens(self, value: list[str]) -> None:
        self.evidence.raw_screen_ids = list(value)

    @property
    def container_type(self) -> ContainerType:
        return self.abstraction.container_type

    @container_type.setter
    def container_type(self, value: ContainerType | str) -> None:
        self.abstraction.container_type = ContainerType(value)

    @property
    def container_resource_id(self) -> str | None:
        return self.abstraction.container_selector.get("resource_id")

    @container_resource_id.setter
    def container_resource_id(self, value: str | None) -> None:
        selector = self.abstraction.container_selector
        if value is None:
            selector.pop("resource_id", None)
        else:
            selector["resource_id"] = value

    @property
    def sub_fsm_template_id(self) -> str | None:
        return self.abstraction.template_id

    @sub_fsm_template_id.setter
    def sub_fsm_template_id(self, value: str | None) -> None:
        self.abstraction.template_id = value

    @property
    def semantic_profile(self) -> StateSemanticProfile | None:
        if (
            not self.annotations.alt_text
            and not self.annotations.page_function
            and not self.annotations.expected_actions
            and not self.annotations.widget_aliases
            and self.annotations.generation_confidence == 0.0
        ):
            return None
        return _semantic_profile_from_annotations(self.annotations)

    @semantic_profile.setter
    def semantic_profile(self, value: Any) -> None:
        if value is None:
            self.annotations = StateAnnotations(display_name=self.annotations.display_name)
            return
        payload = _annotations_from_semantic_profile(value)
        payload["display_name"] = self.annotations.display_name
        self.annotations = StateAnnotations(**payload)

    @property
    def state_invariants(self) -> list[str]:
        return [spec.expr for spec in self.invariant_specs]

    @state_invariants.setter
    def state_invariants(self, value: list[str]) -> None:
        confidence = self.invariant_confidence
        prior_conf = {spec.expr: spec.confidence for spec in self.invariant_specs}
        new_specs = [
            StateInvariant(
                expr=str(expr),
                confidence=prior_conf.get(str(expr), confidence),
                source=(
                    "mined_multivisit" if prior_conf.get(str(expr), confidence) > 0.0 else "unknown"
                ),
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
            spec.confidence = value
            if spec.source == "unknown" and value > 0.0:
                spec.source = "mined_multivisit"

    @property
    def invariants(self) -> list[str]:
        """Deprecated legacy alias — kept out of runtime-enforced invariants."""
        return list(self.legacy_invariants)

    @invariants.setter
    def invariants(self, value: list[str]) -> None:
        object.__setattr__(self, "legacy_invariants", [str(expr) for expr in value])

    def model_dump_with_legacy_mirrors(self) -> dict[str, Any]:
        """Dump schema v3 plus transitional flat compatibility mirrors.

        Schema v3 canonical storage remains the nested submodels above. For one
        migration window, serialized FSM JSON also includes legacy flat fields
        computed from those nested fields so raw-JSON consumers can migrate
        without reading stale duplicate state.
        """
        data = self.model_dump(mode="json")
        semantic_profile = self.semantic_profile
        data.update(
            {
                "fingerprint": self.fingerprint,
                "structural_fingerprint": self.structural_fingerprint,
                "activity_name": self.activity_name,
                "raw_screens": list(self.raw_screens),
                "container_type": self.container_type.value,
                "container_resource_id": self.container_resource_id,
                "sub_fsm_template_id": self.sub_fsm_template_id,
                "semantic_profile": (
                    semantic_profile.model_dump(mode="json")
                    if semantic_profile is not None
                    else None
                ),
                "state_invariants": list(self.state_invariants),
                "invariant_confidence": self.invariant_confidence,
                "invariants": list(self.invariants),
            }
        )
        return data


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

    def model_dump_with_legacy_mirrors(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["states"] = {
            sid: state.model_dump_with_legacy_mirrors() for sid, state in self.states.items()
        }
        data["transitions"] = [t.model_dump(mode="json") for t in self.transitions]
        return data


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
            and state.abstraction.template_id
            and state.abstraction.container_type == ContainerType.DYNAMIC
            and proposed_type == "click"
        ):
            template = self.sub_fsm_templates.get(state.abstraction.template_id)
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
            if state.identity.functional_hash == fingerprint:
                return state.state_id
        return None

    def serialize(self, path: str | Path) -> None:
        """Serialize the FSM to schema v3 JSON with migration-window flat mirrors."""
        path = Path(path)
        data = {
            "app_package": self.app_package,
            "version": self.version,
            "schema_version": "3",
            "initial_state": self.initial_state,
            "states": {sid: s.model_dump_with_legacy_mirrors() for sid, s in self.states.items()},
            "transitions": [t.model_dump() for t in self.transitions],
            "evolution_log": self.evolution_log,
            "sub_fsm_templates": {
                tid: t.model_dump_with_legacy_mirrors() for tid, t in self.sub_fsm_templates.items()
            },
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
