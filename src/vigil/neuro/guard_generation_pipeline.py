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
  ``semantic_binding_incomplete`` for a semantic-required transition.
- ``audit``: replay previously persisted LLM audit candidates, then rerun deterministic
  admission/fallback without calling the model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from vigil.models.guard import (
    GuardAdmissionStatus,
    GuardContract,
    IntentSlot,
    LlmGuardContractCandidate,
    TransitionPostcondition,
)
from vigil.neuro.guard_admission import (
    GuardAdmissionResult,
    PostconditionAdmissionResult,
    admit_guard_contract,
    admit_postcondition_contract,
)
from vigil.neuro.guard_contract_llm import (
    DEFAULT_GUARD_PROMPT,
    generate_llm_guard_candidate,
)
from vigil.neuro.guard_contract_synthesizer import synthesize_guard_contract
from vigil.neuro.guard_evidence import build_all_guard_evidence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.core.llm_client import LlmClient
    from vigil.models.fsm import AppFSM
    from vigil.models.guard import LlmGuardContractCandidate
    from vigil.neuro.app_prior import AppPrior
    from vigil.neuro.guard_evidence import GuardEvidence

GuardSource = Literal["deterministic", "llm", "hybrid", "audit"]


@dataclass(frozen=True)
class _ResolvedContract:
    contract: GuardContract
    guard_origin: str
    fallback_reason: str
    precomputed_result: GuardAdmissionResult | None = None
    llm_audit_path: str = ""
    postcondition: TransitionPostcondition | None = None
    postcondition_incomplete: bool = False


@dataclass(frozen=True)
class _CachedLlmCandidate:
    candidate: LlmGuardContractCandidate
    audit_path: str
    action_schema_index: int


def _prompt_schema_key(evidence: GuardEvidence) -> tuple[Any, ...]:
    """LLM amortization key for one source/action/target guard packet.

    Gamma depends mostly on source/action evidence, while Psi depends on the target
    state and source-to-target diff. Include the transition endpoints so a postcondition
    generated for one target is never reused for a different target state.
    """
    return (
        ("source", evidence.source_state_id),
        ("target", evidence.target_state_id),
        *guard_action_schema_key(evidence.action),
    )


def _action_summary(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": action.get("type", ""),
        "target": action.get("target", ""),
        "target_text": action.get("target_text", ""),
        "target_resource_id": action.get("target_resource_id", action.get("resource_id", "")),
    }


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def guard_action_schema_key(action: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Action-schema key used only to amortize LLM guard prompting.

    This deliberately excludes capture-local element handles and selector internals used
    by structural replay. Guard candidates are generated once per stable GUI action
    schema, then admitted separately against each concrete source-state registry.
    """
    selector = action.get("target_selector")
    selector_map = selector if isinstance(selector, dict) else {}
    resource_id = _first_nonempty(
        action.get("target_resource_id"),
        action.get("resource_id"),
        selector_map.get("resource_id"),
    )
    target_text = _first_nonempty(
        action.get("target_text"),
        selector_map.get("text"),
        selector_map.get("nearby_text"),
    )
    return (
        ("type", _first_nonempty(action.get("type"), action.get("action_type"))),
        ("resource_id", resource_id),
        ("target_text", target_text),
        (
            "target_content_desc",
            _first_nonempty(
                action.get("target_content_desc"),
                selector_map.get("content_description"),
            ),
        ),
        (
            "value",
            _first_nonempty(action.get("input_text"), action.get("text"), action.get("value")),
        ),
    )


def _semantic_binding_required(contract: GuardContract) -> bool:
    """Whether this contract explicitly requires a semantic ``$intent.*`` binding."""
    return contract.semantic_binding_required or contract.semantic_binding_incomplete


def _slot_map(slots: list[IntentSlot]) -> dict[str, IntentSlot]:
    return {slot.name: slot for slot in slots if slot.name}


def _postcondition_slot_consistency_errors(
    contract: GuardContract,
    postcondition: TransitionPostcondition | None,
) -> list[str]:
    """Check that Psi intent slots are declared consistently by Gamma."""
    if postcondition is None:
        return []
    gamma_slots = _slot_map(contract.required_slots)
    errors: list[str] = []
    for name, psi_slot in _slot_map(postcondition.required_slots).items():
        gamma_slot = gamma_slots.get(name)
        if gamma_slot is None:
            errors.append(f"postcondition slot '$intent.{name}' not declared by precondition")
            continue
        if (
            psi_slot.slot_type != gamma_slot.slot_type
            and psi_slot.slot_type.value != "unknown"
            and gamma_slot.slot_type.value != "unknown"
        ):
            errors.append(
                f"slot '$intent.{name}' type mismatch: "
                f"precondition={gamma_slot.slot_type.value}, "
                f"postcondition={psi_slot.slot_type.value}"
            )
    return errors


def _candidate_from_audit(path: Path) -> LlmGuardContractCandidate:
    """Load a persisted LLM guard attempt without re-querying the model."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        contract_payload = payload.get("contract") or payload.get("precondition") or {}
        contract = GuardContract.model_validate(contract_payload)
        postcondition_payload = payload.get("postcondition")
        postcondition = (
            TransitionPostcondition.model_validate(postcondition_payload)
            if isinstance(postcondition_payload, dict)
            else None
        )
        postcondition_incomplete = bool(
            payload.get(
                "postcondition_incomplete",
                postcondition.intent_effect_incomplete if postcondition is not None else False,
            )
        )
        if postcondition is not None:
            postcondition.intent_effect_incomplete = (
                postcondition.intent_effect_incomplete or postcondition_incomplete
            )
        raw_responses = payload.get("raw_responses") or []
        parse_errors = payload.get("parse_errors") or []
        return LlmGuardContractCandidate(
            contract=contract,
            postcondition=postcondition,
            semantic_binding_incomplete=contract.semantic_binding_incomplete,
            postcondition_incomplete=postcondition_incomplete,
            rejection_reason=str(payload.get("rejection_reason") or ""),
            raw_responses=[str(item) for item in raw_responses],
            parse_errors=[str(item) for item in parse_errors],
            repair_attempted=bool(payload.get("repair_attempted", False)),
        )
    except Exception as exc:  # noqa: BLE001 - replay should degrade like LLM failure
        return LlmGuardContractCandidate(
            contract=GuardContract(),
            rejection_reason=f"failed to load llm audit {path}: {exc}",
            parse_errors=[f"failed to load llm audit {path}: {exc}"],
        )


def _prompt_hash(evidence: GuardEvidence, guard_prompt: str) -> str:
    payload = {
        "guard_prompt": guard_prompt,
        "transition_index": evidence.transition_index,
        "source": evidence.source_state_id,
        "target": evidence.target_state_id,
        "action": evidence.action,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _write_llm_attempt_audit(
    evidence: GuardEvidence,
    candidate: LlmGuardContractCandidate,
    audit_dir: Path | None,
    guard_prompt: str,
) -> str:
    """Persist raw LLM guard attempts under output_docs for debugging/repro."""
    if audit_dir is None:
        return ""
    audit_dir.mkdir(parents=True, exist_ok=True)
    prompt_hash = _prompt_hash(evidence, guard_prompt)
    filename = f"transition_{evidence.transition_index:04d}_{prompt_hash}.json"
    path = audit_dir / filename
    payload = {
        "transition_index": evidence.transition_index,
        "prompt_hash": prompt_hash,
        "source": evidence.source_state_id,
        "target": evidence.target_state_id,
        "action": evidence.action,
        "rejection_reason": candidate.rejection_reason,
        "parse_errors": candidate.parse_errors,
        "repair_attempted": candidate.repair_attempted,
        "precondition": candidate.contract.model_dump(mode="json"),
        "contract": candidate.contract.model_dump(mode="json"),
        "postcondition": (
            candidate.postcondition.model_dump(mode="json")
            if candidate.postcondition is not None
            else None
        ),
        "postcondition_incomplete": candidate.postcondition_incomplete,
        "raw_responses": candidate.raw_responses
        or ([candidate.raw_response] if candidate.raw_response else []),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _try_llm_contract(
    evidence: GuardEvidence,
    llm: LlmClient,
    guard_prompt: str,
    *,
    guard_use_images: bool = True,
) -> tuple[GuardContract | None, str, GuardAdmissionResult | None]:
    """Generate + vet an LLM contract.

    Returns ``(contract, fallback_reason, admission_result)``. ``contract`` is ``None``
    (with a ``fallback_reason``) when the LLM candidate should be rejected in favor of the
    deterministic fallback: invalid/unparseable, rejected by admission, or admitted yet
    semantically incomplete for a semantic-required action.
    """
    candidate = generate_llm_guard_candidate(
        evidence,
        llm,
        prompt_name=guard_prompt,
        use_images=guard_use_images,
    )
    contract = candidate.contract
    if candidate.rejection_reason:
        return None, f"llm candidate rejected: {candidate.rejection_reason}", None

    result = admit_guard_contract(contract, evidence)
    if not result.admitted:
        return None, f"llm contract rejected by admission: {result.reason}", None
    if result.semantic_binding_incomplete and _semantic_binding_required(contract):
        return (
            None,
            "llm contract admitted but semantic binding incomplete for a "
            "semantic-required action",
            None,
        )
    return contract, "", result


def _resolve_contract(
    evidence: GuardEvidence,
    guard_source: GuardSource,
    llm: LlmClient | None,
    guard_prompt: str,
    *,
    guard_use_images: bool = True,
    llm_audit_dir: Path | None = None,
) -> _ResolvedContract:
    """Resolve the guard contract source for one transition evidence object.

    ``precomputed_result`` is the admission result already computed for an accepted
    LLM contract (so we do not admit twice); ``None`` otherwise.
    """
    if guard_source == "deterministic":
        return _ResolvedContract(
            contract=synthesize_guard_contract(evidence),
            guard_origin="deterministic",
            fallback_reason="",
        )

    assert llm is not None
    candidate = generate_llm_guard_candidate(
        evidence,
        llm,
        prompt_name=guard_prompt,
        use_images=guard_use_images,
    )
    audit_path = _write_llm_attempt_audit(evidence, candidate, llm_audit_dir, guard_prompt)

    if guard_source == "llm":
        return _ResolvedContract(
            contract=candidate.contract,
            guard_origin="llm",
            fallback_reason=candidate.rejection_reason,
            llm_audit_path=audit_path,
            postcondition=candidate.postcondition,
            postcondition_incomplete=candidate.postcondition_incomplete,
        )

    # hybrid
    contract = candidate.contract
    if candidate.rejection_reason:
        return _ResolvedContract(
            contract=synthesize_guard_contract(evidence),
            guard_origin="fallback",
            fallback_reason=f"llm candidate rejected: {candidate.rejection_reason}",
            llm_audit_path=audit_path,
        )

    result = admit_guard_contract(contract, evidence)
    if not result.admitted:
        return _ResolvedContract(
            contract=synthesize_guard_contract(evidence),
            guard_origin="fallback",
            fallback_reason=f"llm contract rejected by admission: {result.reason}",
            llm_audit_path=audit_path,
        )
    if result.semantic_binding_incomplete and _semantic_binding_required(contract):
        return _ResolvedContract(
            contract=synthesize_guard_contract(evidence),
            guard_origin="fallback",
            fallback_reason=(
                "llm contract admitted but semantic binding incomplete for a "
                "semantic-required action"
            ),
            llm_audit_path=audit_path,
        )
    return _ResolvedContract(
        contract=contract,
        guard_origin="llm",
        fallback_reason="",
        precomputed_result=result,
        llm_audit_path=audit_path,
        postcondition=candidate.postcondition,
        postcondition_incomplete=candidate.postcondition_incomplete,
    )


def _resolve_contract_from_llm_candidate(
    evidence: GuardEvidence,
    guard_source: GuardSource,
    cached: _CachedLlmCandidate,
) -> _ResolvedContract:
    """Resolve one edge using an LLM candidate generated for its action schema.

    LLM prompting is per stable guard action schema; admission remains per concrete
    ``(source, action, target)`` edge so a reused candidate must still parse, resolve,
    and evaluate against the current source-state widget registry.
    """
    candidate = cached.candidate
    audit_path = cached.audit_path

    contract = candidate.contract.model_copy(deep=True)
    if guard_source == "llm":
        return _ResolvedContract(
            contract=contract,
            guard_origin="llm",
            fallback_reason=candidate.rejection_reason,
            llm_audit_path=audit_path,
            postcondition=candidate.postcondition,
            postcondition_incomplete=candidate.postcondition_incomplete,
        )

    # hybrid / audit replay
    if candidate.rejection_reason:
        return _ResolvedContract(
            contract=synthesize_guard_contract(evidence),
            guard_origin="fallback",
            fallback_reason=f"llm candidate rejected: {candidate.rejection_reason}",
            llm_audit_path=audit_path,
        )

    result = admit_guard_contract(contract, evidence)
    if not result.admitted:
        return _ResolvedContract(
            contract=synthesize_guard_contract(evidence),
            guard_origin="fallback",
            fallback_reason=f"llm contract rejected by admission: {result.reason}",
            llm_audit_path=audit_path,
        )
    if result.semantic_binding_incomplete and _semantic_binding_required(contract):
        return _ResolvedContract(
            contract=synthesize_guard_contract(evidence),
            guard_origin="fallback",
            fallback_reason=(
                "llm contract admitted but semantic binding incomplete for a "
                "semantic-required action"
            ),
            llm_audit_path=audit_path,
        )
    return _ResolvedContract(
        contract=contract,
        guard_origin="llm",
        fallback_reason="",
        precomputed_result=result,
        llm_audit_path=audit_path,
        postcondition=candidate.postcondition,
        postcondition_incomplete=candidate.postcondition_incomplete,
    )


def generate_contract_guards(
    fsm: AppFSM,
    raw_screens: dict[str, Any],
    app_prior: AppPrior | None = None,
    *,
    guard_source: GuardSource = "deterministic",
    llm: LlmClient | None = None,
    guard_prompt: str = DEFAULT_GUARD_PROMPT,
    guard_use_images: bool = True,
    llm_audit_dir: Path | None = None,
    llm_audit_report: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Synthesize, admit, and attach contract guards across ``fsm``'s transitions.

    Returns a per-transition report. The FSM graph is left structurally unchanged; only
    guard / admission metadata is written onto the transitions. ``guard_source`` selects
    the deterministic, LLM, hybrid, or audit-replay contract source; ``llm`` is required
    only for live LLM modes. ``guard_prompt`` names the system-prompt file used by the
    live LLM path.
    """
    if guard_source in ("llm", "hybrid") and llm is None:
        raise ValueError(f"guard_source={guard_source!r} requires an LLM client")
    if guard_source == "audit" and llm_audit_report is None:
        raise ValueError("guard_source='audit' requires llm_audit_report")

    evidence_items = build_all_guard_evidence(fsm, raw_screens, app_prior)
    report: list[dict[str, Any]] = []
    llm_candidates_by_prompt: dict[tuple[Any, ...], _CachedLlmCandidate] = {}
    audit_rows_by_transition = {
        int(row.get("transition_index", -1)): row
        for row in (llm_audit_report or [])
        if isinstance(row, dict)
    }
    audit_candidates_by_path: dict[str, LlmGuardContractCandidate] = {}

    for index, transition in enumerate(fsm.transitions):
        evidence = evidence_items[index]
        prompt_schema_key = _prompt_schema_key(evidence)
        action_schema_index = None
        if guard_source in ("llm", "hybrid"):
            cached = llm_candidates_by_prompt.get(prompt_schema_key)
            if cached is None:
                assert llm is not None
                candidate = generate_llm_guard_candidate(
                    evidence,
                    llm,
                    prompt_name=guard_prompt,
                    use_images=guard_use_images,
                )
                audit_path = _write_llm_attempt_audit(
                    evidence,
                    candidate,
                    llm_audit_dir,
                    guard_prompt,
                )
                cached = _CachedLlmCandidate(
                    candidate=candidate,
                    audit_path=audit_path,
                    action_schema_index=len(llm_candidates_by_prompt),
                )
            llm_candidates_by_prompt[prompt_schema_key] = cached
            action_schema_index = cached.action_schema_index
            resolved = _resolve_contract_from_llm_candidate(evidence, guard_source, cached)
        elif guard_source == "audit":
            audit_row = audit_rows_by_transition.get(index, {})
            action_schema_index = audit_row.get("action_schema_index")
            audit_path = str(audit_row.get("llm_audit_path") or "")
            if audit_path:
                candidate = audit_candidates_by_path.get(audit_path)
                if candidate is None:
                    candidate = _candidate_from_audit(Path(audit_path))
                    audit_candidates_by_path[audit_path] = candidate
                resolved = _resolve_contract_from_llm_candidate(
                    evidence,
                    "hybrid",
                    _CachedLlmCandidate(
                        candidate=candidate,
                        audit_path=audit_path,
                        action_schema_index=(
                            action_schema_index
                            if isinstance(action_schema_index, int)
                            else len(audit_candidates_by_path) - 1
                        ),
                    ),
                )
            else:
                resolved = _ResolvedContract(
                    contract=synthesize_guard_contract(evidence),
                    guard_origin="fallback",
                    fallback_reason="missing llm audit path for transition",
                )
        else:
            resolved = _resolve_contract(
                evidence,
                guard_source,
                llm,
                guard_prompt,
                guard_use_images=guard_use_images,
                llm_audit_dir=llm_audit_dir,
            )
        contract = resolved.contract
        guard_origin = resolved.guard_origin
        fallback_reason = resolved.fallback_reason
        precomputed = resolved.precomputed_result
        result = precomputed or admit_guard_contract(contract, evidence)
        postcondition = (
            resolved.postcondition.model_copy(deep=True)
            if resolved.postcondition is not None
            else None
        )
        postcondition_result: PostconditionAdmissionResult | None = None
        if postcondition is not None:
            postcondition_result = admit_postcondition_contract(postcondition, evidence)
            slot_errors = _postcondition_slot_consistency_errors(contract, postcondition)
            if slot_errors:
                postcondition_result = PostconditionAdmissionResult(
                    admitted=False,
                    status=GuardAdmissionStatus.REJECTED,
                    postcondition=None,
                    reason="postcondition/precondition intent slot mismatch: "
                    + "; ".join(slot_errors),
                    rejected_predicates=list(postcondition_result.rejected_predicates),
                    unsupported_effects=list(postcondition_result.unsupported_effects),
                    intent_effect_incomplete=True,
                )
        postcondition_incomplete = bool(
            resolved.postcondition_incomplete
            or (postcondition.intent_effect_incomplete if postcondition is not None else False)
            or (
                postcondition_result.intent_effect_incomplete
                if postcondition_result is not None
                else False
            )
        )

        # Sync the deterministic admission outcome onto the contract so it survives
        # serialize/deserialize alongside the transition-level metadata. Admission is the
        # authority for semantic completeness; stale LLM self-assessments are audit input,
        # not final metadata.
        contract.admission_status = result.status
        contract.admission_reason = result.reason
        contract.semantic_binding_incomplete = result.semantic_binding_incomplete

        # Attach metadata (no graph mutation).
        transition.guard_contract = contract
        transition.postcondition_contract = postcondition
        transition.postcondition_incomplete = postcondition_incomplete
        transition.postcondition = None
        transition.postcondition_admission_status = None
        transition.postcondition_admission_reason = ""
        transition.postcondition_rejected_predicates = []
        transition.postcondition_unsupported_effects = []
        if postcondition_result is not None:
            transition.postcondition_admission_status = postcondition_result.status
            transition.postcondition_admission_reason = postcondition_result.reason
            transition.postcondition_rejected_predicates = list(
                postcondition_result.rejected_predicates
            )
            transition.postcondition_unsupported_effects = list(
                postcondition_result.unsupported_effects
            )
            if postcondition_result.admitted and postcondition_result.postcondition:
                transition.postcondition = postcondition_result.postcondition
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
                "action_schema_index": action_schema_index,
                "kind": contract.kind.value,
                "risk": contract.risk_level.value,
                "required": contract.required,
                "guard_origin": guard_origin,
                "llm_audit_path": resolved.llm_audit_path,
                "fallback_reason": fallback_reason,
                "status": result.status.value,
                "reason": result.reason,
                "semantic_binding_required": contract.semantic_binding_required,
                "semantic_binding_incomplete": contract.semantic_binding_incomplete,
                "precondition": contract.model_dump(mode="json"),
                "postcondition": (
                    postcondition.model_dump(mode="json") if postcondition is not None else None
                ),
                "postcondition_incomplete": postcondition_incomplete,
                "postcondition_dsl": (
                    postcondition_result.postcondition if postcondition_result is not None else None
                ),
                "postcondition_status": (
                    postcondition_result.status.value
                    if postcondition_result is not None
                    else "none"
                ),
                "postcondition_reason": (
                    postcondition_result.reason if postcondition_result is not None else ""
                ),
                "postcondition_rejected_predicates": (
                    postcondition_result.rejected_predicates
                    if postcondition_result is not None
                    else []
                ),
                "postcondition_unsupported_effects": (
                    postcondition_result.unsupported_effects
                    if postcondition_result is not None
                    else []
                ),
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
