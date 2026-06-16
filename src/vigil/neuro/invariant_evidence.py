"""Per-state invariant evidence view for contract-first invariant generation.

An :class:`InvariantEvidence` is the deterministic, LLM-free evidence bundle for one
*arrival* FSM state that the invariant/guard candidate generator consumes. It mirrors
:mod:`vigil.neuro.guard_evidence` (which is per-transition) but is keyed per-state. It
joins:

- the target-state identity / page-function annotations,
- the arrival-state :class:`~vigil.neuro.guard_registry.WidgetRegistry`,
- every resolvable raw-screen observation of the state (repeated visits are stronger
  evidence than a single screenshot, and are what the admission layer replays against),
- the existing ``invariant_specs`` already on the state,
- incoming / outgoing transition summaries (for side-effect classification + guard candidates),
- static-prior hints (``priors only, never proof``).

This module only *reads* the FSM and raw trace screens. It adds no edges, changes no
state identity / replay confidence, compiles no DSL, validates no admission, and calls no
LLM.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from vigil.models.fsm import StateInvariant, canonical_action_key
from vigil.neuro.guard_evidence import static_prior_hints
from vigil.neuro.guard_registry import WidgetRegistry, _as_dict, build_widget_registry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.models.fsm import AbstractState, AppFSM
    from vigil.neuro.app_prior import AppPrior


def canonical_action_key_str(action: dict[str, Any]) -> str:
    """Lossless, stable string form of the canonical action identity ``<tau, q, v>``.

    Encodes every *present* field of :func:`~vigil.models.fsm.canonical_action_key` as a
    deterministic JSON object. Only ``None`` (absent) fields are dropped, so falsy-but-real
    identity values such as ``0`` and ``False`` are preserved — unlike a truthiness filter,
    which would collapse ``value=0`` and an absent ``value`` into the same string and let
    distinct actions alias. Field order follows ``_ACTION_KEY_FIELDS``, so the string is
    byte-for-byte reproducible for prompt rendering and best-effort transition matching.

    This does not weaken or replace ``canonical_action_key``, which stays the identity
    authority.
    """
    present = {field: value for field, value in canonical_action_key(action) if value is not None}
    return json.dumps(present, ensure_ascii=False, default=str)


class TransitionSummary(BaseModel):
    """A lightweight incoming/outgoing transition view for one state."""

    source_state_id: str
    target_state_id: str
    action: dict[str, Any] = Field(default_factory=dict)
    canonical_action_key: str = ""
    replay_confidence: float = 0.0
    low_trust: bool = False


class InvariantEvidence(BaseModel):
    """Deterministic per-state evidence bundle for invariant/guard synthesis."""

    target_state_id: str
    target_state_name: str = ""
    activity_name: str = ""
    window_name: str = ""
    container_type: str = ""
    template_id: str = ""
    page_function: str = ""
    display_name: str = ""
    raw_screen_ids: list[str] = Field(default_factory=list)
    observation_count: int = 0
    observations: list[dict[str, Any]] = Field(default_factory=list)
    existing_invariant_specs: list[StateInvariant] = Field(default_factory=list)
    arrival_registry: WidgetRegistry
    incoming: list[TransitionSummary] = Field(default_factory=list)
    outgoing: list[TransitionSummary] = Field(default_factory=list)
    static_prior_hints: list[str] = Field(default_factory=list)


def _resolve_observations(
    state: AbstractState,
    raw_screens: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return every raw-screen dict of ``state`` that resolves in ``raw_screens``."""
    observations: list[dict[str, Any]] = []
    for sid in state.evidence.raw_screen_ids:
        candidate = raw_screens.get(sid) if raw_screens else None
        if candidate is None:
            continue
        observations.append(_as_dict(candidate))
    return observations


def _transition_summary(transition: Any) -> TransitionSummary:
    return TransitionSummary(
        source_state_id=transition.source,
        target_state_id=transition.target,
        action=transition.action,
        canonical_action_key=canonical_action_key_str(transition.action),
        replay_confidence=transition.confidence,
        low_trust=transition.low_trust,
    )


def build_invariant_evidence(
    fsm: AppFSM,
    state: AbstractState,
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None = None,
) -> InvariantEvidence:
    """Build the :class:`InvariantEvidence` view for a single arrival state."""
    registry = build_widget_registry(state, raw_screens, app_prior)
    observations = _resolve_observations(state, raw_screens)

    incoming: list[TransitionSummary] = []
    outgoing: list[TransitionSummary] = []
    for transition in fsm.transitions:
        if transition.target == state.state_id:
            incoming.append(_transition_summary(transition))
        if transition.source == state.state_id:
            outgoing.append(_transition_summary(transition))

    return InvariantEvidence(
        target_state_id=state.state_id,
        target_state_name=state.name,
        activity_name=state.android_context.activity_name or "",
        window_name=state.android_context.window_type or "",
        container_type=str(getattr(state.abstraction.container_type, "value", "")),
        template_id=state.abstraction.template_id or "",
        page_function=state.annotations.page_function,
        display_name=state.annotations.display_name,
        raw_screen_ids=list(state.evidence.raw_screen_ids),
        observation_count=len(observations),
        observations=observations,
        existing_invariant_specs=list(state.invariant_specs),
        arrival_registry=registry,
        incoming=incoming,
        outgoing=outgoing,
        static_prior_hints=static_prior_hints(state, app_prior),
    )


def build_all_invariant_evidence(
    fsm: AppFSM,
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None = None,
) -> list[InvariantEvidence]:
    """Build :class:`InvariantEvidence` for every state in ``fsm`` (insertion order)."""
    return [
        build_invariant_evidence(fsm, state, raw_screens, app_prior)
        for state in fsm.states.values()
    ]
