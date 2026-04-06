"""Trajectory-level verification for action sequences.

Verifies planned action trajectories by simulating execution on the FSM graph,
and provides per-action verification with trajectory history awareness (loop detection).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from vigil.core.config import VerificationConfig
from vigil.models.fsm import AppFSM
from vigil.symbolic.fsm_checker import FsmChecker, VerificationOutput, VerifyReason, VerifyResult


class TrajectoryStep(BaseModel):
    """A single step in a planned trajectory.

    Attributes:
        action: Action dict (e.g., {"type": "click", "target": "e_001"}).
        expected_state: Optional hint for plan verification.
    """

    action: dict[str, Any]
    expected_state: str | None = None


class TrajectoryVerification(BaseModel):
    """Result of verifying an entire action sequence.

    Attributes:
        overall_result: ALLOW only if ALL steps pass.
        step_results: Per-step verification results.
        furthest_valid_step: Index of the last ALLOW step (-1 if none).
        total_steps: Number of steps in the trajectory.
    """

    overall_result: VerifyResult
    step_results: list[VerificationOutput]
    furthest_valid_step: int
    total_steps: int


_LOOP_THRESHOLD = 5


class TrajectoryVerifier:
    """Verifies action sequences against the FSM by simulating execution.

    Two modes:
    - verify_trajectory(): simulate entire plan on FSM offline (no device)
    - verify_realtime(): per-action with trajectory history awareness

    Args:
        fsm: The app's verified FSM.
        config: Verification config (for confidence_threshold).
    """

    def __init__(self, fsm: AppFSM, config: VerificationConfig | None = None) -> None:
        self._fsm = fsm
        self._checker = FsmChecker(fsm, config)

    def verify_trajectory(
        self,
        start_state_id: str,
        actions: list[TrajectoryStep],
        goal_state: str | None = None,
    ) -> TrajectoryVerification:
        """Simulate the action sequence on the FSM graph.

        Walks through each action, checking transition validity at each step.
        Stops on DENY but continues through UNCERTAIN steps.

        Args:
            start_state_id: FSM state to start from.
            actions: Ordered list of trajectory steps.
            goal_state: Optional goal state for reachability checking.

        Returns:
            TrajectoryVerification with per-step results.
        """
        if not actions:
            return TrajectoryVerification(
                overall_result=VerifyResult.ALLOW,
                step_results=[],
                furthest_valid_step=-1,
                total_steps=0,
            )

        current_state = start_state_id
        step_results: list[VerificationOutput] = []
        furthest_valid = -1
        has_uncertain = False

        for i, step in enumerate(actions):
            result = self._checker.verify_by_state(current_state, step.action, goal_state)
            step_results.append(result)

            if result.result == VerifyResult.DENY:
                return TrajectoryVerification(
                    overall_result=VerifyResult.DENY,
                    step_results=step_results,
                    furthest_valid_step=furthest_valid,
                    total_steps=len(actions),
                )

            if result.result == VerifyResult.UNCERTAIN:
                has_uncertain = True
                # Continue but don't advance state — can't determine target
                continue

            # ALLOW — advance to target state
            furthest_valid = i
            target = self._fsm.get_transition_target(current_state, step.action)
            if target is None:
                # Defensive: ALLOW but no target — treat as UNCERTAIN, stop
                step_results[-1] = VerificationOutput(
                    result=VerifyResult.UNCERTAIN,
                    reason=VerifyReason.STATE_UNKNOWN,
                    current_state_id=current_state,
                    details="Transition target unknown after ALLOW",
                )
                has_uncertain = True
                break
            current_state = target

        overall = VerifyResult.UNCERTAIN if has_uncertain else VerifyResult.ALLOW
        return TrajectoryVerification(
            overall_result=overall,
            step_results=step_results,
            furthest_valid_step=furthest_valid,
            total_steps=len(actions),
        )

    def verify_realtime(
        self,
        current_state_id: str,
        proposed_action: dict[str, Any],
        trajectory_history: list[str] | None = None,
        goal_state: str | None = None,
    ) -> VerificationOutput:
        """Per-action verification with trajectory awareness.

        Checks for loops in the trajectory history before delegating to
        the structural FSM checker.

        Args:
            current_state_id: Current FSM state.
            proposed_action: The action to verify.
            trajectory_history: List of state_ids visited so far in this task.
            goal_state: Target state for reachability check.

        Returns:
            VerificationOutput with decision and reasoning.
        """
        # Check for loops in trajectory history
        if trajectory_history:
            visit_count = trajectory_history.count(current_state_id)
            if visit_count >= _LOOP_THRESHOLD:
                return VerificationOutput(
                    result=VerifyResult.UNCERTAIN,
                    reason=VerifyReason.LOW_CONFIDENCE,
                    current_state_id=current_state_id,
                    details=(
                        f"Possible loop detected: visited state "
                        f"{current_state_id} {visit_count} times"
                    ),
                )

        return self._checker.verify_by_state(current_state_id, proposed_action, goal_state)
