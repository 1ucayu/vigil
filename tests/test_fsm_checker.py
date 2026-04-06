"""Tests for vigil.symbolic.state_locator and vigil.symbolic.fsm_checker."""

from __future__ import annotations

import pytest

from vigil.core.config import VerificationConfig
from vigil.models.fsm import AppFSM
from vigil.symbolic.fsm_checker import FsmChecker, VerifyReason, VerifyResult
from vigil.symbolic.state_locator import LocateResult, StateLocator

# ============================================================
# StateLocator tests
# ============================================================


class TestStateLocator:
    def test_locate_by_fingerprint_exact(self, sample_fsm: AppFSM) -> None:
        locator = StateLocator(sample_fsm)
        loc = locator.locate_by_fingerprint("fp_main")
        assert loc.result == LocateResult.EXACT
        assert loc.state_id == "s1"
        assert loc.confidence == 1.0

    def test_locate_by_fingerprint_unknown(self, sample_fsm: AppFSM) -> None:
        locator = StateLocator(sample_fsm)
        loc = locator.locate_by_fingerprint("fp_nonexistent")
        assert loc.result == LocateResult.UNKNOWN
        assert loc.state_id is None
        assert loc.confidence == 0.0

    def test_locate_by_fingerprint_all_states(self, sample_fsm: AppFSM) -> None:
        locator = StateLocator(sample_fsm)
        expected = {"fp_main": "s1", "fp_wifi": "s2", "fp_wifi_detail": "s3"}
        for fp, expected_id in expected.items():
            loc = locator.locate_by_fingerprint(fp)
            assert loc.result == LocateResult.EXACT
            assert loc.state_id == expected_id


# ============================================================
# FsmChecker tests
# ============================================================


class TestFsmChecker:
    @pytest.fixture
    def checker(self, sample_fsm: AppFSM) -> FsmChecker:
        return FsmChecker(sample_fsm)

    def test_verify_allow(self, checker: FsmChecker) -> None:
        out = checker.verify_by_state("s1", {"type": "click"})
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.TRANSITION_VALID
        assert out.current_state_id == "s1"
        assert out.target_state_id == "s2"
        assert out.confidence == 0.95

    def test_verify_deny_invalid(self, checker: FsmChecker) -> None:
        out = checker.verify_by_state("s1", {"type": "scroll_up"})
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.TRANSITION_INVALID
        assert out.current_state_id == "s1"

    def test_verify_deny_no_outgoing(self, checker: FsmChecker) -> None:
        out = checker.verify_by_state("s3", {"type": "click"})
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.TRANSITION_INVALID

    def test_verify_deny_nonexistent_state(self, checker: FsmChecker) -> None:
        out = checker.verify_by_state("s_nonexistent", {"type": "click"})
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.TRANSITION_INVALID

    def test_verify_deny_goal_unreachable(self, checker: FsmChecker) -> None:
        # s2 → click → s3, goal=s1. s3 has no outgoing edges → can't reach s1
        out = checker.verify_by_state("s2", {"type": "click"}, goal_state="s1")
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.GOAL_UNREACHABLE
        assert out.target_state_id == "s3"

    def test_verify_allow_goal_reachable(self, checker: FsmChecker) -> None:
        # s1 → click → s2, goal=s3. s2 can reach s3 → ALLOW
        out = checker.verify_by_state("s1", {"type": "click"}, goal_state="s3")
        assert out.result == VerifyResult.ALLOW
        assert out.target_state_id == "s2"

    def test_verify_uncertain_low_confidence(self, sample_fsm: AppFSM) -> None:
        config = VerificationConfig(confidence_threshold=0.99)
        checker = FsmChecker(sample_fsm, config=config)
        # s1 → s2 confidence is 0.95, below 0.99 threshold
        out = checker.verify_by_state("s1", {"type": "click"})
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.LOW_CONFIDENCE
        assert out.confidence == 0.95

    def test_verify_no_goal_skips_reachability(self, checker: FsmChecker) -> None:
        # s2 → click → s3, goal=None. Should skip reachability → ALLOW
        out = checker.verify_by_state("s2", {"type": "click"})
        assert out.result == VerifyResult.ALLOW
        assert out.target_state_id == "s3"

    def test_verify_by_state_direct(self, checker: FsmChecker) -> None:
        out = checker.verify_by_state("s1", {"type": "click"})
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.TRANSITION_VALID
