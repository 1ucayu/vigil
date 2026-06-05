"""Regression tests for runtime guard-admission policy enforcement."""

from __future__ import annotations

from unittest.mock import MagicMock

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.models.guard import GuardAdmissionStatus, RiskLevel
from vigil.symbolic.decision_engine import DecisionEngine
from vigil.symbolic.dsl_evaluator import ScreenContext
from vigil.symbolic.fsm_checker import VerifyReason, VerifyResult
from vigil.symbolic.llm_fallback import LlmFallback

_ACTION = {"type": "click", "target": "pay_button", "target_text": "Pay"}


def _make_transition(**overrides: object) -> Transition:
    params: dict[str, object] = {
        "source": "s1",
        "target": "s2",
        "action": dict(_ACTION),
        "confidence": 0.95,
        "observed_count": 5,
    }
    params.update(overrides)
    return Transition(**params)


def _make_fsm(transition: Transition) -> AppFSM:
    fsm = AppFSM(app_package="com.test.guardpolicy")
    fsm.add_state(
        AbstractState(
            state_id="s1",
            name="Source",
            fingerprint="fp_source",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
    )
    fsm.add_state(
        AbstractState(
            state_id="s2",
            name="Target",
            fingerprint="fp_target",
            hierarchy_level=HierarchyLevel.ACTIVITY,
        )
    )
    fsm.initial_state = "s1"
    fsm.add_transition(transition)
    return fsm


def _verify(transition: Transition, **kwargs: object):
    return DecisionEngine(_make_fsm(transition)).verify_by_state("s1", dict(_ACTION), **kwargs)


def test_high_risk_without_executable_guard_is_uncertain() -> None:
    transition = _make_transition(
        guard=None,
        risk_level=RiskLevel.HIGH,
        guard_admission_status=GuardAdmissionStatus.ADMITTED,
    )
    transition.risk_level = "HIGH"

    out = _verify(transition)

    assert out.result is VerifyResult.UNCERTAIN
    assert out.reason is VerifyReason.GUARD_POLICY_UNSATISFIED


def test_rejected_guard_admission_is_uncertain_even_with_guard() -> None:
    transition = _make_transition(
        guard='action(target_text) == "Pay"',
        risk_level=RiskLevel.HIGH,
        guard_admission_status=GuardAdmissionStatus.REJECTED,
    )
    transition.guard_admission_status = "REJECTED"

    out = _verify(transition, action_ctx={"target_text": "Pay"})

    assert out.result is VerifyResult.UNCERTAIN
    assert out.reason is VerifyReason.GUARD_POLICY_UNSATISFIED


def test_requires_guard_without_executable_guard_is_uncertain() -> None:
    transition = _make_transition(
        guard=None,
        requires_guard=True,
        risk_level=RiskLevel.LOW,
        guard_admission_status=GuardAdmissionStatus.ADMITTED,
    )

    out = _verify(transition)

    assert out.result is VerifyResult.UNCERTAIN
    assert out.reason is VerifyReason.GUARD_POLICY_UNSATISFIED


def test_low_risk_without_guard_still_allows() -> None:
    transition = _make_transition(
        guard=None,
        requires_guard=False,
        risk_level=RiskLevel.LOW,
    )

    out = _verify(transition)

    assert out.result is VerifyResult.ALLOW
    assert out.reason is VerifyReason.TRANSITION_VALID


def test_high_risk_admitted_guard_true_allows() -> None:
    transition = _make_transition(
        guard='action(target_text) == "Pay"',
        risk_level=RiskLevel.HIGH,
        guard_admission_status=GuardAdmissionStatus.ADMITTED,
    )

    out = _verify(transition, action_ctx={"target_text": "Pay"})

    assert out.result is VerifyResult.ALLOW
    assert out.reason is VerifyReason.TRANSITION_VALID


def test_high_risk_admitted_guard_requires_evaluator() -> None:
    transition = _make_transition(
        guard='action(target_text) == "Pay"',
        risk_level=RiskLevel.HIGH,
        guard_admission_status=GuardAdmissionStatus.ADMITTED,
    )
    fsm = _make_fsm(transition)
    engine = DecisionEngine(fsm, grammar_path="/nonexistent/grammar.lark")

    out = engine.verify_by_state("s1", dict(_ACTION), action_ctx={"target_text": "Pay"})

    assert out.result is VerifyResult.UNCERTAIN
    assert out.reason is VerifyReason.GUARD_POLICY_UNSATISFIED


def test_guard_policy_uncertain_bypasses_llm_fallback() -> None:
    transition = _make_transition(
        guard=None,
        risk_level=RiskLevel.HIGH,
        guard_admission_status=GuardAdmissionStatus.ADMITTED,
    )
    fsm = _make_fsm(transition)
    llm = MagicMock()
    llm.generate.return_value = '{"decision": "ALLOW", "reason": "ok"}'
    engine = DecisionEngine(fsm, llm_fallback=LlmFallback(llm, fsm))

    out = engine.verify_by_state("s1", dict(_ACTION))

    assert out.result is VerifyResult.UNCERTAIN
    assert out.reason is VerifyReason.GUARD_POLICY_UNSATISFIED
    llm.generate.assert_not_called()


def test_high_risk_executable_enabled_only_guard_evaluates_normally() -> None:
    # An executable, evidence-backed enabled-only guard on a high-risk transition must be
    # evaluated normally (not auto-UNCERTAIN by guard policy).
    transition = _make_transition(
        guard="read(com.test:id/pay, is_enabled) == true",
        risk_level=RiskLevel.HIGH,
        guard_admission_status=GuardAdmissionStatus.ADMITTED,
    )
    engine = DecisionEngine(_make_fsm(transition))
    screen_ctx = ScreenContext(elements={"com.test:id/pay": {"is_enabled": True}})

    out = engine.verify_by_state("s1", dict(_ACTION), screen_ctx=screen_ctx)

    assert out.result is VerifyResult.ALLOW
    assert out.reason is VerifyReason.TRANSITION_VALID
