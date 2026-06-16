"""Tests for the contract-first guard generation pipeline (step 4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vigil.core.structured import StructuredResult
from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.models.guard import GuardAdmissionStatus, GuardKind
from vigil.models.llm_structured import LlmTransitionGuardResponse
from vigil.neuro.guard_generation_pipeline import (
    generate_contract_guards,
    guard_action_schema_key,
    write_guard_generation_report,
)
from vigil.neuro.guard_registry import WidgetRegistry, WidgetRegistryEntry  # noqa: F401

PKG = "com.test.app"


def _el(element_id: str, **overrides) -> dict:
    base = {
        "element_id": element_id,
        "class_name": "android.view.View",
        "resource_id": "",
        "text": "",
        "content_description": "",
        "is_clickable": False,
        "is_enabled": True,
    }
    base.update(overrides)
    return base


def _screen(screen_id: str, *elements: dict) -> dict:
    return {
        "screen_id": screen_id,
        "activity_name": f"{PKG}.MainActivity",
        "package_name": PKG,
        "interactable_elements": list(elements),
    }


def _state(state_id: str, name: str, screen_id: str, page_function: str) -> AbstractState:
    state = AbstractState(
        state_id=state_id,
        name=name,
        fingerprint=f"fp_{state_id}",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        raw_screens=[screen_id],
        activity_name=f"{PKG}.MainActivity",
    )
    state.annotations.page_function = page_function
    return state


def _build_fsm() -> tuple[AppFSM, dict[str, dict[str, Any]]]:
    fsm = AppFSM(app_package=PKG)
    fsm.add_state(_state("s1", "Contacts", "scr_s1", "messaging/contacts"))
    fsm.add_state(_state("s2", "Thread", "scr_s2", "messaging/thread"))
    fsm.add_state(_state("s3", "Sent", "scr_s3", "messaging/sent"))
    fsm.initial_state = "s1"

    scr_s1 = _screen(
        "scr_s1",
        _el(
            "e_alice",
            class_name="android.widget.TextView",
            resource_id=f"{PKG}:id/contact_alice",
            text="Alice",
            is_clickable=True,
        ),
        _el(
            "e_bob",
            class_name="android.widget.TextView",
            resource_id=f"{PKG}:id/contact_bob",
            text="Bob",
            is_clickable=True,
        ),
    )
    scr_s2 = _screen(
        "scr_s2",
        _el(
            "e_send",
            class_name="android.widget.Button",
            resource_id=f"{PKG}:id/send",
            text="Send",
            is_clickable=True,
        ),
        _el(
            "e_msg",
            class_name="android.widget.EditText",
            resource_id=f"{PKG}:id/message_input",
            is_editable=True,
        ),
        # Side-effecting control with NO resource_id -> not runtime-resolvable -> rejected.
        _el(
            "e_delete",
            class_name="android.widget.Button",
            text="Delete",
            is_clickable=True,
        ),
    )
    scr_s3 = _screen("scr_s3")
    raw_screens = {"scr_s1": scr_s1, "scr_s2": scr_s2, "scr_s3": scr_s3}

    # T0: click Alice (item binding), T1 sibling click Bob.
    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={"type": "click", "target": "e_alice", "target_text": "Alice"},
            confidence=0.9,
        )
    )
    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={"type": "click", "target": "e_bob", "target_text": "Bob"},
            confidence=0.9,
        )
    )
    # T2: click Send (commit-like guard candidate).
    fsm.add_transition(
        Transition(
            source="s2",
            target="s3",
            action={"type": "click", "target": "e_send", "target_text": "Send"},
            confidence=0.9,
        )
    )
    # T3: input_text into message field (now admitted as action(input_text)).
    fsm.add_transition(
        Transition(
            source="s2",
            target="s2",
            action={"type": "input_text", "target": "e_msg", "text": "hi"},
            confidence=0.9,
        )
    )
    # T4: delete action with no resolvable resource_id; carries a pre-existing guard.
    fsm.add_transition(
        Transition(
            source="s2",
            target="s3",
            action={"type": "click", "target": "e_delete", "target_text": "Delete"},
            guard="preexisting_guard",
            confidence=0.9,
        )
    )
    return fsm, raw_screens


def test_list_item_transition_gets_guard_attached():
    fsm, raw_screens = _build_fsm()
    generate_contract_guards(fsm, raw_screens, guard_source="deterministic")

    t0 = fsm.transitions[0]
    assert t0.guard == "action(target_text) == $intent.contact_name"
    assert t0.requires_guard is False
    assert t0.guard_contract is not None
    assert t0.guard_contract.kind is GuardKind.ITEM_BINDING
    assert t0.guard_admission_status == GuardAdmissionStatus.ADMITTED.value


def test_commit_transition_attaches_executable_enabled_guard():
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(fsm, raw_screens, guard_source="deterministic")

    t2 = fsm.transitions[2]
    assert t2.requires_guard is False
    # Enabled-only guard is executable + evidence-backed -> attached normally.
    assert t2.guard == f"read({PKG}:id/send, is_enabled) == true"
    assert t2.guard_admission_status == GuardAdmissionStatus.ADMITTED.value
    assert t2.guard_admission_reason == "admitted: 1 executable predicate(s)"
    # Contract metadata is synced too.
    assert t2.guard_contract is not None
    assert t2.guard_contract.admission_status is GuardAdmissionStatus.ADMITTED
    assert t2.guard_contract.admission_reason == "admitted: 1 executable predicate(s)"
    assert "semantic_binding_incomplete" not in report[2]


def test_input_text_transition_attaches_input_text_guard():
    fsm, raw_screens = _build_fsm()
    generate_contract_guards(fsm, raw_screens, guard_source="deterministic")

    t3 = fsm.transitions[3]
    assert t3.guard == "action(input_text) == $intent.message_text"
    assert t3.guard_admission_status == GuardAdmissionStatus.ADMITTED.value


def test_rejected_result_does_not_overwrite_existing_guard():
    fsm, raw_screens = _build_fsm()
    generate_contract_guards(fsm, raw_screens, guard_source="deterministic")

    t4 = fsm.transitions[4]
    # Delete has no resolvable resource_id -> rejected -> guard untouched.
    assert t4.guard == "preexisting_guard"
    assert t4.guard_admission_status == GuardAdmissionStatus.REJECTED.value


def test_report_includes_status_and_reason():
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(fsm, raw_screens, guard_source="deterministic")

    assert len(report) == len(fsm.transitions)
    for row in report:
        assert "status" in row and "reason" in row
        assert "kind" in row and "required" in row
        assert "semantic_binding_incomplete" not in row
        assert "contract" in row
    assert report[0]["guard"] == "action(target_text) == $intent.contact_name"


def test_report_can_be_written(tmp_path):
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(fsm, raw_screens, guard_source="deterministic")
    out = tmp_path / "sub" / "guard_report.json"
    write_guard_generation_report(report, out)
    assert out.exists()
    assert '"transition_index"' in out.read_text(encoding="utf-8")


def test_serialize_deserialize_preserves_guard_metadata(tmp_path):
    fsm, raw_screens = _build_fsm()
    generate_contract_guards(fsm, raw_screens, guard_source="deterministic")

    path = tmp_path / "fsm.json"
    fsm.serialize(path)
    restored = AppFSM.deserialize(path)

    rt0 = restored.transitions[0]
    assert rt0.guard == "action(target_text) == $intent.contact_name"
    assert rt0.requires_guard is False
    assert rt0.guard_contract is not None
    assert rt0.guard_contract.kind is GuardKind.ITEM_BINDING
    assert rt0.guard_admission_status is GuardAdmissionStatus.ADMITTED

    rt2 = restored.transitions[2]
    assert rt2.guard == f"read({PKG}:id/send, is_enabled) == true"
    assert rt2.guard_admission_status is GuardAdmissionStatus.ADMITTED
    # Contract-level admission metadata survives the round-trip.
    assert rt2.guard_contract is not None
    assert rt2.guard_contract.admission_status is GuardAdmissionStatus.ADMITTED
    assert rt2.guard_contract.admission_reason == "admitted: 1 executable predicate(s)"


# ---------------------------------------------------------------------------
# LLM / hybrid / audit modes (fake structured client)
# ---------------------------------------------------------------------------


class _FakeStructuredLlm:
    """Stand-in exposing the structured interface only (no plain generate())."""

    def __init__(
        self,
        parsed: LlmTransitionGuardResponse | None,
        *,
        schema_constraint_mode: str = "native_schema",
        refusal: str | None = None,
        validation_errors: list[str] | None = None,
    ) -> None:
        self._parsed = parsed
        self._mode = schema_constraint_mode
        self._refusal = refusal
        self._validation_errors = validation_errors or []
        self.calls = 0

    def _result(self, schema_name: str) -> StructuredResult:
        self.calls += 1
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
        return self._result(schema_name)

    # Intentionally NO generate()/generate_with_images(): the structured path must never call them.


def _item_response() -> LlmTransitionGuardResponse:
    return LlmTransitionGuardResponse.model_validate(
        {
            "contract": {
                "kind": "item_binding",
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
    )


def test_default_llm_mode_requires_client():
    fsm, raw_screens = _build_fsm()
    with pytest.raises(ValueError):
        generate_contract_guards(fsm, raw_screens)


def test_llm_mode_attaches_admitted_guard_from_structured_output():
    fsm, raw_screens = _build_fsm()
    llm = _FakeStructuredLlm(_item_response())
    report = generate_contract_guards(fsm, raw_screens, guard_source="llm", llm=llm)

    assert report[0]["guard_origin"] == "llm"
    assert report[0]["guard"] == "action(target_text) == $intent.contact_name"
    assert report[0]["schema_constraint_mode"] == "native_schema"
    assert report[0]["parsed_ok"] is True
    assert fsm.transitions[0].guard == "action(target_text) == $intent.contact_name"
    # requires_guard now agrees with the canonical contract.
    assert fsm.transitions[0].requires_guard is fsm.transitions[0].guard_contract.required


def test_llm_mode_structured_unavailable_is_rejected_not_faked():
    fsm, raw_screens = _build_fsm()
    llm = _FakeStructuredLlm(
        None,
        schema_constraint_mode="prompt_only_unavailable",
        validation_errors=["proxy does not support json_schema"],
    )
    report = generate_contract_guards(fsm, raw_screens, guard_source="llm", llm=llm)

    for row in report:
        # Clear rejection: no admission on a placeholder, no faked ADMITTED, no guard string.
        assert row["status"] == GuardAdmissionStatus.REJECTED.value
        assert row["guard"] is None
        assert row["parsed_ok"] is False
        assert row["schema_constraint_mode"] == "prompt_only_unavailable"
        assert "structured output unavailable" in row["reason"]
    for transition in fsm.transitions:
        if transition.action.get("type") == "click" and transition.target == "s3":
            # The delete edge keeps its pre-existing guard; it is never clobbered.
            continue
        assert transition.guard is None
        assert transition.guard_admission_status == GuardAdmissionStatus.REJECTED.value
    # The pre-existing guard on the delete edge is untouched.
    assert fsm.transitions[4].guard == "preexisting_guard"


def test_hybrid_accepts_complete_llm_contract():
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(
        fsm, raw_screens, guard_source="hybrid", llm=_FakeStructuredLlm(_item_response())
    )
    assert report[0]["guard_origin"] == "llm"
    assert report[0]["guard"] == "action(target_text) == $intent.contact_name"
    assert report[0]["contract"]["kind"] == "item_binding"
    assert fsm.transitions[0].guard == "action(target_text) == $intent.contact_name"


def test_hybrid_falls_back_to_deterministic_on_structured_failure():
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(
        fsm,
        raw_screens,
        guard_source="hybrid",
        llm=_FakeStructuredLlm(None, schema_constraint_mode="prompt_only_unavailable"),
    )
    assert all(row["guard_origin"] == "fallback" for row in report)
    assert all(row["fallback_reason"] for row in report)
    # Deterministic synthesis still attaches the item-binding guard.
    assert report[0]["guard"] == "action(target_text) == $intent.contact_name"


def test_hybrid_prompts_once_per_canonical_action_but_reports_every_edge():
    fsm, raw_screens = _build_fsm()
    fsm.add_transition(
        Transition(
            source="s2",
            target="s3",
            action={"type": "click", "target": "e_send", "target_text": "Send"},
            confidence=0.9,
        )
    )
    llm = _FakeStructuredLlm(_item_response())

    report = generate_contract_guards(fsm, raw_screens, guard_source="hybrid", llm=llm)

    unique_actions = {guard_action_schema_key(t.action) for t in fsm.transitions}
    assert llm.calls == len(unique_actions)
    assert len(report) == len(fsm.transitions)
    assert report[2]["action_schema_index"] == report[-1]["action_schema_index"]


def test_hybrid_writes_llm_audit_for_structured_failure(tmp_path):
    fsm, raw_screens = _build_fsm()
    audit_dir = tmp_path / "llm_guard_attempts"
    report = generate_contract_guards(
        fsm,
        raw_screens,
        guard_source="hybrid",
        llm=_FakeStructuredLlm(
            None,
            schema_constraint_mode="prompt_only_unavailable",
            validation_errors=["unsupported schema"],
        ),
        llm_audit_dir=audit_dir,
    )
    assert audit_dir.exists()
    first_audit = report[0]["llm_audit_path"]
    assert first_audit
    payload = json.loads(Path(first_audit).read_text(encoding="utf-8"))
    assert payload["transition_index"] == 0
    assert payload["parsed_ok"] is False
    assert payload["schema_constraint_mode"] == "prompt_only_unavailable"
    assert payload["validation_errors"] == ["unsupported schema"]
    assert "prompt_hash" in payload
    assert "spec_hash" in payload
    assert "contract" in payload


def test_audit_source_replays_existing_candidate_without_llm(tmp_path):
    fsm, raw_screens = _build_fsm()
    audit_path = tmp_path / "transition_0000_audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "transition_index": 0,
                "parsed_ok": True,
                "schema_constraint_mode": "native_schema",
                "rejection_reason": "",
                "parse_errors": [],
                "contract": {
                    "kind": "item_binding",
                    "required": True,
                    "required_slots": [{"name": "contact_name", "slot_type": "string"}],
                    "predicates": [
                        {
                            "predicate_type": "action",
                            "property": "target_text",
                            "operator": "==",
                            "expected": {"kind": "intent", "slot": "contact_name"},
                        }
                    ],
                },
                "raw_responses": ["{}"],
            }
        ),
        encoding="utf-8",
    )
    audit_report = [
        {
            "transition_index": index,
            "action_schema_index": index,
            "llm_audit_path": str(audit_path),
        }
        for index in range(len(fsm.transitions))
    ]

    report = generate_contract_guards(
        fsm,
        raw_screens,
        guard_source="audit",
        llm_audit_report=audit_report,
    )

    assert report[0]["guard_origin"] == "llm"
    assert report[0]["llm_audit_path"] == str(audit_path)
    assert report[0]["guard"] == "action(target_text) == $intent.contact_name"
    assert fsm.transitions[0].guard == "action(target_text) == $intent.contact_name"


def test_deterministic_and_audit_modes_do_not_call_live_llm(tmp_path):
    fsm, raw_screens = _build_fsm()

    class _ExplodingLlm:
        def generate_structured(self, *a, **k):  # pragma: no cover - must never run
            raise AssertionError("deterministic/audit must not call the LLM")

        generate = generate_structured
        generate_with_images = generate_structured
        generate_structured_with_images = generate_structured

    # Deterministic ignores any provided client entirely.
    generate_contract_guards(fsm, raw_screens, guard_source="deterministic", llm=_ExplodingLlm())

    audit_path = tmp_path / "t0.json"
    audit_path.write_text(json.dumps({"parsed_ok": True, "contract": {"kind": "navigation"}}))
    audit_report = [
        {"transition_index": i, "llm_audit_path": str(audit_path)}
        for i in range(len(fsm.transitions))
    ]
    generate_contract_guards(fsm, raw_screens, guard_source="audit", llm_audit_report=audit_report)


def test_hybrid_graph_is_unchanged_on_fallback():
    fsm, raw_screens = _build_fsm()
    before_states = set(fsm.states)
    before_edges = [(t.source, t.target, t.action.get("type")) for t in fsm.transitions]
    generate_contract_guards(
        fsm,
        raw_screens,
        guard_source="hybrid",
        llm=_FakeStructuredLlm(None, schema_constraint_mode="prompt_only_unavailable"),
    )
    after_edges = [(t.source, t.target, t.action.get("type")) for t in fsm.transitions]
    assert set(fsm.states) == before_states
    assert before_edges == after_edges
