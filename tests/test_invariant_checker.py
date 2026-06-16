"""Tests for vigil.symbolic.invariant_checker."""

from __future__ import annotations

import pytest

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel
from vigil.symbolic.dsl_evaluator import ScreenContext
from vigil.symbolic.invariant_checker import InvariantChecker


@pytest.fixture
def fsm_with_invariants() -> AppFSM:
    fsm = AppFSM(app_package="com.test.app")
    s1 = AbstractState(
        state_id="s1",
        name="AlarmList",
        fingerprint="fp1",
        hierarchy_level=HierarchyLevel.FRAGMENT,
        state_invariants=[
            "count(tab_layout) == 4",
            "read(fab_button, is_clickable) == true",
        ],
        invariant_confidence=0.8,
    )
    s2 = AbstractState(
        state_id="s2",
        name="Settings",
        fingerprint="fp2",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        state_invariants=[],
    )
    fsm.add_state(s1)
    fsm.add_state(s2)
    fsm.initial_state = "s1"
    return fsm


class TestInvariantChecker:
    def test_all_pass(self, fsm_with_invariants: AppFSM) -> None:
        checker = InvariantChecker(fsm_with_invariants)
        ctx = ScreenContext(
            elements={
                "tab_layout": {"children_count": 4},
                "fab_button": {"is_clickable": True},
            }
        )
        result = checker.check_state("s1", ctx)
        assert result.all_passed
        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0

    def test_one_fails(self, fsm_with_invariants: AppFSM) -> None:
        checker = InvariantChecker(fsm_with_invariants)
        ctx = ScreenContext(
            elements={
                "tab_layout": {"children_count": 3},
                "fab_button": {"is_clickable": True},
            }
        )
        result = checker.check_state("s1", ctx)
        assert not result.all_passed
        assert result.passed == 1
        assert result.failed == 1
        assert len(result.failed_invariants) == 1
        assert "count(tab_layout)" in result.failed_invariants[0][0]

    def test_no_invariants(self, fsm_with_invariants: AppFSM) -> None:
        checker = InvariantChecker(fsm_with_invariants)
        result = checker.check_state("s2", ScreenContext())
        assert result.all_passed
        assert result.total == 0

    def test_unknown_state(self, fsm_with_invariants: AppFSM) -> None:
        checker = InvariantChecker(fsm_with_invariants)
        result = checker.check_state("nonexistent", ScreenContext())
        assert result.all_passed
        assert result.total == 0

    def test_all_state_invariants_pass_true(self, fsm_with_invariants: AppFSM) -> None:
        checker = InvariantChecker(fsm_with_invariants)
        ctx = ScreenContext(
            elements={
                "tab_layout": {"children_count": 4},
                "fab_button": {"is_clickable": True},
            }
        )
        assert checker.all_state_invariants_pass("s1", ctx) is True

    def test_all_state_invariants_pass_false(self, fsm_with_invariants: AppFSM) -> None:
        checker = InvariantChecker(fsm_with_invariants)
        ctx = ScreenContext(
            elements={
                "tab_layout": {"children_count": 2},
                "fab_button": {"is_clickable": False},
            }
        )
        assert checker.all_state_invariants_pass("s1", ctx) is False


# ── Three-valued invariants ─────────────────────────────────────


from vigil.symbolic.dsl_evaluator import DSLEvaluator  # noqa: E402


class TestInvariantThreeValued:
    def _fsm(self, invariants: list[str]) -> AppFSM:
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s1",
                name="X",
                fingerprint="fp",
                hierarchy_level=HierarchyLevel.ACTIVITY,
                state_invariants=invariants,
            )
        )
        return fsm

    def test_unknown_invariant_does_not_count_as_failure(self) -> None:
        fsm = self._fsm(["count(missing) == 4"])
        checker = InvariantChecker(fsm, evaluator=DSLEvaluator())
        result = checker.check_state("s1", ScreenContext())
        # No FALSE -> all_passed stays True; UNKNOWN is reported separately.
        assert result.all_passed is True
        assert result.has_unknown is True
        assert result.unknown == 1
        assert result.failed == 0

    def test_false_invariant_dominates_unknown(self) -> None:
        fsm = self._fsm(["count(missing) == 4", "count(present) == 4"])
        checker = InvariantChecker(fsm, evaluator=DSLEvaluator())
        ctx = ScreenContext(elements={"present": {"children_count": 3}})
        result = checker.check_state("s1", ctx)
        assert result.failed == 1
        assert result.unknown == 1
        assert result.all_passed is False
