"""Tests for the structured-output LLM invariant/guard candidate-packet path."""

from __future__ import annotations

from typing import Any

from vigil.core.structured import StructuredResult
from vigil.models.llm_structured import LlmInvariantGuardResponse
from vigil.neuro.guard_registry import build_widget_registry_from_screen
from vigil.neuro.invariant_evidence import InvariantEvidence
from vigil.neuro.invariant_guard_llm import (
    build_invariant_user_prompt,
    candidate_from_structured_result,
    generate_llm_invariant_guard_candidate,
)
from vigil.neuro.prompt_redaction import PromptRedactor


class FakeStructuredLlm:
    """Stand-in exposing only the structured interface."""

    def __init__(
        self,
        parsed: LlmInvariantGuardResponse | None,
        *,
        schema_constraint_mode: str = "native_schema",
        validation_errors: list[str] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._parsed = parsed
        self._mode = schema_constraint_mode
        self._validation_errors = validation_errors or []
        self._raise = raise_exc
        self.structured_calls = 0

    def _result(self, schema_name: str) -> StructuredResult:
        if self._raise is not None:
            raise self._raise
        return StructuredResult(
            parsed=self._parsed,
            raw_text="{}" if self._parsed is not None else "",
            provider="proxy",
            model="fake-model",
            schema_name=schema_name,
            schema_hash="deadbeef",
            schema_constraint_mode=self._mode,
            validation_errors=list(self._validation_errors),
        )

    def generate_structured(self, system_prompt, user_prompt, response_model, schema_name, **_kw):
        self.structured_calls += 1
        return self._result(schema_name)

    def generate_structured_with_images(self, *a, schema_name="x", **k):
        self.structured_calls += 1
        return self._result(schema_name)

    def generate(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError("structured path must not call plain generate()")

    def generate_with_images(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError("structured path must not call plain generate_with_images()")


def _evidence() -> InvariantEvidence:
    screen = {
        "screen_id": "a",
        "interactable_elements": [
            {"element_id": "t", "resource_id": "com.app:id/title", "text": "Done"}
        ],
    }
    registry = build_widget_registry_from_screen("s1", screen)
    return InvariantEvidence(
        target_state_id="s1",
        target_state_name="State1",
        raw_screen_ids=["scr_0001"],
        arrival_registry=registry,
        observations=[screen],
        observation_count=1,
        visual_alt_text="The screenshot shows a stable completion banner not encoded as XML text.",
    )


def _packet_response() -> LlmInvariantGuardResponse:
    return LlmInvariantGuardResponse.model_validate(
        {
            "candidates": [{"kind": "status", "expr": 'value(title) contains "Done"'}],
        }
    )


# ---------------------------------------------------------------------------
# Structured-result conversion
# ---------------------------------------------------------------------------


def test_candidate_from_parsed_packet() -> None:
    result = StructuredResult(
        parsed=_packet_response(),
        raw_text="{}",
        provider="proxy",
        model="m",
        schema_name="LlmInvariantGuardResponse",
        schema_hash="h",
        schema_constraint_mode="native_schema",
    )
    candidate = candidate_from_structured_result(result, spec_hash="abc")
    assert candidate.parsed_ok is True
    assert candidate.packet.state_invariant_candidates[0].expr == 'value(title) contains "Done"'
    assert candidate.packet.effect_invariant_hints == []
    assert candidate.spec_hash == "abc"


def test_candidate_from_unavailable_packet() -> None:
    result = StructuredResult(
        parsed=None,
        raw_text="",
        provider="proxy",
        model="m",
        schema_name="LlmInvariantGuardResponse",
        schema_hash="h",
        schema_constraint_mode="prompt_only_unavailable",
        validation_errors=["unsupported"],
    )
    candidate = candidate_from_structured_result(result, spec_hash="abc")
    assert candidate.parsed_ok is False
    assert candidate.packet.state_invariant_candidates == []
    assert "structured output unavailable" in candidate.rejection_reason


# ---------------------------------------------------------------------------
# End-to-end with a fake structured client
# ---------------------------------------------------------------------------


def test_generate_uses_structured_interface() -> None:
    llm = FakeStructuredLlm(_packet_response())
    candidate = generate_llm_invariant_guard_candidate(_evidence(), llm, use_images=False)
    assert candidate.parsed_ok is True
    assert candidate.packet.state_invariant_candidates
    assert llm.structured_calls == 1


def test_generate_llm_failure_degrades_to_empty_packet() -> None:
    llm = FakeStructuredLlm(None, raise_exc=RuntimeError("boom"))
    candidate = generate_llm_invariant_guard_candidate(_evidence(), llm, use_images=False)
    assert candidate.parsed_ok is False
    assert "llm call failed" in candidate.rejection_reason
    assert candidate.packet.state_invariant_candidates == []


def test_user_prompt_has_key_sections() -> None:
    prompt = build_invariant_user_prompt(_evidence())
    for marker in (
        "[Target state]",
        "[State observations]",
        "[Visual Caption Cache]",
        "[Arrival-state widget registry]",
        "[Incoming transitions]",
        "[Outgoing transitions]",
    ):
        assert marker in prompt
    assert "$intent" in prompt
    assert "completion banner" in prompt
    assert "[Transition Guard Candidate Checklist]" not in prompt
    assert "transition_guard_candidates" not in prompt


def test_user_prompt_redacts_identifiers_when_redactor_supplied() -> None:
    redactor = PromptRedactor(packages=["com.app"], screen_ids=["scr_0001"])
    prompt = build_invariant_user_prompt(_evidence(), redactor=redactor)
    assert "com.app:" not in prompt
    assert "scr_0001" not in prompt
    # Sanitized resource hint suffix survives.
    assert "<app>:id/title" in prompt


def test_generate_passes_redactor_to_prompt(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_build(evidence, *, redactor=None):
        captured["redactor"] = redactor
        return "PROMPT"

    monkeypatch.setattr("vigil.neuro.invariant_guard_llm.build_invariant_user_prompt", _fake_build)
    redactor = PromptRedactor(packages=["com.app"])
    generate_llm_invariant_guard_candidate(
        _evidence(), FakeStructuredLlm(_packet_response()), use_images=False, redactor=redactor
    )
    assert captured["redactor"] is redactor
