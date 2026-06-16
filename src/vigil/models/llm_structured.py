"""Lean, strict LLM-facing response models for guard/invariant generation.

The active generation path asks the LLM for the smallest typed object that can be
compiled and admitted deterministically. Audit/provenance/confidence/rejection reports are
owned by structured-output plumbing and symbolic admission, not by the model response.

Runtime models in :mod:`vigil.models.guard` and :mod:`vigil.models.invariant_candidate`
remain richer for compatibility and reporting. These schema models are intentionally
narrower: no open dicts, no free-form notes, no LLM self-rated confidence, no semantic
completeness labels, and no effect-hint side channel.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vigil.models.guard import (
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
    InvariantGuardCandidatePacket,
    StateInvariantCandidate,
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
    "unknown",
]
SlotTypeLiteral = Literal["string", "number", "boolean", "enum", "unknown"]
PredicateTypeLiteral = Literal[
    "read",
    "value",
    "action",
    "contains",
    "count",
]
OperatorLiteral = Literal["==", "!=", ">", "<", ">=", "<=", "contains", "not_contains"]
InvariantKindLiteral = Literal[
    "structural",
    "stable_label",
    "container_shape",
    "form_status",
    "status",
    "semantic_role",
    "unknown",
]


class StrictValueRef(BaseModel):
    """RHS value reference restricted to the two kinds the LLM may propose."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["literal", "intent"]
    value: str | float | bool | None = None
    slot: str = ""

    def to_runtime(self) -> ValueRef:
        if self.kind == "literal":
            return ValueRef(kind="literal", value=self.value)
        return ValueRef(kind="intent", slot=self.slot)


class StrictPredicateSpec(BaseModel):
    """One typed predicate. The runtime ``args`` open dict is intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    predicate_type: PredicateTypeLiteral
    element: str = ""
    property: str = ""
    operator: OperatorLiteral = "=="
    expected: StrictValueRef = Field(default_factory=lambda: StrictValueRef(kind="literal"))

    def to_runtime(self) -> PredicateSpec:
        return PredicateSpec(
            predicate_type=self.predicate_type,
            element=self.element,
            property=self.property,
            operator=self.operator,
            expected=self.expected.to_runtime(),
            args={},
            source="llm",
        )


class StrictIntentSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    slot_type: SlotTypeLiteral = "string"

    def to_runtime(self) -> IntentSlot:
        return IntentSlot(
            name=self.name,
            slot_type=SlotType(self.slot_type),
            description="",
            required=True,
            value_domain=[],
            source="llm",
        )


class StrictGuardContract(BaseModel):
    """Minimal guard contract shape emitted by the LLM."""

    model_config = ConfigDict(extra="forbid")

    kind: GuardKindLiteral = "unknown"
    slots: list[StrictIntentSlot] = Field(default_factory=list)
    predicates: list[StrictPredicateSpec] = Field(default_factory=list)

    def to_runtime(self) -> GuardContract:
        required = bool(self.predicates) and self.kind != "none"
        return GuardContract(
            kind=GuardKind(self.kind),
            required=required,
            required_slots=[slot.to_runtime() for slot in self.slots],
            predicates=[pred.to_runtime() for pred in self.predicates],
            admission_status=GuardAdmissionStatus.PENDING,
            admission_reason="",
        )


class LlmTransitionGuardResponse(BaseModel):
    """Canonical guard response: exactly one minimal contract."""

    model_config = ConfigDict(extra="forbid")

    contract: StrictGuardContract = Field(default_factory=StrictGuardContract)

    def to_runtime(self) -> LlmGuardContractCandidate:
        contract = self.contract.to_runtime()
        return LlmGuardContractCandidate(
            contract=contract,
            rejection_reason="",
        )


class StrictStateInvariantCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: InvariantKindLiteral = "unknown"
    expr: str = ""

    def to_runtime(self) -> StateInvariantCandidate:
        return StateInvariantCandidate(
            kind=self.kind,
            expr=self.expr,
            admission_target="runtime_state_invariant",
            source="llm",
        )


class LlmInvariantGuardResponse(BaseModel):
    """Canonical invariant response: minimal state-invariant candidates only."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[StrictStateInvariantCandidate] = Field(default_factory=list)

    def to_runtime(self) -> InvariantGuardCandidatePacket:
        return InvariantGuardCandidatePacket(
            state_invariant_candidates=[candidate.to_runtime() for candidate in self.candidates],
        )
