"""Tests for the pure GuardContract/PredicateSpec -> DSL compiler (step 4)."""

from __future__ import annotations

from vigil.models.guard import (
    EffectRequirement,
    GuardContract,
    GuardKind,
    PredicateSpec,
    RiskLevel,
    ValueRef,
)
from vigil.neuro.guard_dsl_compiler import (
    compile_effect_requirement,
    compile_guard_contract,
    compile_predicate_spec,
)


def test_action_intent_predicate_compiles():
    pred = PredicateSpec(
        predicate_type="action",
        property="target_text",
        operator="==",
        expected=ValueRef(kind="intent", slot="contact_name"),
    )
    assert compile_predicate_spec(pred) == "action(target_text) == $intent.contact_name"


def test_action_input_text_predicate_compiles():
    pred = PredicateSpec(
        predicate_type="action",
        property="input_text",
        operator="==",
        expected=ValueRef(kind="intent", slot="message_text"),
    )
    assert compile_predicate_spec(pred) == "action(input_text) == $intent.message_text"


def test_read_enabled_literal_compiles():
    pred = PredicateSpec(
        predicate_type="read",
        element="pay_btn",
        property="is_enabled",
        operator="==",
        expected=ValueRef(kind="literal", value=True),
    )
    assert compile_predicate_spec(pred) == "read(pay_btn, is_enabled) == true"


def test_multiple_predicates_join_with_and():
    contract = GuardContract(
        kind=GuardKind.CONFIRM_COMMIT,
        required=True,
        risk_level=RiskLevel.HIGH,
        predicates=[
            PredicateSpec(
                predicate_type="read",
                element="btn",
                property="is_enabled",
                operator="==",
                expected=ValueRef(kind="literal", value=True),
            ),
            PredicateSpec(
                predicate_type="action",
                property="target_text",
                operator="==",
                expected=ValueRef(kind="intent", slot="recipient"),
            ),
        ],
    )
    assert (
        compile_guard_contract(contract)
        == "read(btn, is_enabled) == true && action(target_text) == $intent.recipient"
    )


def test_literal_rendering_string_bool_number():
    string_pred = PredicateSpec(
        predicate_type="action",
        property="action_type",
        operator="==",
        expected=ValueRef(kind="literal", value="click"),
    )
    assert compile_predicate_spec(string_pred) == 'action(action_type) == "click"'

    num_pred = PredicateSpec(
        predicate_type="count",
        element="list",
        operator=">",
        expected=ValueRef(kind="literal", value=3),
    )
    assert compile_predicate_spec(num_pred) == "count(list) > 3"

    false_pred = PredicateSpec(
        predicate_type="read",
        element="box",
        property="is_checked",
        operator="==",
        expected=ValueRef(kind="literal", value=False),
    )
    assert compile_predicate_spec(false_pred) == "read(box, is_checked) == false"


def test_string_literal_is_json_escaped():
    pred = PredicateSpec(
        predicate_type="read",
        element="label",
        property="text",
        operator="==",
        expected=ValueRef(kind="literal", value='say "hi"'),
    )
    assert compile_predicate_spec(pred) == r'read(label, text) == "say \"hi\""'


def test_unsupported_rhs_valueref_drops_predicate():
    # An action/read value on the RHS cannot be a VALUE token -> None.
    action_rhs = PredicateSpec(
        predicate_type="read",
        element="a",
        property="text",
        operator="==",
        expected=ValueRef(kind="action", property="target_text"),
    )
    assert compile_predicate_spec(action_rhs) is None

    read_rhs = PredicateSpec(
        predicate_type="read",
        element="a",
        property="text",
        operator="==",
        expected=ValueRef(kind="read", element="b", property="text"),
    )
    assert compile_predicate_spec(read_rhs) is None


def test_contains_and_in_state_and_time_in():
    contains = PredicateSpec(
        predicate_type="contains",
        element="picker",
        expected=ValueRef(kind="intent", slot="attachment"),
    )
    assert compile_predicate_spec(contains) == "value(picker) contains $intent.attachment"

    in_state = PredicateSpec(predicate_type="in_state", args={"state": "checkout"})
    assert compile_predicate_spec(in_state) == "in_state(checkout)"

    time_in = PredicateSpec(predicate_type="time_in", args={"start": "09:00", "end": "17:00"})
    assert compile_predicate_spec(time_in) == "time_in(09:00, 17:00)"


def test_optional_contract_without_predicates_compiles_to_none():
    contract = GuardContract(kind=GuardKind.NAVIGATION, required=False)
    assert compile_guard_contract(contract) is None


def test_effect_requirement_is_audit_only_not_dsl():
    assert (
        compile_effect_requirement(
            EffectRequirement(name="query_appeared", effect_kind="appeared", element="search.query")
        )
        is None
    )
    assert (
        compile_effect_requirement(
            EffectRequirement(
                name="feed_disappeared",
                effect_kind="disappeared",
                element="home.feed",
            )
        )
        is None
    )
    assert (
        compile_effect_requirement(
            EffectRequirement(
                name="badge_changes",
                effect_kind="value_changed",
                element="cart.badge_count",
            )
        )
        is None
    )
    assert (
        compile_effect_requirement(
            EffectRequirement(
                name="title_changes",
                effect_kind="value_changed",
                element="top_bar.title",
                before=ValueRef(kind="literal", value="Home"),
                after=ValueRef(kind="literal", value="Cart"),
            )
        )
        is None
    )


def test_missing_parts_return_none():
    assert compile_predicate_spec(PredicateSpec(predicate_type="read", element="a")) is None
    assert compile_predicate_spec(PredicateSpec(predicate_type="action")) is None
