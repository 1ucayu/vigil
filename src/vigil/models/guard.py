"""Typed guard-contract models for contract-first DSL guard generation.

These models are synthesis intermediate representation (IR) / metadata for the
guard-generation pipeline:

    existing FSM + trace evidence -> transition evidence view -> widget registry
    -> typed GuardContract -> DSL compilation -> admission validation
    -> attach guard metadata to the FSM

A ``GuardContract`` describes the intended pre-action guard for a transition in a
typed, compiler-friendly form. It is *not* a runtime verdict source by itself: the
executable backend remains ``Transition.guard`` (a DSL string consumed by
``DSLEvaluator``). This module deliberately contains no registry, compiler,
admission validator, or LLM logic — only the data models that later stages fill in
and consume.

Models are permissive (sensible defaults, optional fields) so future compiler and
admission passes can populate them incrementally, while still being typed enough to
catch obvious construction mistakes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RiskLevel(StrEnum):
    """Legacy report metadata kept for serialized FSM compatibility.

    Runtime guard obligations are represented by ``required`` and
    ``semantic_binding_required``; admission must not infer obligations from this
    metadata.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class GuardKind(StrEnum):
    """Category of guard a contract represents."""

    NONE = "none"
    NAVIGATION = "navigation"
    ITEM_BINDING = "item_binding"
    INPUT_BINDING = "input_binding"
    TOGGLE_BINDING = "toggle_binding"
    FORM_CHECK = "form_check"
    CONFIRM_COMMIT = "confirm_commit"
    SAFETY_CHECK = "safety_check"
    INVARIANT_HINT = "invariant_hint"
    UNKNOWN = "unknown"


class SlotType(StrEnum):
    """Value type of an intent slot."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"
    UNKNOWN = "unknown"


class GuardAdmissionStatus(StrEnum):
    """Lifecycle status of a guard contract through admission validation."""

    PENDING = "pending"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    LOW_TRUST = "low_trust"


class IntentSlot(BaseModel):
    """A frozen ``$intent.*`` variable the guard may reference.

    Slots describe the typed inputs a guard binds against (e.g. a recipient name,
    a transfer amount). They are populated during contract synthesis and resolved
    against the contract slot schema during admission.
    """

    name: str
    slot_type: SlotType = SlotType.STRING
    description: str = ""
    required: bool = True
    value_domain: list[str] = Field(default_factory=list)
    source: str = "generated"


class ValueRef(BaseModel):
    """A reference to a value used on one side of a predicate.

    ``kind`` selects where the value comes from:

    - ``literal``: a constant in ``value``.
    - ``intent``: a frozen intent slot named by ``slot``.
    - ``action``: a property of the proposed action named by ``property``.
    - ``read``: a property (``property``) read off a source-screen ``element``.
    """

    kind: Literal["literal", "intent", "action", "read"]
    value: Any | None = None
    slot: str | None = None
    property: str | None = None
    element: str | None = None


class PredicateSpec(BaseModel):
    """A single typed predicate within a guard contract.

    This is the contract-level analogue of one DSL predicate. ``predicate_type``
    mirrors the supported DSL predicate vocabulary; the compiler lowers a
    ``PredicateSpec`` into concrete DSL syntax during the (later) compilation pass.
    """

    predicate_type: Literal[
        "read",
        "value",
        "action",
        "contains",
        "count",
        "in_state",
        "time_in",
        "appeared",
        "disappeared",
        "value_changed",
    ]
    element: str | None = None
    property: str | None = None
    operator: str | None = None
    expected: ValueRef | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    source: str = "generated"


class BindingRequirement(BaseModel):
    """A typed UI/action-side (``$bind.*``) binding requirement — metadata only.

    ``$bind.*`` variables describe a binding that must be resolved from the *source
    screen / proposed action / row / selector* (e.g. the selected payee chip, this
    row's product id). The current DSL grammar and :class:`DSLEvaluator` do **not**
    support ``$bind.*``, so a ``BindingRequirement`` is **never** compiled into an
    executable predicate and **never** counts toward semantic completeness. It is
    recorded so the enriched FSM and reports can surface unresolved UI-side bindings
    until grammar/evaluator support exists.
    """

    name: str
    bind_kind: str = ""
    description: str = ""
    value_domain: list[str] = Field(default_factory=list)
    source: str = "generated"


class EffectRequirement(BaseModel):
    """A typed, audit-side postcondition/effect requirement.

    These records describe what should be checked after a transition arrives at its
    target state, such as content appearing, an item disappearing, or a value
    changing. Supported effect kinds are lowered into executable/auditable Psi DSL
    during postcondition admission when they carry enough grounding fields.
    """

    name: str
    effect_kind: str = ""
    description: str = ""
    element: str | None = None
    property: str | None = None
    before: ValueRef | None = None
    after: ValueRef | None = None
    evidence: str = ""
    source: str = "generated"
    unsupported_reason: str = ""


class GuardContract(BaseModel):
    """Typed, pre-compilation description of a transition's intended guard.

    A ``GuardContract`` is synthesis IR / metadata attached to a ``Transition``. It
    captures the guard's kind, legacy report metadata, required intent slots,
    and typed predicates, plus admission bookkeeping. It does not by itself determine
    a runtime verdict — the executable guard remains the compiled ``Transition.guard``
    DSL string.
    """

    kind: GuardKind = GuardKind.UNKNOWN
    required: bool = False
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    required_slots: list[IntentSlot] = Field(default_factory=list)
    predicates: list[PredicateSpec] = Field(default_factory=list)
    binding_requirements: list[BindingRequirement] = Field(default_factory=list)
    admission_status: GuardAdmissionStatus = GuardAdmissionStatus.PENDING
    admission_reason: str = ""
    confidence: float = 0.0
    provenance: list[str] = Field(default_factory=list)
    notes: str = ""
    # Whether the app/spec/task policy requires a semantic (intent-binding) guard.
    semantic_binding_required: bool = False
    # Whether the admitted guard lacks a complete semantic binding (e.g. only
    # enabledness / structural predicates survived, or the only binding is a
    # non-executable ``$bind.*`` requirement). Always False unless deliberately set.
    semantic_binding_incomplete: bool = False


class TransitionPostcondition(BaseModel):
    """Typed, pre-admission description of a transition's intended postcondition.

    ``TransitionPostcondition`` is the target/effect-side sibling of
    :class:`GuardContract`: it records candidate ``Psi`` predicates and effect
    obligations proposed by the LLM. Executable predicates are admitted separately into
    ``Transition.postcondition``; effect requirements remain audit-only metadata unless
    a future evaluator can express them.
    """

    kind: str = "unknown"
    required: bool = False
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    required_slots: list[IntentSlot] = Field(default_factory=list)
    predicates: list[PredicateSpec] = Field(default_factory=list)
    effect_requirements: list[EffectRequirement] = Field(default_factory=list)
    intent_effect_required: bool = False
    intent_effect_incomplete: bool = False
    confidence: float = 0.0
    provenance: list[str] = Field(default_factory=list)
    notes: str = ""


class LlmGuardContractCandidate(BaseModel):
    """An LLM-produced guard-contract candidate, before admission.

    The LLM emits a typed precondition :class:`GuardContract` (never free-form DSL)
    and may also emit a target/effect-side :class:`TransitionPostcondition`. This
    thin wrapper carries the parsed contract plus the model's self-reported
    completeness / rejection signal. ``raw_response`` is audit-only and is attached
    by the pipeline, never requested from the model; it is **not** serialized into
    the FSM.
    """

    contract: GuardContract = Field(default_factory=GuardContract)
    postcondition: TransitionPostcondition | None = None
    semantic_binding_incomplete: bool = False
    postcondition_incomplete: bool = False
    rejection_reason: str = ""
    raw_response: str = ""
    raw_responses: list[str] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
    repair_attempted: bool = False
