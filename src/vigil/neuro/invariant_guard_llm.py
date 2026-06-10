"""LLM-backed, contract-first invariant + guard candidate generation (Stage 4).

This asks an LLM to produce a typed
:class:`~vigil.models.invariant_candidate.InvariantGuardCandidatePacket` for a *single,
already-built* arrival state, using ``src/vigil/system_prompt/invariant_guard_generaton.spec``
as the normative schema. The model is a candidate generator only: it proposes typed
state-invariant candidates, transition-guard candidates, effect-invariant hints, and
rejected candidates. Admission, DSL parsing, alias resolution, replay confidence, and
runtime verdicts all remain deterministic / symbolic downstream.

Parsing is defensive: any LLM/parse failure degrades to an empty packet with a
``rejection_reason`` rather than raising into the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from vigil.models.invariant_candidate import InvariantGuardCandidatePacket
from vigil.neuro.guard_contract_llm import _parse_json, _registry_lines
from vigil.system_prompt import load_system_prompt

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.core.llm_client import LlmClient
    from vigil.neuro.invariant_evidence import InvariantEvidence


DEFAULT_INVARIANT_PROMPT = "invariant_guard_generaton.spec"

# Cap how much per-observation detail enters the prompt so multi-visit states stay bounded.
_MAX_PROMPT_OBSERVATIONS = 4
_MAX_OBSERVATION_ELEMENTS = 25
_MAX_PROMPT_IMAGES = 3


class LlmInvariantPacketCandidate(BaseModel):
    """An LLM-produced candidate packet, before admission."""

    packet: InvariantGuardCandidatePacket = Field(default_factory=InvariantGuardCandidatePacket)
    rejection_reason: str = ""
    raw_response: str = ""
    raw_responses: list[str] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)


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


def build_invariant_user_prompt(evidence: InvariantEvidence) -> str:
    """Build the per-state user prompt for invariant/guard candidate generation."""
    reg_lines = _registry_lines(evidence.arrival_registry)
    existing = [spec.expr for spec in evidence.existing_invariant_specs]

    sections: list[str] = []
    sections.append(
        "/* Contract-first invariant + guard synthesis */\n"
        "Generate the typed InvariantGuardCandidatePacket for this ALREADY-BUILT arrival "
        "state. Topology is fixed: do not invent states/transitions/actions/confidence.\n"
        "Runtime state invariants are post-arrival checks evaluated with ScreenContext "
        "only — they may use read/value/contains/count over the arrival registry, and must "
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
        "Purpose: preservation evidence + post-arrival effect/side-effect classification.\n"
        + ("\n".join(_transition_lines(evidence.incoming)) if evidence.incoming else "- (none)")
    )

    sections.append(
        "[Outgoing transitions]\n"
        "Purpose: pre-action guard obligations and sibling choices.\n"
        + ("\n".join(_transition_lines(evidence.outgoing)) if evidence.outgoing else "- (none)")
    )

    sections.append(
        "[Global Information / Static APK Priors]\n"
        "Purpose: semantic role/domain/risk hints only; never runtime proof.\n"
        + (
            "\n".join(f"- {hint}" for hint in evidence.static_prior_hints)
            if evidence.static_prior_hints
            else "- (none)"
        )
    )

    sections.append(
        "[Output]\n"
        "Return JSON only: the InvariantGuardCandidatePacket object from the system prompt "
        "(state_invariant_candidates, transition_guard_candidates, effect_invariant_hints, "
        "rejected_candidates, notes).\n"
        "For every transition_guard_candidate, copy the matching transition's exact `cak=` "
        "value into canonical_action_key so it binds to the right transition when several "
        "share the same (source, target)."
    )

    return "\n\n".join(sections)


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
# Parsing
# ---------------------------------------------------------------------------


def parse_invariant_guard_packet(raw_response: str) -> LlmInvariantPacketCandidate:
    """Parse a raw LLM response into a candidate packet, never raising."""
    parsed = _parse_json(raw_response)
    if parsed is None:
        return LlmInvariantPacketCandidate(
            rejection_reason="LLM output is not valid JSON",
            raw_response=raw_response,
            raw_responses=[raw_response],
            parse_errors=["LLM output is not valid JSON"],
        )
    if not isinstance(parsed, dict):
        return LlmInvariantPacketCandidate(
            rejection_reason="LLM output is not a JSON object",
            raw_response=raw_response,
            raw_responses=[raw_response],
            parse_errors=["LLM output is not a JSON object"],
        )
    try:
        packet = InvariantGuardCandidatePacket.model_validate(parsed)
    except ValidationError as exc:
        reason = f"packet schema validation failed: {exc.error_count()} error(s)"
        return LlmInvariantPacketCandidate(
            rejection_reason=reason,
            raw_response=raw_response,
            raw_responses=[raw_response],
            parse_errors=[reason],
        )
    return LlmInvariantPacketCandidate(
        packet=packet,
        raw_response=raw_response,
        raw_responses=[raw_response],
    )


def generate_llm_invariant_guard_candidate(
    evidence: InvariantEvidence,
    llm: LlmClient,
    *,
    prompt_name: str = DEFAULT_INVARIANT_PROMPT,
    use_images: bool = True,
) -> LlmInvariantPacketCandidate:
    """Generate an invariant/guard candidate packet for one state via the LLM."""
    system_prompt = load_system_prompt(prompt_name)
    user_prompt = build_invariant_user_prompt(evidence)
    try:
        images, image_labels = invariant_image_paths(evidence) if use_images else ([], [])
        if images and hasattr(llm, "generate_with_images"):
            raw = llm.generate_with_images(system_prompt, user_prompt, images, image_labels)
        else:
            raw = llm.generate(system_prompt, user_prompt)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash the pipeline
        logger.warning(
            f"LLM invariant generation failed for state {evidence.target_state_id}: {exc}"
        )
        return LlmInvariantPacketCandidate(
            rejection_reason=f"llm call failed: {exc}",
            parse_errors=[f"llm call failed: {exc}"],
        )
    return parse_invariant_guard_packet(raw)
