"""Tests for vigil.symbolic.state_locator and vigil.symbolic.fsm_checker."""

from __future__ import annotations

import pytest

from vigil.core.config import VerificationConfig
from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel, Transition
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

    def test_ambiguous_fingerprint_is_not_exact(self) -> None:
        fsm = AppFSM(app_package="com.test.app")
        fsm.add_state(
            AbstractState(
                state_id="s_func_a",
                name="Shared Functional A",
                fingerprint="fp_shared",
                structural_fingerprint="struct_func_a",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
        fsm.add_state(
            AbstractState(
                state_id="s_func_b",
                name="Shared Functional B",
                fingerprint="fp_shared",
                structural_fingerprint="struct_func_b",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
        fsm.add_state(
            AbstractState(
                state_id="s_struct_a",
                name="Shared Structural A",
                fingerprint="fp_struct_a",
                structural_fingerprint="struct_shared",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
        fsm.add_state(
            AbstractState(
                state_id="s_struct_b",
                name="Shared Structural B",
                fingerprint="fp_struct_b",
                structural_fingerprint="struct_shared",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )
        fsm.add_state(
            AbstractState(
                state_id="s_unique",
                name="Unique",
                fingerprint="fp_unique",
                structural_fingerprint="struct_unique",
                hierarchy_level=HierarchyLevel.ACTIVITY,
            )
        )

        locator = StateLocator(fsm)

        shared_functional = locator.locate_by_fingerprint("fp_shared")
        assert shared_functional.result != LocateResult.EXACT
        assert shared_functional.state_id is None

        shared_structural = locator.locate_by_fingerprint("struct_shared")
        assert shared_structural.result != LocateResult.EXACT
        assert shared_structural.state_id is None

        unique = locator.locate_by_fingerprint("fp_unique")
        assert unique.result == LocateResult.EXACT
        assert unique.state_id == "s_unique"


# ============================================================
# FsmChecker tests
# ============================================================


class TestFsmChecker:
    @pytest.fixture
    def checker(self, sample_fsm: AppFSM) -> FsmChecker:
        return FsmChecker(sample_fsm)

    def test_verify_allow(self, checker: FsmChecker) -> None:
        out = checker.verify_by_state("s1", {"type": "click", "target": "wifi_entry"})
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
        out = checker.verify_by_state(
            "s2", {"type": "click", "target": "wifi_network"}, goal_state="s1"
        )
        assert out.result == VerifyResult.DENY
        assert out.reason == VerifyReason.GOAL_UNREACHABLE
        assert out.target_state_id == "s3"

    def test_verify_allow_goal_reachable(self, checker: FsmChecker) -> None:
        # s1 → click → s2, goal=s3. s2 can reach s3 → ALLOW
        out = checker.verify_by_state(
            "s1", {"type": "click", "target": "wifi_entry"}, goal_state="s3"
        )
        assert out.result == VerifyResult.ALLOW
        assert out.target_state_id == "s2"

    def test_verify_uncertain_low_confidence(self, sample_fsm: AppFSM) -> None:
        config = VerificationConfig(confidence_threshold=0.99)
        checker = FsmChecker(sample_fsm, config=config)
        # s1 → s2 confidence is 0.95, below 0.99 threshold
        out = checker.verify_by_state("s1", {"type": "click", "target": "wifi_entry"})
        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.LOW_CONFIDENCE
        assert out.confidence == 0.95

    def test_verify_no_goal_skips_reachability(self, checker: FsmChecker) -> None:
        # s2 → click → s3, goal=None. Should skip reachability → ALLOW
        out = checker.verify_by_state("s2", {"type": "click", "target": "wifi_network"})
        assert out.result == VerifyResult.ALLOW
        assert out.target_state_id == "s3"

    def test_verify_by_state_direct(self, checker: FsmChecker) -> None:
        out = checker.verify_by_state("s1", {"type": "click", "target": "wifi_entry"})
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.TRANSITION_VALID

    def test_verify_uncertain_ambiguous_type_only_click(self) -> None:
        fsm = AppFSM(app_package="com.test.app")
        for sid in ("s1", "s2", "s3"):
            fsm.add_state(
                AbstractState(
                    state_id=sid,
                    name=sid,
                    fingerprint=f"fp_{sid}",
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                )
            )
        fsm.add_transition(
            Transition(
                source="s1",
                target="s2",
                action={"type": "click", "target_resource_id": "com.app:id/a"},
                confidence=0.95,
            )
        )
        fsm.add_transition(
            Transition(
                source="s1",
                target="s3",
                action={"type": "click", "target_resource_id": "com.app:id/b"},
                confidence=0.95,
            )
        )

        out = FsmChecker(fsm).verify_by_state("s1", {"type": "click"})

        assert out.result == VerifyResult.UNCERTAIN
        assert out.reason == VerifyReason.ACTION_AMBIGUOUS


class TestStructuralPurity:
    """FsmChecker stays purely structural."""

    def test_missing_required_guard_still_allows_when_confidence_passes(self) -> None:
        from vigil.models.guard import GuardAdmissionStatus

        fsm = AppFSM(app_package="com.test.app")
        for sid, name, fp in [("s1", "Source", "fp_s1"), ("s2", "Target", "fp_s2")]:
            fsm.add_state(
                AbstractState(
                    state_id=sid,
                    name=name,
                    fingerprint=fp,
                    hierarchy_level=HierarchyLevel.ACTIVITY,
                )
            )
        fsm.initial_state = "s1"
        fsm.add_transition(
            Transition(
                source="s1",
                target="s2",
                action={"type": "click", "target": "pay_button", "target_text": "Pay"},
                confidence=0.95,
                requires_guard=True,
                guard=None,
                guard_admission_status=GuardAdmissionStatus.ADMITTED,
            )
        )

        out = FsmChecker(fsm).verify_by_state(
            "s1", {"type": "click", "target": "pay_button", "target_text": "Pay"}
        )

        # Structurally valid + confident -> ALLOW. Legacy guard metadata is ignored.
        assert out.result == VerifyResult.ALLOW
        assert out.reason == VerifyReason.TRANSITION_VALID
