"""LLM-backed, contract-first invariant + guard candidate generation (Stage 4).

This asks an LLM to produce a typed
:class:`~vigil.models.invariant_candidate.InvariantGuardCandidatePacket` for a *single,
already-built* arrival state via **provider structured output** (a strict
:class:`~vigil.models.llm_structured.LlmInvariantGuardResponse` schema), then converts the
parsed object into a :class:`LlmInvariantPacketCandidate`. The model is a candidate generator
only: it proposes typed state-invariant candidates, transition-guard candidates,
effect-invariant hints, and rejected candidates. Admission, DSL parsing, alias resolution,
replay confidence, and runtime verdicts all remain deterministic / symbolic downstream.

When structured output is unavailable (provider/schema failure, refusal, or validation
failure), the result is a clearly rejected candidate (``parsed_ok=False``) with the
provider/schema error — never a fabricated success. There is no prompt-only JSON parsing and
no repair-prompt loop on this path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, Field

from vigil.models.invariant_candidate import InvariantGuardCandidatePacket
from vigil.models.llm_structured import LlmInvariantGuardResponse
from vigil.neuro.guard_contract_llm import _failure_reason, _registry_lines
from vigil.system_prompt import load_system_prompt

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.core.llm_client import LlmClient
    from vigil.core.structured import StructuredResult
    from vigil.neuro.invariant_evidence import InvariantEvidence
    from vigil.neuro.prompt_redaction import PromptRedactor


DEFAULT_INVARIANT_PROMPT = "invariant_guard_generaton.spec"
INVARIANT_SCHEMA_NAME = "LlmInvariantGuardResponse"

# Cap how much per-observation detail enters the prompt so multi-visit states stay bounded.
_MAX_PROMPT_OBSERVATIONS = 4
_MAX_OBSERVATION_ELEMENTS = 25
_MAX_PROMPT_IMAGES = 3


class LlmInvariantPacketCandidate(BaseModel):
    """An LLM-produced candidate packet, before admission.

    ``parsed_ok`` is the authoritative success flag: ``True`` only when the provider returned
    a schema-valid object. When ``False`` the pipeline must treat this as a clear rejection
    and must not synthesize/attach invariants as if generation had succeeded.
    """

    packet: InvariantGuardCandidatePacket = Field(default_factory=InvariantGuardCandidatePacket)
    rejection_reason: str = ""
    raw_response: str = ""
    raw_responses: list[str] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
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


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _observation_lines(observation: dict[str, Any]) -> list[str]:
    from vigil.neuro.guard_registry import _elements_of

    lines: list[str] = []
    for element in _elements_of(observation)[:_MAX_OBSERVATION_ELEMENTS]:
        rid = str(element.get("resource_id") or "")
        text = str(element.get("text") or "").strip()
        cls = str(element.get("class_name") or "").rsplit(".", 1)[-1]
        ref = rid or cls or str(element.get("element_id") or "")
        suffix = f" text={text!r}" if text else ""
        lines.append(f"  - {ref}{suffix}")
    return lines


def _observations_section(evidence: InvariantEvidence) -> str:
    parts: list[str] = [
        "[State observations]",
        "Purpose: ONLY evidence for state-invariant candidates. Repeated visits are stronger.",
        f"- observation_count: {evidence.observation_count}",
    ]
    for index, observation in enumerate(evidence.observations[:_MAX_PROMPT_OBSERVATIONS]):
        screen_id = str(observation.get("screen_id") or f"obs_{index}")
        parts.append(f"- observation {index} (screen_id={screen_id}):")
        parts.extend(_observation_lines(observation))
    return "\n".join(parts)


def _transition_lines(transitions: Any) -> list[str]:
    lines: list[str] = []
    for summary in transitions:
        action_type = str(summary.action.get("type") or "")
        target_text = str(summary.action.get("target_text") or "").strip()
        suffix = f" target_text={target_text!r}" if target_text else ""
        # Surface the exact canonical_action_key so the model can copy it verbatim into a
        # transition_guard_candidate.canonical_action_key — the disambiguator when several
        # transitions share the same (source, target).
        lines.append(
            f"- {summary.source_state_id} -> {summary.target_state_id} "
            f"[{action_type}{suffix}] cak={summary.canonical_action_key} "
            f"conf={summary.replay_confidence} low_trust={summary.low_trust}"
        )
    return lines


def build_invariant_user_prompt(
    evidence: InvariantEvidence,
    *,
    redactor: PromptRedactor | None = None,
) -> str:
    """Build the per-state user prompt for invariant/guard candidate generation.

    When ``redactor`` is supplied, identifier/benchmark leakage is masked in the assembled
    prompt while usable registry aliases, permissions, and action properties are preserved.
    """
    reg_lines = _registry_lines(evidence.arrival_registry)
    existing = [spec.expr for spec in evidence.existing_invariant_specs]

    sections: list[str] = []
    sections.append(
        "/* Contract-first invariant + guard synthesis */\n"
        "Generate the typed InvariantGuardCandidatePacket for this ALREADY-BUILT arrival "
        "state. Topology is fixed: do not invent states/transitions/actions/confidence.\n"
        "Runtime state invariants are evaluated with ScreenContext only — they may use "
        "read/value/count over the arrival registry, including "
        "contains/not_contains operators on readable text/list values, and must "
        "NOT use $intent.*, $bind.*, action(...), in_state(...), or time_in(...). Put "
        "intent/action-dependent facts in effect_invariant_hints; put pre-action safety "
        "checks in transition_guard_candidates."
    )

    sections.append(
        "[Target state]\n"
        f"- state_id: {evidence.target_state_id}\n"
        f"- state_name: {evidence.target_state_name!r}\n"
        f"- activity_name: {evidence.activity_name!r}\n"
        f"- window_name: {evidence.window_name!r}\n"
        f"- container_type: {evidence.container_type!r}\n"
        f"- template_id: {evidence.template_id!r}\n"
        f"- page_function: {evidence.page_function!r}\n"
        f"- display_name: {evidence.display_name!r}\n"
        f"- raw_screen_ids: {evidence.raw_screen_ids}\n"
        f"- existing_invariant_specs: {existing}"
    )

    sections.append(_observations_section(evidence))

    sections.append(
        "[Arrival-state widget registry]\n"
        "Purpose: the ONLY element aliases executable state-invariant predicates may use.\n"
        + ("\n".join(reg_lines) if reg_lines else "- (none resolved)")
    )

    sections.append(
        "[Incoming transitions]\n"
        "Purpose: preservation evidence + state-consistency/side-effect classification.\n"
        + ("\n".join(_transition_lines(evidence.incoming)) if evidence.incoming else "- (none)")
    )

    sections.append(
        "[Outgoing transitions]\n"
        "Purpose: pre-action guard candidates and sibling choices.\n"
        + ("\n".join(_transition_lines(evidence.outgoing)) if evidence.outgoing else "- (none)")
    )

    sections.append(
        "[Transition Guard Candidate Checklist]\n"
        "For any transition_guard_candidates you emit, `required_slots` is the "
        "candidate contract's declared intent interface. Declare slots only when "
        "grounded in source/action evidence or an explicitly supplied task intent "
        "interface; use generic role-derived names, not app-specific fixtures.\n"
        "For input_text, row/item selection, option selection, form submission, and "
        "commit-like actions, first try an executable semantic binding predicate over "
        "action(...), read(...), or value(...) against a declared $intent.* slot. "
        "Enabledness/clickability can be readiness checks, but an enabled/clickable-only "
        "guard is incomplete when executable semantic binding evidence is available."
    )

    sections.append(
        "[Global Information / Static APK Priors]\n"
        "Purpose: semantic role/domain hints only; never runtime proof.\n"
        + (
            "\n".join(f"- {hint}" for hint in evidence.static_prior_hints)
            if evidence.static_prior_hints
            else "- (none)"
        )
    )

    sections.append(
        "[Output]\n"
        "Emit the InvariantGuardCandidatePacket object from the system prompt "
        "(state_invariant_candidates, transition_guard_candidates, effect_invariant_hints, "
        "rejected_candidates, notes).\n"
        "For every transition_guard_candidate, copy the matching transition's exact `cak=` "
        "value into canonical_action_key so it binds to the right transition when several "
        "share the same (source, target)."
    )

    prompt = "\n\n".join(sections)
    if redactor is not None:
        prompt = redactor.redact(prompt)
    return prompt


def invariant_image_paths(evidence: InvariantEvidence) -> tuple[list[Path], list[str]]:
    """Return existing observation screenshots to attach to the LLM request."""
    images: list[Path] = []
    labels: list[str] = []
    seen: set[Path] = set()
    for observation in evidence.observations:
        raw_path = str(observation.get("screenshot_path") or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        if not (path.exists() and path.is_file()):
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        images.append(path)
        labels.append(
            f"Observation screenshot of arrival state {evidence.target_state_id} "
            f"(screen={observation.get('screen_id') or 'obs'})"
        )
        if len(images) >= _MAX_PROMPT_IMAGES:
            break
    return images, labels


# ---------------------------------------------------------------------------
# Structured-result conversion
# ---------------------------------------------------------------------------


def _attach_structured_metadata(
    candidate: LlmInvariantPacketCandidate,
    result: StructuredResult,
    spec_hash: str,
) -> LlmInvariantPacketCandidate:
    candidate.schema_name = result.schema_name
    candidate.schema_hash = result.schema_hash
    candidate.schema_constraint_mode = result.schema_constraint_mode
    candidate.provider = result.provider
    candidate.model = result.model
    candidate.refusal = result.refusal or ""
    candidate.validation_errors = list(result.validation_errors)
    candidate.spec_hash = spec_hash
    candidate.raw_response = result.raw_text
    candidate.raw_responses = [result.raw_text] if result.raw_text else []
    return candidate


def candidate_from_structured_result(
    result: StructuredResult,
    spec_hash: str,
) -> LlmInvariantPacketCandidate:
    """Convert a :class:`StructuredResult` into an invariant packet candidate (never raises)."""
    if result.parsed is not None:
        assert isinstance(result.parsed, LlmInvariantGuardResponse)
        candidate = LlmInvariantPacketCandidate(packet=result.parsed.to_runtime(), parsed_ok=True)
        return _attach_structured_metadata(candidate, result, spec_hash)

    reason = _failure_reason(result)
    candidate = LlmInvariantPacketCandidate(
        parsed_ok=False,
        rejection_reason=reason,
        parse_errors=[reason],
    )
    return _attach_structured_metadata(candidate, result, spec_hash)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_llm_invariant_guard_candidate(
    evidence: InvariantEvidence,
    llm: LlmClient,
    *,
    prompt_name: str = DEFAULT_INVARIANT_PROMPT,
    use_images: bool = True,
    redactor: PromptRedactor | None = None,
    allow_provider_fallback: bool = False,
) -> LlmInvariantPacketCandidate:
    """Generate an invariant/guard candidate packet for one state via structured output."""
    system_prompt = load_system_prompt(prompt_name)
    spec_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    user_prompt = build_invariant_user_prompt(evidence, redactor=redactor)
    try:
        images, image_labels = invariant_image_paths(evidence) if use_images else ([], [])
        if redactor is not None:
            image_labels = [redactor.redact(label) for label in image_labels]
        if images and hasattr(llm, "generate_structured_with_images"):
            result = llm.generate_structured_with_images(
                system_prompt,
                user_prompt,
                images,
                LlmInvariantGuardResponse,
                INVARIANT_SCHEMA_NAME,
                image_labels,
                allow_provider_fallback=allow_provider_fallback,
            )
        else:
            result = llm.generate_structured(
                system_prompt,
                user_prompt,
                LlmInvariantGuardResponse,
                INVARIANT_SCHEMA_NAME,
                allow_provider_fallback=allow_provider_fallback,
            )
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash the pipeline
        logger.warning(
            f"LLM invariant generation failed for state {evidence.target_state_id}: {exc}"
        )
        reason = f"llm call failed: {exc}"
        return LlmInvariantPacketCandidate(
            parsed_ok=False,
            rejection_reason=reason,
            parse_errors=[reason],
            validation_errors=[reason],
            schema_name=INVARIANT_SCHEMA_NAME,
            schema_constraint_mode="prompt_only_unavailable",
            spec_hash=spec_hash,
        )
    return candidate_from_structured_result(result, spec_hash)
