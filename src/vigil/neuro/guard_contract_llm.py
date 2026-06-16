"""LLM-backed, contract-first guard generation (Stage 4).

This module asks an LLM to produce a typed transition
:class:`~vigil.models.guard.GuardContract` for a *single, already-known* transition via
**provider structured output** (a strict
:class:`~vigil.models.llm_structured.LlmTransitionGuardResponse` schema), then converts
the parsed object into a
:class:`~vigil.models.guard.LlmGuardContractCandidate`.

Design constraints (CLAUDE.md -> "DSL Guard Generation Direction"; plan):

- The LLM never emits free-form DSL and never raw/fenced JSON — it emits a single
  schema-valid ``LlmTransitionGuardResponse`` object. Compilation/admission to executable
  DSL is a later deterministic step. There is no prompt-only JSON parsing and no
  repair-prompt loop on this path.
- The LLM may not create/modify FSM states, actions, transitions, replay confidence, or runtime
  verdicts. Target-state evidence is background context only; executable guard predicates may
  read only the source screen, proposed action, and frozen intent.
- The lean LLM schema exposes only ``kind``, ``slots``, and executable ``predicates``.
  Non-executable binding ideas are omitted; admission reports unsupported semantics.
- When structured output is unavailable (provider/schema failure, refusal, or validation
  failure), the result is a clearly rejected candidate (``parsed_ok=False``) carrying the
  provider/schema error — never a fabricated success.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from vigil.models.guard import LlmGuardContractCandidate
from vigil.models.llm_structured import LlmTransitionGuardResponse
from vigil.system_prompt import load_system_prompt

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vigil.core.llm_client import LlmClient
    from vigil.core.structured import StructuredResult
    from vigil.neuro.guard_evidence import GuardEvidence, ScreenEvidence
    from vigil.neuro.guard_registry import WidgetRegistry
    from vigil.neuro.prompt_redaction import PromptRedactor


DEFAULT_GUARD_PROMPT = "transition_guard_generation.spec"
GUARD_SCHEMA_NAME = "LlmTransitionGuardResponse"


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


def _invariant_lines(invariants: list[str]) -> list[str]:
    return [f"- {expr}" for expr in invariants if str(expr).strip()]


def _fenced(label: str, value: str) -> str:
    if not value.strip():
        return f"{label}:\n(none)"
    return f"{label}:\n```text\n{value}\n```"


def _screen_section(title: str, screen: ScreenEvidence, *, source_readable: bool) -> str:
    screenshot_status = "available in trace" if screen.screenshot_path else "not available"
    purpose = (
        "This is P/source: the ONLY UI state that transition guard Gamma may read."
        if source_readable
        else (
            "This is Q/target: background evidence for classifying the transition. "
            "Do NOT reference target-only elements in guard predicates."
        )
    )

    parts = [
        f"[{title}]",
        f"Purpose: {purpose}",
        f"- state_id: {screen.state_id}",
        f"- activity: {screen.activity_name or '(none)'}",
        f"- display_name: {screen.display_name!r}",
        f"- page_function: {screen.page_function!r}",
        f"- screenshot: {screenshot_status}; not attached unless explicit image mode is enabled",
        _fenced(
            "Visual Caption Cache (screenshot-only perception hint, not admission proof)",
            screen.alt_text,
        ),
        _fenced("Compact accessibility/XML tree summary", screen.compact_tree_text),
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
            "TARGET screenshot: successor Q. Background evidence only for Gamma.",
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


def build_guard_user_prompt(
    evidence: GuardEvidence,
    *,
    redactor: PromptRedactor | None = None,
) -> str:
    """Build the user prompt for one transition guard contract.

    When ``redactor`` is supplied, benchmark/identifier leakage (package names, app slugs,
    raw screen ids, local paths, evaluator labels) is masked in the assembled prompt while
    usable registry aliases, permissions, and action properties are preserved.
    """
    reg_lines = _registry_lines(evidence.source_registry)
    sib_lines = _sibling_lines(evidence.sibling_actions)
    invariant_lines = _invariant_lines(evidence.target_invariants)
    hints = evidence.static_prior_hints

    sections: list[str] = []
    sections.append(
        "/* Transition Guard Evidence */\n"
        "Generate a minimal transition guard contract Gamma for this already-known transition.\n"
        "Gamma may reference only source screen P, known_action properties, and frozen "
        "$intent.* variables."
    )

    sections.append(
        "[Transition]\n"
        f"- index: {evidence.transition_index}\n"
        f"- source_state_id: {evidence.source_state_id}\n"
        f"- target_state_id: {evidence.target_state_id}\n"
        f"- source_state_name: {evidence.source_state_name!r}\n"
        f"- source_page_function: {evidence.source_page_function!r}\n"
        f"- replay_confidence: {evidence.replay_confidence}"
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
            "Post-state Evidence: Q / target (background only)",
            evidence.target_screen,
            source_readable=False,
        )
    )

    sections.append(
        "[Target-state invariants I(Q)]\n"
        "Purpose: reusable target-state facts already admitted before this transition "
        "guard pass. Use them only as background context for deciding whether the "
        "source-side guard needs semantic or safety bindings.\n"
        + ("\n".join(invariant_lines) if invariant_lines else "- (none admitted)")
    )

    sections.append(
        "[Source-to-target semantic/evidence diff]\n"
        "Purpose: Transition classification context; do not predicate on target-only UI "
        "in Gamma.\n"
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
        "Purpose: Hints only; never transition proof, runtime proof, or proof of "
        "guard satisfaction.\n" + ("\n".join(f"- {h}" for h in hints) if hints else "- (none)")
    )

    sections.append(
        "[Verifier Basis]\n"
        "- Emit a single transition guard contract object (the structured schema fixes the "
        "shape).\n"
        "- Predicates compile to conjunctions over: read, value, action, count, "
        "and contains, with contains/not_contains as comparison operators.\n"
        "- Guard predicates may reference only source widget aliases, proposed action "
        "properties, literal values, and declared frozen $intent.* slots.\n"
        "- `slots` is the contract's declared intent interface. Declare slots "
        "only when grounded in source/action evidence or an explicitly supplied task "
        "intent interface; use generic role-derived names, not app-specific fixtures."
    )

    sections.append(
        "[Semantic Binding Checklist]\n"
        "- For input_text, row/item selection, option selection, form submission, and "
        "commit-like actions, first try an executable semantic binding predicate over "
        "action(...), read(...), or value(...) against a declared $intent.* slot.\n"
        "- A guard with only read(..., is_enabled) == true or "
        "read(..., is_clickable) == true is incomplete for those transitions when "
        "source/action evidence can bind the intended value or selected object.\n"
        "- Enabledness/clickability may be included as readiness checks in addition to "
        "semantic binding.\n"
        "- If semantic binding is plausible but cannot be grounded with source/action "
        "evidence and the supported vocabulary, leave executable predicates empty or "
        "readiness-only."
    )

    sections.append(
        "[Output]\n"
        "Emit only the minimal transition guard contract object described in the system prompt."
    )

    prompt = "\n\n".join(sections)
    if redactor is not None:
        prompt = redactor.redact(prompt)
    return prompt


# ---------------------------------------------------------------------------
# Structured-result conversion
# ---------------------------------------------------------------------------


def _failure_reason(result: StructuredResult) -> str:
    """Human-readable reason for a structured result that produced no parsed object."""
    if result.refusal:
        return f"provider refusal: {result.refusal}"
    if result.schema_constraint_mode == "prompt_only_unavailable":
        detail = result.validation_errors[0] if result.validation_errors else ""
        return f"structured output unavailable: {detail}".strip()
    if result.validation_errors:
        return f"schema validation failed: {result.validation_errors[0]}"
    if result.incomplete:
        return f"incomplete response: {result.incomplete_detail or 'truncated'}"
    return "no schema-valid object returned"


def _attach_structured_metadata(
    candidate: LlmGuardContractCandidate,
    result: StructuredResult,
    spec_hash: str,
) -> LlmGuardContractCandidate:
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
) -> LlmGuardContractCandidate:
    """Convert a :class:`StructuredResult` into a guard candidate (never raises)."""
    if result.parsed is not None:
        assert isinstance(result.parsed, LlmTransitionGuardResponse)
        candidate = result.parsed.to_runtime()
        candidate.parsed_ok = True
        return _attach_structured_metadata(candidate, result, spec_hash)

    reason = _failure_reason(result)
    candidate = LlmGuardContractCandidate(
        parsed_ok=False,
        rejection_reason=reason,
        parse_errors=[reason],
    )
    return _attach_structured_metadata(candidate, result, spec_hash)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_llm_guard_candidate(
    evidence: GuardEvidence,
    llm: LlmClient,
    *,
    prompt_name: str = DEFAULT_GUARD_PROMPT,
    use_images: bool = False,
    redactor: PromptRedactor | None = None,
    allow_provider_fallback: bool = False,
) -> LlmGuardContractCandidate:
    """Generate a guard-contract candidate for one transition via structured output.

    Loads the system prompt, builds the (optionally redacted) user prompt from ``evidence``,
    and calls the provider's schema-constrained structured-output path with the strict
    :class:`LlmTransitionGuardResponse` model. By default this stage does not resend
    screenshots; it consumes the visual caption cache produced by visual grounding. Explicit
    image mode remains available for debugging or low-confidence perception fallback. The
    parsed object is converted directly into a candidate; a structured-output failure yields a
    clearly rejected candidate (``parsed_ok=False``) rather than a fabricated success. No
    prompt-only JSON parsing or repair re-prompts run on this path.
    """
    system_prompt = load_system_prompt(prompt_name)
    spec_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    user_prompt = build_guard_user_prompt(evidence, redactor=redactor)
    try:
        images, image_labels = guard_image_paths(evidence) if use_images else ([], [])
        if redactor is not None:
            image_labels = [redactor.redact(label) for label in image_labels]
        if images and hasattr(llm, "generate_structured_with_images"):
            result = llm.generate_structured_with_images(
                system_prompt,
                user_prompt,
                images,
                LlmTransitionGuardResponse,
                GUARD_SCHEMA_NAME,
                image_labels,
                allow_provider_fallback=allow_provider_fallback,
            )
        else:
            result = llm.generate_structured(
                system_prompt,
                user_prompt,
                LlmTransitionGuardResponse,
                GUARD_SCHEMA_NAME,
                allow_provider_fallback=allow_provider_fallback,
            )
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash the pipeline
        logger.warning(
            f"LLM guard generation failed for transition {evidence.transition_index}: {exc}"
        )
        reason = f"llm call failed: {exc}"
        return LlmGuardContractCandidate(
            parsed_ok=False,
            rejection_reason=reason,
            parse_errors=[reason],
            validation_errors=[reason],
            schema_name=GUARD_SCHEMA_NAME,
            schema_constraint_mode="prompt_only_unavailable",
            spec_hash=spec_hash,
        )
    return candidate_from_structured_result(result, spec_hash)
