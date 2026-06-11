"""Deterministic, conservative state-invariant candidate synthesis (no LLM).

This is the rule-based source for ``invariant_source="deterministic"``. It proposes a
small, *generic* set of candidates from cross-visit-stable runtime evidence — never from
benchmark-specific strings, resource ids, or app names:

- numeric value-domain facts: when an element's value parses as a number in every
  observation, propose ``value(rid) == 0`` (all zero) or ``value(rid) >= 0`` (all
  non-negative);
- stable chrome/label facts: when a title/status element shows the same non-empty text in
  every observation, propose ``read(rid, text) == "<text>"``.

Every candidate is only a *proposal*: the deterministic admission
(:func:`~vigil.neuro.invariant_admission.admit_state_invariant_candidate`) re-verifies it
by replaying against each observation, so noisy proposals are dropped rather than trusted.
The richer numeric-range / semantic invariants are expected from the LLM source; this
keeps the offline, no-LLM path useful and fully deterministic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vigil.models.invariant_candidate import (
    InvariantGuardCandidatePacket,
    StateInvariantCandidate,
)
from vigil.neuro.guard_registry import WidgetRole, _elements_of

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.neuro.guard_registry import WidgetRegistryEntry
    from vigil.neuro.invariant_evidence import InvariantEvidence


# Roles whose stable text is a meaningful localization/status label (not a transient
# action control). Kept generic — no app-specific labels.
_LABEL_ROLES: frozenset[WidgetRole] = frozenset({WidgetRole.TITLE})
_LABEL_ID_HINTS: tuple[str, ...] = ("title", "status", "message", "label", "header", "result")


def _observed_values(resource_id: str, observations: list[dict[str, Any]]) -> list[str]:
    """Per-observation runtime ``value`` (== element text) for ``resource_id``.

    Runtime ``DecisionEngine._build_screen_context`` exposes ``value`` as ``e.text or ""``,
    so deterministic value-domain candidates must be derived from text. A separate raw
    ``value`` field is invisible to the runtime invariant checker and must not seed a
    candidate the live verifier could never confirm.
    """
    values: list[str] = []
    for observation in observations:
        for element in _elements_of(observation):
            if str(element.get("resource_id") or "") != resource_id:
                continue
            text = element.get("text")
            values.append("" if text is None else str(text))
            break
    return values


def _as_numbers(values: list[str]) -> list[float] | None:
    numbers: list[float] = []
    for value in values:
        token = value.strip()
        if not token:
            return None
        try:
            numbers.append(float(token))
        except ValueError:
            return None
    return numbers


def _looks_labelish(entry: WidgetRegistryEntry) -> bool:
    if entry.role in _LABEL_ROLES:
        return True
    rid = entry.resource_id.lower()
    return any(hint in rid for hint in _LABEL_ID_HINTS)


def _numeric_candidate(entry: WidgetRegistryEntry, numbers: list[float]) -> StateInvariantCandidate:
    if all(number == 0 for number in numbers):
        expr = f"value({entry.resource_id}) == 0"
        notes = "numeric field is zero across observations"
    else:
        expr = f"value({entry.resource_id}) >= 0"
        notes = "numeric field is non-negative across observations"
    return StateInvariantCandidate(
        kind="value_domain",
        expr=expr,
        admission_target="runtime_state_invariant",
        source="deterministic",
        evidence_count=len(numbers),
        volatility="likely_stable",
        notes=notes,
    )


def _label_candidate(entry: WidgetRegistryEntry, text: str, count: int) -> StateInvariantCandidate:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return StateInvariantCandidate(
        kind="stable_label",
        expr=f'read({entry.resource_id}, text) == "{escaped}"',
        admission_target="runtime_state_invariant",
        source="deterministic",
        evidence_count=count,
        volatility="likely_stable",
        notes="stable label text across observations",
    )


def synthesize_invariant_candidates(evidence: InvariantEvidence) -> InvariantGuardCandidatePacket:
    """Propose conservative state-invariant candidates for one state's evidence."""
    packet = InvariantGuardCandidatePacket(notes="deterministic synthesis")
    observations = evidence.observations
    if not observations:
        return packet

    for entry in evidence.arrival_registry.entries.values():
        if not entry.resource_id:
            continue
        values = _observed_values(entry.resource_id, observations)
        if len(values) != len(observations) or not values:
            # Element not present in every observation — not a stable state fact.
            continue

        numbers = _as_numbers(values)
        if numbers is not None:
            packet.state_invariant_candidates.append(_numeric_candidate(entry, numbers))
            continue

        unique = {value.strip() for value in values}
        if len(unique) == 1 and all(value.strip() for value in values) and _looks_labelish(entry):
            packet.state_invariant_candidates.append(
                _label_candidate(entry, values[0], len(values))
            )

    return packet
