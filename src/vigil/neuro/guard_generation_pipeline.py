"""Contract-first guard generation pipeline (step 4).

Ties the guard stages together over an existing ``AppFSM``:

    build_all_guard_evidence -> (deterministic | llm | hybrid contract) -> admit_guard_contract
    -> attach admitted guard + metadata onto each Transition

This enriches transitions with ``Gamma`` (guards) and admission metadata only. It does
**not** mutate the FSM graph, state identity, replay confidence, or provenance, and it
never overwrites an existing guard with a rejected/None result.

Guard sources:

- ``deterministic``: rule-based :func:`synthesize_guard_contract` only (no LLM).
- ``llm``: an LLM-produced typed :class:`GuardContract` (never free-form DSL).
- ``hybrid``: try the LLM first; fall back to deterministic synthesis when the LLM
  candidate is invalid/rejected, rejected by admission, or admitted yet
  ``semantic_binding_incomplete`` for a required/high-risk transition.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from vigil.models.guard import GuardContract, RiskLevel
from vigil.neuro.guard_admission import GuardAdmissionResult, admit_guard_contract
from vigil.neuro.guard_contract_llm import (
    DEFAULT_GUARD_PROMPT,
    generate_llm_guard_candidate,
)
from vigil.neuro.guard_contract_synthesizer import synthesize_guard_contract
from vigil.neuro.guard_evidence import build_all_guard_evidence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.core.llm_client import LlmClient
    from vigil.models.fsm import AppFSM
    from vigil.neuro.app_prior import AppPrior
    from vigil.neuro.guard_evidence import GuardEvidence

GuardSource = Literal["deterministic", "llm", "hybrid"]


def _action_summary(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": action.get("type", ""),
        "target": action.get("target", ""),
        "target_text": action.get("target_text", ""),
    }


def _binding_required(contract: GuardContract) -> bool:
    """Whether this contract's action class requires a semantic (intent) binding."""
    return (
        contract.required
        or contract.risk_level == RiskLevel.HIGH
        or contract.semantic_binding_required
    )


def _try_llm_contract(
    evidence: GuardEvidence,
    llm: LlmClient,
    guard_prompt: str,
) -> tuple[GuardContract | None, str, GuardAdmissionResult | None]:
    """Generate + vet an LLM contract.

    Returns ``(contract, fallback_reason, admission_result)``. ``contract`` is ``None``
    (with a ``fallback_reason``) when the LLM candidate should be rejected in favor of the
    deterministic fallback: invalid/unparseable, rejected by admission, or admitted yet
    semantically incomplete for a required/high-risk action.
    """
    candidate = generate_llm_guard_candidate(evidence, llm, prompt_name=guard_prompt)
    contract = candidate.contract
    if candidate.rejection_reason:
        return None, f"llm candidate rejected: {candidate.rejection_reason}", None

    result = admit_guard_contract(contract, evidence)
    if not result.admitted:
        return None, f"llm contract rejected by admission: {result.reason}", None
    if result.semantic_binding_incomplete and _binding_required(contract):
        return (
            None,
            "llm contract admitted but semantic binding incomplete for a "
            "required/high-risk action",
            None,
        )
    return contract, "", result


def _resolve_contract(
    evidence: GuardEvidence,
    guard_source: GuardSource,
    llm: LlmClient | None,
    guard_prompt: str,
) -> tuple[GuardContract, str, str, GuardAdmissionResult | None]:
    """Produce ``(contract, guard_origin, fallback_reason, precomputed_result)``.

    ``precomputed_result`` is the admission result already computed for an accepted LLM
    contract (so we do not admit twice); ``None`` otherwise.
    """
    if guard_source == "deterministic":
        return synthesize_guard_contract(evidence), "deterministic", "", None

    if guard_source == "llm":
        assert llm is not None
        candidate = generate_llm_guard_candidate(evidence, llm, prompt_name=guard_prompt)
        return candidate.contract, "llm", candidate.rejection_reason, None

    # hybrid
    assert llm is not None
    contract, fallback_reason, result = _try_llm_contract(evidence, llm, guard_prompt)
    if contract is not None:
        return contract, "llm", "", result
    return synthesize_guard_contract(evidence), "fallback", fallback_reason, None


def generate_contract_guards(
    fsm: AppFSM,
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None = None,
    *,
    guard_source: GuardSource = "deterministic",
    llm: LlmClient | None = None,
    guard_prompt: str = DEFAULT_GUARD_PROMPT,
) -> list[dict[str, Any]]:
    """Synthesize, admit, and attach contract guards across ``fsm``'s transitions.

    Returns a per-transition report. The FSM graph is left structurally unchanged; only
    guard / admission metadata is written onto the transitions. ``guard_source`` selects
    the deterministic, LLM, or hybrid contract source; ``llm`` is required for the latter
    two. ``guard_prompt`` names the system-prompt file used by the LLM path.
    """
    if guard_source in ("llm", "hybrid") and llm is None:
        raise ValueError(f"guard_source={guard_source!r} requires an LLM client")

    evidence_items = build_all_guard_evidence(fsm, raw_screens, app_prior)
    report: list[dict[str, Any]] = []

    for index, transition in enumerate(fsm.transitions):
        evidence = evidence_items[index]
        contract, guard_origin, fallback_reason, precomputed = _resolve_contract(
            evidence, guard_source, llm, guard_prompt
        )
        result = precomputed or admit_guard_contract(contract, evidence)

        # Sync the admission outcome onto the contract so it survives serialize/
        # deserialize alongside the transition-level metadata. Keep the contract's
        # semantic-binding-incomplete flag consistent with admission so the enriched FSM
        # never looks complete when the report says incomplete.
        contract.admission_status = result.status
        contract.admission_reason = result.reason
        contract.semantic_binding_incomplete = (
            contract.semantic_binding_incomplete or result.semantic_binding_incomplete
        )

        # Attach metadata (no graph mutation).
        transition.guard_contract = contract
        transition.requires_guard = contract.required
        transition.risk_level = contract.risk_level
        transition.guard_admission_status = result.status
        transition.guard_admission_reason = result.reason
        # Only attach an executable guard string; never clobber an existing guard with a
        # rejected / None result.
        if result.admitted and result.guard is not None:
            transition.guard = result.guard

        report.append(
            {
                "transition_index": index,
                "source": transition.source,
                "target": transition.target,
                "action": _action_summary(transition.action),
                "kind": contract.kind.value,
                "risk": contract.risk_level.value,
                "required": contract.required,
                "guard_origin": guard_origin,
                "fallback_reason": fallback_reason,
                "status": result.status.value,
                "reason": result.reason,
                "semantic_binding_required": contract.semantic_binding_required,
                "semantic_binding_incomplete": contract.semantic_binding_incomplete,
                "rejected_predicates": result.rejected_predicates,
                "guard": result.guard if (result.admitted and result.guard) else None,
            }
        )

    return report


def write_guard_generation_report(report: list[dict[str, Any]], path: Path) -> None:
    """Write a guard-generation report as JSON. Callers pass an ``output_docs/`` path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
