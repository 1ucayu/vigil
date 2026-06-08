"""Guard admission *policy* — runtime enforcement predicates (Tier 2 routing).

These pure predicates decide whether a transition's guard-admission metadata satisfies
runtime policy. They are deliberately **separate** from :mod:`vigil.symbolic.fsm_checker`,
which performs only structural (Tier 1) verification: state localization, transition
validity, goal reachability, and replay confidence.

Policy enforcement lives at the :class:`~vigil.symbolic.decision_engine.DecisionEngine`
layer, which calls these helpers after structural verification. ``requires_guard``
transitions without an admitted, executable guard route to ``UNCERTAIN``
(``GUARD_POLICY_UNSATISFIED``); the LLM fallback must not override that.

Per project rules, static APK priors alone never create a high-trust guard or a runtime
``ALLOW`` — admission status plus an executable guard string gate trust here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.models.fsm import Transition


def _policy_token(value: Any) -> str:
    """Normalize enum or raw-string policy metadata for case-insensitive checks."""
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().lower()


def transition_requires_guard_policy(transition: Transition | None) -> bool:
    """Return whether policy requires this transition to have an admitted guard."""
    if transition is None:
        return False
    return bool(transition.requires_guard)


# Admission statuses whose non-empty guard string is considered runtime-executable.
# An executable, evidence-backed guard is evaluated normally rather than auto-UNCERTAIN —
# this includes the legacy ``low_trust`` status, which is no longer a blocker.
_EXECUTABLE_STATUSES = frozenset({"admitted", "low_trust"})


def transition_has_admitted_executable_guard(transition: Transition | None) -> bool:
    """Return True iff a transition carries a runtime-executable, admitted guard.

    A non-empty guard string with an admitted (or legacy ``low_trust``) admission status
    counts as executable. ``rejected`` / ``pending`` / empty guards do not.
    """
    if transition is None:
        return False
    return _policy_token(transition.guard_admission_status) in _EXECUTABLE_STATUSES and bool(
        (transition.guard or "").strip()
    )


def guard_policy_violation_details(transition: Transition | None) -> str | None:
    """Return an UNCERTAIN detail message when guard policy blocks ALLOW, else None."""
    if transition is None:
        return None

    status = _policy_token(transition.guard_admission_status)
    if status == "rejected":
        return "Transition guard admission status is rejected"

    if not transition_requires_guard_policy(transition):
        return None

    if transition_has_admitted_executable_guard(transition):
        return None

    missing: list[str] = []
    if status != "admitted":
        missing.append("admitted guard status")
    if not (transition.guard or "").strip():
        missing.append("executable guard")

    return f"Guard policy requires requires_guard=True; missing {', '.join(missing)}"
