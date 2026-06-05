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
from pathlib import Path
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
    from vigil.neuro.guard_evidence import GuardEvidence, ScreenEvidence
    from vigil.neuro.guard_registry import WidgetRegistry


DEFAULT_GUARD_PROMPT = "guard_generation.spec"

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


def _fenced(label: str, value: str) -> str:
    if not value.strip():
        return f"{label}:\n(none)"
    return f"{label}:\n```text\n{value}\n```"


def _screen_section(title: str, screen: ScreenEvidence, *, source_readable: bool) -> str:
    image_status = "not available"
    if screen.screenshot_path:
        image_status = "attached as image if the file exists"

    purpose = (
        "This is P/source: the ONLY UI state that guard predicates may read."
        if source_readable
        else (
            "This is Q/target: EFFECT-ONLY evidence for understanding the known action. "
            "Do NOT reference target-only elements in guard predicates."
        )
    )

    parts = [
        f"[{title}]",
        f"Purpose: {purpose}",
        f"- state_id: {screen.state_id}",
        f"- screen_id: {screen.screen_id or '(none)'}",
        f"- activity: {screen.activity_name or '(none)'}",
        f"- package: {screen.package_name or '(none)'}",
        f"- display_name: {screen.display_name!r}",
        f"- page_function: {screen.page_function!r}",
        f"- screenshot_path: {screen.screenshot_path or '(none)'} ({image_status})",
        f"- xml_tree_path: {screen.xml_tree_path or '(none)'}",
        _fenced("LLM-derived visual alt text / layout summary", screen.alt_text),
        _fenced("Compact accessibility/XML tree summary", screen.compact_tree_text),
        _fenced("Bounded XML file excerpt", screen.xml_excerpt),
    ]
    return "\n".join(parts)


def _existing_image_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.exists() and path.is_file():
        return path
    return None


def guard_image_paths(evidence: GuardEvidence) -> tuple[list[Path], list[str]]:
    """Return existing source/target screenshots to attach to the LLM request."""
    images: list[Path] = []
    labels: list[str] = []
    seen: set[Path] = set()
    for side, screen, label in (
        (
            "SOURCE",
            evidence.source_screen,
            "SOURCE screenshot: pre-state P. Guard predicates may read this UI.",
        ),
        (
            "TARGET",
            evidence.target_screen,
            "TARGET screenshot: post-state Q/effect-only. Do not predicate on target-only UI.",
        ),
    ):
        path = _existing_image_path(screen.screenshot_path)
        if path is None:
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        images.append(path)
        labels.append(f"{label} state={screen.state_id} screen={screen.screen_id or side.lower()}")
    return images, labels


def build_guard_user_prompt(evidence: GuardEvidence) -> str:
    """Build the Hoare-style user prompt for one transition's guard contract."""
    reg_lines = _registry_lines(evidence.source_registry)
    sib_lines = _sibling_lines(evidence.sibling_actions)
    hints = evidence.static_prior_hints

    sections: list[str] = []
    sections.append(
        "/* Hoare-style Transition Evidence */\n"
        "Generate only the pre-action GuardContract Gamma for this transition:\n"
        "{ Gamma(source screen P, known_action properties, frozen $intent.*) } "
        "known_action { target_state / effect-only evidence Q }.\n"
        "$bind.* needs are metadata in binding_requirements, not executable Gamma predicates."
    )

    sections.append(
        "[Transition]\n"
        f"- index: {evidence.transition_index}\n"
        f"- source_state_id: {evidence.source_state_id}\n"
        f"- target_state_id: {evidence.target_state_id}\n"
        f"- source_state_name: {evidence.source_state_name!r}\n"
        f"- source_page_function: {evidence.source_page_function!r}\n"
        f"- source_screen_ids: {evidence.source_screen_ids}\n"
        f"- replay_confidence: {evidence.replay_confidence}\n"
        f"- low_trust: {evidence.low_trust}"
    )

    sections.append(
        "[Known action]\n"
        "Purpose: Fixed transition action.\n"
        f"{json.dumps(evidence.action, ensure_ascii=False, indent=2)}\n"
        f"- resolved source-widget alias: {evidence.action_target_alias!r} "
        f"({evidence.action_target_alias_reason})"
    )

    sections.append(
        _screen_section(
            "Pre-state Evidence: P / source",
            evidence.source_screen,
            source_readable=True,
        )
    )

    sections.append(
        "[Source widget registry]\n"
        "Purpose: The ONLY source element aliases executable predicates may reference.\n"
        + ("\n".join(reg_lines) if reg_lines else "- (none resolved)")
    )

    sections.append(
        _screen_section(
            "Post-state Evidence: Q / target (effect-only)",
            evidence.target_screen,
            source_readable=False,
        )
    )

    sections.append(
        "[Source-to-target semantic/evidence diff]\n"
        "Purpose: Effect-only semantic disambiguation; do not predicate on target-only UI.\n"
        f"- target_state_name: {evidence.target_state_name!r}\n"
        f"- target_page_function: {evidence.target_page_function!r}\n"
        f"- target_screen_ids: {evidence.target_screen_ids}\n"
        f"- source->target diff: {evidence.diff_summary or '(none)'}"
    )

    sections.append(
        "[Sibling outgoing actions]\n"
        "Purpose: Distinguish choices, form controls, commits, navigation, and cancel actions.\n"
        + ("\n".join(sib_lines) if sib_lines else "- (none)")
    )

    sections.append(
        "[Global Information / Static APK Priors]\n"
        "Purpose: Hints only; never transition proof, runtime proof, or post-state checks.\n"
        + ("\n".join(f"- {h}" for h in hints) if hints else "- (none)")
    )

    sections.append(
        "[Verifier Basis]\n"
        "- Output typed GuardContract JSON.\n"
        "- Predicates compile to conjunctions over: read, value, action, contains, count, "
        "in_state, time_in.\n"
        "- Predicates may reference only source widget aliases, proposed action properties, "
        "literal values, and declared frozen $intent.* slots.\n"
        "- Put UI/action-side $bind.* needs in binding_requirements metadata only; never "
        "inside executable predicates."
    )

    sections.append(
        "[Output]\n"
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
    use_images: bool = True,
) -> LlmGuardContractCandidate:
    """Generate a guard-contract candidate for one transition via the LLM.

    Loads the system prompt by name, builds the Hoare user prompt from ``evidence``, calls
    the LLM (multimodal when source/target screenshots exist), and parses the response
    into a validated candidate. Any LLM/parse failure degrades to a rejected candidate
    rather than raising.
    """
    system_prompt = load_system_prompt(prompt_name)
    user_prompt = build_guard_user_prompt(evidence)
    try:
        images, image_labels = guard_image_paths(evidence) if use_images else ([], [])
        if images and hasattr(llm, "generate_with_images"):
            raw = llm.generate_with_images(system_prompt, user_prompt, images, image_labels)
        else:
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
