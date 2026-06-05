"""Tests for the contract-first guard generation pipeline (step 4)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.models.guard import GuardAdmissionStatus, GuardKind, RiskLevel
from vigil.neuro.guard_evidence import GuardEvidence
from vigil.neuro.guard_generation_pipeline import (
    _try_llm_contract,
    generate_contract_guards,
    write_guard_generation_report,
)
from vigil.neuro.guard_registry import WidgetRegistry, WidgetRegistryEntry

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
        # High-risk control with NO resource_id -> not runtime-resolvable -> rejected.
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
    # T2: click Send (high risk).
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
    # T4: high-risk Delete with no resolvable resource_id; carries a pre-existing guard.
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
    generate_contract_guards(fsm, raw_screens)

    t0 = fsm.transitions[0]
    assert t0.guard == "action(target_text) == $intent.contact_name"
    assert t0.requires_guard is True
    assert t0.guard_contract is not None
    assert t0.guard_contract.kind is GuardKind.ITEM_BINDING
    assert t0.guard_admission_status == GuardAdmissionStatus.ADMITTED.value


def test_high_risk_transition_attaches_executable_partial_guard():
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(fsm, raw_screens)

    t2 = fsm.transitions[2]
    assert t2.requires_guard is True
    assert t2.risk_level is RiskLevel.HIGH
    # Enabled-only high-risk is executable + evidence-backed -> attached, flagged partial.
    assert t2.guard == f"read({PKG}:id/send, is_enabled) == true"
    assert t2.guard_admission_status == GuardAdmissionStatus.ADMITTED.value
    assert "semantic binding incomplete" in t2.guard_admission_reason
    # Contract metadata is synced too.
    assert t2.guard_contract is not None
    assert t2.guard_contract.admission_status is GuardAdmissionStatus.ADMITTED
    assert "semantic binding incomplete" in t2.guard_contract.admission_reason
    assert report[2]["semantic_binding_incomplete"] is True


def test_input_text_transition_attaches_input_text_guard():
    fsm, raw_screens = _build_fsm()
    generate_contract_guards(fsm, raw_screens)

    t3 = fsm.transitions[3]
    assert t3.guard == "action(input_text) == $intent.message_text"
    assert t3.guard_admission_status == GuardAdmissionStatus.ADMITTED.value


def test_rejected_result_does_not_overwrite_existing_guard():
    fsm, raw_screens = _build_fsm()
    generate_contract_guards(fsm, raw_screens)

    t4 = fsm.transitions[4]
    # High-risk Delete has no resolvable resource_id -> rejected -> guard untouched.
    assert t4.guard == "preexisting_guard"
    assert t4.guard_admission_status == GuardAdmissionStatus.REJECTED.value


def test_report_includes_status_and_reason():
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(fsm, raw_screens)

    assert len(report) == len(fsm.transitions)
    for row in report:
        assert "status" in row and "reason" in row
        assert "kind" in row and "risk" in row and "required" in row
        assert "semantic_binding_incomplete" in row
    assert report[0]["guard"] == "action(target_text) == $intent.contact_name"


def test_report_can_be_written(tmp_path):
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(fsm, raw_screens)
    out = tmp_path / "sub" / "guard_report.json"
    write_guard_generation_report(report, out)
    assert out.exists()
    assert '"transition_index"' in out.read_text(encoding="utf-8")


def test_serialize_deserialize_preserves_guard_metadata(tmp_path):
    fsm, raw_screens = _build_fsm()
    generate_contract_guards(fsm, raw_screens)

    path = tmp_path / "fsm.json"
    fsm.serialize(path)
    restored = AppFSM.deserialize(path)

    rt0 = restored.transitions[0]
    assert rt0.guard == "action(target_text) == $intent.contact_name"
    assert rt0.requires_guard is True
    assert rt0.guard_contract is not None
    assert rt0.guard_contract.kind is GuardKind.ITEM_BINDING
    assert rt0.guard_admission_status is GuardAdmissionStatus.ADMITTED

    rt2 = restored.transitions[2]
    assert rt2.risk_level is RiskLevel.HIGH
    assert rt2.guard == f"read({PKG}:id/send, is_enabled) == true"
    assert rt2.guard_admission_status is GuardAdmissionStatus.ADMITTED
    # Contract-level admission metadata survives the round-trip.
    assert rt2.guard_contract is not None
    assert rt2.guard_contract.admission_status is GuardAdmissionStatus.ADMITTED
    assert "semantic binding incomplete" in rt2.guard_contract.admission_reason


# ---------------------------------------------------------------------------
# LLM / hybrid modes (fake client)
# ---------------------------------------------------------------------------


class _FakeLlm:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return self.response


_VALID_ITEM_CONTRACT = json.dumps(
    {
        "contract": {
            "kind": "item_binding",
            "required": True,
            "risk_level": "medium",
            "required_slots": [{"name": "contact_name", "slot_type": "string"}],
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


def test_llm_mode_requires_client():
    fsm, raw_screens = _build_fsm()
    with pytest.raises(ValueError):
        generate_contract_guards(fsm, raw_screens, guard_source="llm")


def test_hybrid_accepts_complete_llm_contract():
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(
        fsm, raw_screens, guard_source="hybrid", llm=_FakeLlm(_VALID_ITEM_CONTRACT)
    )
    assert report[0]["guard_origin"] == "llm"
    assert report[0]["guard"] == "action(target_text) == $intent.contact_name"
    assert fsm.transitions[0].guard == "action(target_text) == $intent.contact_name"


def test_hybrid_falls_back_to_deterministic_on_invalid_llm():
    fsm, raw_screens = _build_fsm()
    report = generate_contract_guards(
        fsm, raw_screens, guard_source="hybrid", llm=_FakeLlm("not json at all")
    )
    assert all(row["guard_origin"] == "fallback" for row in report)
    assert all(row["fallback_reason"] for row in report)
    # Deterministic synthesis still attaches the item-binding guard.
    assert report[0]["guard"] == "action(target_text) == $intent.contact_name"


def test_hybrid_graph_is_unchanged_on_fallback():
    fsm, raw_screens = _build_fsm()
    before_states = set(fsm.states)
    before_edges = [(t.source, t.target, t.action.get("type")) for t in fsm.transitions]
    generate_contract_guards(fsm, raw_screens, guard_source="hybrid", llm=_FakeLlm("garbage"))
    after_edges = [(t.source, t.target, t.action.get("type")) for t in fsm.transitions]
    assert set(fsm.states) == before_states
    assert before_edges == after_edges


def _binding_evidence() -> GuardEvidence:
    reg = WidgetRegistry(state_id="s1")
    reg.entries["pay"] = WidgetRegistryEntry(alias="pay", resource_id=f"{PKG}:id/pay")
    reg.resource_id_to_alias[f"{PKG}:id/pay"] = "pay"
    return GuardEvidence(
        transition_index=0,
        source_state_id="s1",
        target_state_id="s2",
        action={"type": "click", "target_text": "Pay"},
        source_registry=reg,
    )


def test_try_llm_contract_rejects_incomplete_high_risk():
    # High-risk, required, enabled-only (no intent binding) on a resolvable element:
    # admitted-but-incomplete -> hybrid must reject in favor of deterministic fallback.
    enabled_only = json.dumps(
        {
            "contract": {
                "kind": "confirm_commit",
                "required": True,
                "risk_level": "high",
                "predicates": [
                    {
                        "predicate_type": "read",
                        "element": "pay",
                        "property": "is_enabled",
                        "operator": "==",
                        "expected": {"kind": "literal", "value": True},
                    }
                ],
            }
        }
    )
    contract, reason, result = _try_llm_contract(
        _binding_evidence(), _FakeLlm(enabled_only), "guard_generation.spec"
    )
    assert contract is None
    assert result is None
    assert "incomplete" in reason


def test_try_llm_contract_accepts_complete_binding():
    complete = json.dumps(
        {
            "contract": {
                "kind": "confirm_commit",
                "required": True,
                "risk_level": "high",
                "required_slots": [{"name": "recipient", "slot_type": "string"}],
                "predicates": [
                    {
                        "predicate_type": "read",
                        "element": "pay",
                        "property": "text",
                        "operator": "==",
                        "expected": {"kind": "intent", "slot": "recipient"},
                    }
                ],
            }
        }
    )
    contract, reason, result = _try_llm_contract(
        _binding_evidence(), _FakeLlm(complete), "guard_generation.spec"
    )
    assert contract is not None
    assert reason == ""
    assert result is not None and result.admitted is True
    assert result.semantic_binding_incomplete is False
