"""Tests for the LLM-backed guard-contract generator (fake LlmClient only)."""

from __future__ import annotations

import json
from typing import Any

from vigil.models.guard import GuardKind, RiskLevel
from vigil.neuro.guard_contract_llm import (
    build_guard_user_prompt,
    generate_llm_guard_candidate,
    guard_image_paths,
    parse_llm_guard_candidate,
)
from vigil.neuro.guard_evidence import GuardEvidence, ScreenEvidence
from vigil.neuro.guard_registry import WidgetRegistry, WidgetRegistryEntry, WidgetRole


class FakeLlmClient:
    """Minimal stand-in exposing ``generate(system_prompt, user_prompt) -> str``."""

    def __init__(self, response: str = "", *, raise_exc: Exception | None = None) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, str]] = []
        self.image_calls: list[tuple[str, str, list[Any], list[str] | None]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response

    def generate_with_images(
        self,
        system_prompt: str,
        text_prompt: str,
        images: list[Any],
        image_labels: list[str] | None = None,
    ) -> str:
        self.image_calls.append((system_prompt, text_prompt, images, image_labels))
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
        source_screen_ids=["scr_source"],
        target_screen_ids=["scr_target"],
        source_screen=ScreenEvidence(
            state_id="s1",
            screen_id="scr_source",
            screenshot_path="screens/source.png",
            xml_tree_path="trees/source.xml",
            compact_tree_text='[c_0001] Button send ;click; text="Send"',
            xml_excerpt='<node resource-id="com.test:id/send" text="Send" />',
            alt_text="A chat thread with a message composer and Send button.",
            page_function="messaging/thread",
        ),
        target_screen=ScreenEvidence(
            state_id="s2",
            screen_id="scr_target",
            screenshot_path="screens/target.png",
            xml_tree_path="trees/target.xml",
            compact_tree_text='[c_0001] TextView banner ;; text="Sent"',
            xml_excerpt='<node resource-id="com.test:id/banner" text="Sent" />',
            alt_text="The sent confirmation banner is visible.",
            page_function="messaging/sent",
        ),
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
    assert "transition pre/post contract" in prompt
    assert "precondition" in prompt
    assert "postcondition" in prompt
    assert "compatibility contract" in prompt
    assert "EFFECT-ONLY" in prompt
    assert "messaging/thread" in prompt
    assert "[Transition]" in prompt
    assert "[Known action]" in prompt
    assert "[Pre-state Evidence: P / source]" in prompt
    assert "[Post-state Evidence: Q / target" in prompt
    assert "[Global Information / Static APK Priors]" in prompt
    assert "trees/source.xml" in prompt
    assert "screens/source.png" in prompt
    assert "XML file text" in prompt
    assert 'resource-id="com.test:id/send"' in prompt
    assert "Button send" in prompt
    assert "A chat thread with a message composer" in prompt
    assert "target-only" in prompt
    # Source registry alias is offered as a referenceable element.
    assert "alias=send" in prompt
    assert "perm:SEND_SMS" in prompt


def test_guard_image_paths_uses_existing_source_and_target_screenshots(tmp_path):
    source_img = tmp_path / "source.png"
    target_img = tmp_path / "target.png"
    source_img.write_bytes(b"fake")
    target_img.write_bytes(b"fake")
    ev = _evidence()
    ev.source_screen.screenshot_path = str(source_img)
    ev.target_screen.screenshot_path = str(target_img)

    images, labels = guard_image_paths(ev)

    assert images == [source_img, target_img]
    assert labels is not None
    assert "SOURCE screenshot" in labels[0]
    assert "TARGET screenshot" in labels[1]


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


def test_parses_precondition_from_transition_contract_wrapper():
    raw = json.dumps(
        {
            "precondition": _contract_json(),
            "postcondition": {
                "kind": "message_sent",
                "required": True,
                "predicates": [],
                "intent_effect_required": True,
                "intent_effect_incomplete": True,
            },
            "semantic_binding_incomplete": False,
            "postcondition_incomplete": True,
        }
    )
    candidate = parse_llm_guard_candidate(raw)
    assert candidate.rejection_reason == ""
    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT
    assert candidate.contract.required is True
    assert candidate.postcondition is not None
    assert candidate.postcondition.kind == "message_sent"
    assert candidate.postcondition.intent_effect_required is True
    assert candidate.postcondition_incomplete is True


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


def test_disallowed_postcondition_expected_kind_is_rejected():
    postcondition = {
        "kind": "arrival_state",
        "required": True,
        "predicates": [
            {
                "predicate_type": "read",
                "element": "banner",
                "property": "text",
                "operator": "==",
                "expected": {"kind": "read", "element": "other", "property": "text"},
            }
        ],
    }
    candidate = parse_llm_guard_candidate(
        json.dumps({"contract": _contract_json(), "postcondition": postcondition})
    )
    assert candidate.rejection_reason
    assert "postcondition predicate" in candidate.rejection_reason


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
    assert "[Known action]" in user_prompt
    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT


def test_generate_uses_images_when_screenshot_files_exist(tmp_path):
    source_img = tmp_path / "source.png"
    source_img.write_bytes(b"fake")
    ev = _evidence()
    ev.source_screen.screenshot_path = str(source_img)
    ev.target_screen.screenshot_path = ""
    llm = FakeLlmClient(json.dumps({"contract": _contract_json()}))

    candidate = generate_llm_guard_candidate(ev, llm)

    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT
    assert len(llm.calls) == 0
    assert len(llm.image_calls) == 1
    _, prompt, images, labels = llm.image_calls[0]
    assert "Pre-state Evidence: P / source" in prompt
    assert images == [source_img]
    assert labels is not None and "SOURCE screenshot" in labels[0]


def test_generate_handles_client_exception():
    llm = FakeLlmClient(raise_exc=RuntimeError("proxy down"))
    candidate = generate_llm_guard_candidate(_evidence(), llm)
    assert "llm call failed" in candidate.rejection_reason
    assert candidate.contract.kind is GuardKind.UNKNOWN
