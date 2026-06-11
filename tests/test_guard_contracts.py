"""Tests for typed guard-contract models and their attachment to ``Transition``.

These cover the data-model foundation only: constructing contracts, round-tripping
guard metadata on a ``Transition``, backward compatibility with legacy transition
JSON, full ``AppFSM`` serialize/deserialize preservation, and the invariant that
``Transition.guard`` stays an independent executable DSL string.
"""

from __future__ import annotations

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
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


def _make_state(state_id: str, name: str) -> AbstractState:
    """Build a minimal AbstractState (matches conftest/test_models style)."""
    return AbstractState(
        state_id=state_id,
        name=name,
        fingerprint=f"fp_{state_id}",
        hierarchy_level=HierarchyLevel.ACTIVITY,
    )


def _sample_contract() -> GuardContract:
    """A confirm/commit contract with one intent slot and one action predicate."""
    return GuardContract(
        kind=GuardKind.CONFIRM_COMMIT,
        required=True,
        risk_level=RiskLevel.HIGH,
        required_slots=[
            IntentSlot(
                name="recipient",
                slot_type=SlotType.STRING,
                description="Frozen transfer recipient",
            )
        ],
        predicates=[
            PredicateSpec(
                predicate_type="action",
                property="target_text",
                operator="==",
                expected=ValueRef(kind="intent", slot="recipient"),
            )
        ],
        admission_status=GuardAdmissionStatus.ADMITTED,
        admission_reason="recipient slot resolved against source registry",
        confidence=0.9,
        provenance=["trace:step=12"],
    )


def _sample_postcondition() -> TransitionPostcondition:
    """A target/effect-side postcondition candidate for a completed send action."""
    return TransitionPostcondition(
        kind="message_sent",
        required=True,
        risk_level=RiskLevel.HIGH,
        required_slots=[IntentSlot(name="message_text", slot_type=SlotType.STRING)],
        predicates=[
            PredicateSpec(
                predicate_type="contains",
                element="message_list",
                expected=ValueRef(kind="intent", slot="message_text"),
            )
        ],
        effect_requirements=[
            EffectRequirement(
                name="message_visible_after_send",
                effect_kind="appears",
                description="Sent message should appear in the thread.",
            )
        ],
        intent_effect_required=True,
        intent_effect_incomplete=False,
        confidence=0.8,
        provenance=["llm"],
    )


def test_construct_guard_contract_with_slot_and_predicate():
    contract = _sample_contract()

    assert contract.kind is GuardKind.CONFIRM_COMMIT
    assert contract.required is True
    assert contract.risk_level is RiskLevel.HIGH
    assert contract.admission_status is GuardAdmissionStatus.ADMITTED

    assert len(contract.required_slots) == 1
    slot = contract.required_slots[0]
    assert slot.name == "recipient"
    assert slot.slot_type is SlotType.STRING
    assert slot.required is True  # default
    assert slot.value_domain == []  # default factory, not shared mutable

    assert len(contract.predicates) == 1
    pred = contract.predicates[0]
    assert pred.predicate_type == "action"
    assert pred.expected is not None
    assert pred.expected.kind == "intent"
    assert pred.expected.slot == "recipient"


def test_guard_contract_default_factories_are_independent():
    a = GuardContract()
    b = GuardContract()

    a.required_slots.append(IntentSlot(name="x"))
    a.predicates.append(PredicateSpec(predicate_type="read"))
    a.provenance.append("note")

    assert b.required_slots == []
    assert b.predicates == []
    assert b.provenance == []
    assert b.kind is GuardKind.UNKNOWN
    assert b.risk_level is RiskLevel.UNKNOWN
    assert b.admission_status is GuardAdmissionStatus.PENDING


def test_transition_roundtrip_preserves_guard_contract():
    contract = _sample_contract()
    t = Transition(
        source="s1",
        target="s2",
        action={"type": "click", "target": "e_0001"},
        guard='read(confirm_btn, enabled) == "true"',
        postcondition="in_state(s2)",
        confidence=0.95,
        guard_contract=contract,
        postcondition_contract=_sample_postcondition(),
        postcondition_incomplete=False,
        postcondition_admission_status=GuardAdmissionStatus.ADMITTED,
        postcondition_admission_reason="postcondition admitted",
        postcondition_unsupported_effects=["message_visible_after_send: audit-only"],
        requires_guard=True,
        risk_level=RiskLevel.HIGH,
        guard_admission_status=GuardAdmissionStatus.ADMITTED,
        guard_admission_reason="admitted",
    )

    dump = t.model_dump()
    restored = Transition(**dump)

    assert restored.requires_guard is True
    assert restored.risk_level is RiskLevel.HIGH
    assert restored.guard_admission_status is GuardAdmissionStatus.ADMITTED
    assert restored.guard_admission_reason == "admitted"
    assert restored.postcondition == "in_state(s2)"
    assert restored.postcondition_admission_status is GuardAdmissionStatus.ADMITTED
    assert restored.postcondition_admission_reason == "postcondition admitted"
    assert restored.postcondition_unsupported_effects == ["message_visible_after_send: audit-only"]

    assert restored.guard_contract is not None
    assert restored.guard_contract.kind is GuardKind.CONFIRM_COMMIT
    assert restored.guard_contract.required_slots[0].name == "recipient"
    assert restored.guard_contract.predicates[0].expected.slot == "recipient"
    assert restored.postcondition_contract is not None
    assert restored.postcondition_contract.kind == "message_sent"
    assert restored.postcondition_contract.required_slots[0].name == "message_text"
    assert restored.postcondition_incomplete is False


def test_legacy_transition_dict_still_deserializes():
    """A pre-guard-metadata transition dict must build with defaults applied."""
    legacy = {
        "source": "s1",
        "target": "s2",
        "action": {"type": "click", "target": "e_0001"},
        "guard": 'read(wifi_item, text) != ""',
        "confidence": 0.85,
    }

    t = Transition(**legacy)

    assert t.guard == 'read(wifi_item, text) != ""'
    assert t.confidence == 0.85
    # New optional fields fall back to defaults.
    assert t.guard_contract is None
    assert t.postcondition_contract is None
    assert t.postcondition_incomplete is False
    assert t.postcondition is None
    assert t.postcondition_admission_status is None
    assert t.postcondition_admission_reason == ""
    assert t.postcondition_rejected_predicates == []
    assert t.postcondition_unsupported_effects == []
    assert t.requires_guard is False
    assert t.risk_level is RiskLevel.UNKNOWN
    assert t.guard_admission_status is None
    assert t.guard_admission_reason == ""


def test_appfsm_roundtrip_preserves_guard_metadata(tmp_path):
    fsm = AppFSM(app_package="com.test.app")
    fsm.add_state(_make_state("s1", "Home"))
    fsm.add_state(_make_state("s2", "Confirm"))
    fsm.initial_state = "s1"

    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={"type": "click", "target": "e_pay"},
            guard='read(pay_btn, enabled) == "true"',
            postcondition="in_state(s2)",
            confidence=0.9,
            guard_contract=_sample_contract(),
            postcondition_contract=_sample_postcondition(),
            requires_guard=True,
            risk_level=RiskLevel.HIGH,
            guard_admission_status=GuardAdmissionStatus.ADMITTED,
            guard_admission_reason="admitted",
            postcondition_admission_status=GuardAdmissionStatus.ADMITTED,
            postcondition_admission_reason="postcondition admitted",
        )
    )

    path = tmp_path / "fsm.json"
    fsm.serialize(path)
    restored = AppFSM.deserialize(path)

    assert len(restored.transitions) == 1
    rt = restored.transitions[0]
    assert rt.requires_guard is True
    assert rt.risk_level is RiskLevel.HIGH
    assert rt.guard_admission_status is GuardAdmissionStatus.ADMITTED
    assert rt.guard_admission_reason == "admitted"

    assert rt.guard_contract is not None
    assert rt.guard_contract.kind is GuardKind.CONFIRM_COMMIT
    assert rt.guard_contract.admission_status is GuardAdmissionStatus.ADMITTED
    assert rt.guard_contract.required_slots[0].slot_type is SlotType.STRING
    assert rt.guard_contract.predicates[0].expected.kind == "intent"
    assert rt.postcondition == "in_state(s2)"
    assert rt.postcondition_admission_status is GuardAdmissionStatus.ADMITTED
    assert rt.postcondition_admission_reason == "postcondition admitted"
    assert rt.postcondition_contract is not None
    assert rt.postcondition_contract.kind == "message_sent"
    assert rt.postcondition_contract.effect_requirements[0].effect_kind == "appears"


def test_guard_remains_independent_executable_string():
    """guard_contract is metadata; Transition.guard stays the DSL string backend."""
    t = Transition(
        source="s1",
        target="s2",
        action={"type": "click", "target": "e_0001"},
        guard='read(confirm_btn, enabled) == "true"',
        guard_contract=_sample_contract(),
    )

    assert isinstance(t.guard, str)
    assert t.guard == 'read(confirm_btn, enabled) == "true"'
    # The contract does not overwrite or shadow the executable guard string.
    assert t.guard_contract is not None
    assert t.guard != t.guard_contract
