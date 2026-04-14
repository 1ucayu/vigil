"""State invariant checking (Daikon-style).

Verifies that state-level invariants hold for the current screen.
Called as post-arrival check after an agent reaches a new state.
Invariants are mined during offline FSM construction (Stage 2.5)
and stored in AbstractState.state_invariants.
"""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel

from vigil.models.fsm import AppFSM
from vigil.symbolic.dsl_evaluator import DSLEvaluator, ScreenContext


class InvariantCheckResult(BaseModel):
    """Result of checking all invariants for a state."""

    state_id: str
    all_passed: bool
    total: int
    passed: int
    failed: int
    failed_invariants: list[tuple[str, str]] = []


class InvariantChecker:
    """Checks state invariants against runtime screen state.

    Uses DSLEvaluator to evaluate each invariant expression from
    AbstractState.state_invariants against the current ScreenContext.
    """

    def __init__(self, fsm: AppFSM, evaluator: DSLEvaluator | None = None) -> None:
        self._fsm = fsm
        self._evaluator = evaluator or DSLEvaluator()

    def check_state(
        self,
        state_id: str,
        screen_ctx: ScreenContext,
    ) -> InvariantCheckResult:
        """Check all invariants for a state against current screen."""
        state = self._fsm.states.get(state_id)
        if state is None:
            logger.warning(f"State {state_id} not found in FSM")
            return InvariantCheckResult(
                state_id=state_id, all_passed=True, total=0, passed=0, failed=0
            )

        invariants = state.state_invariants
        if not invariants:
            return InvariantCheckResult(
                state_id=state_id, all_passed=True, total=0, passed=0, failed=0
            )

        passed_count = 0
        failed_list: list[tuple[str, str]] = []

        for inv_expr in invariants:
            result = self._evaluator.evaluate(inv_expr, screen_ctx=screen_ctx)
            if result.passed:
                passed_count += 1
            else:
                reason = result.failure_reason or f"Invariant evaluated to False: {inv_expr}"
                failed_list.append((inv_expr, reason))
                logger.debug(f"Invariant failed for {state_id}: {inv_expr} — {reason}")

        return InvariantCheckResult(
            state_id=state_id,
            all_passed=len(failed_list) == 0,
            total=len(invariants),
            passed=passed_count,
            failed=len(failed_list),
            failed_invariants=failed_list,
        )

    def check_arrival(
        self,
        state_id: str,
        screen_ctx: ScreenContext,
    ) -> bool:
        """Quick check: do all invariants pass? Returns True/False."""
        return self.check_state(state_id, screen_ctx).all_passed
