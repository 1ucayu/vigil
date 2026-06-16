"""Tests for the deterministic rule-based GuardContract synthesizer (step 3).

These cover the classification rules over ``GuardEvidence``: navigation/scroll/cancel,
side-effect commits (with and without executable evidence), input binding, toggle binding,
item binding (and its exclusions), unknown fallback, generic domain inference, and the
no-invented-aliases / confidence invariants. No LLM, DSL compilation, or admission logic.
"""

from __future__ import annotations

from typing import Any

from vigil.models.guard import (
    GuardAdmissionStatus,
    GuardKind,
    SlotType,
)
from vigil.neuro.guard_contract_synthesizer import (
    synthesize_all_guard_contracts,
    synthesize_guard_contract,
)
from vigil.neuro.guard_evidence import GuardEvidence
from vigil.neuro.guard_registry import (
    WidgetRegistry,
    WidgetRegistryEntry,
    WidgetRole,
)


def _entry(
    alias: str,
    *,
    element_id: str = "",
    resource_id: str = "",
    text: str = "",
    role: WidgetRole = WidgetRole.UNKNOWN,
) -> WidgetRegistryEntry:
    return WidgetRegistryEntry(
        alias=alias,
        element_id=element_id,
        resource_id=resource_id,
        text=text,
        role=role,
    )


def _registry(*entries: WidgetRegistryEntry, state_id: str = "s1") -> WidgetRegistry:
    reg = WidgetRegistry(state_id=state_id)
    for e in entries:
        reg.entries[e.alias] = e
        if e.element_id:
            reg.element_id_to_alias[e.element_id] = e.alias
        if e.resource_id:
            reg.resource_id_to_alias.setdefault(e.resource_id, e.alias)
    return reg


def _evidence(
    action: dict[str, Any],
    *,
    source_registry: WidgetRegistry | None = None,
    action_target_alias: str | None = None,
    siblings: list[dict[str, Any]] | None = None,
    source_page_function: str = "",
    target_page_function: str = "",
    source_state_id: str = "s1",
    target_state_id: str = "s2",
) -> GuardEvidence:
    return GuardEvidence(
        transition_index=0,
        source_state_id=source_state_id,
        target_state_id=target_state_id,
        action=action,
        source_page_function=source_page_function,
        target_page_function=target_page_function,
        source_registry=source_registry or WidgetRegistry(state_id=source_state_id),
        sibling_actions=siblings or [],
        action_target_alias=action_target_alias,
    )


# ---------------------------------------------------------------------------
# Navigation / scroll / cancel
# ---------------------------------------------------------------------------


def test_navigate_back_is_navigation():
    contract = synthesize_guard_contract(_evidence({"type": "navigate_back"}))
    assert contract.kind is GuardKind.NAVIGATION
    assert contract.required is False


def test_scroll_is_none_guard():
    contract = synthesize_guard_contract(_evidence({"type": "scroll_down"}))
    assert contract.kind is GuardKind.NONE
    assert contract.required is False


def test_cancel_button_is_navigation():
    contract = synthesize_guard_contract(_evidence({"type": "click", "target_text": "Cancel"}))
    assert contract.required is False
    assert contract.kind is GuardKind.NAVIGATION


def test_side_effect_word_is_not_downgraded_to_navigation():
    # A "Delete" control is classified as a safety-check candidate, not navigation.
    contract = synthesize_guard_contract(_evidence({"type": "click", "target_text": "Delete"}))
    assert contract.required is False
    assert contract.kind is GuardKind.SAFETY_CHECK


# ---------------------------------------------------------------------------
# Side-effect commits
# ---------------------------------------------------------------------------


def test_send_with_resolved_alias_has_enabled_predicate_strong_conf():
    reg = _registry(_entry("send", element_id="e_send", text="Send"))
    ev = _evidence(
        {"type": "click", "target": "e_send", "target_text": "Send"},
        source_registry=reg,
        action_target_alias="send",
        source_page_function="messaging/thread",
    )
    contract = synthesize_guard_contract(ev)

    assert contract.kind is GuardKind.CONFIRM_COMMIT
    assert contract.required is False
    assert contract.confidence == 0.8
    # Enabled predicate references the resolved alias.
    reads = [p for p in contract.predicates if p.predicate_type == "read"]
    assert len(reads) == 1
    assert reads[0].element == "send"
    assert reads[0].property == "is_enabled"
    # chat domain commit slots.
    slot_names = {s.name for s in contract.required_slots}
    assert slot_names == {"contact_name", "message_text"}


def test_side_effect_without_resolved_alias_is_weak_and_invents_no_predicate():
    # No registry entry, no resolved alias -> only intent slots, weak confidence.
    ev = _evidence(
        {"type": "click", "target_text": "Send"},
        source_page_function="messaging/thread",
    )
    contract = synthesize_guard_contract(ev)

    assert contract.required is False
    assert contract.confidence == 0.5
    assert contract.admission_status is GuardAdmissionStatus.PENDING
    assert contract.notes  # explains the missing binding
    # No read/value predicate referencing a fabricated element.
    assert all(p.predicate_type not in ("read", "value") for p in contract.predicates)


def test_delete_is_safety_check():
    reg = _registry(_entry("del", text="Delete"))
    ev = _evidence(
        {"type": "click", "target_text": "Delete"},
        source_registry=reg,
        action_target_alias="del",
    )
    contract = synthesize_guard_contract(ev)
    assert contract.kind is GuardKind.SAFETY_CHECK
    assert contract.required is False


def test_bank_transfer_has_amount_and_recipient_slots():
    reg = _registry(_entry("transfer", text="Transfer"))
    ev = _evidence(
        {"type": "click", "target_text": "Transfer"},
        source_registry=reg,
        action_target_alias="transfer",
        source_page_function="bank/transfer/confirm",
    )
    contract = synthesize_guard_contract(ev)
    assert contract.kind is GuardKind.CONFIRM_COMMIT
    assert contract.required is False
    slot_names = {s.name for s in contract.required_slots}
    assert slot_names == {"amount", "recipient"}
    # amount is numeric.
    amount = next(s for s in contract.required_slots if s.name == "amount")
    assert amount.slot_type is SlotType.NUMBER


# ---------------------------------------------------------------------------
# Input binding
# ---------------------------------------------------------------------------


def test_input_text_amount_binds_action_text_property():
    reg = _registry(_entry("amount_input", role=WidgetRole.TEXT_FIELD, text=""))
    ev = _evidence(
        {"type": "input_text", "target_text": "", "text": "100"},
        source_registry=reg,
        action_target_alias="amount_input",
        source_page_function="bank/transfer/form",
    )
    contract = synthesize_guard_contract(ev)

    assert contract.kind is GuardKind.INPUT_BINDING
    assert contract.required is False
    assert len(contract.predicates) == 1
    pred = contract.predicates[0]
    assert pred.predicate_type == "action"
    # The typed value is exposed on the runtime action context as `input_text`.
    assert pred.property == "input_text"
    assert pred.expected is not None
    assert pred.expected.kind == "intent"
    assert pred.expected.slot == "amount"
    assert {s.name for s in contract.required_slots} == {"amount"}


def test_input_text_chat_message_slot():
    ev = _evidence(
        {"type": "input_text", "text": "hi"},
        action_target_alias="message_box",
        source_page_function="messaging/thread",
    )
    contract = synthesize_guard_contract(ev)
    assert contract.kind is GuardKind.INPUT_BINDING
    assert {s.name for s in contract.required_slots} == {"message_text"}


def test_input_text_classified_before_side_effect_word_in_resource_id():
    # An amount field whose resource id contains "transfer" must still bind the typed
    # value to an intent slot, NOT collapse to an enabled-only commit guard.
    reg = _registry(
        _entry(
            "amount_input",
            resource_id="x:id/transfer_amount",
            role=WidgetRole.TEXT_FIELD,
        )
    )
    ev = _evidence(
        {"type": "input_text", "text": "100", "target_text": ""},
        source_registry=reg,
        action_target_alias="amount_input",
        source_page_function="bank/transfer/form",
    )
    contract = synthesize_guard_contract(ev)

    assert contract.kind is GuardKind.INPUT_BINDING
    assert len(contract.predicates) == 1
    pred = contract.predicates[0]
    assert pred.predicate_type == "action"
    assert pred.property == "input_text"
    assert pred.expected is not None and pred.expected.slot == "amount"
    # No enabled-only read predicate sneaks in.
    assert all(p.predicate_type != "read" for p in contract.predicates)


# ---------------------------------------------------------------------------
# Commit classification remains policy-free
# ---------------------------------------------------------------------------


def test_side_effect_commit_does_not_mark_semantic_binding_required():
    reg = _registry(_entry("send", text="Send"))
    ev = _evidence(
        {"type": "click", "target_text": "Send"},
        source_registry=reg,
        action_target_alias="send",
        source_page_function="messaging/thread",
    )
    contract = synthesize_guard_contract(ev)
    assert contract.semantic_binding_required is False
    assert contract.semantic_binding_incomplete is False


def test_checkout_click_is_commit_candidate_without_required_binding():
    reg = _registry(_entry("checkout_btn", resource_id="x:id/checkout", text="Checkout"))
    ev = _evidence(
        {"type": "click", "target_text": "Checkout"},
        source_registry=reg,
        action_target_alias="checkout_btn",
        source_page_function="commerce/cart",
    )
    contract = synthesize_guard_contract(ev)
    assert contract.kind is GuardKind.CONFIRM_COMMIT
    assert contract.required is False
    assert contract.semantic_binding_required is False
    assert contract.semantic_binding_incomplete is False


def test_stopwatch_lap_is_commit_candidate_without_required_binding():
    reg = _registry(_entry("lap_btn", resource_id="x:id/lap", text="Lap"))
    ev = _evidence(
        {"type": "click", "target_text": "Lap"},
        source_registry=reg,
        action_target_alias="lap_btn",
        source_page_function="clock/stopwatch",
    )
    contract = synthesize_guard_contract(ev)
    assert contract.kind is GuardKind.CONFIRM_COMMIT
    assert contract.required is False
    assert contract.semantic_binding_required is False
    assert contract.semantic_binding_incomplete is False


def test_plain_navigation_is_not_a_commit():
    contract = synthesize_guard_contract(
        _evidence(
            {"type": "click", "target_text": "Settings"},
            source_state_id="s1",
            target_state_id="s2",
        )
    )
    assert contract.kind is GuardKind.NAVIGATION
    assert contract.semantic_binding_required is False


# ---------------------------------------------------------------------------
# Toggle binding
# ---------------------------------------------------------------------------


def test_toggle_role_yields_toggle_binding_with_enabled_predicate():
    reg = _registry(_entry("wifi", role=WidgetRole.TOGGLE, resource_id="x:id/wifi"))
    ev = _evidence(
        {"type": "click", "target_text": ""},
        source_registry=reg,
        action_target_alias="wifi",
    )
    contract = synthesize_guard_contract(ev)

    assert contract.kind is GuardKind.TOGGLE_BINDING
    assert contract.required is False
    assert {s.name for s in contract.required_slots} == {"desired_state"}
    desired = contract.required_slots[0]
    assert desired.slot_type is SlotType.BOOLEAN
    reads = [p for p in contract.predicates if p.predicate_type == "read"]
    assert len(reads) == 1
    assert reads[0].element == "wifi"


# ---------------------------------------------------------------------------
# Item binding and its exclusions
# ---------------------------------------------------------------------------


def test_item_binding_fires_with_similar_dynamic_sibling_rows():
    reg = _registry(_entry("row_alice", element_id="e_a", text="Alice"))
    ev = _evidence(
        {"type": "click", "target": "e_a", "target_text": "Alice"},
        source_registry=reg,
        action_target_alias="row_alice",
        siblings=[
            {"type": "click", "target_text": "Bob"},
            {"type": "click", "target_text": "Carol"},
        ],
        source_page_function="messaging/contacts",
    )
    contract = synthesize_guard_contract(ev)

    assert contract.kind is GuardKind.ITEM_BINDING
    assert contract.required is False
    assert len(contract.predicates) == 1
    pred = contract.predicates[0]
    assert pred.predicate_type == "action"
    assert pred.property == "target_text"
    assert pred.expected is not None and pred.expected.slot == "contact_name"


def test_command_button_siblings_do_not_trigger_item_binding():
    ev = _evidence(
        {"type": "click", "target_text": "Details"},
        siblings=[
            {"type": "click", "target_text": "Save"},
            {"type": "click", "target_text": "Cancel"},
        ],
        source_state_id="s1",
        target_state_id="s2",
    )
    contract = synthesize_guard_contract(ev)
    assert contract.kind is not GuardKind.ITEM_BINDING


def test_no_dynamic_text_aliases_are_invented():
    # Side-effect action, empty registry: no predicate may reference an element alias.
    ev = _evidence(
        {"type": "click", "target_text": "Pay"},
        source_page_function="commerce/checkout",
    )
    contract = synthesize_guard_contract(ev)
    for pred in contract.predicates:
        assert pred.element is None or pred.element in ev.source_registry.entries


# ---------------------------------------------------------------------------
# Navigation vs unknown
# ---------------------------------------------------------------------------


def test_static_nav_text_state_change_is_navigation():
    contract = synthesize_guard_contract(
        _evidence(
            {"type": "click", "target_text": "Settings"},
            source_state_id="s1",
            target_state_id="s2",
        )
    )
    assert contract.kind is GuardKind.NAVIGATION
    assert contract.required is False


def test_dynamic_click_without_item_evidence_is_unknown():
    contract = synthesize_guard_contract(
        _evidence(
            {"type": "click", "target_text": "Quux Widget"},
            source_state_id="s1",
            target_state_id="s2",
        )
    )
    assert contract.kind is GuardKind.UNKNOWN
    assert contract.required is False


# ---------------------------------------------------------------------------
# Generic domain inference + invariants
# ---------------------------------------------------------------------------


def test_domain_inference_is_generic_token_based():
    # A generic "messaging" token (no benchmark/package/fixture string) yields chat slots.
    reg = _registry(_entry("send", text="Send"))
    ev = _evidence(
        {"type": "click", "target_text": "Send"},
        source_registry=reg,
        action_target_alias="send",
        source_page_function="messaging/conversation",
    )
    contract = synthesize_guard_contract(ev)
    assert {s.name for s in contract.required_slots} == {"contact_name", "message_text"}


def test_confidence_and_provenance_populated():
    contract = synthesize_guard_contract(_evidence({"type": "navigate_back"}))
    assert 0.0 < contract.confidence <= 1.0
    assert contract.provenance
    assert contract.admission_status is GuardAdmissionStatus.PENDING


def test_synthesize_all_is_order_preserving():
    evs = [
        _evidence({"type": "navigate_back"}),
        _evidence({"type": "scroll_down"}),
    ]
    contracts = synthesize_all_guard_contracts(evs)
    assert len(contracts) == 2
    assert contracts[0].kind is GuardKind.NAVIGATION
    assert contracts[1].kind is GuardKind.NONE
