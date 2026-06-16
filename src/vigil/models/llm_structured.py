"""Strict, LLM-facing response models for schema-constrained guard/invariant generation.

These models are the *single canonical output shape* the LLM emits under provider structured
output. They are deliberately narrower than the runtime models in :mod:`vigil.models.guard`
and :mod:`vigil.models.invariant_candidate` so the generated JSON Schema is strict-safe across
providers (OpenAI strict, Google constrained decoding, Anthropic tool-use):

- ``model_config = ConfigDict(extra="forbid")`` — exactly one shape, no smuggled keys.
- only concrete types — no open ``Any`` / ``dict[str, Any]`` fields (which strict providers
  reject server-side) and no top-level ``anyOf``.
- ``StrictValueRef`` is restricted to ``literal`` / ``intent`` (the only RHS kinds the LLM may
  propose); UI/action-side bindings stay in ``binding_requirements`` metadata.
- the LLM never emits admission status/reason, runtime verdicts, graph mutations, replay
  confidence, or state/transition ids — those fields are simply absent here.

Each model exposes ``.to_runtime()`` to convert into the existing runtime model that the
deterministic admission layer consumes. The runtime models stay untouched.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vigil.models.guard import (
    BindingRequirement,
    GuardAdmissionStatus,
    GuardContract,
    GuardKind,
    IntentSlot,
    LlmGuardContractCandidate,
    PredicateSpec,
    SlotType,
    ValueRef,
)
from vigil.models.invariant_candidate import (
    EffectInvariantHint,
    InvariantGuardCandidatePacket,
    RejectedCandidate,
    StateInvariantCandidate,
    TransitionGuardCandidate,
)

GuardKindLiteral = Literal[
    "none",
    "navigation",
    "item_binding",
    "input_binding",
    "toggle_binding",
    "form_check",
    "confirm_commit",
    "safety_check",
    "invariant_hint",
    "unknown",
]
SlotTypeLiteral = Literal["string", "number", "boolean", "enum", "unknown"]
PredicateTypeLiteral = Literal[
    "read",
    "value",
    "action",
    "contains",
    "count",
    "in_state",
    "time_in",
]


class StrictValueRef(BaseModel):
    """RHS value reference restricted to the two kinds the LLM may propose."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["literal", "intent"]
    literal_value: str | float | bool | None = None
    intent_slot: str | None = None

    def to_runtime(self) -> ValueRef:
        if self.kind == "literal":
            return ValueRef(kind="literal", value=self.literal_value)
        return ValueRef(kind="intent", slot=self.intent_slot)


class StrictPredicateSpec(BaseModel):
    """One typed predicate. The runtime ``args`` open dict is intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    predicate_type: PredicateTypeLiteral
    element: str | None = None
    property: str | None = None
    operator: str | None = None
    expected: StrictValueRef | None = None
    source: str = "generated"

    def to_runtime(self) -> PredicateSpec:
        return PredicateSpec(
            predicate_type=self.predicate_type,
            element=self.element,
            property=self.property,
            operator=self.operator,
            expected=self.expected.to_runtime() if self.expected is not None else None,
            args={},
            source=self.source,
        )


class StrictIntentSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    slot_type: SlotTypeLiteral = "string"
    description: str = ""
    required: bool = True
    value_domain: list[str] = Field(default_factory=list)
    source: str = "generated"

    def to_runtime(self) -> IntentSlot:
        return IntentSlot(
            name=self.name,
            slot_type=SlotType(self.slot_type),
            description=self.description,
            required=self.required,
            value_domain=self.value_domain,
            source=self.source,
        )


class StrictBindingRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    bind_kind: str = ""
    description: str = ""
    value_domain: list[str] = Field(default_factory=list)
    source: str = "generated"

    def to_runtime(self) -> BindingRequirement:
        return BindingRequirement(
            name=self.name,
            bind_kind=self.bind_kind,
            description=self.description,
            value_domain=self.value_domain,
            source=self.source,
        )


class StrictGuardContract(BaseModel):
    """Strict contract shape. Omits admission_status/admission_reason (deterministic-owned)."""

    model_config = ConfigDict(extra="forbid")

    kind: GuardKindLiteral = "unknown"
    required: bool = False
    required_slots: list[StrictIntentSlot] = Field(default_factory=list)
    predicates: list[StrictPredicateSpec] = Field(default_factory=list)
    binding_requirements: list[StrictBindingRequirement] = Field(default_factory=list)
    semantic_binding_required: bool = False
    semantic_binding_incomplete: bool = False
    confidence: float = 0.0
    provenance: list[str] = Field(default_factory=list)
    notes: str = ""

    def to_runtime(self) -> GuardContract:
        return GuardContract(
            kind=GuardKind(self.kind),
            required=self.required,
            required_slots=[slot.to_runtime() for slot in self.required_slots],
            predicates=[pred.to_runtime() for pred in self.predicates],
            binding_requirements=[bind.to_runtime() for bind in self.binding_requirements],
            admission_status=GuardAdmissionStatus.PENDING,
            admission_reason="",
            confidence=self.confidence,
            provenance=self.provenance,
            notes=self.notes,
            semantic_binding_required=self.semantic_binding_required,
            semantic_binding_incomplete=self.semantic_binding_incomplete,
        )


class LlmGuardResponse(BaseModel):
    """Canonical guard response: exactly ``contract`` + completeness/rejection signal."""

    model_config = ConfigDict(extra="forbid")

    contract: StrictGuardContract = Field(default_factory=StrictGuardContract)
    semantic_binding_incomplete: bool = False
    rejection_reason: str = ""

    def to_runtime(self) -> LlmGuardContractCandidate:
        contract = self.contract.to_runtime()
        incomplete = contract.semantic_binding_incomplete or self.semantic_binding_incomplete
        contract.semantic_binding_incomplete = incomplete
        return LlmGuardContractCandidate(
            contract=contract,
            semantic_binding_incomplete=incomplete,
            rejection_reason=self.rejection_reason,
        )


# ---------------------------------------------------------------------------
# Invariant packet (the only strict blocker is TransitionGuardCandidate.contract)
# ---------------------------------------------------------------------------


class StrictStateInvariantCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "unknown"
    expr: str = ""
    scope: str = "state"
    admission_target: str = "runtime_state_invariant"
    confidence: float = 0.0
    evidence_count: int = 0
    source: str = "llm"
    volatility: str = "unknown"
    provenance: list[str] = Field(default_factory=list)
    notes: str = ""
    rejection_reason: str = ""

    def to_runtime(self) -> StateInvariantCandidate:
        return StateInvariantCandidate(**self.model_dump())


class StrictTransitionGuardCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_state_id: str = ""
    target_state_id: str = ""
    canonical_action_key: str = ""
    contract: StrictGuardContract = Field(default_factory=StrictGuardContract)
    semantic_binding_incomplete: bool = False
    rejection_reason: str = ""

    def to_runtime(self) -> TransitionGuardCandidate:
        return TransitionGuardCandidate(
            source_state_id=self.source_state_id,
            target_state_id=self.target_state_id,
            canonical_action_key=self.canonical_action_key,
            contract=self.contract.to_runtime(),
            semantic_binding_incomplete=self.semantic_binding_incomplete,
            rejection_reason=self.rejection_reason,
        )


class StrictEffectInvariantHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incoming_source_state_id: str = ""
    target_state_id: str = ""
    canonical_action_key: str = ""
    description: str = ""
    desired_expr: str = ""
    why_not_runtime_state_invariant: str = "unknown"
    provenance: list[str] = Field(default_factory=list)

    def to_runtime(self) -> EffectInvariantHint:
        return EffectInvariantHint(**self.model_dump())


class StrictRejectedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_type: str = "state_invariant"
    expr_or_summary: str = ""
    reason: str = ""

    def to_runtime(self) -> RejectedCandidate:
        return RejectedCandidate(**self.model_dump())


class LlmInvariantGuardResponse(BaseModel):
    """Canonical invariant/guard packet response shape."""

    model_config = ConfigDict(extra="forbid")

    state_invariant_candidates: list[StrictStateInvariantCandidate] = Field(default_factory=list)
    transition_guard_candidates: list[StrictTransitionGuardCandidate] = Field(default_factory=list)
    effect_invariant_hints: list[StrictEffectInvariantHint] = Field(default_factory=list)
    rejected_candidates: list[StrictRejectedCandidate] = Field(default_factory=list)
    notes: str = ""

    def to_runtime(self) -> InvariantGuardCandidatePacket:
        return InvariantGuardCandidatePacket(
            state_invariant_candidates=[
                candidate.to_runtime() for candidate in self.state_invariant_candidates
            ],
            transition_guard_candidates=[
                candidate.to_runtime() for candidate in self.transition_guard_candidates
            ],
            effect_invariant_hints=[hint.to_runtime() for hint in self.effect_invariant_hints],
            rejected_candidates=[candidate.to_runtime() for candidate in self.rejected_candidates],
            notes=self.notes,
        )
