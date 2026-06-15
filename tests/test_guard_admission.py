"""Tests for the executability-gated guard admission validator (step 4)."""

from __future__ import annotations

from vigil.models.guard import (
    EffectRequirement,
    GuardAdmissionStatus,
    GuardContract,
    GuardKind,
    IntentSlot,
    PredicateSpec,
    RiskLevel,
    SlotType,
    TransitionPostcondition,
    ValueRef,
)
from vigil.neuro.guard_admission import admit_guard_contract, admit_postcondition_contract
from vigil.neuro.guard_evidence import GuardEvidence
from vigil.neuro.guard_registry import WidgetRegistry, WidgetRegistryEntry

PKG = "com.test.app"


def _entry(alias: str, *, resource_id: str = "", text: str = "") -> WidgetRegistryEntry:
    return WidgetRegistryEntry(alias=alias, resource_id=resource_id, text=text)


def _registry(*entries: WidgetRegistryEntry) -> WidgetRegistry:
    reg = WidgetRegistry(state_id="s1")
    for e in entries:
        reg.entries[e.alias] = e
        if e.resource_id:
            reg.resource_id_to_alias.setdefault(e.resource_id, e.alias)
    return reg


def _evidence(registry: WidgetRegistry | None = None) -> GuardEvidence:
    return GuardEvidence(
        transition_index=0,
        source_state_id="s1",
        target_state_id="s2",
        action={"type": "click"},
        source_registry=registry or WidgetRegistry(state_id="s1"),
    )


def _contract(**kw) -> GuardContract:
    return GuardContract(**kw)


# ---------------------------------------------------------------------------
# Admit paths
# ---------------------------------------------------------------------------


def test_valid_action_intent_guard_admitted():
    contract = _contract(
        kind=GuardKind.ITEM_BINDING,
        required=True,
        risk_level=RiskLevel.MEDIUM,
        required_slots=[IntentSlot(name="contact_name", slot_type=SlotType.STRING)],
        predicates=[
            PredicateSpec(
                predicate_type="action",
                property="target_text",
                operator="==",
                expected=ValueRef(kind="intent", slot="contact_name"),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence())
    assert result.admitted is True
    assert result.status is GuardAdmissionStatus.ADMITTED
    assert result.guard == "action(target_text) == $intent.contact_name"


def test_element_alias_lowered_to_resource_id():
    reg = _registry(_entry("send", resource_id=f"{PKG}:id/send"))
    contract = _contract(
        kind=GuardKind.TOGGLE_BINDING,
        required=True,
        risk_level=RiskLevel.MEDIUM,
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="send",
                property="is_enabled",
                operator="==",
                expected=ValueRef(kind="literal", value=True),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence(reg))
    assert result.admitted is True
    assert result.guard == f"read({PKG}:id/send, is_enabled) == true"


def test_boolean_string_literal_normalized_for_readable_bool_property():
    reg = _registry(_entry("send", resource_id=f"{PKG}:id/send"))
    for raw, rendered in (("true", "true"), ("false", "false")):
        contract = _contract(
            kind=GuardKind.TOGGLE_BINDING,
            required=True,
            risk_level=RiskLevel.MEDIUM,
            predicates=[
                PredicateSpec(
                    predicate_type="read",
                    element="send",
                    property="is_enabled",
                    operator="==",
                    expected=ValueRef(kind="literal", value=raw),
                )
            ],
        )
        result = admit_guard_contract(contract, _evidence(reg))
        assert result.admitted is True
        assert result.status is GuardAdmissionStatus.ADMITTED
        assert result.guard == f"read({PKG}:id/send, is_enabled) == {rendered}"


def test_non_boolean_string_literal_still_rejected_for_readable_bool_property():
    reg = _registry(_entry("send", resource_id=f"{PKG}:id/send"))
    contract = _contract(
        kind=GuardKind.TOGGLE_BINDING,
        required=True,
        risk_level=RiskLevel.MEDIUM,
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="send",
                property="is_enabled",
                operator="==",
                expected=ValueRef(kind="literal", value="yes"),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence(reg))
    assert result.admitted is False
    assert result.status is GuardAdmissionStatus.REJECTED
    assert "boolean property" in result.reason


def test_element_without_resource_id_rejected():
    reg = _registry(_entry("noid", resource_id=""))
    contract = _contract(
        kind=GuardKind.TOGGLE_BINDING,
        required=True,
        risk_level=RiskLevel.MEDIUM,
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="noid",
                property="is_enabled",
                operator="==",
                expected=ValueRef(kind="literal", value=True),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence(reg))
    assert result.admitted is False
    assert result.status is GuardAdmissionStatus.REJECTED
    assert result.guard is None


def test_missing_element_alias_rejected():
    contract = _contract(
        required=True,
        risk_level=RiskLevel.MEDIUM,
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="ghost",
                property="is_enabled",
                operator="==",
                expected=ValueRef(kind="literal", value=True),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence(_registry()))
    assert result.admitted is False
    assert result.rejected_predicates


def test_undeclared_intent_slot_rejected():
    contract = _contract(
        kind=GuardKind.ITEM_BINDING,
        required=True,
        risk_level=RiskLevel.MEDIUM,
        required_slots=[],  # contact_name not declared
        predicates=[
            PredicateSpec(
                predicate_type="action",
                property="target_text",
                operator="==",
                expected=ValueRef(kind="intent", slot="contact_name"),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence())
    assert result.admitted is False
    assert "undeclared intent slot" in result.reason


def test_literal_read_proven_false_rejected():
    reg = _registry(_entry("title", resource_id=f"{PKG}:id/title", text="Hello"))
    contract = _contract(
        required=True,
        risk_level=RiskLevel.MEDIUM,
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="title",
                property="text",
                operator="==",
                expected=ValueRef(kind="literal", value="Goodbye"),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence(reg))
    assert result.admitted is False
    assert "proven false" in result.reason


def test_action_text_property_rejected():
    # Runtime exposes `input_text`, not `text`, so action(text) is non-executable.
    contract = _contract(
        kind=GuardKind.INPUT_BINDING,
        required=True,
        risk_level=RiskLevel.MEDIUM,
        required_slots=[IntentSlot(name="amount", slot_type=SlotType.NUMBER)],
        predicates=[
            PredicateSpec(
                predicate_type="action",
                property="text",
                operator="==",
                expected=ValueRef(kind="intent", slot="amount"),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence())
    assert result.admitted is False
    assert result.status is GuardAdmissionStatus.REJECTED
    assert "not runtime-resolvable" in result.reason


def test_action_input_text_property_admitted():
    contract = _contract(
        kind=GuardKind.INPUT_BINDING,
        required=True,
        risk_level=RiskLevel.MEDIUM,
        required_slots=[IntentSlot(name="message_text", slot_type=SlotType.STRING)],
        predicates=[
            PredicateSpec(
                predicate_type="action",
                property="input_text",
                operator="==",
                expected=ValueRef(kind="intent", slot="message_text"),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence())
    assert result.admitted is True
    assert result.status is GuardAdmissionStatus.ADMITTED
    assert result.guard == "action(input_text) == $intent.message_text"


def test_action_type_normalized_to_action_type():
    contract = _contract(
        kind=GuardKind.NAVIGATION,
        required=False,
        risk_level=RiskLevel.LOW,
        predicates=[
            PredicateSpec(
                predicate_type="action",
                property="type",
                operator="==",
                expected=ValueRef(kind="literal", value="click"),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence())
    assert result.admitted is True
    assert result.guard == 'action(action_type) == "click"'


def test_risk_level_high_alone_does_not_require_semantic_binding():
    # Risk is report metadata only; semantic completeness is controlled by the
    # explicit semantic_binding_required obligation.
    reg = _registry(_entry("pay", resource_id=f"{PKG}:id/pay"))
    contract = _contract(
        kind=GuardKind.CONFIRM_COMMIT,
        required=True,
        risk_level=RiskLevel.HIGH,
        required_slots=[IntentSlot(name="amount", slot_type=SlotType.NUMBER)],
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="pay",
                property="is_enabled",
                operator="==",
                expected=ValueRef(kind="literal", value=True),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence(reg))
    assert result.admitted is True
    assert result.status is GuardAdmissionStatus.ADMITTED
    assert result.guard == f"read({PKG}:id/pay, is_enabled) == true"
    assert result.semantic_binding_incomplete is False
    assert result.reason == "admitted: 1 executable predicate(s)"


def test_risk_metadata_with_binding_predicate_admitted():
    reg = _registry(_entry("recipient", resource_id=f"{PKG}:id/recipient"))
    contract = _contract(
        kind=GuardKind.CONFIRM_COMMIT,
        required=True,
        risk_level=RiskLevel.HIGH,
        required_slots=[IntentSlot(name="recipient", slot_type=SlotType.STRING)],
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="recipient",
                property="text",
                operator="==",
                expected=ValueRef(kind="intent", slot="recipient"),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence(reg))
    assert result.admitted is True
    assert result.guard == f"read({PKG}:id/recipient, text) == $intent.recipient"
    assert result.semantic_binding_incomplete is False


def test_semantic_binding_required_medium_enabled_only_incomplete():
    # A contract that declares semantic_binding_required is held to the semantic
    # completeness bar: enabled-only -> admitted but semantically incomplete.
    reg = _registry(_entry("checkout", resource_id=f"{PKG}:id/checkout"))
    contract = _contract(
        kind=GuardKind.CONFIRM_COMMIT,
        required=True,
        risk_level=RiskLevel.MEDIUM,
        semantic_binding_required=True,
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="checkout",
                property="is_enabled",
                operator="==",
                expected=ValueRef(kind="literal", value=True),
            )
        ],
    )
    result = admit_guard_contract(contract, _evidence(reg))
    assert result.admitted is True
    assert result.semantic_binding_incomplete is True


def test_required_guard_without_predicate_rejected():
    contract = _contract(
        kind=GuardKind.CONFIRM_COMMIT,
        required=True,
        risk_level=RiskLevel.HIGH,
        required_slots=[IntentSlot(name="amount", slot_type=SlotType.NUMBER)],
        predicates=[],
    )
    result = admit_guard_contract(contract, _evidence())
    assert result.admitted is False
    assert result.status is GuardAdmissionStatus.REJECTED


def test_optional_no_guard_contract_admitted():
    contract = _contract(kind=GuardKind.NAVIGATION, required=False, predicates=[])
    result = admit_guard_contract(contract, _evidence())
    assert result.admitted is True
    assert result.status is GuardAdmissionStatus.ADMITTED
    assert result.guard is None


def test_precondition_rejects_postcondition_only_predicate():
    contract = _contract(
        required=True,
        predicates=[PredicateSpec(predicate_type="appeared", element="target_only")],
    )
    result = admit_guard_contract(contract, _evidence(_registry()))
    assert result.admitted is False
    assert "postcondition-only" in result.reason


def _postcondition_evidence(
    source: WidgetRegistry,
    target: WidgetRegistry,
) -> GuardEvidence:
    return GuardEvidence(
        transition_index=0,
        source_state_id="s1",
        target_state_id="s2",
        action={"type": "click"},
        source_registry=source,
        target_registry=target,
    )


def test_postcondition_keeps_arrival_fact_on_edge():
    postcondition = TransitionPostcondition(
        kind="arrival_state",
        required=False,
        predicates=[
            PredicateSpec(
                predicate_type="in_state",
                expected=ValueRef(kind="literal", value="s2"),
                args={"state": "s2"},
            )
        ],
    )
    result = admit_postcondition_contract(
        postcondition,
        _postcondition_evidence(_registry(), _registry()),
    )
    assert result.admitted is True
    assert result.postcondition == "in_state(s2)"
    assert result.reason == "admitted: 1 executable postcondition predicate(s)"


def test_postcondition_appeared_resolves_target_only_element():
    source = _registry(_entry("feed", resource_id=f"{PKG}:id/feed"))
    target = _registry(_entry("query", resource_id=f"{PKG}:id/search_query"))
    postcondition = TransitionPostcondition(
        kind="content_effect",
        required=True,
        effect_requirements=[
            EffectRequirement(
                name="query_appeared",
                effect_kind="appeared",
                element="query",
            )
        ],
    )
    result = admit_postcondition_contract(postcondition, _postcondition_evidence(source, target))
    assert result.admitted is True
    assert result.postcondition == f"appeared({PKG}:id/search_query)"
    assert result.unsupported_effects == []
    assert postcondition.effect_requirements[0].unsupported_reason == ""


def test_postcondition_disappeared_resolves_source_element():
    source = _registry(_entry("feed", resource_id=f"{PKG}:id/feed"))
    target = _registry(_entry("query", resource_id=f"{PKG}:id/search_query"))
    postcondition = TransitionPostcondition(
        kind="content_effect",
        required=True,
        effect_requirements=[
            EffectRequirement(
                name="feed_disappeared",
                effect_kind="disappeared",
                element="feed",
            )
        ],
    )
    result = admit_postcondition_contract(postcondition, _postcondition_evidence(source, target))
    assert result.admitted is True
    assert result.postcondition == f"disappeared({PKG}:id/feed)"


def test_postcondition_value_changed_requires_stable_element_across_pair():
    source = _registry(_entry("badge", resource_id=f"{PKG}:id/badge"))
    target = _registry(_entry("badge", resource_id=f"{PKG}:id/badge"))
    postcondition = TransitionPostcondition(
        kind="content_effect",
        required=True,
        effect_requirements=[
            EffectRequirement(
                name="badge_changes",
                effect_kind="value_changed",
                element="badge",
                before=ValueRef(kind="literal", value="0"),
                after=ValueRef(kind="literal", value="1"),
            )
        ],
    )
    result = admit_postcondition_contract(postcondition, _postcondition_evidence(source, target))
    assert result.admitted is True
    assert result.postcondition == f'value_changed({PKG}:id/badge, "0", "1")'
