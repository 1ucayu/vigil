"""Tests for the structured-output LLM guard-contract generator (fake client only)."""

from __future__ import annotations

from typing import Any

from vigil.core.structured import StructuredResult
from vigil.models.guard import GuardKind
from vigil.models.llm_structured import LlmTransitionGuardResponse
from vigil.neuro.guard_contract_llm import (
    build_guard_user_prompt,
    candidate_from_structured_result,
    generate_llm_guard_candidate,
    guard_image_paths,
)
from vigil.neuro.guard_evidence import GuardEvidence, ScreenEvidence
from vigil.neuro.guard_registry import WidgetRegistry, WidgetRegistryEntry, WidgetRole
from vigil.neuro.prompt_redaction import PromptRedactor


class FakeStructuredLlm:
    """Stand-in exposing only the structured interface.

    The plain ``generate`` / ``generate_with_images`` raise, so any test that accidentally
    routes through the old prompt-only path fails loudly.
    """

    def __init__(
        self,
        parsed: LlmTransitionGuardResponse | None,
        *,
        schema_constraint_mode: str = "native_schema",
        refusal: str | None = None,
        validation_errors: list[str] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._parsed = parsed
        self._mode = schema_constraint_mode
        self._refusal = refusal
        self._validation_errors = validation_errors or []
        self._raise = raise_exc
        self.structured_calls = 0
        self.image_calls = 0

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
            refusal=self._refusal,
            validation_errors=list(self._validation_errors),
        )

    def generate_structured(self, system_prompt, user_prompt, response_model, schema_name, **_kw):
        self.structured_calls += 1
        return self._result(schema_name)

    def generate_structured_with_images(
        self,
        system_prompt,
        text_prompt,
        images,
        response_model,
        schema_name,
        image_labels=None,
        **_kw,
    ):
        self.image_calls += 1
        return self._result(schema_name)

    def generate(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError("structured path must not call plain generate()")

    def generate_with_images(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError("structured path must not call plain generate_with_images()")


def _evidence() -> GuardEvidence:
    reg = WidgetRegistry(state_id="s1")
    reg.entries["send"] = WidgetRegistryEntry(
        alias="send",
        resource_id="com.test:id/send",
        text="Send",
        role=WidgetRole.BUTTON,
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
            package_name="com.test",
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
            package_name="com.test",
            screenshot_path="screens/target.png",
            xml_tree_path="trees/target.xml",
            compact_tree_text='[c_0001] TextView banner ;; text="Sent"',
            xml_excerpt='<node resource-id="com.test:id/banner" text="Sent" />',
            alt_text="The sent confirmation banner is visible.",
            page_function="messaging/sent",
        ),
        source_registry=reg,
        target_invariants=['read(com.test:id/banner, text) == "Sent"'],
        action_target_alias="send",
        sibling_actions=[{"type": "click", "target_text": "Attach"}],
        static_prior_hints=["perm:SEND_SMS"],
        diff_summary="+com.test:id/banner",
    )


def _item_response(**overrides: Any) -> LlmTransitionGuardResponse:
    payload = {
        "contract": {
            "kind": "confirm_commit",
            "slots": [{"name": "contact_name", "slot_type": "string"}],
            "predicates": [
                {
                    "predicate_type": "action",
                    "property": "target_text",
                    "operator": "==",
                    "expected": {"kind": "intent", "slot": "contact_name"},
                }
            ],
        }
    }
    payload.update(overrides)
    return LlmTransitionGuardResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_user_prompt_includes_transition_guard_scope():
    prompt = build_guard_user_prompt(_evidence())
    assert "s1" in prompt and "s2" in prompt
    assert "Send" in prompt
    assert "transition guard contract" in prompt
    assert "[Target-state invariants I(Q)]" in prompt
    assert "[Transition]" in prompt
    assert "[Known action]" in prompt
    assert "[Pre-state Evidence: P / source]" in prompt
    assert "[Semantic Binding Checklist]" in prompt
    assert "alias=send" in prompt
    assert "perm:SEND_SMS" in prompt
    assert "XML file text" not in prompt
    # No legacy wrapper language remains.
    assert "precondition" not in prompt.lower()


def test_user_prompt_redacts_identifiers_when_redactor_supplied():
    redactor = PromptRedactor(
        packages=["com.test"], screen_ids=["scr_source", "scr_target"], paths=["screens/source.png"]
    )
    prompt = build_guard_user_prompt(_evidence(), redactor=redactor)
    assert "com.test:" not in prompt
    assert "scr_source" not in prompt
    # Sanitized resource hint keeps the suffix; alias and permission survive.
    assert "<app>:id/send" in prompt
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
    assert "SOURCE screenshot" in labels[0]
    assert "TARGET screenshot" in labels[1]


# ---------------------------------------------------------------------------
# Structured-result conversion
# ---------------------------------------------------------------------------


def test_candidate_from_parsed_result_is_admissible():
    result = StructuredResult(
        parsed=_item_response(),
        raw_text="{}",
        provider="proxy",
        model="m",
        schema_name="LlmTransitionGuardResponse",
        schema_hash="h",
        schema_constraint_mode="native_schema",
    )
    candidate = candidate_from_structured_result(result, spec_hash="abc")
    assert candidate.parsed_ok is True
    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT
    assert candidate.schema_constraint_mode == "native_schema"
    assert candidate.spec_hash == "abc"
    assert candidate.rejection_reason == ""


def test_candidate_from_unavailable_result_is_rejected():
    result = StructuredResult(
        parsed=None,
        raw_text="",
        provider="proxy",
        model="m",
        schema_name="LlmTransitionGuardResponse",
        schema_hash="h",
        schema_constraint_mode="prompt_only_unavailable",
        validation_errors=["proxy lacks json_schema"],
    )
    candidate = candidate_from_structured_result(result, spec_hash="abc")
    assert candidate.parsed_ok is False
    assert candidate.contract.kind is GuardKind.UNKNOWN
    assert "structured output unavailable" in candidate.rejection_reason
    assert candidate.schema_constraint_mode == "prompt_only_unavailable"


# ---------------------------------------------------------------------------
# End-to-end with a fake structured client
# ---------------------------------------------------------------------------


def test_generate_uses_structured_interface_not_plain_generate():
    llm = FakeStructuredLlm(_item_response())
    candidate = generate_llm_guard_candidate(_evidence(), llm, use_images=False)
    assert llm.structured_calls == 1
    assert llm.image_calls == 0
    assert candidate.parsed_ok is True
    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT


def test_generate_uses_structured_images_when_screenshot_files_exist(tmp_path):
    source_img = tmp_path / "source.png"
    source_img.write_bytes(b"fake")
    ev = _evidence()
    ev.source_screen.screenshot_path = str(source_img)
    ev.target_screen.screenshot_path = ""
    llm = FakeStructuredLlm(_item_response())

    candidate = generate_llm_guard_candidate(ev, llm, use_images=True)

    assert candidate.parsed_ok is True
    assert llm.image_calls == 1
    assert llm.structured_calls == 0


def test_generate_defaults_to_caption_cache_even_when_screenshot_exists(tmp_path):
    source_img = tmp_path / "source.png"
    source_img.write_bytes(b"fake")
    ev = _evidence()
    ev.source_screen.screenshot_path = str(source_img)
    llm = FakeStructuredLlm(_item_response())

    candidate = generate_llm_guard_candidate(ev, llm)

    assert candidate.parsed_ok is True
    assert llm.structured_calls == 1
    assert llm.image_calls == 0


def test_generate_structured_unavailable_degrades_to_rejected_candidate():
    llm = FakeStructuredLlm(
        None,
        schema_constraint_mode="prompt_only_unavailable",
        validation_errors=["unsupported schema"],
    )
    candidate = generate_llm_guard_candidate(_evidence(), llm, use_images=False)
    assert candidate.parsed_ok is False
    assert "structured output unavailable" in candidate.rejection_reason
    assert candidate.contract.kind is GuardKind.UNKNOWN


def test_generate_handles_client_exception():
    llm = FakeStructuredLlm(None, raise_exc=RuntimeError("proxy down"))
    candidate = generate_llm_guard_candidate(_evidence(), llm, use_images=False)
    assert candidate.parsed_ok is False
    assert "llm call failed" in candidate.rejection_reason
    assert candidate.contract.kind is GuardKind.UNKNOWN


def test_generate_passes_redactor_to_prompt(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_build(evidence, *, redactor=None):
        captured["redactor"] = redactor
        return "PROMPT"

    monkeypatch.setattr("vigil.neuro.guard_contract_llm.build_guard_user_prompt", _fake_build)
    redactor = PromptRedactor(packages=["com.test"])
    generate_llm_guard_candidate(
        _evidence(), FakeStructuredLlm(_item_response()), use_images=False, redactor=redactor
    )
    assert captured["redactor"] is redactor
