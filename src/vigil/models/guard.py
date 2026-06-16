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
    mirrors the supported contract vocabulary; the compiler lowers executable
    ``PredicateSpec`` objects into concrete DSL syntax during the compilation pass.
    The ``contains`` predicate type is compatibility IR that lowers to
    ``value(element) contains ...``.
    """

    predicate_type: Literal[
        "read",
        "value",
        "action",
        "contains",
        "count",
        "in_state",
        "time_in",
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


class GuardContract(BaseModel):
    """Typed, pre-compilation description of a transition's intended guard.

    A ``GuardContract`` is synthesis IR / metadata attached to a ``Transition``. It
    captures the guard's kind, required intent slots, typed predicates, and admission
    bookkeeping. It does not by itself determine a runtime verdict — the executable
    guard remains the compiled ``Transition.guard`` DSL string.
    """

    kind: GuardKind = GuardKind.UNKNOWN
    required: bool = False
    required_slots: list[IntentSlot] = Field(default_factory=list)
    predicates: list[PredicateSpec] = Field(default_factory=list)
    binding_requirements: list[BindingRequirement] = Field(default_factory=list)
    admission_status: GuardAdmissionStatus = GuardAdmissionStatus.PENDING
    admission_reason: str = ""
    confidence: float = 0.0
    provenance: list[str] = Field(default_factory=list)
    notes: str = ""
    # Legacy audit metadata from older guard-generation schemas; not a runtime gate.
    semantic_binding_required: bool = False
    # Legacy audit metadata from older guard-generation schemas; admission no longer
    # rejects or downgrades guards based on semantic-completeness labels.
    semantic_binding_incomplete: bool = False


class LlmGuardContractCandidate(BaseModel):
    """An LLM-produced guard-contract candidate, before admission.

    The LLM emits a typed transition :class:`GuardContract` (never free-form DSL) under
    provider structured output. This thin wrapper carries the parsed contract plus the
    model's self-reported completeness / rejection signal and structured-output provenance.

    ``parsed_ok`` is the authoritative success flag: ``True`` only when the provider returned
    a schema-valid object. When ``False`` (structured output unavailable, refusal, or
    validation failure) the pipeline must treat this as a clear rejection — it must NOT run
    admission on the empty placeholder ``contract`` or attach guard metadata as if admission
    succeeded.

    ``raw_response`` and the structured-output metadata are audit-only; they are attached by
    the generator/pipeline and are **not** serialized into the FSM.
    """

    contract: GuardContract = Field(default_factory=GuardContract)
    semantic_binding_incomplete: bool = False
    rejection_reason: str = ""
    raw_response: str = ""
    raw_responses: list[str] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
    repair_attempted: bool = False
    # Structured-output provenance (audit only).
    parsed_ok: bool = False
    schema_name: str = ""
    schema_hash: str = ""
    schema_constraint_mode: str = ""
    provider: str = ""
    model: str = ""
    refusal: str = ""
    validation_errors: list[str] = Field(default_factory=list)
    spec_hash: str = ""
    # Populated only by the opt-in legacy audit migration utility.
    normalization_warnings: list[str] = Field(default_factory=list)
