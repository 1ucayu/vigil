"""Tests for vigil.symbolic.llm_fallback — LLM fallback on UNCERTAIN results."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
from vigil.models.state import RawScreen, UIElement
from vigil.symbolic.decision_engine import DecisionEngine
from vigil.symbolic.fsm_checker import (
    VerificationOutput,
    VerifyReason,
    VerifyResult,
)
from vigil.symbolic.llm_fallback import LlmFallback


@pytest.fixture
def fallback_fsm() -> AppFSM:
    """FSM with two states and a confidence-low transition."""
    fsm = AppFSM(app_package="com.test.app")
    s1 = AbstractState(
        state_id="s1",
        name="Main",
        fingerprint="fp_main",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        activity_name="com.test.app.Main",
    )
    s2 = AbstractState(
        state_id="s2",
        name="Detail",
        fingerprint="fp_detail",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        activity_name="com.test.app.Main",
    )
    fsm.add_state(s1)
    fsm.add_state(s2)
    fsm.initial_state = "s1"
    fsm.add_transition(
        Transition(
            source="s1",
            target="s2",
            action={"type": "click"},
            guard=None,
            confidence=0.1,  # well below default 0.7 → UNCERTAIN
            observed_count=1,
        )
    )
    return fsm


def _make_llm(responses: list[str]) -> MagicMock:
    """Build a mock LlmClient whose .generate() returns `responses` in order."""
    llm = MagicMock()
    llm.generate.side_effect = responses
    return llm


class TestResolveBasics:
    """Direct tests of LlmFallback.resolve()."""

    def test_passthrough_allow(self, fallback_fsm: AppFSM) -> None:
        """ALLOW results bypass the LLM entirely."""
        llm = _make_llm([])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.ALLOW,
            reason=VerifyReason.TRANSITION_VALID,
            current_state_id="s1",
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved is out
        llm.generate.assert_not_called()

    def test_passthrough_deny(self, fallback_fsm: AppFSM) -> None:
        """DENY results bypass the LLM entirely."""
        llm = _make_llm([])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.DENY,
            reason=VerifyReason.TRANSITION_INVALID,
            current_state_id="s1",
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved is out
        llm.generate.assert_not_called()

    def test_uncertain_to_allow(self, fallback_fsm: AppFSM) -> None:
        """UNCERTAIN + parseable LLM ALLOW → VerifyResult.ALLOW with LLM_FALLBACK reason."""
        llm = _make_llm(['{"decision": "ALLOW", "reason": "routine click"}'])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.LOW_CONFIDENCE,
            current_state_id="s1",
            target_state_id="s2",
            confidence=0.1,
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved.result == VerifyResult.ALLOW
        assert resolved.reason == VerifyReason.LLM_FALLBACK
        assert resolved.current_state_id == "s1"
        assert resolved.target_state_id == "s2"
        assert "routine click" in resolved.details
        assert "low_confidence" in resolved.details  # original uncertainty preserved
        llm.generate.assert_called_once()

    def test_uncertain_to_deny(self, fallback_fsm: AppFSM) -> None:
        """UNCERTAIN + parseable LLM DENY → VerifyResult.DENY with LLM_FALLBACK reason."""
        llm = _make_llm(['{"decision": "DENY", "reason": "unknown screen, risky"}'])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.STATE_UNKNOWN,
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved.result == VerifyResult.DENY
        assert resolved.reason == VerifyReason.LLM_FALLBACK
        assert "unknown screen" in resolved.details

    def test_markdown_fenced_response(self, fallback_fsm: AppFSM) -> None:
        """Response wrapped in ```json fences is still parsed."""
        llm = _make_llm(['```json\n{"decision": "ALLOW", "reason": "ok"}\n```'])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.LOW_CONFIDENCE,
            current_state_id="s1",
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved.result == VerifyResult.ALLOW

    def test_retries_once_on_parse_failure(self, fallback_fsm: AppFSM) -> None:
        """First garbage response → retry → success."""
        llm = _make_llm(["not json at all", '{"decision": "DENY", "reason": "retry worked"}'])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.LOW_CONFIDENCE,
            current_state_id="s1",
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved.result == VerifyResult.DENY
        assert llm.generate.call_count == 2

    def test_unparseable_preserves_uncertain(self, fallback_fsm: AppFSM) -> None:
        """Two unparseable responses → keep the original UNCERTAIN result."""
        llm = _make_llm(["garbage 1", "garbage 2"])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.LOW_CONFIDENCE,
            current_state_id="s1",
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved.result == VerifyResult.UNCERTAIN
        assert resolved.reason == VerifyReason.LOW_CONFIDENCE
        assert resolved is out  # identity preserved
        assert llm.generate.call_count == 2

    def test_unknown_decision_value_preserves_uncertain(self, fallback_fsm: AppFSM) -> None:
        """Parseable JSON but decision value isn't ALLOW/DENY → preserve UNCERTAIN."""
        llm = _make_llm(
            [
                '{"decision": "MAYBE", "reason": "unsure"}',
                '{"decision": "???", "reason": "still unsure"}',
            ]
        )
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.STATE_UNKNOWN,
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved.result == VerifyResult.UNCERTAIN
        assert resolved is out

    def test_llm_exception_preserves_uncertain(self, fallback_fsm: AppFSM) -> None:
        """LLM raises → preserve UNCERTAIN rather than crash."""
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("network down")
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.LOW_CONFIDENCE,
            current_state_id="s1",
        )
        resolved = fallback.resolve(out, None, {"type": "click"})
        assert resolved.result == VerifyResult.UNCERTAIN
        assert resolved is out


class TestPromptBuilding:
    """The LLM prompt should carry enough context for a real decision."""

    def test_prompt_includes_state_and_action(self, fallback_fsm: AppFSM) -> None:
        llm = _make_llm(['{"decision": "ALLOW", "reason": "ok"}'])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.LOW_CONFIDENCE,
            current_state_id="s1",
            confidence=0.1,
            details="confidence 0.10 below threshold 0.70",
        )
        fallback.resolve(out, None, {"type": "click", "target": "e_0001"})
        (_, user_msg) = llm.generate.call_args[0]
        assert "Main" in user_msg
        assert "s1" in user_msg
        assert "click" in user_msg
        assert "low_confidence" in user_msg
        # Candidate transitions are enumerated when state is known
        assert "click → Detail" in user_msg

    def test_prompt_includes_raw_instruction(self, fallback_fsm: AppFSM) -> None:
        llm = _make_llm(['{"decision": "ALLOW", "reason": "ok"}'])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.LOW_CONFIDENCE,
            current_state_id="s1",
        )
        fallback.resolve(
            out,
            None,
            {"type": "click"},
            raw_instruction="Open the detail page",
        )
        (_, user_msg) = llm.generate.call_args[0]
        assert "Open the detail page" in user_msg

    def test_prompt_includes_screen_elements(self, fallback_fsm: AppFSM) -> None:
        llm = _make_llm(['{"decision": "ALLOW", "reason": "ok"}'])
        fallback = LlmFallback(llm, fallback_fsm)
        screen = RawScreen(
            screen_id="scr_1",
            elements=[
                UIElement(
                    element_id="e_0001",
                    class_name="android.widget.TextView",
                    text="Open detail",
                    is_clickable=True,
                    is_enabled=True,
                ),
                UIElement(
                    element_id="e_0002",
                    class_name="android.widget.TextView",
                    text="Not interactable",
                    is_enabled=True,
                ),
            ],
        )
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.LOW_CONFIDENCE,
            current_state_id="s1",
        )
        fallback.resolve(out, screen, {"type": "click", "target": "e_0001"})
        (_, user_msg) = llm.generate.call_args[0]
        # Interactable element appears
        assert "Open detail" in user_msg
        # Target text is surfaced in the action line
        assert 'text="Open detail"' in user_msg
        # Non-interactable text is NOT listed in the elements block
        assert "Not interactable" not in user_msg

    def test_prompt_unknown_state(self, fallback_fsm: AppFSM) -> None:
        llm = _make_llm(['{"decision": "DENY", "reason": "unknown state"}'])
        fallback = LlmFallback(llm, fallback_fsm)
        out = VerificationOutput(
            result=VerifyResult.UNCERTAIN,
            reason=VerifyReason.STATE_UNKNOWN,
        )
        fallback.resolve(out, None, {"type": "click"})
        (_, user_msg) = llm.generate.call_args[0]
        assert "UNKNOWN" in user_msg
        # No candidate transitions block when state is unknown
        assert "Candidate transitions" not in user_msg


class TestDecisionEngineIntegration:
    """End-to-end: DecisionEngine routes UNCERTAIN through the fallback."""

    def test_fallback_called_on_low_confidence(self, fallback_fsm: AppFSM) -> None:
        """verify_by_state with low-confidence transition → fallback invoked."""
        llm = _make_llm(['{"decision": "ALLOW", "reason": "safe tap"}'])
        fallback = LlmFallback(llm, fallback_fsm)
        engine = DecisionEngine(fallback_fsm, llm_fallback=fallback)
        out = engine.verify_by_state("s1", {"type": "click"})
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.LLM_FALLBACK
        llm.generate.assert_called_once()

    def test_no_fallback_preserves_uncertain(self, fallback_fsm: AppFSM) -> None:
        """DecisionEngine without fallback keeps UNCERTAIN as before."""
        engine = DecisionEngine(fallback_fsm)
        out = engine.verify_by_state("s1", {"type": "click"})
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.LOW_CONFIDENCE

    def test_fallback_not_called_on_deny(self, fallback_fsm: AppFSM) -> None:
        """Structural DENY skips the fallback entirely."""
        llm = _make_llm([])
        fallback = LlmFallback(llm, fallback_fsm)
        engine = DecisionEngine(fallback_fsm, llm_fallback=fallback)
        out = engine.verify_by_state("s1", {"type": "scroll_up"})  # not a valid transition
        assert out.result == VerifyResult.DENY
        llm.generate.assert_not_called()

    def test_fallback_not_called_on_allow(self, fallback_fsm: AppFSM) -> None:
        """ALLOW from Tier 1 (confidence 0) after we drop threshold skips fallback."""
        from vigil.core.config import VerificationConfig

        llm = _make_llm([])
        fallback = LlmFallback(llm, fallback_fsm)
        engine = DecisionEngine(
            fallback_fsm,
            config=VerificationConfig(confidence_threshold=0.0),
            llm_fallback=fallback,
        )
        out = engine.verify_by_state("s1", {"type": "click"})
        assert out.result == VerifyResult.ALLOW
        llm.generate.assert_not_called()
