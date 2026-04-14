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

    def test_check_arrival_true(self, fsm_with_invariants: AppFSM) -> None:
        checker = InvariantChecker(fsm_with_invariants)
        ctx = ScreenContext(
            elements={
                "tab_layout": {"children_count": 4},
                "fab_button": {"is_clickable": True},
            }
        )
        assert checker.check_arrival("s1", ctx) is True

    def test_check_arrival_false(self, fsm_with_invariants: AppFSM) -> None:
        checker = InvariantChecker(fsm_with_invariants)
        ctx = ScreenContext(
            elements={
                "tab_layout": {"children_count": 2},
                "fab_button": {"is_clickable": False},
            }
        )
        assert checker.check_arrival("s1", ctx) is False
