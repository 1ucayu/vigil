"""Typed candidate models for contract-first invariant + guard generation.

These models mirror the ``InvariantGuardCandidatePacket`` output schema declared in
``src/vigil/system_prompt/invariant_guard_generation.spec``. They are the LLM (or
deterministic synthesizer) *candidate* IR — proposed, not admitted. Deterministic
admission (:mod:`vigil.neuro.invariant_admission`) decides which state-invariant
candidates become runtime ``AbstractState.invariant_specs`` entries; the existing guard
admission (:mod:`vigil.neuro.guard_admission`) decides transition-guard candidates.

Per project rules the LLM may only *propose*: it never decides state equality, edges,
replay confidence, or runtime verdicts. Classification fields (``kind``, ``volatility``,
``admission_target`` …) are deliberately permissive ``str`` so a slightly off-vocabulary
LLM response is not dropped wholesale — the deterministic layer is authoritative.

The transition-guard candidate reuses the existing :class:`~vigil.models.guard.GuardContract`
shape so the guard contract / admission / compiler path is shared, not reinvented.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from vigil.models.guard import GuardContract


class StateInvariantCandidate(BaseModel):
    """A proposed state invariant ``phi`` for the target state.

    ``expr`` is the runtime/admission IR: a single DSL predicate string over the
    target-state widget registry. LLM-facing structured output should emit typed
    predicates first; deterministic compilation lowers those predicates to ``expr``.
    ``admission_target`` is the LLM's intent (``runtime_state_invariant`` |
    ``metadata_only`` | ``reject``); deterministic admission still has the final say.
    """

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


class TransitionGuardCandidate(BaseModel):
    """A proposed pre-action guard ``Gamma`` for an existing transition.

    The ``contract`` reuses :class:`~vigil.models.guard.GuardContract`; admission goes
    through the existing :func:`~vigil.neuro.guard_admission.admit_guard_contract`.
    """

    source_state_id: str = ""
    target_state_id: str = ""
    canonical_action_key: str = ""
    contract: GuardContract = Field(default_factory=GuardContract)
    semantic_binding_incomplete: bool = False
    rejection_reason: str = ""


class EffectInvariantHint(BaseModel):
    """A transition-specific fact that is useful but not a current runtime state invariant.

    These are conditional/action-aware/intent-aware facts. They are metadata only and
    must never be written into
    ``AbstractState.invariant_specs`` under the current ``ScreenContext``-only checker.
    """

    incoming_source_state_id: str = ""
    target_state_id: str = ""
    canonical_action_key: str = ""
    description: str = ""
    desired_expr: str = ""
    why_not_runtime_state_invariant: str = "unknown"
    provenance: list[str] = Field(default_factory=list)


class RejectedCandidate(BaseModel):
    """A candidate explicitly rejected, with an evidence-based reason."""

    candidate_type: str = "state_invariant"
    expr_or_summary: str = ""
    reason: str = ""


class InvariantGuardCandidatePacket(BaseModel):
    """The full typed packet returned by invariant/guard candidate generation."""

    state_invariant_candidates: list[StateInvariantCandidate] = Field(default_factory=list)
    transition_guard_candidates: list[TransitionGuardCandidate] = Field(default_factory=list)
    effect_invariant_hints: list[EffectInvariantHint] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    notes: str = ""
