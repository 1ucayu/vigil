"""Tests for the LLM-backed guard-contract generator (fake LlmClient only)."""

from __future__ import annotations

import json
from typing import Any

from vigil.models.guard import GuardKind, RiskLevel
from vigil.neuro.guard_contract_llm import (
    build_guard_user_prompt,
    generate_llm_guard_candidate,
    parse_llm_guard_candidate,
)
from vigil.neuro.guard_evidence import GuardEvidence
from vigil.neuro.guard_registry import WidgetRegistry, WidgetRegistryEntry, WidgetRole


class FakeLlmClient:
    """Minimal stand-in exposing ``generate(system_prompt, user_prompt) -> str``."""

    def __init__(self, response: str = "", *, raise_exc: Exception | None = None) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _evidence() -> GuardEvidence:
    reg = WidgetRegistry(state_id="s1")
    reg.entries["send"] = WidgetRegistryEntry(
        alias="send",
        resource_id="com.test:id/send",
        text="Send",
        role=WidgetRole.BUTTON,
        risk_hints=["send"],
        readable_props=["text", "is_enabled"],
    )
    return GuardEvidence(
        transition_index=7,
        source_state_id="s1",
        target_state_id="s2",
        action={"type": "click", "target": "e_send", "target_text": "Send"},
        source_state_name="Thread",
        source_page_function="messaging/thread",
        target_state_name="Sent",
        target_page_function="messaging/sent",
        source_registry=reg,
        action_target_alias="send",
        sibling_actions=[{"type": "click", "target_text": "Attach"}],
        static_prior_hints=["perm:SEND_SMS"],
        diff_summary="+com.test:id/banner",
    )


def _contract_json(**overrides: Any) -> dict[str, Any]:
    contract = {
        "kind": "confirm_commit",
        "required": True,
        "risk_level": "high",
        "required_slots": [
            {"name": "contact_name", "slot_type": "string", "description": "", "required": True}
        ],
        "predicates": [
            {
                "predicate_type": "action",
                "property": "target_text",
                "operator": "==",
                "expected": {"kind": "intent", "slot": "contact_name"},
            }
        ],
        "binding_requirements": [],
        "semantic_binding_required": True,
        "semantic_binding_incomplete": False,
        "confidence": 0.7,
        "provenance": ["llm"],
        "notes": "",
    }
    contract.update(overrides)
    return contract


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_user_prompt_includes_evidence_and_marks_target_effect_only():
    prompt = build_guard_user_prompt(_evidence())
    assert "s1" in prompt and "s2" in prompt
    assert "Send" in prompt
    assert "EFFECT-ONLY" in prompt
    assert "messaging/thread" in prompt
    # Source registry alias is offered as a referenceable element.
    assert "alias=send" in prompt
    assert "perm:SEND_SMS" in prompt


# ---------------------------------------------------------------------------
# JSON parsing / validation
# ---------------------------------------------------------------------------


def test_parses_wrapper_json():
    raw = json.dumps({"contract": _contract_json(), "semantic_binding_incomplete": False})
    candidate = parse_llm_guard_candidate(raw)
    assert candidate.rejection_reason == ""
    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT
    assert candidate.contract.risk_level is RiskLevel.HIGH
    assert candidate.raw_response == raw


def test_parses_bare_contract_object():
    raw = json.dumps(_contract_json())
    candidate = parse_llm_guard_candidate(raw)
    assert candidate.rejection_reason == ""
    assert candidate.contract.required is True


def test_strips_code_fences():
    raw = "```json\n" + json.dumps({"contract": _contract_json()}) + "\n```"
    candidate = parse_llm_guard_candidate(raw)
    assert candidate.rejection_reason == ""
    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT


def test_garbage_is_rejected_not_raised():
    candidate = parse_llm_guard_candidate("I think the guard should be read(send, is_enabled)")
    assert candidate.rejection_reason
    assert candidate.contract.kind is GuardKind.UNKNOWN
    assert candidate.raw_response  # preserved for audit


def test_top_level_incomplete_flag_propagates():
    raw = json.dumps({"contract": _contract_json(), "semantic_binding_incomplete": True})
    candidate = parse_llm_guard_candidate(raw)
    assert candidate.semantic_binding_incomplete is True
    assert candidate.contract.semantic_binding_incomplete is True


def test_disallowed_predicate_expected_kind_is_rejected():
    # A predicate that compares against a read/action value (or a smuggled $bind) is not
    # allowed on the LLM path — the whole candidate is rejected, not silently dropped.
    bad = _contract_json(
        predicates=[
            {
                "predicate_type": "read",
                "element": "send",
                "property": "text",
                "operator": "==",
                "expected": {"kind": "read", "element": "other", "property": "text"},
            }
        ]
    )
    candidate = parse_llm_guard_candidate(json.dumps({"contract": bad}))
    assert candidate.rejection_reason
    assert "not allowed" in candidate.rejection_reason


def test_binding_requirements_preserved_as_metadata():
    contract = _contract_json(
        binding_requirements=[{"name": "selected_payee", "bind_kind": "row", "description": "chip"}]
    )
    candidate = parse_llm_guard_candidate(json.dumps({"contract": contract}))
    assert candidate.rejection_reason == ""
    assert len(candidate.contract.binding_requirements) == 1
    assert candidate.contract.binding_requirements[0].name == "selected_payee"


# ---------------------------------------------------------------------------
# End-to-end with a fake client
# ---------------------------------------------------------------------------


def test_generate_calls_client_and_returns_candidate():
    llm = FakeLlmClient(json.dumps({"contract": _contract_json()}))
    candidate = generate_llm_guard_candidate(_evidence(), llm)
    assert len(llm.calls) == 1
    system_prompt, user_prompt = llm.calls[0]
    assert "guard contract" in system_prompt.lower() or "GuardContract" in system_prompt
    assert "Known action" in user_prompt
    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT


def test_generate_handles_client_exception():
    llm = FakeLlmClient(raise_exc=RuntimeError("proxy down"))
    candidate = generate_llm_guard_candidate(_evidence(), llm)
    assert "llm call failed" in candidate.rejection_reason
    assert candidate.contract.kind is GuardKind.UNKNOWN
