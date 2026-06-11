"""Tests for the LLM invariant/guard candidate-packet path (parsing + prompt)."""

from __future__ import annotations

import json

from vigil.neuro.guard_registry import build_widget_registry_from_screen
from vigil.neuro.invariant_evidence import InvariantEvidence
from vigil.neuro.invariant_guard_llm import (
    build_invariant_user_prompt,
    generate_llm_invariant_guard_candidate,
    parse_invariant_guard_packet,
)


class FakeLlmClient:
    """Minimal stand-in for LlmClient that records calls and returns a fixed response."""

    def __init__(self, response: str = "", *, raise_exc: Exception | None = None) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_exc:
            raise self.raise_exc
        return self.response

    def generate_with_images(self, system_prompt, text_prompt, images, image_labels=None) -> str:
        self.calls.append((system_prompt, text_prompt))
        if self.raise_exc:
            raise self.raise_exc
        return self.response


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
        arrival_registry=registry,
        observations=[screen],
        observation_count=1,
    )


_PACKET_JSON = json.dumps(
    {
        "state_invariant_candidates": [
            {
                "kind": "success_presence",
                "expr": 'contains(title, "Done")',
                "admission_target": "runtime_state_invariant",
                "source": "llm",
            }
        ],
        "transition_guard_candidates": [],
        "effect_invariant_hints": [
            {
                "target_state_id": "s1",
                "description": "after send, status is Sent",
                "desired_expr": 'read(status, text) == "Sent"',
                "why_not_runtime_state_invariant": "depends_on_action",
            }
        ],
        "rejected_candidates": [],
        "notes": "ok",
    }
)


def test_parse_packet_success() -> None:
    candidate = parse_invariant_guard_packet(_PACKET_JSON)
    assert candidate.rejection_reason == ""
    assert len(candidate.packet.state_invariant_candidates) == 1
    assert candidate.packet.state_invariant_candidates[0].expr == 'contains(title, "Done")'
    assert len(candidate.packet.effect_invariant_hints) == 1


def test_parse_packet_handles_fenced_json() -> None:
    fenced = f"```json\n{_PACKET_JSON}\n```"
    candidate = parse_invariant_guard_packet(fenced)
    assert candidate.rejection_reason == ""
    assert candidate.packet.state_invariant_candidates


def test_parse_packet_invalid_json_degrades() -> None:
    candidate = parse_invariant_guard_packet("not json at all")
    assert candidate.rejection_reason
    assert candidate.packet.state_invariant_candidates == []


def test_generate_uses_llm_and_returns_packet() -> None:
    llm = FakeLlmClient(_PACKET_JSON)
    candidate = generate_llm_invariant_guard_candidate(_evidence(), llm)
    assert candidate.rejection_reason == ""
    assert candidate.packet.state_invariant_candidates
    assert len(llm.calls) == 1


def test_generate_llm_failure_degrades_to_empty_packet() -> None:
    llm = FakeLlmClient(raise_exc=RuntimeError("boom"))
    candidate = generate_llm_invariant_guard_candidate(_evidence(), llm)
    assert "llm call failed" in candidate.rejection_reason
    assert candidate.packet.state_invariant_candidates == []


def test_user_prompt_has_key_sections() -> None:
    prompt = build_invariant_user_prompt(_evidence())
    for marker in (
        "[Target state]",
        "[State observations]",
        "[Arrival-state widget registry]",
        "[Incoming transitions]",
        "[Outgoing transitions]",
    ):
        assert marker in prompt
    # No screenshots on disk -> plain text generate path is used.
    assert "$intent" in prompt  # boundary instruction is present
