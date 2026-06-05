"""LLM-backed, contract-first guard generation (Stage 4).

This module asks an LLM to produce a typed :class:`~vigil.models.guard.GuardContract`
(the Hoare precondition ``Gamma``) for a *single, already-known* transition, then parses
and validates that JSON into a :class:`~vigil.models.guard.LlmGuardContractCandidate`.

Design constraints (CLAUDE.md → "DSL Guard Generation Direction"; plan):

- The LLM never emits free-form DSL as its primary artifact — it emits typed contract
  JSON. Compilation/admission to executable DSL is a later deterministic step.
- The LLM may not create/modify FSM states, actions, transitions, replay confidence, or
  runtime verdicts. Target-state evidence is effect-only.
- ``$bind.*`` is metadata only: it appears in ``contract.binding_requirements``, never as a
  predicate / :class:`~vigil.models.guard.ValueRef`. A predicate's ``expected.kind`` may
  only be ``literal`` or ``intent`` on this path; anything else makes the candidate a
  rejection (the compiler/admission would drop it anyway).
- Parsing is defensive: any failure returns a rejected candidate (with a reason) rather
  than raising into the pipeline.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError

from vigil.models.guard import (
    GuardContract,
    LlmGuardContractCandidate,
    RiskLevel,
)
from vigil.system_prompt import load_system_prompt

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.core.llm_client import LlmClient
    from vigil.neuro.guard_evidence import GuardEvidence
    from vigil.neuro.guard_registry import WidgetRegistry


DEFAULT_GUARD_PROMPT = "guard_contract_generation.md"

# RHS value kinds the LLM path is allowed to put inside a predicate. ``$bind.*`` and other
# UI/action-side references must live in ``binding_requirements``, not predicates.
_ALLOWED_EXPECTED_KINDS: frozenset[str] = frozenset({"literal", "intent"})


# ---------------------------------------------------------------------------
# JSON parsing (mirrors visual_grounder._parse_json)
# ---------------------------------------------------------------------------


def _parse_json(response: str) -> Any | None:
    """Parse possibly fenced / prose-wrapped JSON, returning ``None`` on failure."""
    text = (response or "").strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _registry_lines(registry: WidgetRegistry) -> list[str]:
    lines: list[str] = []
    for alias, entry in registry.entries.items():
        props = ", ".join(entry.readable_props)
        parts = [f"- alias={alias}"]
        if entry.resource_id:
            parts.append(f"resource_id={entry.resource_id}")
        if entry.text:
            parts.append(f"text={entry.text!r}")
        if entry.content_description:
            parts.append(f"content_desc={entry.content_description!r}")
        parts.append(f"role={entry.role.value}")
        if entry.risk_hints:
            parts.append(f"risk_hints={','.join(entry.risk_hints)}")
        if props:
            parts.append(f"readable=[{props}]")
        lines.append(" ".join(parts))
    return lines


def _sibling_lines(siblings: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for action in siblings:
        atype = str(action.get("type") or "")
        text = str(action.get("target_text") or "").strip()
        suffix = f" target_text={text!r}" if text else ""
        lines.append(f"- {atype}{suffix}")
    return lines


def build_guard_user_prompt(evidence: GuardEvidence) -> str:
    """Build the Hoare-style user prompt for one transition's guard contract."""
    reg_lines = _registry_lines(evidence.source_registry)
    sib_lines = _sibling_lines(evidence.sibling_actions)
    hints = evidence.static_prior_hints

    sections: list[str] = []
    sections.append(
        "Generate the pre-action guard contract Gamma for this known transition.\n"
        "Reason over the SOURCE screen + the proposed action + frozen $intent.*; the\n"
        "TARGET screen is effect-only evidence and must not be referenced by predicates."
    )

    sections.append(
        "## Transition\n"
        f"- index: {evidence.transition_index}\n"
        f"- source_state_id: {evidence.source_state_id}\n"
        f"- target_state_id: {evidence.target_state_id}\n"
        f"- source_state_name: {evidence.source_state_name!r}\n"
        f"- source_page_function: {evidence.source_page_function!r}\n"
        f"- replay_confidence: {evidence.replay_confidence}\n"
        f"- low_trust: {evidence.low_trust}"
    )

    sections.append(
        "## Known action (fixed — do not change)\n"
        f"{json.dumps(evidence.action, ensure_ascii=False, indent=2)}\n"
        f"- resolved source-widget alias: {evidence.action_target_alias!r} "
        f"({evidence.action_target_alias_reason})"
    )

    sections.append(
        "## Source widget registry (the ONLY elements predicates may reference)\n"
        + ("\n".join(reg_lines) if reg_lines else "- (none resolved)")
    )

    sections.append(
        "## Target state (EFFECT-ONLY — never reference in predicates)\n"
        f"- target_state_name: {evidence.target_state_name!r}\n"
        f"- target_page_function: {evidence.target_page_function!r}\n"
        f"- source->target diff: {evidence.diff_summary or '(none)'}"
    )

    sections.append(
        "## Sibling outgoing actions from the source state\n"
        + ("\n".join(sib_lines) if sib_lines else "- (none)")
    )

    sections.append(
        "## Static APK prior hints (hints only — never proof)\n"
        + ("\n".join(f"- {h}" for h in hints) if hints else "- (none)")
    )

    sections.append(
        "## Output\n"
        "Return ONLY the JSON object described in the system prompt. No prose, no code\n"
        "fences, no DSL string. Put any $bind.* UI-side binding in binding_requirements,\n"
        "never in predicates."
    )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Candidate validation
# ---------------------------------------------------------------------------


def _coerce_candidate(parsed: Any) -> tuple[LlmGuardContractCandidate | None, str]:
    """Validate parsed JSON into a candidate. Returns ``(candidate, reason)``.

    ``candidate`` is ``None`` when the JSON cannot be validated as a contract.
    """
    if not isinstance(parsed, dict):
        return None, "LLM output is not a JSON object"

    # Accept either a wrapper {contract: {...}, ...} or a bare contract object.
    contract_payload = parsed.get("contract") if "contract" in parsed else parsed
    if not isinstance(contract_payload, dict):
        return None, "missing 'contract' object"

    try:
        contract = GuardContract.model_validate(contract_payload)
    except ValidationError as exc:
        return None, f"contract schema validation failed: {exc.error_count()} error(s)"

    # Boundary enforcement: predicates may only compare against literal / intent values.
    # Anything else (action/read RHS, or a smuggled $bind reference) is not allowed on the
    # LLM path — treat the whole candidate as rejected so we do not silently drop bindings.
    for pred in contract.predicates:
        kind = pred.expected.kind if pred.expected is not None else None
        if kind is not None and kind not in _ALLOWED_EXPECTED_KINDS:
            return None, (
                f"predicate expected.kind={kind!r} not allowed on the LLM path "
                "($bind.* / action / read RHS must be metadata, not a predicate)"
            )

    incomplete = bool(
        parsed.get("semantic_binding_incomplete", contract.semantic_binding_incomplete)
    )
    contract.semantic_binding_incomplete = contract.semantic_binding_incomplete or incomplete
    rejection_reason = str(parsed.get("rejection_reason") or "")
    return (
        LlmGuardContractCandidate(
            contract=contract,
            semantic_binding_incomplete=contract.semantic_binding_incomplete,
            rejection_reason=rejection_reason,
        ),
        "",
    )


def parse_llm_guard_candidate(raw_response: str) -> LlmGuardContractCandidate:
    """Parse a raw LLM response into a candidate, never raising.

    On any parse/validation failure, returns a candidate whose ``contract`` is empty and
    whose ``rejection_reason`` explains why. ``raw_response`` is always preserved for audit.
    """
    parsed = _parse_json(raw_response)
    if parsed is None:
        return LlmGuardContractCandidate(
            rejection_reason="LLM output is not valid JSON",
            raw_response=raw_response,
        )
    candidate, reason = _coerce_candidate(parsed)
    if candidate is None:
        return LlmGuardContractCandidate(
            rejection_reason=reason,
            raw_response=raw_response,
        )
    candidate.raw_response = raw_response
    return candidate


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_llm_guard_candidate(
    evidence: GuardEvidence,
    llm: LlmClient,
    *,
    prompt_name: str = DEFAULT_GUARD_PROMPT,
) -> LlmGuardContractCandidate:
    """Generate a guard-contract candidate for one transition via the LLM.

    Loads the system prompt by name, builds the Hoare user prompt from ``evidence``, calls
    ``llm.generate``, and parses the response into a validated candidate. Any LLM/parse
    failure degrades to a rejected candidate rather than raising.
    """
    system_prompt = load_system_prompt(prompt_name)
    user_prompt = build_guard_user_prompt(evidence)
    try:
        raw = llm.generate(system_prompt, user_prompt)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash the pipeline
        logger.warning(
            f"LLM guard generation failed for transition {evidence.transition_index}: {exc}"
        )
        return LlmGuardContractCandidate(
            rejection_reason=f"llm call failed: {exc}",
            contract=GuardContract(risk_level=RiskLevel.UNKNOWN),
        )
    return parse_llm_guard_candidate(raw)
