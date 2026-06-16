"""Tests for the strict, LLM-facing structured response models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from vigil.core.structured import schema_hash, to_strict_schema
from vigil.models.guard import GuardKind
from vigil.models.llm_structured import (
    LlmGuardResponse,
    LlmInvariantGuardResponse,
    StrictValueRef,
)


def _assert_strict_safe(model: type) -> None:
    schema = to_strict_schema(model)
    assert "anyOf" not in schema, "top-level anyOf is not strict-safe"
    assert '"additionalProperties": true' not in json.dumps(schema), "open dict present"

    def check(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(schema)


@pytest.mark.parametrize("model", [LlmGuardResponse, LlmInvariantGuardResponse])
def test_strict_schema_is_provider_safe(model: type) -> None:
    _assert_strict_safe(model)
    assert len(schema_hash(model)) == 16


def test_guard_response_round_trips_to_runtime() -> None:
    response = LlmGuardResponse.model_validate(
        {
            "contract": {
                "kind": "confirm_commit",
                "required": True,
                "required_slots": [{"name": "typed_value", "slot_type": "string"}],
                "predicates": [
                    {
                        "predicate_type": "action",
                        "property": "input_text",
                        "operator": "==",
                        "expected": {"kind": "intent", "intent_slot": "typed_value"},
                    }
                ],
            },
            "semantic_binding_incomplete": True,
        }
    )
    candidate = response.to_runtime()
    assert candidate.contract.kind is GuardKind.CONFIRM_COMMIT
    assert candidate.contract.required is True
    assert candidate.contract.required_slots[0].name == "typed_value"
    predicate = candidate.contract.predicates[0]
    assert predicate.expected is not None
    assert predicate.expected.kind == "intent"
    assert predicate.expected.slot == "typed_value"
    # admission fields are deterministic-owned and start unset.
    assert candidate.contract.admission_status.value == "pending"
    assert candidate.semantic_binding_incomplete is True


def test_strict_value_ref_only_allows_literal_and_intent() -> None:
    assert StrictValueRef(kind="literal", literal_value="x").to_runtime().value == "x"
    assert StrictValueRef(kind="intent", intent_slot="amount").to_runtime().slot == "amount"
    with pytest.raises(ValidationError):
        StrictValueRef(kind="read", literal_value="x")


def test_invariant_response_round_trips_to_runtime() -> None:
    response = LlmInvariantGuardResponse.model_validate(
        {
            "state_invariant_candidates": [
                {"expr": 'read(title, text) == "X"', "admission_target": "runtime_state_invariant"}
            ],
            "transition_guard_candidates": [
                {
                    "source_state_id": "s1",
                    "target_state_id": "s2",
                    "canonical_action_key": "k",
                    "contract": {"kind": "navigation"},
                }
            ],
            "effect_invariant_hints": [{"desired_expr": 'read(s, text) == "ok"'}],
            "rejected_candidates": [{"expr_or_summary": "bad", "reason": "volatile"}],
            "notes": "n",
        }
    )
    packet = response.to_runtime()
    assert packet.state_invariant_candidates[0].expr == 'read(title, text) == "X"'
    assert packet.transition_guard_candidates[0].contract.kind is GuardKind.NAVIGATION
    assert packet.effect_invariant_hints[0].desired_expr == 'read(s, text) == "ok"'
    assert packet.rejected_candidates[0].reason == "volatile"
    assert packet.notes == "n"


def test_extra_keys_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        LlmGuardResponse.model_validate({"contract": {}, "precondition": {}})
    with pytest.raises(ValidationError):
        LlmGuardResponse.model_validate({"contract": {"admission_status": "admitted"}})
