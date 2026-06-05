"""Tier 1: FSM Structural Verification.

Pure symbolic checks against the FSM graph:
- Transition validity: is the proposed action legal from the current state?
- Reachability: can we still reach the goal state? O(V+E)
- Confidence check: is this transition well-tested?

Returns ALLOW / DENY / UNCERTAIN with zero LLM calls.

Guard-admission *policy* is intentionally NOT enforced here — it lives in
:mod:`vigil.symbolic.guard_policy` and is applied by
:class:`~vigil.symbolic.decision_engine.DecisionEngine`. This keeps Tier 1 a pure
structural layer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from vigil.core.config import VerificationConfig
from vigil.models.fsm import AppFSM, TransitionLookupStatus
from vigil.models.state import RawScreen
from vigil.symbolic.state_locator import LocateResult, StateLocator


class VerifyResult(StrEnum):
    """Verification decision."""

    ALLOW = "allow"
    DENY = "deny"
    UNCERTAIN = "uncertain"


class VerifyReason(StrEnum):
    """Reason for the verification decision."""

    TRANSITION_VALID = "transition_valid"
    TRANSITION_INVALID = "transition_not_in_fsm"
    ACTION_AMBIGUOUS = "action_ambiguous"
    STATE_UNKNOWN = "state_unknown"
    GOAL_UNREACHABLE = "goal_unreachable"
    LOW_CONFIDENCE = "low_confidence"
    STATE_SIMILAR = "state_similar_fuzzy_match"
    GUARD_FAILED = "guard_failed"
    GUARD_INCONCLUSIVE = "guard_inconclusive"
    GUARD_POLICY_UNSATISFIED = "guard_policy_unsatisfied"
    INVARIANT_FAILED = "invariant_failed"
    INVARIANT_INCONCLUSIVE = "invariant_inconclusive"
    LLM_FALLBACK = "llm_fallback"


class VerificationOutput(BaseModel):
    """Full verification result with reasoning.

    Attributes:
        result: ALLOW, DENY, or UNCERTAIN.
        reason: Why this decision was made.
        current_state_id: The FSM state the screen was localized to.
        target_state_id: The state the proposed action would transition to.
        confidence: Confidence in the transition (from replay verification).
        details: Human-readable explanation.
    """

    result: VerifyResult
    reason: VerifyReason
    current_state_id: str | None = None
    target_state_id: str | None = None
    confidence: float = 0.0
    details: str = ""


class FsmChecker:
    """Tier 1: Structural FSM verification.

    Implements the VERIFY pseudocode from CLAUDE.md §4.2. Takes a current
    screen and proposed action, returns ALLOW / DENY / UNCERTAIN using pure
    graph lookups on the pre-built FSM.

    Args:
        fsm: The app's verified FSM.
        config: Verification config (for confidence_threshold). Defaults to 0.7.
    """

    def __init__(self, fsm: AppFSM, config: VerificationConfig | None = None) -> None:
        self._fsm = fsm
        self._locator = StateLocator(fsm)
        self._confidence_threshold = config.confidence_threshold if config else 0.7

    def verify(
        self,
        current_screen: RawScreen,
        proposed_action: dict[str, Any],
        goal_state: str | None = None,
    ) -> VerificationOutput:
        """Verify a proposed action against the FSM.

        Localizes the current screen to an FSM state, then checks if the
        proposed action is valid from that state.

        Args:
            current_screen: The current device screen.
            proposed_action: Action dict (e.g., {"type": "click", "target": "e_001"}).
            goal_state: Optional goal state ID for reachability checking.

        Returns:
            VerificationOutput with decision and reasoning.
        """
        location = self._locator.locate(current_screen)

        if location.result == LocateResult.UNKNOWN:
            return VerificationOutput(
                result=VerifyResult.UNCERTAIN,
                reason=VerifyReason.STATE_UNKNOWN,
                details="Current screen does not match any known FSM state",
            )

        if location.result == LocateResult.SIMILAR:
            return VerificationOutput(
                result=VerifyResult.UNCERTAIN,
                reason=VerifyReason.STATE_SIMILAR,
                current_state_id=location.state_id,
                confidence=location.confidence,
                details=(
                    f"Screen matched state {location.state_id} via fuzzy matching "
                    f"(confidence={location.confidence})"
                ),
            )

        # EXACT match — proceed with structural verification
        assert location.state_id is not None
        return self.verify_by_state(location.state_id, proposed_action, goal_state)

    def verify_by_state(
        self,
        current_state_id: str,
        proposed_action: dict[str, Any],
        goal_state: str | None = None,
    ) -> VerificationOutput:
        """Verify when the current FSM state is already known.

        Args:
            current_state_id: The current FSM state ID.
            proposed_action: Action dict (e.g., {"type": "click"}).
            goal_state: Optional goal state ID for reachability checking.

        Returns:
            VerificationOutput with decision and reasoning.
        """
        # 1. Transition validity and action identity resolution
        lookup = self._fsm.resolve_transition(current_state_id, proposed_action)
        if lookup.status is TransitionLookupStatus.UNCERTAIN:
            return VerificationOutput(
                result=VerifyResult.UNCERTAIN,
                reason=VerifyReason.ACTION_AMBIGUOUS,
                current_state_id=current_state_id,
                details=lookup.details,
            )
        if lookup.status is not TransitionLookupStatus.MATCH:
            return VerificationOutput(
                result=VerifyResult.DENY,
                reason=VerifyReason.TRANSITION_INVALID,
                current_state_id=current_state_id,
                details=(
                    f"Action {proposed_action.get('type')} is not a valid transition "
                    f"from state {current_state_id}"
                ),
            )

        # 2. Get target state
        target_id = lookup.target_state_id

        # 3. Goal reachability
        if (
            goal_state is not None
            and target_id is not None
            and not self._fsm.is_reachable(target_id, goal_state)
        ):
            return VerificationOutput(
                result=VerifyResult.DENY,
                reason=VerifyReason.GOAL_UNREACHABLE,
                current_state_id=current_state_id,
                target_state_id=target_id,
                details=(f"Goal state {goal_state} is not reachable from target state {target_id}"),
            )

        # 4. Confidence check (guard-admission policy is enforced by DecisionEngine,
        #    not here — Tier 1 stays purely structural).
        transition = lookup.transition
        confidence = transition.confidence if transition else 0.0
        if confidence < self._confidence_threshold:
            return VerificationOutput(
                result=VerifyResult.UNCERTAIN,
                reason=VerifyReason.LOW_CONFIDENCE,
                current_state_id=current_state_id,
                target_state_id=target_id,
                confidence=confidence,
                details=(
                    f"Transition confidence {confidence:.2f} is below "
                    f"threshold {self._confidence_threshold:.2f}"
                ),
            )

        # 5. All checks passed
        return VerificationOutput(
            result=VerifyResult.ALLOW,
            reason=VerifyReason.TRANSITION_VALID,
            current_state_id=current_state_id,
            target_state_id=target_id,
            confidence=confidence,
            details=f"Valid transition from {current_state_id} to {target_id}",
        )
