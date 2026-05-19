"""Tests for vigil.symbolic.trajectory_verifier."""

from __future__ import annotations

import pytest

from vigil.models.fsm import AppFSM
from vigil.symbolic.fsm_checker import VerifyReason, VerifyResult
from vigil.symbolic.trajectory_verifier import TrajectoryStep, TrajectoryVerifier

# Identity-bearing click actions matching the sample_fsm fixture in
# tests/conftest.py: s1→s2 stores target="wifi_entry", s2→s3 stores
# target="wifi_network". Canonical action matching now requires identity,
# so trajectory_verifier tests must supply it (do NOT weaken matching).
_CLICK_S1 = {"type": "click", "target": "wifi_entry"}
_CLICK_S2 = {"type": "click", "target": "wifi_network"}


class TestVerifyTrajectory:
    @pytest.fixture
    def verifier(self, sample_fsm: AppFSM) -> TrajectoryVerifier:
        return TrajectoryVerifier(sample_fsm)

    def test_valid_trajectory(self, verifier: TrajectoryVerifier) -> None:
        steps = [
            TrajectoryStep(action=_CLICK_S1),
            TrajectoryStep(action=_CLICK_S2),
        ]
        result = verifier.verify_trajectory("s1", steps)
        assert result.overall_result == VerifyResult.ALLOW
        assert result.furthest_valid_step == 1
        assert result.total_steps == 2
        assert len(result.step_results) == 2
        assert all(r.result == VerifyResult.ALLOW for r in result.step_results)

    def test_trajectory_blocked_midway(self, verifier: TrajectoryVerifier) -> None:
        steps = [
            TrajectoryStep(action=_CLICK_S1),
            TrajectoryStep(action={"type": "scroll_up"}),
        ]
        result = verifier.verify_trajectory("s1", steps)
        assert result.overall_result == VerifyResult.DENY
        assert result.furthest_valid_step == 0
        assert result.total_steps == 2
        assert result.step_results[0].result == VerifyResult.ALLOW
        assert result.step_results[1].result == VerifyResult.DENY
        assert result.step_results[1].reason == VerifyReason.TRANSITION_INVALID

    def test_trajectory_with_goal_reached(self, verifier: TrajectoryVerifier) -> None:
        steps = [
            TrajectoryStep(action=_CLICK_S1),
            TrajectoryStep(action=_CLICK_S2),
        ]
        result = verifier.verify_trajectory("s1", steps, goal_state="s3")
        assert result.overall_result == VerifyResult.ALLOW
        assert result.furthest_valid_step == 1

    def test_trajectory_goal_not_reached_but_reachable(self, verifier: TrajectoryVerifier) -> None:
        # s1 → s2 (click), goal=s3. s2 can reach s3, so per-step check passes.
        steps = [TrajectoryStep(action=_CLICK_S1)]
        result = verifier.verify_trajectory("s1", steps, goal_state="s3")
        assert result.overall_result == VerifyResult.ALLOW
        assert result.furthest_valid_step == 0

    def test_empty_trajectory(self, verifier: TrajectoryVerifier) -> None:
        result = verifier.verify_trajectory("s1", [])
        assert result.overall_result == VerifyResult.ALLOW
        assert result.furthest_valid_step == -1
        assert result.total_steps == 0
        assert result.step_results == []

    def test_trajectory_denied_at_first_step(self, verifier: TrajectoryVerifier) -> None:
        steps = [TrajectoryStep(action={"type": "scroll_up"})]
        result = verifier.verify_trajectory("s1", steps)
        assert result.overall_result == VerifyResult.DENY
        assert result.furthest_valid_step == -1

    def test_trajectory_from_terminal_state(self, verifier: TrajectoryVerifier) -> None:
        # s3 has no outgoing transitions
        steps = [TrajectoryStep(action=_CLICK_S1)]
        result = verifier.verify_trajectory("s3", steps)
        assert result.overall_result == VerifyResult.DENY


class TestVerifyRealtime:
    @pytest.fixture
    def verifier(self, sample_fsm: AppFSM) -> TrajectoryVerifier:
        return TrajectoryVerifier(sample_fsm)

    def test_realtime_basic(self, verifier: TrajectoryVerifier) -> None:
        result = verifier.verify_realtime("s1", _CLICK_S1)
        assert result.result == VerifyResult.ALLOW
        assert result.reason == VerifyReason.TRANSITION_VALID

    def test_realtime_loop_detection(self, verifier: TrajectoryVerifier) -> None:
        # 5 occurrences of "s1" → triggers loop detection (threshold=5)
        history = ["s1", "s2", "s1", "s2", "s1", "s2", "s1", "s2", "s1"]
        result = verifier.verify_realtime("s1", _CLICK_S1, trajectory_history=history)
        assert result.result == VerifyResult.UNCERTAIN
        assert "loop detected" in result.details.lower()

    def test_realtime_no_history(self, verifier: TrajectoryVerifier) -> None:
        result = verifier.verify_realtime("s1", _CLICK_S1, trajectory_history=None)
        assert result.result == VerifyResult.ALLOW

    def test_realtime_short_history_no_loop(self, verifier: TrajectoryVerifier) -> None:
        history = ["s1", "s2", "s1"]
        result = verifier.verify_realtime("s1", _CLICK_S1, trajectory_history=history)
        # Only 2 visits to s1, below threshold of 5
        assert result.result == VerifyResult.ALLOW

    def test_realtime_deny_invalid(self, verifier: TrajectoryVerifier) -> None:
        result = verifier.verify_realtime("s1", {"type": "scroll_up"})
        assert result.result == VerifyResult.DENY
        assert result.reason == VerifyReason.TRANSITION_INVALID
